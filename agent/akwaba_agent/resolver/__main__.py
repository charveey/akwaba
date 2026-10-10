"""Résout une ou plusieurs IP via la LocalAPI. Affiche le statut, pas le login.

    python3 -m akwaba_agent.resolver 100.91.19.91 [IP ...]
"""
import sys

from akwaba_agent.resolver.whois import WhoisResolver


def main(argv=None) -> int:
    ips = (argv if argv is not None else sys.argv[1:])
    if not ips:
        print("usage : python3 -m akwaba_agent.resolver IP [IP ...]")
        return 2
    resolver = WhoisResolver()
    for ip in ips:
        r = resolver.resolve(ip)
        if r.identity:
            masked = r.identity.login_name.split("@")[0][:2] + "***@" + r.identity.login_name.split("@")[-1]
            print("%-16s %s sharee=%s login=%s hostname=%s" % (
                ip, r.status, r.identity.is_sharee, masked, r.identity.hostname))
        else:
            print("%-16s %s erreur_technique=%s" % (ip, r.status, r.technical_error))
    print("whois_ok=%s" % resolver.whois_ok)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
