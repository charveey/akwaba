"""Affiche un résumé agrégé de tailscale status. Aucun login, aucun hostname.

    python3 -m akwaba_agent.peers [--sudo]
"""
import argparse

from akwaba_agent.peers.status import StatusReader


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m akwaba_agent.peers")
    ap.add_argument("--sudo", action="store_true")
    args = ap.parse_args(argv)
    r = StatusReader(prefix=("sudo", "-n") if args.sudo else ()).read()
    if not r.ok:
        print("status_ok=false (%s)" % r.error)
        return 2
    n = len(r.peers)
    print("status_ok=true pairs=%d ips_indexees=%d ips_en_double=%d" % (n, len(r.by_ip), r.duplicate_ips))
    print("partages=%d etiquetes=%d expires=%d en_ligne=%d actifs=%d exit_nodes=%d" % (
        sum(p.is_sharee for p in r.peers), sum(p.tagged for p in r.peers), sum(p.expired for p in r.peers),
        sum(p.online for p in r.peers), sum(p.active for p in r.peers), sum(p.exit_node for p in r.peers)))
    print("avec_handshake=%d sans_handshake=%d" % (
        sum(p.last_handshake is not None for p in r.peers), sum(p.last_handshake is None for p in r.peers)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
