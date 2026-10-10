"""Lecture unique de conntrack, sans aucun envoi réseau.

    python3 -m akwaba_agent.collector [--sudo] [--window 60] [--exclude-source IP] [--file FICHIER]
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from akwaba_agent.aggregator.flows import summarize
from akwaba_agent.collector.conntrack import CollectorError, parse_output, read_conntrack
from akwaba_agent.collector.timeouts import DEFAULT_DIR, load_timeouts


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m akwaba_agent.collector")
    ap.add_argument("--file", help="lire un fichier au lieu d'exécuter conntrack")
    ap.add_argument("--sudo", action="store_true", help="préfixer conntrack par `sudo -n`")
    ap.add_argument("--window", type=int, default=60)
    ap.add_argument("--exclude-source", action="append", default=[])
    ap.add_argument("--timeouts-dir", default=DEFAULT_DIR)
    args = ap.parse_args(argv)

    try:
        if args.file:
            result = parse_output(Path(args.file).read_text(encoding="utf-8"))
        else:
            result = read_conntrack(prefix=("sudo", "-n") if args.sudo else ())
    except (CollectorError, OSError) as exc:
        print("conntrack_ok=false (%s)" % exc)
        return 2

    timeouts = load_timeouts(args.timeouts_dir)
    try:
        summaries = summarize(result.flows, timeouts, args.window, args.exclude_source)
    except ValueError as exc:
        print("argument invalide : %s" % exc)
        return 2

    print("conntrack_ok=true lignes=%d non_reconnues=%d flux_client_vers_exterieur=%d fenetre=%ds" % (
        result.lines, result.unparsed, sum(s.total_flows for s in summaries), args.window))
    for w in result.warnings:
        print("avertissement : %s" % w)
    print("timeouts lus (%d) : %s" % (len(timeouts), ", ".join(
        "%s=%d" % (k.replace("nf_conntrack_", ""), v) for k, v in sorted(timeouts.items()))))
    print("%-40s %6s %8s %9s %9s" % ("source", "total", "recents", "repondus", "age_inc."))
    for s in summaries:
        print("%-40s %6d %8d %9d %9d" % (
            s.src, s.total_flows, s.recent_flows, s.recent_replied_flows, s.unknown_age_flows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
