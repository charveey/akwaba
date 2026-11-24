"""Lot 14b — deploy/restore.sh (PostgreSQL 16 RÉEL) et deploy/rollback.sh (faux docker, vrai git).

restore.sh : le faux `docker` relaie `compose exec postgres pg_dump|pg_restore|psql` vers les vrais
binaires (en super-utilisateur, comme POSTGRES_USER dans l'image officielle) et `compose run backend alembic`
vers le vrai Alembic du dépôt. Les renommages de bases, la restauration et les migrations sont réels.
"""
import io
import json
import os
import pathlib
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PG_BIN = pathlib.Path("/usr/lib/postgresql/16/bin")
TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
needs_pg = pytest.mark.skipif(not TEST_URL or not PG_BIN.exists() or shutil.which("age") is None or os.geteuid() != 0,
                              reason="nécessite TEST_DATABASE_URL, PostgreSQL 16, age et root")

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, shlex, subprocess, sys
args = sys.argv[1:]
fake = pathlib.Path(os.environ["FAKE_DIR"])
with open(fake / "calls", "a") as log:
    log.write(json.dumps(args) + "\n")
BIN = "/usr/lib/postgresql/16/bin"
SOCK = ["-h", "/tmp", "-p", "5433"]

def su(cmd):
    return subprocess.run(["su", "postgres", "-s", "/bin/sh", "-c", shlex.join(cmd)]).returncode

def strip_user(cmd):
    out, skip = [], False
    for a in cmd:
        if skip: skip = False; continue
        if a == "-U": skip = True; continue
        out.append(a)
    return out

if args[:3] == ["compose", "ps", "-q"]:
    print("cid-" + args[3]); sys.exit(0)
if args[:4] == ["compose", "exec", "-T", "postgres"]:
    cmd = strip_user(args[4:])
    tool = cmd[0]
    if tool == "pg_restore" and "-d" in cmd and os.environ.get("FAKE_FAIL_RESTORE"):
        sys.exit(1)
    if tool in ("pg_restore",) and "-d" not in cmd:
        sys.exit(subprocess.run([BIN + "/pg_restore", *cmd[1:]]).returncode)
    sys.exit(su([BIN + "/" + tool, *SOCK, *cmd[1:]]))
if args[:2] == ["compose", "run"]:
    cmd = args[args.index("backend") + 1:]
    db = os.environ["FAKE_PROD_DB"]
    env = {**os.environ, "DATABASE_URL": "postgresql+psycopg://akwaba:akwaba@127.0.0.1:5433/" + db,
           "SECRET_KEY": "k" * 48, "FIELD_ENCRYPTION_KEY": "A" * 43 + "=", "ALLOWED_ORIGINS": "https://a.test"}
    sys.exit(subprocess.run([sys.executable, "-m", *cmd], cwd=os.environ["FAKE_BACKEND"], env=env).returncode)
if args[0] == "inspect":
    print("healthy"); sys.exit(0)
if args[:2] in (["compose", "stop"], ["compose", "up"]):
    sys.exit(0)
sys.exit(3)
'''


def su_psql(sql, db="postgres"):
    res = subprocess.run(["su", "postgres", "-s", "/bin/sh", "-c",
                          shlex.join([str(PG_BIN / "psql"), "-h", "/tmp", "-p", "5433", "-d", db, "-At",
                                      "-v", "ON_ERROR_STOP=1", "-c", sql])], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip()


def su_dump(db, path: pathlib.Path):
    with open(path, "wb") as f:
        subprocess.run(["su", "postgres", "-s", "/bin/sh", "-c",
                        shlex.join([str(PG_BIN / "pg_dump"), "-h", "/tmp", "-p", "5433", "-d", db, "-Fc"])],
                       stdout=f, check=True)


def migrate(db, rev="head"):
    env = {**os.environ, "DATABASE_URL": f"postgresql+psycopg://akwaba:akwaba@127.0.0.1:5433/{db}",
           "SECRET_KEY": "k" * 48, "FIELD_ENCRYPTION_KEY": "A" * 43 + "=", "ALLOWED_ORIGINS": "https://a.test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", rev], cwd=ROOT / "backend", env=env, check=True,
                   capture_output=True)


def databases(prefix):
    return su_psql(f"SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE datname LIKE '{prefix}%'")


@pytest.fixture
def env(tmp_path):
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL absent")
    prod = "akrs" + secrets.token_hex(3)
    su_psql(f"CREATE DATABASE {prod} OWNER akwaba")
    migrate(prod)
    su_psql("INSERT INTO members (full_name) VALUES ('Avant la sauvegarde')", prod)
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copy(ROOT / "deploy" / "restore.sh", repo / "deploy" / "restore.sh")
    (repo / ".env").write_text(f"POSTGRES_USER=akwaba\nPOSTGRES_DB={prod}\nSECRET_KEY={'s' * 48}\n"
                               "FIELD_ENCRYPTION_KEY=cle-des-champs\n")
    (repo / ".env").chmod(0o600)
    fake = tmp_path / "fake"
    fake.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(FAKE_DOCKER)
    (bindir / "docker").chmod(0o755)
    verify = tmp_path / "verify.sh"
    verify.write_text("#!/bin/sh\necho verify-all-appelé\nexit ${FAKE_VERIFY_RC:-0}\n")
    verify.chmod(0o755)
    dump = tmp_path / "sauvegarde.dump"
    su_dump(prod, dump)
    su_psql("INSERT INTO members (full_name) VALUES ('Après la sauvegarde')", prod)
    yield {"repo": repo, "prod": prod, "dump": dump, "fake": fake, "tmp": tmp_path,
           "env": {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_DIR": str(fake),
                   "FAKE_PROD_DB": prod, "FAKE_BACKEND": str(ROOT / "backend"), "VERIFY_ALL": str(verify),
                   "HEALTH_TIMEOUT": "5"}}
    for db in (databases(prod) or "").split(","):
        if db:
            su_psql(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")


def restore(env, *args, stdin="", **extra):
    res = subprocess.run(["bash", str(env["repo"] / "deploy" / "restore.sh"), *map(str, args)], cwd=env["repo"],
                         env={**env["env"], **extra}, input=stdin, capture_output=True, text=True, timeout=300)
    return res.returncode, re.sub(r"\x1b\[[0-9;]*m", "", res.stdout + res.stderr)


def members(db):
    return su_psql("SELECT string_agg(full_name, '|' ORDER BY full_name) FROM members", db)


def calls(env):
    f = env["fake"] / "calls"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def stopped(env):
    return any(c[:2] == ["compose", "stop"] for c in calls(env))


def make_archive(env, key, *, env_text=None, tamper=False) -> pathlib.Path:
    import hashlib
    data = env["dump"].read_bytes()
    meta = {"created_at": "2026-11-24T03:15:00Z", "dump_sha256": hashlib.sha256(b"x" if tamper else data).hexdigest(),
            "alembic_revision": "0011"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in (("akwaba.dump", data), ("env", (env_text or (env["repo"] / ".env").read_text()).encode()),
                              ("meta.json", json.dumps(meta).encode())):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    recipient = re.search(r"age1[0-9a-z]+", key.read_text()).group(0)
    out = env["tmp"] / "akwaba-20261124T031500Z.tar.age"
    subprocess.run(["age", "-r", recipient, "-o", str(out)], input=buf.getvalue(), check=True)
    return out


@pytest.fixture
def key(tmp_path):
    k = tmp_path / "akwaba-backup.key"
    subprocess.run(["age-keygen", "-o", str(k)], check=True, capture_output=True)
    return k


# ------------------------------------------------------------------ restore.sh

@needs_pg
def test_restore_swaps_databases_and_keeps_the_old_one(env):
    rc, out = restore(env, env["dump"], "--confirmation", "RESTAURER")
    assert rc == 0, out
    assert members(env["prod"]) == "Avant la sauvegarde"
    old = [d for d in databases(env["prod"]).split(",") if "_avant_restauration_" in d]
    assert len(old) == 1 and members(old[0]) == "Après la sauvegarde|Avant la sauvegarde"
    assert "_restauration_2" not in databases(env["prod"]).replace("_avant_restauration_", ""), "base temporaire renommée"
    safety = list((env["repo"] / "backups").glob("akwaba-pre-restore-*.dump"))
    assert len(safety) == 1 and stat.S_IMODE(safety[0].stat().st_mode) == 0o600
    kinds = [c[1] for c in calls(env) if c[0] == "compose" and c[1] in ("stop", "up")]
    assert kinds[0] == "stop" and kinds[-2:] == ["up", "up"]
    assert ["compose", "up", "-d", "--no-build", "--force-recreate", "web"] in calls(env)
    assert "verify-all-appelé" in out and "Restauration terminée" in out
    assert su_psql("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE '%guard%'", env["prod"]) == "3"
    assert not any(c[:2] == ["compose", "down"] for c in calls(env))


@needs_pg
def test_older_backup_is_restored_then_migrated(env):
    scratch = env["prod"] + "_old"
    su_psql(f"CREATE DATABASE {scratch} OWNER akwaba")
    migrate(scratch, "0010")
    su_psql("INSERT INTO members (full_name) VALUES ('Révision 0010')", scratch)
    old_dump = env["tmp"] / "ancienne.dump"
    su_dump(scratch, old_dump)
    rc, out = restore(env, old_dump, "--confirmation", "RESTAURER")
    assert rc == 0, out
    assert "migration après restauration" in out and "base migrée 0010 → 0011" in out
    assert su_psql("SELECT version_num FROM alembic_version", env["prod"]) == "0011"
    assert members(env["prod"]) == "Révision 0010"


@needs_pg
def test_backup_newer_than_the_code_is_refused_before_anything(env):
    scratch = env["prod"] + "_new"
    su_psql(f"CREATE DATABASE {scratch} OWNER akwaba")
    migrate(scratch)
    su_psql("UPDATE alembic_version SET version_num = '0099'", scratch)
    newer = env["tmp"] / "future.dump"
    su_dump(scratch, newer)
    rc, out = restore(env, newer, "--confirmation", "RESTAURER")
    assert rc == 1 and "inconnue du code en place" in out and "Rien n'a été modifié" in out
    assert not stopped(env) and members(env["prod"]) == "Après la sauvegarde|Avant la sauvegarde"


@needs_pg
def test_failed_restore_leaves_production_untouched_and_restarts(env):
    rc, out = restore(env, env["dump"], "--confirmation", "RESTAURER", FAKE_FAIL_RESTORE="1")
    assert rc == 1 and "La base de production n'a PAS été modifiée" in out
    assert members(env["prod"]) == "Après la sauvegarde|Avant la sauvegarde"
    assert databases(env["prod"]) == env["prod"], "base temporaire supprimée, aucune autre"
    assert stopped(env) and ["compose", "up", "-d", "--no-build", "backend"] in calls(env)


@needs_pg
@pytest.mark.parametrize("answer", ["", "oui", "restaurer"])
def test_confirmation_is_required(env, answer):
    rc, out = restore(env, env["dump"], stdin=answer + "\n")
    assert rc == 1 and "confirmation absente" in out
    assert not stopped(env) and members(env["prod"]) == "Après la sauvegarde|Avant la sauvegarde"
    assert not list((env["repo"] / "backups").glob("akwaba-pre-restore-*.dump"))


@needs_pg
def test_corrupted_file_is_refused(env):
    bad = env["tmp"] / "corrompu.dump"
    bad.write_bytes(env["dump"].read_bytes()[:500])
    rc, out = restore(env, bad, "--confirmation", "RESTAURER")
    assert rc == 1 and "Rien n'a été modifié" in out and not stopped(env)


@needs_pg
def test_encrypted_archive_with_the_private_key(env, key):
    archive = make_archive(env, key)
    rc, out = restore(env, archive, "--identity", key, "--confirmation", "RESTAURER")
    assert rc == 0, out
    assert "archive déchiffrée et intègre" in out and members(env["prod"]) == "Avant la sauvegarde"
    assert f"shred -u {key}" in out
    assert not list((env["repo"] / "backups").glob(".restore-*")), "aucun clair laissé"


@needs_pg
def test_wrong_key_or_tampered_archive_is_refused(env, key, tmp_path):
    other = tmp_path / "autre.key"
    subprocess.run(["age-keygen", "-o", str(other)], check=True, capture_output=True)
    rc, out = restore(env, make_archive(env, key), "--identity", other, "--confirmation", "RESTAURER")
    assert rc == 1 and "archive illisible" in out and not stopped(env)
    rc, out = restore(env, make_archive(env, key, tamper=True), "--identity", key, "--confirmation", "RESTAURER")
    assert rc == 1 and "archive altérée" in out and not stopped(env)


@needs_pg
def test_archive_env_is_never_replaced_silently(env, key):
    other_env = (env["repo"] / ".env").read_text().replace("cle-des-champs", "autre-cle-des-champs")
    archive = make_archive(env, key, env_text=other_env)
    rc, out = restore(env, archive, "--identity", key, "--confirmation", "RESTAURER")
    assert rc == 1 and "FIELD_ENCRYPTION_KEY" in out and "--restaurer-env" in out and not stopped(env)
    before = (env["repo"] / ".env").read_text()
    rc, out = restore(env, archive, "--identity", key, "--restaurer-env", "--confirmation", "RESTAURER")
    assert rc == 0, out
    assert (env["repo"] / ".env").read_text() == other_env
    assert stat.S_IMODE((env["repo"] / ".env").stat().st_mode) == 0o600
    saved = list(env["repo"].glob(".env.avant-restauration-*"))
    assert len(saved) == 1 and saved[0].read_text() == before


@needs_pg
def test_failed_verification_prints_how_to_swap_back(env):
    rc, out = restore(env, env["dump"], "--confirmation", "RESTAURER", FAKE_VERIFY_RC="1")
    assert rc == 1 and "Pour revenir à la base d'avant la restauration" in out
    assert re.search(r"ALTER DATABASE \w+_avant_restauration_\w+ RENAME TO " + env["prod"], out)


@needs_pg
def test_deferred_verification_for_disaster_recovery(env):
    """Serveur neuf : Serve 8443 et l'agent ne sont pas encore en place, verify-all échouerait forcément."""
    rc, out = restore(env, env["dump"], "--confirmation", "RESTAURER", "--verification-differee", FAKE_VERIFY_RC="1")
    assert rc == 0, out
    assert "verify-all-appelé" not in out and "vérification globale différée" in out
    assert members(env["prod"]) == "Avant la sauvegarde"


# ------------------------------------------------------------------ rollback.sh

FAKE_ROLLBACK_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
st = json.load(open(os.environ["FAKE_STATE"]))
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if args[:2] == ["image", "inspect"]:
    v = st["labels"].get(args[-1])
    if not v: sys.exit(1)
    print(v); sys.exit(0)
if args[:2] == ["image", "tag"]: sys.exit(0)
if args[0] == "run": print(st["rev_prev"] + " (head)"); sys.exit(0)
if args[0] == "inspect": print("healthy"); sys.exit(0)
if args[:4] == ["compose", "exec", "-T", "postgres"]: print(st["rev_db"]); sys.exit(0)
if args[:2] in (["compose", "up"], ["compose", "ps"]): print("cid"); sys.exit(0)
sys.exit(3)
'''


@pytest.fixture
def rb(tmp_path):
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copy(ROOT / "deploy" / "rollback.sh", repo / "deploy" / "rollback.sh")
    (repo / ".gitignore").write_text(".env\nbackups/\n")
    (repo / ".env").write_text("POSTGRES_USER=akwaba\nPOSTGRES_DB=akwaba\n")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "v1"], cwd=repo, check=True)
    prev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    (repo / "VERSION").write_text("v2\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "v2"], cwd=repo, check=True)
    cur = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(FAKE_ROLLBACK_DOCKER)
    (bindir / "docker").chmod(0o755)
    fake_restore = tmp_path / "restore.sh"
    fake_restore.write_text('#!/bin/sh\necho "restore-appelé $*" >> "$FAKE_LOG"\nexit ${FAKE_RESTORE_RC:-0}\n')
    fake_restore.chmod(0o755)
    verify = tmp_path / "verify.sh"
    verify.write_text("#!/bin/sh\necho verify-appelé >> \"$FAKE_LOG\"\n")
    verify.chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"labels": {"akwaba-backend:precedent": prev, "akwaba-backend:0.1.0": cur},
                                 "rev_db": "0011", "rev_prev": "0011"}))
    return {"repo": repo, "prev": prev, "cur": cur, "state": state, "log": tmp_path / "log",
            "env": {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_STATE": str(state),
                    "FAKE_LOG": str(tmp_path / "log"), "RESTORE_SH": str(fake_restore), "VERIFY_ALL": str(verify),
                    "HEALTH_TIMEOUT": "5"}}


def rollback(rb, *args, stdin=""):
    res = subprocess.run(["bash", str(rb["repo"] / "deploy" / "rollback.sh"), *args], cwd=rb["repo"], env=rb["env"],
                         input=stdin, capture_output=True, text=True, timeout=60)
    return res.returncode, re.sub(r"\x1b\[[0-9;]*m", "", res.stdout + res.stderr)


def head(rb):
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=rb["repo"], capture_output=True,
                          text=True).stdout.strip()


def rb_log(rb):
    return rb["log"].read_text() if rb["log"].exists() else ""


def set_rb(rb, **kw):
    s = json.loads(rb["state"].read_text())
    s.update(kw)
    rb["state"].write_text(json.dumps(s))


def test_rollback_code_only_when_the_schema_is_unchanged(rb):
    rc, out = rollback(rb, "--confirmation", "ANNULER")
    assert rc == 0, out
    assert head(rb) == rb["prev"]
    log = rb_log(rb)
    tags = [json.loads(x) for x in log.splitlines() if x.startswith('["image", "tag"')]
    assert tags == [["image", "tag", "akwaba-backend:0.1.0", f"akwaba-backend:annule-{rb['cur']}"],
                    ["image", "tag", "akwaba-backend:precedent", "akwaba-backend:0.1.0"]]
    assert "restore-appelé" not in log and "verify-appelé" in log
    assert '["compose", "up", "-d", "--no-build", "--force-recreate", "web"]' in log
    assert "aucune perte de données" in out


def test_rollback_after_a_migration_restores_the_pre_deploy_dump(rb):
    set_rb(rb, rev_db="0012", rev_prev="0011")
    backups = rb["repo"] / "backups"
    backups.mkdir()
    old = backups / f"akwaba-pre-deploy-{rb['cur']}-20261124T100000Z.dump"
    new = backups / f"akwaba-pre-deploy-{rb['cur']}-20261124T120000Z.dump"
    for i, p in enumerate((old, new)):
        p.write_text("x")
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    (backups / "akwaba-pre-deploy-autre-20261124T130000Z.dump").write_text("x")
    rc, out = rollback(rb, "--confirmation", "ANNULER")
    assert rc == 0, out
    assert f"restore-appelé backups/akwaba-pre-deploy-{rb['cur']}-20261124T120000Z.dump --confirmation RESTAURER" in rb_log(rb)
    assert head(rb) == rb["prev"] and "données écrites depuis ce déploiement" in out


def test_rollback_after_a_migration_without_dump_is_refused(rb):
    set_rb(rb, rev_db="0012", rev_prev="0011")
    rc, out = rollback(rb, "--confirmation", "ANNULER")
    assert rc == 1 and "aucun dump avant déploiement" in out and "Rien n'a été modifié" in out
    assert head(rb) == rb["cur"] and '"tag"' not in rb_log(rb)


def test_rollback_plan_changes_nothing(rb):
    rc, out = rollback(rb, "--plan")
    assert rc == 0 and "Mode --plan" in out
    assert head(rb) == rb["cur"] and '"tag"' not in rb_log(rb)


@pytest.mark.parametrize("change,expected", [
    (lambda rb: set_rb(rb, labels={"akwaba-backend:0.1.0": "x"}), "aucune image akwaba-backend:precedent"),
    (lambda rb: (rb["repo"] / "VERSION").write_text("modifié\n"), "modifications locales non commitées"),
    (lambda rb: set_rb(rb, labels={"akwaba-backend:precedent": "deadbee", "akwaba-backend:0.1.0": "x"}),
     "introuvable dans le dépôt"),
])
def test_rollback_refusals_change_nothing(rb, change, expected):
    change(rb)
    rc, out = rollback(rb, "--confirmation", "ANNULER")
    assert rc == 1 and expected in out and "Rien n'a été modifié" in out
    assert '"tag"' not in rb_log(rb)


def test_rollback_requires_typed_confirmation(rb):
    rc, out = rollback(rb, stdin="oui\n")
    assert rc == 1 and "confirmation absente" in out and head(rb) == rb["cur"]


def test_scripts_never_destroy_data():
    for script in ("restore.sh", "rollback.sh"):
        code = "\n".join(ln for ln in (ROOT / "deploy" / script).read_text().splitlines()
                         if not ln.lstrip().startswith("#") and "echo " not in ln and "printf" not in ln)
        for forbidden in ("compose down", "volume rm", "DROP DATABASE $pg_db", "DROP DATABASE $OLD_DB", "rm -rf /",
                          "git reset --hard", "git clean", "tailscale"):
            assert forbidden not in code, (script, forbidden)
    restore_code = (ROOT / "deploy" / "restore.sh").read_text()
    assert restore_code.count("DROP DATABASE") == 2, "seule la base TEMPORAIRE est supprimée (+ la commande affichée)"
