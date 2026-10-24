"""Envoi des lots et du heartbeat au backend. Stdlib uniquement (Python 3.9). Jamais d'exception vers l'appelant."""
from __future__ import annotations

import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, Optional

from akwaba_agent.heartbeat import to_wire
from akwaba_agent.protocol import build_headers

BATCH_PATH = "/api/agent/batches"
HEARTBEAT_PATH = "/api/agent/heartbeat"
PING_PATH = "/api/agent/ping"
BACKOFF_BASE = 5.0
BACKOFF_CAP = 300.0
DROP_STATUSES = (409, 413, 422)     # refus définitifs : le lot est abandonné, jamais renvoyé
MAX_REPLY_BYTES = 65536


@dataclass
class Reply:
    status: int                       # 0 = aucune réponse HTTP
    date: Optional[str] = None
    category: Optional[str] = None    # si status == 0 : "timeout" ou "network"
    body: bytes = b""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HttpTransport:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/") \
                or parts.query or parts.fragment:
            raise ValueError("backend_url invalide")
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        # Aucun proxy d'environnement, aucune redirection : les en-têtes signés ne vont que là où on les envoie.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def post(self, path: str, body: bytes, headers: Dict[str, str]) -> Reply:
        req = urllib.request.Request(
            self._base + path, data=body, method="POST", headers=dict(headers, **{"Content-Type": "application/json"}))
        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                return Reply(resp.status, resp.headers.get("Date"), None, resp.read(MAX_REPLY_BYTES))
        except urllib.error.HTTPError as exc:
            try:
                data = exc.read(MAX_REPLY_BYTES)
            except Exception:  # noqa: BLE001
                data = b""
            finally:
                exc.close()
            return Reply(exc.code, exc.headers.get("Date") if exc.headers else None, None, data)
        except (socket.timeout, TimeoutError):
            return Reply(0, category="timeout")
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                return Reply(0, category="timeout")
            return Reply(0, category="network")
        except Exception:  # noqa: BLE001
            return Reply(0, category="network")


def _category(reply: Reply) -> str:
    if reply.status == 0:
        return reply.category or "network"
    if reply.status == 401:
        return "http_401"
    if 500 <= reply.status < 600:
        return "http_5xx"
    return "http_%d" % reply.status


class Sender:
    def __init__(
        self,
        transport,
        agent_id: str,
        secret: bytes,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        rand: Callable[[], float] = random.random,
        max_per_cycle: int = 30,
        budget_seconds: float = 20.0,
    ) -> None:
        self._transport, self._agent_id, self._secret = transport, agent_id, secret
        self._wall, self._mono, self._rand = wall, mono, rand
        self.max_per_cycle, self.budget_seconds = max_per_cycle, budget_seconds
        self.sent_batches = 0
        self.rejected_batches = 0
        self.send_failures = 0
        self.last_send_error: Optional[str] = None
        self.clock_skew_seconds: Optional[int] = None
        self.backend_reachable: Optional[bool] = None
        self._failures = 0
        self._retry_at: Optional[float] = None

    # ------------------------------------------------------------------ état
    @property
    def in_backoff(self) -> bool:
        return self._retry_at is not None and self._mono() < self._retry_at

    def stats(self) -> dict:
        return {
            "sent_batches": self.sent_batches, "rejected_batches": self.rejected_batches,
            "send_failures": self.send_failures, "last_send_error": self.last_send_error,
            "clock_skew_seconds": self.clock_skew_seconds, "backend_reachable": self.backend_reachable,
        }

    def _succeed(self) -> None:
        self._failures, self._retry_at = 0, None
        self.last_send_error, self.backend_reachable = None, True

    def _fail(self, reply: Reply) -> None:
        self.send_failures += 1
        self._failures += 1
        self.last_send_error = _category(reply)
        self.backend_reachable = reply.status != 0
        delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** min(self._failures - 1, 10)))
        self._retry_at = self._mono() + delay * (0.8 + 0.4 * self._rand())

    def _reject(self, reply: Reply) -> None:
        self.last_send_error = "rejected_%d" % reply.status
        self.backend_reachable = True

    # ------------------------------------------------------------------ envoi
    def _note_date(self, reply: Reply) -> None:
        if not reply.date:
            return
        try:
            self.clock_skew_seconds = int(round(parsedate_to_datetime(reply.date).timestamp() - self._wall()))
        except (TypeError, ValueError):
            pass

    def _post(self, path: str, body: bytes) -> Reply:
        headers = build_headers(self._agent_id, self._secret, "POST", path, body, now=self._wall())
        try:
            reply = self._transport.post(path, body, headers)
        except Exception:  # noqa: BLE001 : le transport ne doit pas lever, mais on ne parie pas dessus
            reply = Reply(0, category="network")
        self._note_date(reply)
        return reply

    def flush(self, spool) -> None:
        """Envoie les lots du spool, du plus ancien au plus récent, jusqu'au premier échec réessayable."""
        if self.in_backoff:
            return
        deadline = self._mono() + self.budget_seconds
        for batch in spool.peek(self.max_per_cycle):
            if self._mono() > deadline:
                break
            try:
                payload = json.loads(batch.payload)
                if not isinstance(payload, dict):
                    raise ValueError
            except ValueError:
                spool.ack(batch.batch_id)
                self.rejected_batches += 1
                self.last_send_error = "corrupt_batch"
                continue
            payload["batch_id"] = batch.batch_id
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            reply = self._post(BATCH_PATH, body)
            if reply.status == 200:
                spool.ack(batch.batch_id)
                self.sent_batches += 1
                self._succeed()
            elif reply.status in DROP_STATUSES:
                spool.ack(batch.batch_id)
                self.rejected_batches += 1
                self._reject(reply)
            else:
                spool.mark_attempt(batch.batch_id)
                self._fail(reply)
                break

    def send_heartbeat(self, hb: dict) -> None:
        if self.in_backoff:
            return
        body = json.dumps(to_wire(hb), separators=(",", ":"), sort_keys=True).encode("utf-8")
        reply = self._post(HEARTBEAT_PATH, body)
        if reply.status == 200:
            self._succeed()
        elif reply.status in DROP_STATUSES:
            self._reject(reply)            # le heartbeat est un état : on le renverra au prochain cycle
        else:
            self._fail(reply)

    def ping(self) -> dict:
        reply = self._post(PING_PATH, b"{}")
        out = {"ok": reply.status == 200, "status": reply.status, "category": None,
               "clock_skew_seconds": self.clock_skew_seconds, "exit_node": None, "server_time": None}
        if not out["ok"]:
            out["category"] = _category(reply)
            return out
        try:
            data = json.loads(reply.body)
            out["exit_node"], out["server_time"] = data.get("exit_node"), data.get("server_time")
        except (ValueError, AttributeError):
            pass
        return out
