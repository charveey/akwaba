"""Agent d'observation. Aucun envoi réseau à ce stade.

    python3 -m akwaba_agent --once --sudo                          # un cycle, affichage seul
    python3 -m akwaba_agent --config /etc/akwaba-agent/agent.ini   # service
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import signal
import threading
import time
from typing import List, Optional

from akwaba_agent import __version__
from akwaba_agent.aggregator.activity import ActivityTracker
from akwaba_agent.collector.conntrack import read_conntrack
from akwaba_agent.collector.timeouts import load_timeouts
from akwaba_agent.config import Config, ConfigError, load_config
from akwaba_agent.heartbeat import TransitionLogger, build_heartbeat, read_conntrack_load, write_atomic
from akwaba_agent.peers.status import StatusReader
from akwaba_agent.resolver.whois import WhoisResolver
from akwaba_agent.runner import LoopStats, run_loop
from akwaba_agent.sampler import Sampler
from akwaba_agent.spool.store import Spool


def _report(res, spool) -> None:
    if res is None:
        print("cycle en erreur")
        return
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
    ap.add_argument("--config", help="fichier INI (sinon valeurs par défaut)")
    ap.add_argument("--sudo", action="store_true", help="préfixer conntrack par `sudo -n`")
    ap.add_argument("--quiet", action="store_true", help="n'affiche pas le tableau à chaque cycle")
    ap.add_argument("--window", type=int)
    ap.add_argument("--interval", type=int)
    ap.add_argument("--exclude-source", action="append", default=[])
    ap.add_argument("--timeouts-dir")
    ap.add_argument("--spool", help="chemin du spool SQLite")
    ap.add_argument("--heartbeat", help="chemin du fichier heartbeat")
    ap.add_argument("--once", action="store_true", help="un seul cycle, sans spool sauf --spool")
    ap.add_argument("--cycles", type=int, default=0, help="nombre de cycles (0 = sans fin)")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config, must_exist=True) if args.config else Config()
    except ConfigError as exc:
        print("configuration invalide : %s" % exc)
        return 2

    window = args.window if args.window is not None else cfg.window_seconds
    interval = args.interval if args.interval is not None else cfg.interval_seconds
    if window <= 0 or interval <= 0 or interval > window:
        print("arguments invalides : 0 < interval <= window")
        return 2
    try:
        exclude = tuple(str(ipaddress.ip_address(x)) for x in list(cfg.exclude_sources) + args.exclude_source)
    except ValueError:
        print("--exclude-source : adresse IP invalide")
        return 2

    spool_path = args.spool or cfg.spool_path
    if args.heartbeat:
        heartbeat_path = args.heartbeat
    elif args.spool:
        heartbeat_path = os.path.join(os.path.dirname(args.spool), "heartbeat.json")
    else:
        heartbeat_path = cfg.heartbeat_path
    if args.once and not args.spool:
        spool_path, heartbeat_path = None, args.heartbeat
    if not args.once and not spool_path:
        print("un chemin de spool est obligatoire hors --once (--spool ou spool_path)")
        return 2

    netfilter_dir = args.timeouts_dir or cfg.timeouts_dir
    quiet = args.quiet or cfg.quiet
    prefix = ("sudo", "-n") if (args.sudo or cfg.use_sudo) else ()

    sampler = Sampler(
        read=lambda: read_conntrack(prefix=prefix),
        timeouts=lambda: load_timeouts(netfilter_dir),
        status=StatusReader().read,
        resolver=WhoisResolver(),
        tracker=ActivityTracker(cfg.min_replied_flows, cfg.required_windows),
        window_seconds=window,
        exclude_sources=exclude,
    )
    spool = Spool(spool_path) if spool_path else None
    if spool is not None and spool.recovered_corruption:
        print("spool corrompu : ancien fichier mis de côté, nouveau spool créé")

    stats = LoopStats()
    logger = TransitionLogger()

    def on_cycle(res, spool_) -> None:
        hb = build_heartbeat(time.time(), res, stats, spool_, read_conntrack_load(netfilter_dir),
                             cfg.saturation_threshold, __version__)
        if heartbeat_path:
            write_atomic(heartbeat_path, hb)
        for line in logger.changes(hb):
            print(line)
        if not quiet:
            _report(res, spool_)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    run_loop(
        sampler, spool, interval,
        sleep=stop.wait, should_stop=stop.is_set,
        max_cycles=1 if args.once else (args.cycles or None),
        on_cycle=on_cycle, stats=stats,
    )
    print("cycles=%d lots_ecrits=%d erreurs=%d%s" % (
        stats.cycles, stats.enqueued, stats.errors,
        "" if not stats.last_error else " derniere_erreur=%s" % stats.last_error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
