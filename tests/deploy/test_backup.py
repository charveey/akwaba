"""Lot 14a — sauvegardes chiffrées : backup.sh, test-restore.sh, retention.py, scripts d'installation.

Conditions réelles autant que possible, sans Docker :
  - PostgreSQL 16 RÉEL : une base dédiée, migrée par Alembic, sert de « production » ; le faux `docker`
    relaie `compose exec postgres pg_dump | pg_restore | psql` vers les vrais binaires ;
  - le « conteneur temporaire » du test de restauration est une VRAIE instance PostgreSQL éphémère
    (initdb), restaurée par le vrai pg_restore puis détruite ;
  - chiffrement par le vrai `age` (clé de test) ; copie hors serveur par le vrai `rsync` vers un répertoire.
"""
import json
import os
import pathlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import io
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKUP = ROOT / "deploy" / "backup"
sys.path.insert(0, str(BACKUP))
import retention  # noqa: E402

PG_BIN = pathlib.Path("/usr/lib/postgresql/16/bin")
TEST_URL = os.environ.get("TEST_DATABASE_URL", "")
needs_pg = pytest.mark.skipif(
    not TEST_URL or not PG_BIN.exists() or shutil.which("age") is None or shutil.which("rsync") is None
    or os.geteuid() != 0, reason="nécessite TEST_DATABASE_URL, PostgreSQL 16, age, rsync et root (initdb via su)")

FAKE_DOCKER = r'''#!/usr/bin/env python3
"""Faux docker : relaie vers le vrai PostgreSQL local. Journalise chaque appel."""
import json, os, pathlib, shutil, socket, subprocess, sys
args = sys.argv[1:]
state = pathlib.Path(os.environ["FAKE_DIR"])
with open(state / "calls", "a") as log:
    log.write(json.dumps(args) + "\n")
BIN = "/usr/lib/postgresql/16/bin"
LIVE = os.environ["FAKE_LIVE_URL"]

def run(cmd, **kw):
    return subprocess.run(cmd, **kw).returncode

def as_postgres(cmd):
    return run(["su", "postgres", "-s", "/bin/sh", "-c", " ".join(cmd)], stdout=subprocess.DEVNULL)

if args[:2] == ["compose", "config"]:
    print("akwaba-backend:0.1.0\npostgres:16.16-trixie\nnginx:1.30.8-alpine"); sys.exit(0)
if args[:4] == ["compose", "exec", "-T", "postgres"]:
    cmd = args[4:]
    if cmd[0] == "pg_dump":
        if os.environ.get("FAKE_FAIL_DUMP"): sys.exit(1)
        extra = os.environ.get("FAKE_DUMP_EXTRA", "").split()
        sys.exit(run([BIN + "/pg_dump", "-d", LIVE, "-Fc", *extra]))
    if cmd[0] == "pg_restore":
        sys.exit(run([BIN + "/pg_restore", *cmd[1:]]))
    if cmd[0] == "psql":
        q = cmd[cmd.index("-c") + 1]
        sys.exit(run([BIN + "/psql", "-d", LIVE, "-At", "-v", "ON_ERROR_STOP=1", "-c", q]))
if args[:2] == ["run", "-d"]:
    name = args[args.index("--name") + 1]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    d = pathlib.Path("/tmp") / ("akwaba-pgtest-" + name)     # traversable par l'utilisateur postgres
    d.mkdir(); shutil.chown(d, "postgres"); os.chmod(d, 0o700)
    (state / (name + ".dir")).write_text(str(d))
    as_postgres([BIN + "/initdb", "-D", str(d / "data"), "-U", "postgres", "--auth=trust", "-E", "UTF8", "--locale=C.UTF-8"])
    as_postgres([BIN + "/pg_ctl", "-D", str(d / "data"), "-o", "'-p %d -k %s -c listen_addresses=127.0.0.1'" % (port, d), "-l", str(d / "log"), "-w", "start"])
    run([BIN + "/createdb", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "restore_test"])
    (state / (name + ".port")).write_text(str(port))
    print("cid-" + name); sys.exit(0)
if args[0] == "exec":
    i = 1
    if args[1] == "-i": i = 2
    name, cmd = args[i], args[i + 1:]
    port = (state / (name + ".port")).read_text()
    conn = ["-h", "127.0.0.1", "-p", port]
    sys.exit(run([BIN + "/" + cmd[0], *conn, *cmd[1:]]))
if args[:2] == ["rm", "-f"]:
    name = args[2]
    marker = state / (name + ".dir")
    d = pathlib.Path(marker.read_text()) if marker.exists() else pathlib.Path("/nonexistent")
    if d.exists():
        as_postgres([BIN + "/pg_ctl", "-D", str(d / "data"), "-m", "immediate", "stop"])
        shutil.rmtree(d, ignore_errors=True)
    sys.exit(0)
sys.exit(3)
'''


# ------------------------------------------------------------------ retention.py (pur)

def _items(dates):
    return [(d, "akwaba-%s.tar.age" % d.strftime("%Y%m%dT%H%M%SZ")) for d in dates]


def test_retention_keeps_7_days_4_weeks_6_months():
    now = datetime(2026, 11, 24, 3, 15, tzinfo=timezone.utc)
    daily = [now - timedelta(days=i) for i in range(400)]                 # une sauvegarde par jour, 400 jours
    keep = retention.select(_items(daily))
    kept = sorted(datetime.strptime(n[7:23], "%Y%m%dT%H%M%SZ") for n in keep)
    assert len([k for k in kept if (now.replace(tzinfo=None) - k).days < 7]) == 7
    assert len(keep) <= 7 + 4 + 6
    months = {(k.year, k.month) for k in kept}
    assert len(months) == 6                                               # 6 mois couverts, pas plus
    assert min(kept) >= datetime(2026, 6, 1)


def test_retention_keeps_the_newest_of_each_period_and_never_the_last_one_alone():
    now = datetime(2026, 11, 24, 3, 15, tzinfo=timezone.utc)
    same_day = [now, now - timedelta(hours=1), now - timedelta(hours=2)]
    keep = retention.select(_items(same_day))
    assert keep == {_items([now])[0][1]}
    assert retention.select([]) == set()


def test_retention_cli_applies_only_to_archives(tmp_path):
    now = datetime(2026, 11, 24, 3, 15, tzinfo=timezone.utc)
    for d, name in _items([now - timedelta(days=i) for i in range(30)]):
        (tmp_path / name).write_text("x")
        (tmp_path / (name + ".sha256")).write_text("x")
    (tmp_path / "status.json").write_text("{}")
    (tmp_path / "autre.tar.age").write_text("x")
    assert retention.main([str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.tar.age"))) == 31, "sans --apply, rien n'est supprimé"
    assert retention.main([str(tmp_path), "--apply"]) == 0
    remaining = sorted(p.name for p in tmp_path.glob("akwaba-*.tar.age"))
    assert len(remaining) == len(retention.select(retention.archives(str(tmp_path))))
    assert all((tmp_path / (n + ".sha256")).exists() for n in remaining)
    assert len(list(tmp_path.glob("*.sha256"))) == len(remaining)
    assert (tmp_path / "status.json").exists() and (tmp_path / "autre.tar.age").exists()


# ------------------------------------------------------------------ backup.sh + test-restore.sh (réels)

def _psql_admin(sql):
    subprocess.run(["su", "postgres", "-c", f"psql -h /tmp -p 5433 -v ON_ERROR_STOP=1 -c \"{sql}\""],
                   check=True, capture_output=True)


@pytest.fixture(scope="module")
def live_db():
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL absent")
    db = "akwaba_backup_test"
    _psql_admin(f"DROP DATABASE IF EXISTS {db}")
    _psql_admin(f"CREATE DATABASE {db} OWNER akwaba")
    url = TEST_URL.rsplit("/", 1)[0] + "/" + db
    env = {**os.environ, "DATABASE_URL": url, "SECRET_KEY": "k" * 48, "ALLOWED_ORIGINS": "https://a.test",
           "FIELD_ENCRYPTION_KEY": "A" * 43 + "="}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", env=env, check=True,
                   capture_output=True)
    libpq = url.replace("postgresql+psycopg://", "postgresql://")
    subprocess.run([str(PG_BIN / "psql"), "-d", libpq, "-v", "ON_ERROR_STOP=1", "-c",
                    "INSERT INTO members (full_name) VALUES ('Membre sauvegardé'), ('Second membre')"],
                   check=True, capture_output=True)
    yield libpq
    _psql_admin(f"DROP DATABASE IF EXISTS {db}")


@pytest.fixture
def env(tmp_path, live_db):
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copytree(BACKUP, repo / "deploy" / "backup", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / ".env").write_text("POSTGRES_USER=akwaba\nPOSTGRES_DB=akwaba\nSECRET_KEY=" + "s" * 48 +
                               "\nFIELD_ENCRYPTION_KEY=cle-de-chiffrement-des-champs\n")
    (repo / ".env").chmod(0o600)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"],
                   cwd=repo, check=True)
    key = tmp_path / "identity.txt"
    subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
    recipient = re.search(r"age1[0-9a-z]+", key.read_text()).group(0)
    config = repo / "backups" / "config"
    config.mkdir(parents=True)
    (config / "age-recipients.txt").write_text(recipient + "\n")
    fake = tmp_path / "fake"
    fake.mkdir()
    fake.chmod(0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(FAKE_DOCKER)
    (bindir / "docker").chmod(0o755)
    offsite = tmp_path / "exit-node" / "archives"
    offsite.mkdir(parents=True)
    return {"repo": repo, "key": key, "fake": fake, "offsite": offsite, "auto": repo / "backups" / "auto",
            "env": {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_DIR": str(fake),
                    "FAKE_LIVE_URL": live_db, "OFFSITE_DEST": str(offsite) + "/"}}


def run_backup(env, *args, **extra):
    res = subprocess.run(["bash", str(env["repo"] / "deploy" / "backup" / "backup.sh"), *args], cwd=env["repo"],
                         env={**env["env"], **extra}, capture_output=True, text=True, timeout=300)
    return res.returncode, res.stdout + res.stderr


def status(env):
    return json.loads((env["auto"] / "status.json").read_text())


def decrypt(env, archive) -> dict:
    out = subprocess.run(["age", "-d", "-i", str(env["key"]), str(archive)], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(out)) as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


@needs_pg
def test_backup_is_encrypted_complete_restorable_and_copied(env):
    rc, out = run_backup(env, "--test-restore")
    assert rc == 0, out
    archives = sorted(env["auto"].glob("akwaba-*.tar.age"))
    assert len(archives) == 1
    archive = archives[0]
    assert archive.read_bytes().startswith(b"age-encryption.org/v1")
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert b"Membre sauvegard" not in archive.read_bytes() and b"SECRET_KEY" not in archive.read_bytes()
    sha = (env["auto"] / (archive.name + ".sha256")).read_text().split()[0]
    assert sha == subprocess.run(["sha256sum", str(archive)], capture_output=True, text=True).stdout.split()[0]
    content = decrypt(env, archive)
    assert set(content) == {"akwaba.dump", "env", "meta.json"}
    assert content["env"] == (env["repo"] / ".env").read_bytes(), ".env sauvegardé (FIELD_ENCRYPTION_KEY)"
    meta = json.loads(content["meta.json"])
    assert meta["alembic_revision"] and meta["postgres_image"] == "postgres:16.16-trixie" and meta["commit"]
    dump = env["repo"].parent / "extrait.dump"
    dump.write_bytes(content["akwaba.dump"])
    listing = subprocess.run([str(PG_BIN / "pg_restore"), "--list", str(dump)], capture_output=True, text=True)
    assert listing.returncode == 0 and "TABLE public members" in listing.stdout
    # Le clair n'a laissé aucune trace.
    assert not list((env["repo"] / "backups").glob(".tmp-*"))
    assert not list((env["repo"] / "backups").rglob("*.dump"))
    st = status(env)
    assert st["backup"]["ok"] and st["backup"]["archive"] == archive.name
    assert st["restore_test"]["ok"] and "RESTAURATION_OK" in st["restore_test"]["details"]
    assert "lignes_metier=" in st["restore_test"]["details"]
    assert st["offsite"]["ok"]
    copied = env["offsite"] / archive.name
    assert copied.read_bytes() == archive.read_bytes() and stat.S_IMODE(copied.stat().st_mode) == 0o600
    assert (env["offsite"] / (archive.name + ".sha256")).exists()
    assert not list(pathlib.Path("/tmp").glob("akwaba-pgtest-akwaba-restore-test-*")), "instance de test détruite"
    calls = [json.loads(c) for c in (env["fake"] / "calls").read_text().splitlines()]
    run_call = next(c for c in calls if c[:2] == ["run", "-d"])
    assert "--network" in run_call and run_call[run_call.index("--network") + 1] == "none"
    assert not any(c[:2] == ["run", "-d"] and ("-p" in c or "-v" in c) for c in calls), "ni port ni volume"


@needs_pg
def test_restore_test_detects_an_incomplete_dump(env):
    rc, out = run_backup(env, "--test-restore", FAKE_DUMP_EXTRA="--exclude-table=members")
    assert rc == 1, out
    st = status(env)
    assert st["restore_test"]["ok"] is False
    assert "tables différentes" in st["restore_test"]["details"] or "members" in st["restore_test"]["details"]
    assert st["backup"]["ok"], "l'archive est tout de même produite (mieux que rien), mais l'échec est signalé"
    assert "TERMINÉE AVEC ERREUR" in out


@needs_pg
def test_no_restore_test_on_weekdays_keeps_the_last_result(env):
    run_backup(env, "--test-restore")
    first = status(env)["restore_test"]
    rc, out = run_backup(env, TEST_RESTORE="no")
    assert rc == 0, out
    assert status(env)["restore_test"] == first
    assert len(list(env["auto"].glob("akwaba-*.tar.age"))) >= 1


@needs_pg
def test_failed_dump_produces_no_archive(env):
    rc, out = run_backup(env, FAKE_FAIL_DUMP="1")
    assert rc == 1 and "pg_dump a échoué" in out
    assert not list(env["auto"].glob("*.tar.age"))
    assert status(env)["backup"]["ok"] is False


@needs_pg
def test_missing_public_key_is_refused(env):
    (env["repo"] / "backups" / "config" / "age-recipients.txt").unlink()
    rc, out = run_backup(env)
    assert rc == 1 and "setup-server.sh" in out
    assert not list(env["auto"].glob("*.tar.age"))


@needs_pg
def test_failed_offsite_copy_keeps_the_local_archive(env):
    rc, out = run_backup(env, TEST_RESTORE="no", OFFSITE_DEST="/nonexistent/dir/")
    assert rc == 1 and "ÉCHEC de la copie" in out
    assert len(list(env["auto"].glob("akwaba-*.tar.age"))) == 1
    st = status(env)
    assert st["backup"]["ok"] and st["offsite"]["ok"] is False


@needs_pg
def test_rotation_and_pre_deploy_dumps(env):
    auto = env["auto"]
    auto.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 1, 1, 3, 15)
    for i in range(60):
        name = "akwaba-%s.tar.age" % (base + timedelta(days=3 * i)).strftime("%Y%m%dT%H%M%SZ")
        (auto / name).write_text("x")
    for i in range(8):
        d = env["repo"] / "backups" / f"akwaba-pre-deploy-c{i}-2026.dump"
        d.write_text("x")
        os.utime(d, (1_700_000_000 + i, 1_700_000_000 + i))
    rc, out = run_backup(env, TEST_RESTORE="no")
    assert rc == 0, out
    assert len(list(auto.glob("akwaba-*.tar.age"))) <= 7 + 4 + 6
    left = sorted(p.name for p in (env["repo"] / "backups").glob("akwaba-pre-*.dump"))
    assert left == [f"akwaba-pre-deploy-c{i}-2026.dump" for i in range(3, 8)], "les 5 plus récents"


@needs_pg
def test_concurrent_backups_are_refused(env):
    import fcntl
    (env["repo"] / "backups").mkdir(exist_ok=True)
    with open(env["repo"] / "backups" / ".backup.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, out = run_backup(env)
    assert rc == 1 and "déjà en cours" in out


# ------------------------------------------------------------------ installation

def test_setup_exit_node_grants_write_only_access():
    text = "\n".join(ln for ln in (BACKUP / "setup-exit-node.sh").read_text().splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "printf 'restrict,command=\"%s -wo %s\" %s\\n'" in text
    for forbidden in ("sshd_config", "PasswordAuthentication", "akwaba-agent", "tailscale", "NOPASSWD"):
        assert forbidden not in text, forbidden
    unit = (BACKUP / "systemd" / "akwaba-backup-prune.service").read_text()
    assert "User=akwaba-backup" in unit and "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/lib/akwaba-backup/archives" in unit


@pytest.mark.skipif(os.geteuid() != 0, reason="root requis par le script")
def test_setup_exit_node_rejects_anything_but_an_ed25519_key(tmp_path):
    for bad in ("", "ssh-rsa AAAA", "ssh-ed25519 AAAA'; rm -rf /", 'ssh-ed25519 AAAA" x'):
        res = subprocess.run(["sh", str(BACKUP / "setup-exit-node.sh"), bad], capture_output=True, text=True)
        assert res.returncode == 2, (bad, res.stdout)


def test_server_timer_and_service():
    timer = (BACKUP / "systemd" / "akwaba-backup.timer").read_text()
    assert "OnCalendar=*-*-* 03:15:00 UTC" in timer and "Persistent=true" in timer
    service = (BACKUP / "systemd" / "akwaba-backup.service").read_text()
    assert "ExecStart=__REPO__/deploy/backup/backup.sh" in service and "User=__USER__" in service
    assert "UMask=0077" in service and "NoNewPrivileges=yes" in service


@pytest.mark.skipif(os.geteuid() != 0 or shutil.which("setpriv") is None, reason="root + setpriv requis")
def test_setup_server_writes_only_the_public_key_and_a_dedicated_ssh_key():
    import tempfile
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="akwaba-setup-", dir="/tmp"))   # traversable par nobody
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    shutil.copytree(BACKUP, repo / "deploy" / "backup")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text("#!/bin/sh\nexit 0\n")
    (bindir / "docker").chmod(0o755)
    subprocess.run(["chown", "-R", "nobody:nogroup", str(tmp_path)], check=True)
    tmp_path.chmod(0o755)
    key = subprocess.run(["age-keygen"], capture_output=True, text=True).stdout
    recipient = re.search(r"age1[0-9a-z]+", key).group(0)
    nobody = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups"]
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    script = str(repo / "deploy" / "backup" / "setup-server.sh")
    bad = subprocess.run([*nobody, "bash", script, "--recipient", "age1pasbon", "--sans-systemd"], env=env,
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "clé publique age" in bad.stderr
    res = subprocess.run([*nobody, "bash", script, "--recipient", recipient, "--exit-node", "pi-exit.tail14614.ts.net",
                          "--sans-systemd"], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    config = repo / "backups" / "config"
    assert (config / "age-recipients.txt").read_text().strip() == recipient
    assert "AGE-SECRET-KEY" not in "".join(p.read_text(errors="ignore") for p in config.iterdir() if p.is_file())
    assert stat.S_IMODE(config.stat().st_mode) == 0o700
    for name in ("age-recipients.txt", "id_ed25519", "offsite.conf"):
        assert stat.S_IMODE((config / name).stat().st_mode) == 0o600, name
    assert (config / "offsite.conf").read_text() == "OFFSITE_HOST=pi-exit.tail14614.ts.net\nOFFSITE_USER=akwaba-backup\n"
    assert "sudo sh deploy/backup/setup-exit-node.sh 'ssh-ed25519 " in res.stdout
    root_run = subprocess.run(["bash", script, "--recipient", recipient, "--sans-systemd"], env=env,
                              capture_output=True, text=True)
    assert root_run.returncode == 1 and "pas en root" in root_run.stderr
    shutil.rmtree(tmp_path, ignore_errors=True)
