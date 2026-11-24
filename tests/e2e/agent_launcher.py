"""Lance le VRAI agent (`python -m akwaba_agent`) pour les tests de bout en bout du lot 12a.

    python agent_launcher.py <socket tailscaled factice> <arguments de l'agent...>

Seule différence avec la production : le chemin du socket de la LocalAPI tailscaled (codé en dur
dans l'agent, /var/run/tailscale/tailscaled.sock) pointe vers un serveur factice du test.
Aucun fichier de l'agent n'est modifié ; `conntrack` et `tailscale` sont des exécutables factices
placés en tête du PATH par le test.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "agent"))

from akwaba_agent.resolver import whois  # noqa: E402

whois.fetch_whois.__defaults__ = (sys.argv[1], 3.0)   # (socket_path, timeout)

from akwaba_agent.__main__ import main  # noqa: E402

raise SystemExit(main(sys.argv[2:]))
