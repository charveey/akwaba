"""Lot 13c — deploy/agent/update.sh (mise à jour de l'agent sur l'Exit Node).

Le VRAI agent est utilisé pour les pings (configuration, secret, signature HMAC) contre un faux backend.
`systemctl`, `journalctl` et `sudo` sont simulés ; l'état du service dépend du code installé (un fichier
marqueur CASSE dans le nouveau code fait échouer le service après redémarrage). Les répertoires de
production sont remplacés par des répertoires temporaires (variables AGENT_HOME, AGENT_CONF, UNIT_FILE).
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import threading
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "agent" / "update.sh"
AGENT_SRC = ROOT / "agent" / "akwaba_agent"

pytestmark = pytest.mark.skipif(os.geteuid() != 0 or not os.path.exists("/usr/bin/python3"),
                                reason="nécessite root (le script le vérifie) et /usr/bin/python3")

FAKES = {
    "systemctl": r'''#!/bin/sh
st="$FAKE_DIR/service"
echo "systemctl $*" >> "$FAKE_DIR/calls"
case "$1" in
  restart) if [ -f "$AGENT_HOME/akwaba_agent/CASSE" ]; then echo failed > "$st"; else echo active > "$st"; fi ;;
  is-active) cat "$st" ;;
  show) cat "$FAKE_DIR/nrestarts" 2>/dev/null || echo 0 ;;
esac
''',
    "journalctl": '#!/bin/sh\necho "journalctl $*" >> "$FAKE_DIR/calls"\ncat "$FAKE_DIR/journal" 2>/dev/null\n',
    "sudo": '#!/bin/sh\necho "sudo $*" >> "$FAKE_DIR/calls"\n[ "$1" = -u ] && shift 2\nexec "$@"\n',
}


class _Backend(BaseHTTPRequestHandler):
    refuse = False

    def do_POST(self):  # noqa: N802
        import sys
        sys.path.insert(0, str(ROOT / "agent"))
        from akwaba_agent.protocol import verify
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        h = self.headers
        signed = (h.get("X-Akwaba-Agent") == "agt_0123456789abcdef" and
                  verify(bytes.fromhex("ab" * 32), "POST", self.path, body, h.get("X-Akwaba-Timestamp", ""),
                         h.get("X-Akwaba-Nonce", ""), h.get("X-Akwaba-Signature", "")))
        ok = not self.refuse and self.path == "/api/agent/ping" and signed
        body = json.dumps({"exit_node": "exit-test", "server_time": "2026-11-24T19:00:00Z"} if ok
                          else {"detail": "Non authentifié"}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Date", formatdate(usegmt=True))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def backend():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Backend)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Backend.refuse = False
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def copy_agent(dest: pathlib.Path, version: str) -> pathlib.Path:
    shutil.copytree(AGENT_SRC, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    init = dest / "__init__.py"
    init.write_text(re.sub(r'__version__ = ".*"', f'__version__ = "{version}"', init.read_text()))
    return dest


@pytest.fixture
def env(tmp_path, backend):
    home, etc, fake = tmp_path / "opt", tmp_path / "etc", tmp_path / "fake"
    for d in (home, etc, fake):
        d.mkdir()
    home.chmod(0o755)
    copy_agent(home / "akwaba_agent", "0.1.0-ancien")
    secret = etc / "secret"
    secret.write_text("ab" * 32 + "\n")
    secret.chmod(0o600)
    conf = etc / "agent.ini"
    conf.write_text(f"[agent]\nbackend_url = {backend}\nagent_id = agt_0123456789abcdef\nsecret_path = {secret}\n")
    unit = etc / "akwaba-agent.service"
    shutil.copy(ROOT / "deploy" / "agent" / "akwaba-agent.service", unit)
    sudoers = etc / "sudoers-akwaba-agent"
    sudoers.write_text("akwaba-agent ALL=(root) NOPASSWD: /usr/sbin/conntrack -L -f ipv4\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in FAKES.items():
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    (fake / "service").write_text("active\n")
    new = copy_agent(tmp_path / "src" / "akwaba_agent", "0.2.0")
    return {"home": home, "conf": conf, "secret": secret, "unit": unit, "sudoers": sudoers, "fake": fake, "new": new,
            "env": {**{k: v for k, v in os.environ.items() if "proxy" not in k.lower()},
                    "PATH": f"{bindir}:{os.environ['PATH']}", "AGENT_HOME": str(home), "AGENT_CONF": str(conf),
                    "UNIT_FILE": str(unit), "AGENT_USER": "root", "PYTHON": "/usr/bin/python3",
                    "STABLE_SECONDS": "0", "FAKE_DIR": str(fake)}}


def run(env, *args):
    res = subprocess.run(["sh", str(SCRIPT), *args], env=env["env"], capture_output=True, text=True, timeout=120)
    return res.returncode, res.stdout + res.stderr


def version(path: pathlib.Path) -> str:
    return re.search(r'__version__ = "(.*)"', (path / "__init__.py").read_text()).group(1)


def calls(env):
    f = env["fake"] / "calls"
    return f.read_text().splitlines() if f.exists() else []


def restarts(env):
    return [c for c in calls(env) if c.startswith("systemctl restart")]


def fingerprint(env):
    """agent.ini, secret, unité, sudoers : jamais modifiés par la mise à jour."""
    return {k: hashlib.sha256(env[k].read_bytes()).hexdigest() + oct(env[k].stat().st_mode)
            for k in ("conf", "secret", "unit", "sudoers")}


def test_update_switches_code_after_a_signed_ping_with_the_new_code(env):
    before = fingerprint(env)
    rc, out = run(env, str(env["new"]))
    assert rc == 0, out
    assert version(env["home"] / "akwaba_agent") == "0.2.0"
    assert version(env["home"] / "precedent" / "akwaba_agent") == "0.1.0-ancien"
    assert out.count("ping=ok noeud=exit-test") == 2, "ping avec le nouveau code AVANT la bascule, puis après"
    assert out.index("ping=ok") < out.index("3/4 Bascule")
    assert len(restarts(env)) == 1
    assert fingerprint(env) == before
    assert not list(env["home"].glob(".maj-*")), "copie temporaire supprimée"
    installed = env["home"] / "akwaba_agent"
    assert not list(installed.rglob("__pycache__"))
    assert all(p.stat().st_uid == 0 and not p.stat().st_mode & 0o022 for p in [installed, *installed.rglob("*")])
    sudo_calls = [c for c in calls(env) if c.startswith("sudo")]
    assert len(sudo_calls) == 2 and all("-u root" in c and "--ping" in c for c in sudo_calls)


def test_identical_code_changes_nothing(env):
    rc, out = run(env, str(env["home"] / "akwaba_agent"))
    assert rc == 0 and "rien à faire" in out
    assert restarts(env) == [] and not (env["home"] / "precedent").exists()


def test_plan_mode_pings_but_changes_nothing(env):
    rc, out = run(env, str(env["new"]), "--plan")
    assert rc == 0, out
    assert "ping=ok" in out and "Mode --plan" in out
    assert version(env["home"] / "akwaba_agent") == "0.1.0-ancien"
    assert restarts(env) == [] and not list(env["home"].glob(".maj-*"))


def test_code_that_does_not_compile_is_refused(env):
    (env["new"] / "config.py").write_text((env["new"] / "config.py").read_text() + "\ndef cassé(:\n")
    rc, out = run(env, str(env["new"]))
    assert rc == 1 and "ne compile pas" in out and "Rien n'a été modifié" in out
    assert version(env["home"] / "akwaba_agent") == "0.1.0-ancien" and restarts(env) == []


def test_new_code_that_cannot_authenticate_is_refused(env):
    _Backend.refuse = True
    rc, out = run(env, str(env["new"]))
    assert rc == 1 and "ping=echec" in out and "Rien n'a été modifié" in out
    assert version(env["home"] / "akwaba_agent") == "0.1.0-ancien" and restarts(env) == []


def test_new_code_that_cannot_load_the_configuration_is_refused(env):
    cfg = env["new"] / "config.py"
    cfg.write_text(cfg.read_text().replace("def load_config(path: str, must_exist: bool = True) -> Config:",
                                           "def load_config(path: str, must_exist: bool = True) -> Config:\n"
                                           "    raise ConfigError('clé inconnue')\n", 1))
    rc, out = run(env, str(env["new"]))
    assert rc == 1 and "configuration invalide" in out
    assert version(env["home"] / "akwaba_agent") == "0.1.0-ancien" and restarts(env) == []


def test_service_failing_after_restart_rolls_back_automatically(env):
    before = fingerprint(env)
    (env["new"] / "CASSE").write_text("")
    rc, out = run(env, str(env["new"]))
    assert rc == 1, out
    assert "Retour automatique au code précédent" in out and "Code précédent rétabli et fonctionnel" in out
    assert version(env["home"] / "akwaba_agent") == "0.1.0-ancien"
    assert version(env["home"] / "echec" / "akwaba_agent") == "0.2.0"
    assert len(restarts(env)) == 2
    assert fingerprint(env) == before


def test_traceback_in_the_journal_rolls_back(env):
    (env["fake"] / "journal").write_text("Traceback (most recent call last):\n  ...\n")
    rc, out = run(env, str(env["new"]))
    assert rc == 1 and "erreurs dans le journal" in out and "Retour automatique" in out
    assert version(env["home"] / "akwaba_agent") == "0.1.0-ancien"


def test_spontaneous_restarts_roll_back(env):
    (env["fake"] / "nrestarts").write_text("0\n")
    fake = env["env"]["PATH"].split(":")[0] + "/systemctl"
    body = pathlib.Path(fake).read_text().replace(
        "  is-active) cat \"$st\" ;;", "  is-active) echo 3 > \"$FAKE_DIR/nrestarts\"; cat \"$st\" ;;")
    pathlib.Path(fake).write_text(body)
    rc, out = run(env, str(env["new"]))
    assert rc == 1 and "redémarré tout seul" in out


def test_refuses_when_the_agent_is_not_installed(env):
    shutil.rmtree(env["home"] / "akwaba_agent")
    rc, out = run(env, str(env["new"]))
    assert rc == 1 and "install.sh" in out and restarts(env) == []


def test_unit_file_drift_is_reported_not_changed(env):
    env["unit"].write_text(env["unit"].read_text() + "\n# modifiée localement\n")
    before = env["unit"].read_text()
    rc, out = run(env, str(env["new"]))
    assert rc == 0 and "l'unité systemd du dépôt diffère" in out
    assert env["unit"].read_text() == before


def test_script_never_touches_configuration_secret_sudoers_or_collection():
    code = "\n".join(ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#"))
    for forbidden in ("sudoers", "visudo", "daemon-reload", "systemctl enable", "systemctl disable", "conntrack",
                      "tailscale", "agent.db", "> \"$AGENT_CONF\"", "sed -i"):
        assert forbidden not in code, forbidden
