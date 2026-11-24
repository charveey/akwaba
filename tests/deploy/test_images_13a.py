"""Lot 13a — images figées, dépendances verrouillées, durcissement, journaux bornés, issue #8."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "backend" / "Dockerfile").read_text()
LOCK = (ROOT / "backend" / "requirements.lock").read_text()
SERVER_RAM_MB = 954                     # mesuré sur le serveur (free -m)


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    if shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("docker compose absent")
    work = tmp_path_factory.mktemp("compose13a")
    shutil.copy(ROOT / "docker-compose.yml", work / "docker-compose.yml")
    shutil.copy(ROOT / ".env.example", work / ".env")
    r = subprocess.run(["docker", "compose", "--project-directory", str(work), "-p", "akwaba", "config", "--format",
                        "json"], capture_output=True, text=True, cwd=work, env={"PATH": "/usr/bin:/bin",
                                                                               "GIT_COMMIT": "abc1234"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def mb(value) -> float:
    return int(value) / 1024 / 1024


# ------------------------------------------------------------------ Compose

def test_every_container_log_is_bounded(cfg):
    for name, svc in cfg["services"].items():
        log = svc.get("logging") or {}
        assert log.get("driver") == "json-file", name
        assert log["options"] == {"max-size": "10m", "max-file": "3"}, name


def test_memory_and_process_limits_fit_the_server(cfg):
    limits = {n: mb(s["mem_limit"]) for n, s in cfg["services"].items()}
    assert limits == {"postgres": 320, "backend": 256, "web": 64}, limits
    assert sum(limits.values()) <= SERVER_RAM_MB * 0.75, "laisser de la marge à l'hôte, tailscaled et Docker"
    assert {n: s.get("pids_limit") for n, s in cfg["services"].items()} == {"postgres": 128, "backend": 128, "web": 64}


def test_backend_is_hardened_like_web(cfg):
    be = cfg["services"]["backend"]
    assert be["read_only"] is True
    assert be["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in be["security_opt"]
    assert be["tmpfs"] == ["/tmp:size=16m,mode=1777"]
    # Inchangés : bindings de l'agent et de l'administration locale, proxy de confiance = Nginx.
    assert sorted((p.get("host_ip"), str(p["published"])) for p in be["ports"]) == [
        ("100.126.200.88", "8000"), ("127.0.0.1", "8000")]
    assert be["environment"]["TRUSTED_PROXY_IPS"] == "10.250.11.10"


def test_images_are_pinned_to_a_version_and_a_debian_variant(cfg):
    assert re.fullmatch(r"postgres:16\.\d+-(trixie|bookworm)", cfg["services"]["postgres"]["image"])
    assert re.fullmatch(r"nginx:1\.30\.\d+-alpine", cfg["services"]["web"]["image"])
    base = re.search(r"^FROM (\S+)$", DOCKERFILE, re.M).group(1)
    assert re.fullmatch(r"python:3\.12\.\d+-slim-(trixie|bookworm)", base), base


def test_postgres_data_and_network_untouched(cfg):
    pg = cfg["services"]["postgres"]
    assert not pg.get("ports")
    assert any(v["source"] == "pgdata" and v["target"] == "/var/lib/postgresql/data" for v in pg["volumes"])
    assert list(pg["networks"]) == ["internal"]


def test_backend_image_is_tagged_with_the_commit(cfg):
    build = cfg["services"]["backend"]["build"]
    assert build["args"]["GIT_COMMIT"] == "abc1234"
    assert "akwaba-backend:abc1234" in build["tags"]
    assert cfg["services"]["backend"]["image"] == "akwaba-backend:0.1.0"
    assert 'org.opencontainers.image.revision="${GIT_COMMIT}"' in DOCKERFILE


# ------------------------------------------------------------------ Dockerfile et dépendances

def test_dockerfile_installs_only_the_hashed_lock():
    assert "pip install --require-hashes -r requirements.lock" in DOCKERFILE
    assert "requirements.txt" not in DOCKERFILE.replace("généré depuis requirements.txt", "")
    assert "USER akwaba" in DOCKERFILE


def test_lock_pins_every_package_with_hashes():
    entries = re.split(r"\n(?=[a-z0-9])", LOCK.split("\n", 5)[-1].strip())
    assert len(entries) >= 20
    for entry in entries:
        assert re.match(r"^[a-z0-9._-]+==[0-9][^ ]* ", entry), entry.splitlines()[0]
        assert "--hash=sha256:" in entry, entry.splitlines()[0]


def test_lock_satisfies_the_declared_ranges():
    from packaging.requirements import Requirement
    pinned = dict(re.findall(r"^([a-z0-9._-]+)==([^ ]+) ", LOCK, re.M))
    for line in (ROOT / "backend" / "requirements.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        req = Requirement(line)
        name = req.name.lower().replace("_", "-")
        assert name in pinned, name
        assert req.specifier.contains(pinned[name], prereleases=False), (name, pinned[name], str(req.specifier))
        for extra in req.extras:              # psycopg[binary] → psycopg-binary verrouillé à la même version
            assert pinned.get(f"{name}-{extra}") == pinned[name], (name, extra)


def test_uvicorn_never_trusts_forwarded_headers():
    """Issue #8 : --no-proxy-headers dans la commande du conteneur (le harnais e2e reprend ces options)."""
    cmd = json.loads(re.search(r"^CMD (.+)$", DOCKERFILE, re.M).group(1))
    assert cmd[:2] == ["uvicorn", "app.main:app"]
    assert "--no-proxy-headers" in cmd and "--proxy-headers" not in cmd
    assert "--forwarded-allow-ips" not in " ".join(cmd)
    assert cmd[cmd.index("--port") + 1] == "8000" and cmd[cmd.index("--host") + 1] == "0.0.0.0"
