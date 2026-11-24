#!/usr/bin/env python3
"""AKWABA — rotation des sauvegardes chiffrées (Phase 14a), sur le serveur ET sur l'Exit Node.

    python3 retention.py RÉPERTOIRE                 # affiche ce qui serait gardé / supprimé
    python3 retention.py RÉPERTOIRE --apply         # supprime réellement

Garde (« grand-père / père / fils ») : la plus récente de chacun des 7 derniers jours ayant une sauvegarde,
de chacune des 4 dernières semaines ISO et de chacun des 6 derniers mois. La sauvegarde la plus récente
n'est jamais supprimée. Seuls les fichiers `akwaba-AAAAMMJJTHHMMSSZ.tar.age` (et leur `.sha256`) sont
concernés ; tout autre fichier est ignoré. Bibliothèque standard uniquement (Python 3.9+).
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple

PATTERN = re.compile(r"^akwaba-(\d{8}T\d{6}Z)\.tar\.age$")


def archives(directory: str) -> List[Tuple[datetime, str]]:
    out = []
    for name in os.listdir(directory):
        m = PATTERN.match(name)
        if m:
            out.append((datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), name))
    return sorted(out, reverse=True)                     # plus récente d'abord


def select(items: List[Tuple[datetime, str]], daily: int = 7, weekly: int = 4, monthly: int = 6) -> Set[str]:
    """Noms à garder. `items` : (date, nom), dans n'importe quel ordre."""
    items = sorted(items, reverse=True)
    keep: Set[str] = set()
    for count, key in ((daily, lambda d: d.date()), (weekly, lambda d: tuple(d.isocalendar()[:2])),
                       (monthly, lambda d: (d.year, d.month))):
        seen: Dict[object, str] = {}
        for when, name in items:
            k = key(when)
            if k not in seen:
                if len(seen) == count:
                    break
                seen[k] = name                           # la plus récente de la période
        keep.update(seen.values())
    if items:
        keep.add(items[0][1])
    return keep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="retention.py")
    ap.add_argument("directory")
    ap.add_argument("--daily", type=int, default=7)
    ap.add_argument("--weekly", type=int, default=4)
    ap.add_argument("--monthly", type=int, default=6)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if min(args.daily, args.weekly, args.monthly) < 1:
        print("chaque durée de rétention doit être >= 1", file=sys.stderr)
        return 2
    items = archives(args.directory)
    keep = select(items, args.daily, args.weekly, args.monthly)
    removed = 0
    for _, name in items:
        if name in keep:
            continue
        removed += 1
        for path in (name, name + ".sha256"):
            full = os.path.join(args.directory, path)
            if args.apply and os.path.exists(full):
                os.remove(full)
        print(("supprimée " if args.apply else "à supprimer ") + name)
    print("rotation : %d gardée(s), %d %s" % (len(keep), removed, "supprimée(s)" if args.apply else "à supprimer"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
