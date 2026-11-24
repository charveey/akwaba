"""Outils partagés de la Phase 12 : faux Exit Node, agent réel, requêtes signées, client administrateur.

Faux Exit Node (aucun appel système réel) :
  - `conntrack -L -f ipv4|ipv6` et `tailscale status --json` : exécutables factices en tête du PATH ;
  - LocalAPI whois : serveur HTTP sur un socket Unix, réponses lues dans un fichier à chaque requête,
    pour changer les identités d'un scénario à l'autre sans redémarrer.

L'agent est le vrai processus `python -m akwaba_agent` (voir agent_launcher.py) ; l'identifiant est créé
par la commande d'administration de production `python -m app.cli create-agent`.
"""
import http.server
import json
import os
import pathlib
import re
import shutil
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from typing import Iterable, List, Optional

from conftest import BACKEND, ROOT

sys.path.insert(0, str(ROOT / "agent"))

TIMEOUTS = {
    "nf_conntrack_tcp_timeout_established": 432000, "nf_conntrack_tcp_timeout_syn_sent": 120,
    "nf_conntrack_udp_timeout": 30, "nf_conntrack_udp_timeout_stream": 120, "nf_conntrack_icmp_timeout": 30,
    "nf_conntrack_count": 420, "nf_conntrack_max": 1000, "nf_conntrack_acct": 1,
}


@dataclass
class Device:
    """Un appareil client vu par l'Exit Node. login=None : inconnu de la LocalAPI (whois 404)."""
    ip: str
    login: Optional[str] = None
    user_id: Optional[int] = None
    stable_id: Optional[str] = None
    hostname: Optional[str] = None
    sharee: bool = True
    flows: int = 0               # flux TCP établis, avec réponse, actifs il y a 2 s (0 : aucune ligne générée)


def active_flow_lines(ip: str, count: int, port_base: int) -> List[str]:
    """`count` flux sortants récents avec réponse : de quoi confirmer l'activité (≥ 3 sur 2 fenêtres)."""
    return [f"tcp      6 431998 ESTABLISHED src={ip} dst=203.0.113.{30 + i} sport={port_base + i} dport=443 "
            f"src=203.0.113.{30 + i} dst=192.168.1.99 sport=443 dport={port_base + i} [ASSURED] mark=0 use=1"
            for i in range(count)]


class _Whois(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        addr = self.path.split("addr=", 1)[-1]
        entry = json.loads(self.server.map_file.read_text()).get(addr)
        if entry:
            body = json.dumps(entry).encode()
            self.send_response(200)
        else:
            body = b"no match for IP:port"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        sock, _ = super().get_request()
        return sock, ("local", 0)          # BaseHTTPRequestHandler attend une adresse (hôte, port)


def _write_exe(path: pathlib.Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)


class FakeExitNode:
    def __init__(self) -> None:
        self.base = pathlib.Path(tempfile.mkdtemp(prefix="ak12-", dir="/tmp"))   # socket Unix ≤ 108 octets
        self.bin, self.netfilter, self.sock = self.base / "bin", self.base / "netfilter", self.base / "tailscaled.sock"
        self.bin.mkdir()
        self.netfilter.mkdir()
        for name, value in TIMEOUTS.items():
            (self.netfilter / name).write_text(f"{value}\n")
        _write_exe(self.bin / "conntrack", f"""
import sys
family = sys.argv[sys.argv.index("-f") + 1]
for line in open({str(self.base / "conntrack.txt")!r}):
    src = line.split("src=", 1)[1].split()[0] if "src=" in line else ""
    if (family == "ipv6") == (":" in src):
        sys.stdout.write(line)
""")
        _write_exe(self.bin / "tailscale", f"""
import sys
assert sys.argv[1:] == ["status", "--json"], sys.argv
sys.stdout.write(open({str(self.base / "status.json")!r}).read())
""")
        self._server = _UnixHTTPServer(str(self.sock), _Whois)
        self._server.map_file = self.base / "whois.json"
        self.configure([])
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def configure(self, devices: Iterable[Device], extra_lines: Iterable[str] = ()) -> None:
        devices = list(devices)
        lines = list(extra_lines)
        for n, d in enumerate(devices):
            lines += active_flow_lines(d.ip, d.flows, 50000 + 100 * n)
        (self.base / "conntrack.txt").write_text("\n".join(lines) + "\n")
        whois, peers = {}, {}
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for d in devices:
            if d.login is None:
                continue
            whois[d.ip] = {
                "Node": {"StableID": d.stable_id, "Hostinfo": {"Hostname": d.hostname, "ShareeNode": d.sharee}},
                "UserProfile": {"ID": d.user_id, "LoginName": d.login, "DisplayName": d.login.split("@")[0]},
            }
            peer = peers.setdefault(d.stable_id, {
                "ID": d.stable_id, "TailscaleIPs": [], "Online": True, "Active": True, "LastHandshake": now,
                "LastSeen": now, "LastWrite": now, "RxBytes": 1000, "TxBytes": 2000, "ShareeNode": d.sharee,
                "ExitNode": False})
            peer["TailscaleIPs"].append(d.ip)
        (self.base / "whois.json").write_text(json.dumps(whois))
        (self.base / "status.json").write_text(json.dumps({"Peer": {f"nodekey:{k}": v for k, v in peers.items()}}))

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        shutil.rmtree(self.base, ignore_errors=True)


# --------------------------------------------------------------------------- agent

def cli(stack, *args):
    """Commande d'administration de production, avec la configuration du backend de test."""
    return subprocess.run([sys.executable, "-m", "app.cli", *args], cwd=BACKEND, env=stack["backend"].env,
                          capture_output=True, text=True, timeout=60)


def create_agent(stack, node: FakeExitNode, name: str, ip: str, *, rotate: bool = False) -> dict:
    res = cli(stack, "create-agent", "--name", name, "--ip", ip, *(["--rotate"] if rotate else []))
    assert res.returncode == 0, res.stderr
    key_id = re.search(r"key_id = (agt_[0-9a-f]{16})", res.stderr).group(1)
    secret = res.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", secret)
    secret_file = node.base / f"{name}.secret"
    secret_file.write_text(secret + "\n")
    secret_file.chmod(0o600)
    return {"name": name, "key_id": key_id, "secret": secret, "secret_file": secret_file,
            "url": f"http://127.0.0.1:{stack['backend_port']}"}


def _agent_cmd(node: FakeExitNode, agent: dict, url: Optional[str], args) -> tuple:
    env = {**os.environ, "PATH": f"{node.bin}{os.pathsep}{os.environ['PATH']}"}
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(k, None)
    cmd = [sys.executable, str(ROOT / "tests" / "e2e" / "agent_launcher.py"), str(node.sock),
           "--backend-url", url or agent["url"], "--agent-id", agent["key_id"],
           "--secret-file", str(agent["secret_file"]), "--timeouts-dir", str(node.netfilter), *args]
    return cmd, env


def run_agent(node: FakeExitNode, agent: dict, *args, url: Optional[str] = None, timeout: int = 90):
    cmd, env = _agent_cmd(node, agent, url, args)
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)


def spawn_agent(node: FakeExitNode, agent: dict, *args):
    cmd, env = _agent_cmd(node, agent, None, args)
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def summary(stdout: str) -> dict:
    line = [x for x in stdout.splitlines() if x.startswith("cycles=")][-1]
    return {k: v for k, v in (kv.split("=", 1) for kv in line.split())}


def signed_post(agent, path, body, *, secret=None, key_id=None, now=None, nonce=None, url=None, query=""):
    """Requête HTTP réelle signée avec le module de protocole de l'agent (mêmes en-têtes que l'agent)."""
    from akwaba_agent.protocol import build_headers
    from akwaba_agent.sender.client import HttpTransport
    headers = build_headers(key_id or agent["key_id"], bytes.fromhex(secret or agent["secret"]), "POST", path, body,
                            now=now, nonce=nonce)
    return HttpTransport(url or agent["url"], 10).post(path + query, body, headers)


# --------------------------------------------------------------------------- administration

class Client:
    """Client HTTPS brut par la vraie chaîne TLS (serve) → Nginx → backend, avec son propre cookie."""

    def __init__(self, stack) -> None:
        self.base = stack["base"]
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        self.jar = CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx),
            urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, body=None, headers: Optional[dict] = None):
        """Renvoie (statut, en-têtes, corps texte). `body` : dict (JSON) ou bytes.

        Nginx limite /api/auth/login (10 par minute, rafale de 5) : un 429 de Nginx n'atteint jamais le
        backend ; la tentative est alors simplement rejouée après le délai de recharge.
        """
        for _ in range(15):
            status, hdrs, raw = self._request_once(method, path, body, headers)
            if not (status == 429 and path == "/api/auth/login"):
                return status, hdrs, raw
            time.sleep(6.5)
        return status, hdrs, raw

    def _request_once(self, method: str, path: str, body=None, headers: Optional[dict] = None):
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        hdrs = dict(headers or {})
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + path, method=method, data=data, headers=hdrs)
        try:
            with self._opener.open(req, timeout=15) as resp:
                return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read().decode("utf-8", "replace")

    def cookie(self, name: str) -> Optional[str]:
        return next((c.value for c in self.jar if c.name == name), None)


class Admin(Client):
    """Session administrateur par la vraie chaîne : cookie, Origin, CSRF."""

    def __init__(self, stack, email: Optional[str] = None, password: Optional[str] = None) -> None:
        super().__init__(stack)
        self.post("/api/auth/login", {"email": email or stack["email"], "password": password or stack["password"]},
                  csrf=False)
        self.csrf = self.get("/api/auth/me")["csrf_token"]

    def get_raw(self, path):
        status, _, raw = self.request("GET", path)
        return status, raw

    def get(self, path):
        status, raw = self.get_raw(path)
        assert status == 200, (path, status, raw)
        return json.loads(raw)

    def post(self, path, payload=None, *, csrf=True, expect=(200, 201)):
        headers = {"Origin": self.base}
        if csrf:
            headers["X-CSRF-Token"] = self.csrf
        status, _, raw = self.request("POST", path, payload if payload is not None else {}, headers)
        assert status in expect, (path, status, raw)
        return json.loads(raw) if raw else None
