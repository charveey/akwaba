"""Lot 13b — deploy/deploy.sh contre un faux `docker` à état (aucun conteneur réel).

Le faux `docker` simule : révision Alembic de la base, tête du nouveau code, variante Debian de
PostgreSQL, santé des conteneurs, image en place (commit), pg_dump / pg_restore --list. Chaque appel est
journalisé avec GIT_COMMIT : on vérifie l'ORDRE des étapes, les refus préalables (aucune modification)
et l'absence de toute commande destructive.
"""
import fcntl
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "deploy.sh"

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
sf = os.environ["FAKE_STATE"]
st = json.load(open(sf))
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "GIT_COMMIT": os.environ.get("GIT_COMMIT")}) + "\n")
def save(): json.dump(st, open(sf, "w"))
def fail(key):
    if st.get(key): sys.exit(1)
if args[0] == "inspect":
    print(st["health"].get(args[-1].replace("cid-", ""), "healthy")); sys.exit(0)
if args[:2] == ["image", "inspect"]:
    if not st["prev_commit"]: sys.exit(1)
    print(st["prev_commit"]); sys.exit(0)
if args[:2] == ["image", "tag"]:
    sys.exit(0)
assert args[0] == "compose", args
a = args[1:]
if a[0] == "version": print("v5.7.0"); sys.exit(0)
if a[:2] == ["config", "--images"]:
    print("akwaba-backend:0.1.0\n" + st["pg_image"] + "\nnginx:1.30.8-alpine"); sys.exit(0)
if a[:2] == ["ps", "-q"]:
    if a[2] == "postgres" and not st["pg_running"]: sys.exit(0)
    print("cid-" + a[2]); sys.exit(0)
if a[0] == "pull": fail("fail_pull"); sys.exit(0)
if a[0] == "build": fail("fail_build"); sys.exit(0)
if a[0] == "up": fail("fail_up"); sys.exit(0)
if a[0] == "exec" and a[2] == "postgres":
    cmd = a[3:]
    if cmd[0] == "sh": print(st["variant"]); sys.exit(0)
    if cmd[0] == "psql": print(st["rev"]); sys.exit(0)
    if cmd[0] == "pg_dump":
        fail("fail_dump"); sys.stdout.write("PGDMP-fausse-sauvegarde"); sys.exit(0)
    if cmd[0] == "pg_restore":
        sys.exit(0 if sys.stdin.read().startswith("PGDMP") else 1)
if a[0] == "run":
    cmd = a[a.index("backend") + 1:]
    if cmd == ["alembic", "heads"]: print(st["head"] + " (head)"); sys.exit(0)
    if cmd == ["alembic", "upgrade", "head"]:
        fail("fail_migration"); st["rev"] = st["head"]; save(); sys.exit(0)
sys.exit(3)
'''

ENV = {"POSTGRES_USER": "akwaba", "POSTGRES_PASSWORD": "mdp", "POSTGRES_DB": "akwaba",
       "DATABASE_URL": "postgresql+psycopg://akwaba:mdp@postgres/akwaba", "SECRET_KEY": "x" * 48,
       "FIELD_ENCRYPTION_KEY": "y" * 44, "TAILNET_BIND_IP": "100.126.200.88", "AGENT_ALLOWED_IPS": "100.85.214.5",
       "ALLOWED_ORIGINS": "https://ubuntu.tail14614.ts.net:8443", "COOKIE_SECURE": "true",
       "REVOCATION_MODE": "dry_run", "ENFORCER_ENABLED": "false"}
STATE = {"rev": "0011", "head": "0012", "variant": "trixie", "pg_image": "postgres:16.16-trixie", "pg_running": True,
         "prev_commit": "aaaa111", "health": {}}
MUTATING = ("pg_dump", "pull", "tag", "build", "upgrade", "up")


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "deploy" / "deploy.sh")
    (repo / ".gitignore").write_text(".env\nbackups/\n")
    write_env(repo, ENV)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=repo, check=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(FAKE)
    (bindir / "docker").chmod(0o755)
    verify = tmp_path / "verify-all.sh"
    verify.write_text("#!/bin/sh\necho verify-all\nexit ${FAKE_VERIFY_RC:-0}\n")
    verify.chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps(STATE))
    return {"repo": repo, "state": state, "log": tmp_path / "calls.log",
            "env": {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_STATE": str(state),
                    "FAKE_LOG": str(tmp_path / "calls.log"), "VERIFY_ALL": str(verify), "MIN_FREE_MB": "1",
                    "HEALTH_TIMEOUT": "4"}}


def write_env(repo, values):
    (repo / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    (repo / ".env").chmod(0o600)


def set_state(env, **kw):
    s = json.loads(env["state"].read_text())
    s.update(kw)
    env["state"].write_text(json.dumps(s))


def run(env, *args, **extra):
    res = subprocess.run(["bash", str(env["repo"] / "deploy" / "deploy.sh"), *args], cwd=env["repo"],
                         env={**env["env"], **extra}, capture_output=True, text=True, timeout=120)
    return res.returncode, re.sub(r"\x1b\[[0-9;]*m", "", res.stdout + res.stderr)


def calls(env):
    if not env["log"].exists():
        return []
    return [json.loads(line) for line in env["log"].read_text().splitlines()]


def kind(call):
    a = call["args"]
    if a[0] == "inspect":
        return "health"
    if a[:2] == ["image", "tag"]:
        return "tag"
    if a[:2] == ["image", "inspect"]:
        return "image-inspect"
    a = a[1:]
    if a[0] == "exec":
        return a[3]                          # sh, psql, pg_dump, pg_restore
    if a[0] == "run":
        return "upgrade" if a[-1] == "head" and "upgrade" in a else "heads"
    if a[0] == "up":
        return "up-web" if "web" in a else "up"
    return a[0]


def mutating(env):
    return [kind(c) for c in calls(env) if kind(c) in MUTATING]


def commit(env):
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=env["repo"], capture_output=True,
                          text=True).stdout.strip()


# ------------------------------------------------------------------ déploiement nominal

def test_full_deploy_runs_every_step_in_order(env):
    rc, out = run(env)
    assert rc == 0, out
    order = [k for k in (kind(c) for c in calls(env)) if k in ("pg_dump", "pg_restore", "pull", "tag", "build",
                                                               "heads", "upgrade", "up", "up-web")]
    assert order == ["pg_dump", "pg_restore", "pull", "tag", "build", "heads", "upgrade", "up", "up-web"], order
    build = next(c for c in calls(env) if kind(c) == "build")
    assert build["GIT_COMMIT"] == commit(env) and build["args"][1:] == ["build", "backend"]
    up = next(c for c in calls(env) if kind(c) == "up")
    assert up["args"][1:] == ["up", "-d", "--no-build", "postgres", "backend"]
    web = next(c for c in calls(env) if kind(c) == "up-web")
    assert web["args"][1:] == ["up", "-d", "--no-build", "--force-recreate", "web"]
    upgrade = next(c for c in calls(env) if kind(c) == "upgrade")
    assert upgrade["args"][1:4] == ["run", "--rm", "--no-deps"], "migration dans un conteneur jetable, sans ports"
    dumps = list((env["repo"] / "backups").glob(f"akwaba-pre-deploy-{commit(env)}-*.dump"))
    assert len(dumps) == 1 and stat.S_IMODE(dumps[0].stat().st_mode) == 0o600
    logs = list((env["repo"] / "backups").glob("deploy-*.log"))
    assert len(logs) == 1 and stat.S_IMODE(logs[0].stat().st_mode) == 0o600
    assert "migration 0011 → 0012" in out and "base migrée en 0012" in out
    assert f"Déploiement du commit {commit(env)} terminé" in out
    assert "verify-all" in out


def test_no_pending_migration_means_no_upgrade(env):
    set_state(env, head="0011")
    rc, out = run(env)
    assert rc == 0, out
    assert "upgrade" not in [kind(c) for c in calls(env)]
    assert "aucune migration en attente (révision 0011)" in out


def test_first_build_without_previous_image(env):
    set_state(env, prev_commit="")
    rc, out = run(env)
    assert rc == 0, out
    assert "tag" not in [kind(c) for c in calls(env)]


def test_plan_mode_changes_nothing(env):
    rc, out = run(env, "--plan")
    assert rc == 0, out
    assert mutating(env) == []
    assert not list((env["repo"] / "backups").glob("*.dump"))
    assert "Mode --plan" in out and f"GIT_COMMIT={commit(env)} docker compose build backend" in out


# ------------------------------------------------------------------ refus préalables : rien n'est modifié

@pytest.mark.parametrize("change,expected", [
    (lambda e: (e["repo"] / ".env").chmod(0o644), ".env lisible par le groupe ou les autres"),
    (lambda e: write_env(e["repo"], {k: v for k, v in ENV.items() if k != "SECRET_KEY"}), "variables manquantes"),
    (lambda e: write_env(e["repo"], {**ENV, "REVOCATION_MODE": "enforce"}), "mode d'observation modifié"),
    (lambda e: write_env(e["repo"], {**ENV, "ENFORCER_ENABLED": "true"}), "mode d'observation modifié"),
    (lambda e: write_env(e["repo"], {**ENV, "ALLOWED_ORIGINS": "http://ubuntu:8443"}), "origines non https"),
    (lambda e: write_env(e["repo"], {**ENV, "COOKIE_SECURE": "false"}), "COOKIE_SECURE=false"),
    (lambda e: (e["repo"] / "deploy" / "deploy.sh").write_text(
        (e["repo"] / "deploy" / "deploy.sh").read_text() + "\n# modif\n"), "modifications locales non commitées"),
    (lambda e: set_state(e, variant="bookworm"), "variante Debian bookworm → trixie"),
    (lambda e: set_state(e, pg_running=False), "PostgreSQL ne tourne pas"),
])
def test_preflight_refusals_modify_nothing(env, change, expected):
    change(env)
    rc, out = run(env)
    assert rc == 1, out
    assert expected in out, out
    assert "rien n'a été modifié" in out
    assert mutating(env) == [], mutating(env)
    assert not list((env["repo"] / "backups").glob("*.dump"))


def test_low_disk_space_is_refused(env):
    rc, out = run(env, MIN_FREE_MB="999999999")
    assert rc == 1 and "espace disque insuffisant" in out and mutating(env) == []


def test_concurrent_deploy_is_refused(env):
    (env["repo"] / "backups").mkdir(mode=0o700)
    with open(env["repo"] / "backups" / ".deploy.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, out = run(env)
    assert rc == 1 and "un autre déploiement est en cours" in out and calls(env) == []


# ------------------------------------------------------------------ échecs en cours de route

def test_failed_backup_stops_before_anything_else(env):
    set_state(env, fail_dump=True)
    rc, out = run(env)
    assert rc == 1 and "pg_dump a échoué" in out
    assert [k for k in mutating(env) if k != "pg_dump"] == []


def test_failed_build_restarts_nothing(env):
    set_state(env, fail_build=True)
    rc, out = run(env)
    assert rc == 1 and "rien n'a été redémarré" in out
    kinds = [kind(c) for c in calls(env)]
    assert "upgrade" not in kinds and "up" not in kinds and "up-web" not in kinds
    assert "aucune migration appliquée" in out


def test_failed_migration_restarts_nothing_and_points_to_the_backup(env):
    set_state(env, fail_migration=True)
    rc, out = run(env)
    assert rc == 1 and "migration en échec" in out
    assert "up" not in [kind(c) for c in calls(env)]
    assert "des migrations ont été appliquées" in out and ".dump" in out


def test_unhealthy_backend_prints_the_rollback(env):
    set_state(env, health={"backend": "unhealthy"})
    rc, out = run(env)
    assert rc == 1 and "backend non sain" in out
    assert "docker image tag akwaba-backend:precedent akwaba-backend:0.1.0" in out
    assert "git checkout aaaa111" in out


def test_failed_global_verification_fails_the_deploy(env):
    rc, out = run(env, FAKE_VERIFY_RC="1")
    assert rc == 1 and "la vérification globale signale des échecs" in out


# ------------------------------------------------------------------ jamais de commande destructive

def test_never_destructive():
    code = [ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")]
    text = "\n".join(ln for ln in code if not ln.lstrip().startswith(("echo ", "info ", "printf")))
    for forbidden in ("compose down", "volume rm", "volume prune", "image rm", "rmi ", "system prune", "--volumes",
                      "down -v", "--remove-orphans", "tailscale", "funnel", "sed -i", "> .env", ">> .env"):
        assert forbidden not in text, forbidden


def test_full_run_never_calls_destructive_docker_commands(env):
    run(env)
    for c in calls(env):
        a = c["args"]
        assert not ({"down", "rm", "rmi", "prune", "--volumes", "kill", "stop"} & set(a)), a
        assert a[:2] != ["volume", "rm"] and not (a[:1] == ["compose"] and a[1] in ("down", "rm")), a
