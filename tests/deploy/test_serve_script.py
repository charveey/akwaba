"""Lot 11a — deploy/tailscale/serve-8443.sh contre un faux `tailscale`.

Le faux binaire conserve un état JSON au format de `tailscale serve status --json`
(Funnel actif sur 443 vers 127.0.0.1:3000) et journalise chaque commande reçue.
"""
import json
import os
import pathlib
import shutil
import socket
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "tailscale" / "serve-8443.sh"
HOST = "ubuntu.tail14614.ts.net"

FUNNEL_443 = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {f"{HOST}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}},
    "AllowFunnel": {f"{HOST}:443": True},
}

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
state_file = os.environ["FAKE_TS_STATE"]
with open(os.environ["FAKE_TS_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
state = json.load(open(state_file))
args = sys.argv[1:]
host = "ubuntu.tail14614.ts.net"
if args[:2] == ["serve", "status"]:
    print(json.dumps(state) if "--json" in args else "status"); sys.exit(0)
if args and args[0] == "serve":
    if "reset" in args:
        state = {}
    elif args[-1] == "off":
        port = [a for a in args if a.startswith("--https=")][0].split("=")[1]
        state.get("TCP", {}).pop(port, None); state.get("Web", {}).pop(f"{host}:{port}", None)
    else:
        port = [a for a in args if a.startswith("--https=")][0].split("=")[1]
        state.setdefault("TCP", {})[port] = {"HTTPS": True}
        state.setdefault("Web", {})[f"{host}:{port}"] = {"Handlers": {"/": {"Proxy": args[-1]}}}
        if os.environ.get("FAKE_TS_BREAK_443"):
            state["AllowFunnel"] = {}
    json.dump(state, open(state_file, "w")); sys.exit(0)
sys.exit(1)
'''


@pytest.fixture
def env(tmp_path):
    if shutil.which("python3") is None:
        pytest.skip("python3 absent")
    repo = tmp_path / "repo"
    (repo / "deploy" / "tailscale").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "deploy" / "tailscale" / "serve-8443.sh")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "tailscale"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "state.json"
    state.write_text(json.dumps(FUNNEL_443))
    log = tmp_path / "calls.log"
    log.write_text("")
    e = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_TS_STATE": str(state), "FAKE_TS_LOG": str(log)}
    return {"repo": repo, "state": state, "log": log, "env": e}


@pytest.fixture
def nginx_up():
    """Simule Nginx sur 127.0.0.1:8080 (le script vérifie qu'il répond avant d'activer 8443)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

        def log_message(self, *a):
            pass
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 8080), H)
    except OSError:
        pytest.skip("port 8080 occupé")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield
    srv.shutdown()
    srv.server_close()


def run(env, *args, extra=None):
    return subprocess.run(["bash", str(env["repo"] / "deploy" / "tailscale" / "serve-8443.sh"), *args],
                          env={**env["env"], **(extra or {})}, capture_output=True, text=True)


def calls(env):
    return env["log"].read_text().splitlines()


def test_adds_8443_and_keeps_443_intact(env, nginx_up):
    r = run(env)
    assert r.returncode == 0, r.stderr
    state = json.loads(env["state"].read_text())
    assert state["Web"][f"{HOST}:8443"]["Handlers"]["/"]["Proxy"] == "http://127.0.0.1:8080"
    assert state["Web"][f"{HOST}:443"] == FUNNEL_443["Web"][f"{HOST}:443"]
    assert state["AllowFunnel"] == {f"{HOST}:443": True}          # pas de Funnel sur 8443
    assert "serve --bg --https=8443 http://127.0.0.1:8080" in calls(env)
    backups = list((env["repo"] / "backups").glob("tailscale-serve-*.json"))
    assert backups and json.loads(backups[0].read_text()) == FUNNEL_443


def test_never_resets_nor_touches_funnel(env, nginx_up):
    run(env)
    run(env, "--remove")
    for line in calls(env):
        assert "reset" not in line
        assert not line.startswith("funnel")


def test_idempotent(env, nginx_up):
    assert run(env).returncode == 0
    r = run(env)
    assert r.returncode == 0
    assert "rien à faire" in r.stdout
    assert sum(1 for c in calls(env) if c.startswith("serve --bg")) == 1


def test_refuses_when_8443_already_used(env):
    s = json.loads(env["state"].read_text())
    s["TCP"]["8443"] = {"HTTPS": True}
    s["Web"][f"{HOST}:8443"] = {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}}
    env["state"].write_text(json.dumps(s))
    r = run(env)
    assert r.returncode != 0
    assert "déjà utilisé" in r.stderr
    assert not any(c.startswith("serve --bg") for c in calls(env))


def test_refuses_when_nginx_down(env):
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8080)) == 0:
            pytest.skip("port 8080 occupé")
    r = run(env)
    assert r.returncode != 0
    assert "Nginx ne répond pas" in r.stderr
    assert not any(c.startswith("serve --bg") for c in calls(env))


def test_detects_change_on_443(env, nginx_up):
    r = run(env, extra={"FAKE_TS_BREAK_443": "1"})
    assert r.returncode != 0
    assert "PORT 443 A CHANGÉ" in r.stderr


def test_check_mode_changes_nothing(env):
    r = run(env, "--check")
    assert r.returncode == 0
    assert json.loads(env["state"].read_text()) == FUNNEL_443
    assert not any(c.startswith("serve --") for c in calls(env))


def test_remove_only_removes_8443(env, nginx_up):
    run(env)
    r = run(env, "--remove")
    assert r.returncode == 0, r.stderr
    assert json.loads(env["state"].read_text()) == {**FUNNEL_443, "TCP": {"443": {"HTTPS": True}}}
    assert "serve --https=8443 off" in calls(env)
