"""Lot 11a — invariants de docker-compose.yml et cohérence avec la configuration Nginx."""
import ipaddress
import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    if shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("docker compose absent")
    work = tmp_path_factory.mktemp("compose")
    for name in ("docker-compose.yml", ".env.example"):
        shutil.copy(ROOT / name, work / name)
    shutil.copy(ROOT / ".env.example", work / ".env")
    r = subprocess.run(["docker", "compose", "--project-directory", str(work), "-p", "akwaba", "config", "--format", "json"],
                       capture_output=True, text=True, cwd=work)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def ports(svc):
    return sorted((p.get("host_ip", ""), str(p.get("published")), p["target"]) for p in svc.get("ports", []))


def test_postgres_never_published_and_keeps_volume(cfg):
    pg = cfg["services"]["postgres"]
    assert not pg.get("ports")
    assert any(v["source"] == "pgdata" and v["target"] == "/var/lib/postgresql/data" for v in pg["volumes"])
    assert "pgdata" in cfg["volumes"]


def test_backend_bindings_unchanged(cfg):
    assert ports(cfg["services"]["backend"]) == [("100.126.200.88", "8000", 8000), ("127.0.0.1", "8000", 8000)]


def test_web_only_on_loopback_8080(cfg):
    assert ports(cfg["services"]["web"]) == [("127.0.0.1", "8080", 8080)]


def test_web_image_pinned_and_hardened(cfg):
    web = cfg["services"]["web"]
    assert re.fullmatch(r"nginx:1\.30\.\d+-alpine", web["image"])
    assert web["read_only"] is True
    assert web["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in web["security_opt"]
    assert web["user"] == "101:101"
    assert web["restart"] == "unless-stopped"
    assert web["healthcheck"]["test"][:2] == ["CMD", "wget"]
    assert all(v.get("read_only") for v in web["volumes"])
    targets = {v["target"] for v in web["volumes"]}
    assert targets == {"/etc/nginx/nginx.conf", "/etc/nginx/conf.d", "/etc/nginx/snippets", "/usr/share/nginx/akwaba"}


def test_fixed_subnet_and_nginx_ip_consistent_everywhere(cfg):
    ipam = cfg["networks"]["internal"]["ipam"]["config"][0]
    subnet = ipaddress.ip_network(ipam["subnet"])
    ip_range = ipaddress.ip_network(ipam["ip_range"])
    gateway = ipaddress.ip_address(ipam["gateway"])
    web_ip = ipaddress.ip_address(cfg["services"]["web"]["networks"]["internal"]["ipv4_address"])
    assert web_ip in subnet and web_ip not in ip_range and web_ip != gateway
    assert ip_range.subnet_of(subnet)
    # Pas de chevauchement avec le CGNAT Tailscale ni les pools Docker par défaut.
    for reserved in ("100.64.0.0/10", "172.16.0.0/12", "192.168.0.0/16"):
        assert not subnet.overlaps(ipaddress.ip_network(reserved))
    # Le backend ne fait confiance qu'à Nginx…
    assert cfg["services"]["backend"]["environment"]["TRUSTED_PROXY_IPS"] == str(web_ip)
    # … et Nginx qu'à la passerelle (= l'hôte, où tourne tailscaled).
    nginx = (ROOT / "deploy" / "nginx" / "conf.d" / "akwaba.conf").read_text()
    assert re.findall(r"set_real_ip_from\s+(\S+);", nginx) == [str(gateway)]
    for script in ("deploy/migrate-network-11a.sh",):
        text = (ROOT / script).read_text()
        assert f'SUBNET="{subnet}"' in text and f'WEB_IP="{web_ip}"' in text


def test_dry_run_unchanged():
    env = (ROOT / ".env.example").read_text()
    assert re.search(r"^REVOCATION_MODE=dry_run$", env, re.M)
    assert re.search(r"^ENFORCER_ENABLED=false$", env, re.M)
    assert re.search(r"^ALLOWED_ORIGINS=https://[^/\s]+$", env, re.M)


def test_migration_never_deletes_volume():
    text = (ROOT / "deploy" / "migrate-network-11a.sh").read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "docker compose down --remove-orphans" in code
    assert not re.search(r"down[^\n]*\s(-v|--volumes)\b", code)
    assert "volume rm" not in code and "system prune" not in code and "volume prune" not in code


def test_serve_script_never_resets():
    code = "\n".join(l for l in (ROOT / "deploy" / "tailscale" / "serve-8443.sh").read_text().splitlines()
                     if not l.lstrip().startswith("#"))
    assert "serve reset" not in code
    assert not re.search(r"tailscale\s+funnel", code)
