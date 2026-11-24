"""Lot 12d — deploy/verify-all.sh contre un faux `docker` (aucun conteneur réel).

Le faux `docker` simule les conteneurs : `exec web|backend sh -c …` s'exécute sur des copies locales
(racine Nginx et /srv de l'image), `psql` répond selon la requête, `alembic` et `logs` renvoient des
sorties préparées. Chaque appel est journalisé : on vérifie que le script reste en lecture seule.
"""
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "verify-all.sh"
SECRET = "s3cr3t-valeur-de-test-0123456789abcdef"

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
st = json.load(open(os.environ["FAKE_STATE"]))
if args[:1] != ["compose"]:
    sys.exit(1)
a = args[1:]
if a[0] == "logs":
    sys.stdout.write(st["logs"]); sys.exit(0)
if a[0] == "exec" and a[1] == "-T":
    svc, cmd = a[2], a[3:]
    if svc in ("web", "backend") and cmd[:2] == ["sh", "-c"]:
        script = cmd[2].replace("/usr/share/nginx/akwaba", st["web_root"]).replace("cd /srv", "cd " + st["srv"])
        sys.exit(subprocess.run(["sh", "-c", script, *cmd[3:]]).returncode)
    if svc == "backend" and cmd[0] == "alembic":
        sys.stdout.write(st["alembic"][cmd[1]]); sys.exit(0)
    if svc == "postgres" and cmd[0] == "psql":
        q = cmd[cmd.index("-c") + 1]
        if "pg_trigger" in q:
            name = q.split("tgname = '")[1].split("'")[0]
            print(0 if name in st["missing_triggers"] else 1)
        elif "WHERE mode <> 'dry_run'" in q:
            print(st["bad_decisions"])
        elif "FROM enforcement_decisions" in q:
            print(3)
        elif "agent_batches" in q:
            print(st["batch_age"])
        elif "agent_credentials" in q:
            print(st["cred_age"])
        else:
            sys.exit(3)
        sys.exit(0)
sys.exit(2)
'''

HEALTHY = {
    "logs": "backend | INFO requête traitée\nweb | GET / 200\n",
    "alembic": {"current": "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n0011 (head)\n",
                "heads": "0011 (head)\n",
                "check": "INFO  [alembic.runtime.migration] Context impl\nNo new upgrade operations detected.\n"},
    "missing_triggers": [], "bad_decisions": 0, "batch_age": 42, "cred_age": 30,
}


class _Web(BaseHTTPRequestHandler):
    body = {"/": "<!doctype html><title>Akwaba</title>"}

    def do_GET(self):  # noqa: N802
        data = self.body.get(self.path, '{"detail":"Non authentifié"}').encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def web():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Web)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Web.body = {"/": "<!doctype html><title>Akwaba</title>"}
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def env(tmp_path, web):
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "deploy" / "verify-all.sh")
    shutil.copytree(ROOT / "frontend", repo / "frontend", ignore=shutil.ignore_patterns("node_modules"))
    (repo / "backend").mkdir()
    shutil.copy(ROOT / "backend" / "alembic.ini", repo / "backend")
    for d in ("alembic", "app"):
        shutil.copytree(ROOT / "backend" / d, repo / "backend" / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (repo / ".env").write_text(f"POSTGRES_USER=akwaba\nPOSTGRES_DB=akwaba\nPOSTGRES_PASSWORD=mdp-postgres-de-test\n"
                               f"SECRET_KEY={SECRET}\nFIELD_ENCRYPTION_KEY=cle-chiffrement-de-test-xyz\n")
    (repo / ".env").chmod(0o600)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"],
                   cwd=repo, check=True)
    # « Conteneurs » : copies conformes du dépôt.
    web_root, srv = tmp_path / "web_root", tmp_path / "srv"
    shutil.copytree(repo / "frontend", web_root)
    srv.mkdir()
    shutil.copy(repo / "backend" / "alembic.ini", srv)
    shutil.copytree(repo / "backend" / "alembic", srv / "alembic")
    shutil.copytree(repo / "backend" / "app", srv / "app")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (bindir / "systemctl").write_text('#!/bin/sh\n[ "${FAKE_TIMER:-active}" = active ]\n')   # is-active --quiet
    (bindir / "systemctl").chmod(0o755)
    v11a = tmp_path / "verify-11a.sh"
    v11a.write_text("#!/bin/sh\necho 'Résultat : 21 OK, 0 échec(s).'\nexit ${FAKE_11A_RC:-0}\n")
    v11a.chmod(0o755)
    state = {**json.loads(json.dumps(HEALTHY)), "web_root": str(web_root), "srv": str(srv)}
    e = {"repo": repo, "web_root": web_root, "srv": srv, "state": state, "log": tmp_path / "docker.log",
         "env": {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_STATE": str(tmp_path / "state.json"),
                 "FAKE_LOG": str(tmp_path / "docker.log"), "VERIFY_11A": str(v11a), "WEB_URL": web}}
    return e


def run(env, **extra):
    pathlib.Path(env["env"]["FAKE_STATE"]).write_text(json.dumps(env["state"]))
    res = subprocess.run(["bash", str(env["repo"] / "deploy" / "verify-all.sh")], cwd=env["repo"],
                         env={**env["env"], **extra}, capture_output=True, text=True, timeout=120)
    out = re.sub(r"\x1b\[[0-9;]*m", "", res.stdout + res.stderr)
    return res.returncode, out


def failures(out):
    return [line.strip() for line in out.splitlines() if line.strip().startswith("ÉCHEC")]


def test_healthy_server_passes(env):
    rc, out = run(env)
    assert rc == 0, out
    assert failures(out) == []
    assert "Vérifications globales : 12 OK, 0 échec(s)." in out
    assert "frontend servi identique à ./frontend" in out
    assert "Alembic à jour : révision 0011" in out


def test_orphan_frontend_mount_is_detected_with_its_fix(env):
    shutil.rmtree(env["web_root"])
    env["web_root"].mkdir()                       # le conteneur voit un dossier vide
    rc, out = run(env)
    assert rc == 1
    assert any("montage orphelin" in f for f in failures(out)), out
    assert "docker compose up -d --force-recreate web" in out


def test_modified_frontend_file_is_detected(env):
    (env["web_root"] / "index.html").write_text("<!-- ancienne version -->")
    rc, out = run(env)
    assert rc == 1 and any(f.startswith("ÉCHEC  frontend : fichiers servis") for f in failures(out)), out


def test_backend_image_older_than_the_code_is_detected(env):
    (env["srv"] / "app" / "main.py").write_text("# ancienne image\n")
    rc, out = run(env)
    assert rc == 1 and any("image backend ≠ code du dépôt" in f for f in failures(out)), out
    assert "docker compose build backend && docker compose up -d backend" in out


def test_alembic_behind_and_drift_are_detected(env):
    env["state"]["alembic"]["current"] = "0010\n"
    env["state"]["alembic"]["check"] = "ERROR [alembic.util.messaging] New upgrade operations detected: [...]\n"
    rc, out = run(env)
    f = failures(out)
    assert rc == 1
    assert any("base en 0010, code en 0011" in x for x in f), out
    assert any("alembic check" in x for x in f), out


def test_multiple_heads_are_detected(env):
    env["state"]["alembic"]["heads"] = "0011 (head)\n0011b (head)\n"
    rc, out = run(env)
    assert rc == 1 and any("têtes multiples" in f for f in failures(out)), out


def test_missing_immutability_trigger_is_named(env):
    env["state"]["missing_triggers"] = ["payments_guard"]
    rc, out = run(env)
    assert rc == 1 and any("payments_guard" in f for f in failures(out)), out


def test_applied_decision_is_detected(env):
    env["state"]["bad_decisions"] = 1
    rc, out = run(env)
    assert rc == 1 and any("décisions appliquées ou hors dry_run" in f for f in failures(out)), out


def test_silent_agent_is_detected(env):
    env["state"]["batch_age"] = 3600
    env["state"]["cred_age"] = -1
    rc, out = run(env)
    f = failures(out)
    assert rc == 1 and any("aucun lot récent" in x for x in f) and any("signée" in x for x in f), out


def test_secret_in_logs_is_detected_without_being_printed(env):
    env["state"]["logs"] += f"backend | DEBUG cle={SECRET}\n"
    rc, out = run(env)
    assert rc == 1 and any("journaux des conteneurs" in f for f in failures(out)), out
    assert SECRET not in out, "le script ne doit jamais afficher le secret"


def test_secret_in_public_response_is_detected(env):
    _Web.body["/api/health"] = json.dumps({"status": "ok", "debug": SECRET})
    rc, out = run(env)
    assert rc == 1 and any("réponse publique" in f for f in failures(out)), out
    assert SECRET not in out


def test_env_permissions_and_git_tracking(env):
    (env["repo"] / ".env").chmod(0o644)
    subprocess.run(["git", "add", "-f", ".env"], cwd=env["repo"], check=True)
    rc, out = run(env)
    f = failures(out)
    assert rc == 1
    assert any(".env lisible par les autres" in x for x in f) and "chmod 600 .env" in out
    assert any(".env est suivi par git" in x for x in f), out


def test_failure_of_the_11a_checks_fails_the_whole_run(env):
    rc, out = run(env, FAKE_11A_RC="1")
    assert rc == 1 and "Lot 11a : échec(s)" in out


def test_strictly_read_only(env):
    """Aucune commande docker qui modifie : seulement ps/exec en lecture, logs, SELECT, alembic en lecture."""
    (env["web_root"] / "index.html").write_text("x")      # même en échec, rien n'est corrigé automatiquement
    run(env)
    calls = [json.loads(line) for line in env["log"].read_text().splitlines()]
    assert calls
    for c in calls:
        sub = c[1:]  # après « compose »
        assert sub[0] in ("exec", "logs"), c
        if sub[0] == "exec":
            svc, cmd = sub[2], sub[3:]
            if cmd[0] == "alembic":
                assert cmd[1] in ("current", "heads", "check"), c
            if cmd[0] == "psql":
                q = cmd[cmd.index("-c") + 1].strip().upper()
                assert q.startswith("SELECT"), c
            if cmd[:2] == ["sh", "-c"]:
                assert re.search(r"\b(rm|mv|cp|chmod|chown|tee|>)\b", cmd[2].replace("2>/dev/null", "")) is None, c
    text = SCRIPT.read_text()
    code_lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#") and "fix " not in ln and "fix \"" not in ln]
    for forbidden in ("compose up", "compose build", "compose restart", "compose down", "compose rm", "--force-recreate",
                      "alembic upgrade", "alembic downgrade", "alembic stamp", "tailscale serve --", "UPDATE ", "DELETE ",
                      "INSERT ", "chmod 600 .env"):
        assert not any(forbidden in ln for ln in code_lines), forbidden


# ------------------------------------------------------------------ Phase 14a : sauvegardes

def _backups(env, *, backup_h=2, backup_ok=True, offsite_h=2, offsite_ok=True, restore_h=30, restore_ok=True,
             offsite=True):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    at = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    config = env["repo"] / "backups" / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "age-recipients.txt").write_text("age1" + "q" * 58 + "\n")
    if offsite:
        (config / "offsite.conf").write_text("OFFSITE_HOST=pi\nOFFSITE_USER=akwaba-backup\n")
    auto = env["repo"] / "backups" / "auto"
    auto.mkdir(parents=True, exist_ok=True)
    (auto / "status.json").write_text(json.dumps({
        "backup": {"at": at(backup_h), "ok": backup_ok}, "offsite": {"at": at(offsite_h), "ok": offsite_ok},
        "restore_test": {"at": at(restore_h), "ok": restore_ok}}))


def test_backups_not_configured_only_warn(env):
    rc, out = run(env)
    assert rc == 0 and "sauvegardes automatiques non configurées" in out and failures(out) == []


def test_fresh_backups_pass(env):
    _backups(env)
    rc, out = run(env)
    assert rc == 0, out
    for line in ("dernière sauvegarde chiffrée réussie il y a 2 h", "copie sur l'Exit Node réussie il y a 2 h",
                 "test de restauration réussi il y a 30 h", "minuteur akwaba-backup.timer actif"):
        assert line in out, line
    assert "Vérifications globales : 16 OK, 0 échec(s)." in out


@pytest.mark.parametrize("kw,expected", [
    ({"backup_h": 30}, "aucune sauvegarde réussie depuis 26 h"),
    ({"backup_ok": False}, "aucune sauvegarde réussie depuis 26 h"),
    ({"offsite_ok": False}, "copie sur l'Exit Node absente ou en échec"),
    ({"offsite_h": 50}, "copie sur l'Exit Node absente ou en échec"),
    ({"restore_h": 200}, "aucun test de restauration réussi depuis 8 jours"),
    ({"restore_ok": False}, "aucun test de restauration réussi depuis 8 jours"),
])
def test_stale_or_failed_backups_fail(env, kw, expected):
    _backups(env, **kw)
    rc, out = run(env)
    assert rc == 1 and any(expected in f for f in failures(out)), out


def test_inactive_timer_fails(env):
    _backups(env)
    rc, out = run(env, FAKE_TIMER="inactive")
    assert rc == 1 and any("akwaba-backup.timer inactif" in f for f in failures(out))


def test_offsite_not_configured_is_only_reported(env):
    _backups(env, offsite=False, offsite_ok=False)
    rc, out = run(env)
    assert rc == 0 and "copie sur l'Exit Node : non configurée" in out
