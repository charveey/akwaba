"""Échantillonnage local, sans aucun envoi réseau.

    python3 -m akwaba_agent --once --sudo --exclude-source IP          # un cycle, affichage seul
    python3 -m akwaba_agent --cycles 3 --sudo --spool /tmp/x/agent.db  # 3 cycles, écrit dans le spool
"""
from __future__ import annotations

import argparse
import signal
import threading
from typing import List, Optional

from akwaba_agent.collector.conntrack import read_conntrack
from akwaba_agent.collector.timeouts import DEFAULT_DIR, load_timeouts
from akwaba_agent.peers.status import StatusReader
from akwaba_agent.resolver.whois import WhoisResolver
from akwaba_agent.runner import run_loop
from akwaba_agent.sampler import Sampler
from akwaba_agent.spool.store import Spool


def _report(res, spool) -> None:
    h = res.health
    print("conntrack=%s status=%s whois=%s lignes=%d non_reconnues=%d observations=%d spool=%s" % (
        h.conntrack_ok, h.status_ok, h.whois_ok, h.lines, h.unparsed, res.observations,
        "-" if spool is None else spool.pending()))
    for w in h.warnings:
        print("avertissement : %s" % w)
    if res.payload and res.payload["observations"]:
        print("%-18s %6s %8s %9s %8s %6s %9s %s" % (
            "source", "total", "recents", "repondus", "nouveaux", "serie", "confirme", "identite"))
        for o in res.payload["observations"]:
            print("%-18s %6d %8d %9d %8s %6d %9s %s%s" % (
                o["src_ip"], o["total_flows"], o["recent_flows"], o["recent_replied_flows"],
                "-" if o["new_flows"] is None else o["new_flows"], o["consecutive_active_windows"],
                o["active_confirmed"], o["identity_status"],
                " (partage)" if o["identity"] and o["identity"]["is_sharee"] else ""))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m akwaba_agent")
    ap.add_argument("--sudo", action="store_true", help="préfixer conntrack par `sudo -n`")
    ap.add_argument("--window", type=int, default=60)
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--exclude-source", action="append", default=[])
    ap.add_argument("--timeouts-dir", default=DEFAULT_DIR)
    ap.add_argument("--spool", help="chemin du spool SQLite (obligatoire hors --once)")
    ap.add_argument("--once", action="store_true", help="un seul cycle")
    ap.add_argument("--cycles", type=int, default=0, help="nombre de cycles (0 = sans fin)")
    ap.add_argument("--quiet", action="store_true", help="n'affiche pas le tableau à chaque cycle")
    args = ap.parse_args(argv)

    if args.window <= 0 or args.interval <= 0 or args.interval > args.window:
        print("arguments invalides : 0 < interval <= window")
        return 2
    if not args.once and not args.spool:
        print("--spool est obligatoire hors --once")
        return 2

    prefix = ("sudo", "-n") if args.sudo else ()
    resolver = WhoisResolver()
    sampler = Sampler(
        read=lambda: read_conntrack(prefix=prefix),
        timeouts=lambda: load_timeouts(args.timeouts_dir),
        status=StatusReader().read,
        resolver=resolver,
        window_seconds=args.window,
        exclude_sources=args.exclude_source,
    )
    spool = Spool(args.spool) if args.spool else None

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    stats = run_loop(
        sampler, spool, args.interval,
        sleep=stop.wait, should_stop=stop.is_set,
        max_cycles=1 if args.once else (args.cycles or None),
        on_cycle=None if args.quiet else _report,
    )
    print("cycles=%d lots_ecrits=%d erreurs=%d%s" % (
        stats.cycles, stats.enqueued, stats.errors,
        "" if not stats.last_error else " derniere_erreur=%s" % stats.last_error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
