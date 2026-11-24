# AKWABA VPN MANAGEMENT

Application SaaS interne pour :

- gérer les **clients** (membres), leurs **abonnements** et **paiements** ;
- **observer** l'utilisation d'un Exit Node Tailscale ;
- **identifier** les identités clientes et préparer une future logique de quarantaine.

> ## ⚠️ Principe fondateur de la V1
> **AKWABA V1 observe et décide, mais ne bloque rien.**
> Aucune action réseau n'est exécutée en V1 : pas de nftables, pas de commande de
> blocage, pas de suspension Tailscale. Le système calcule uniquement des décisions
> `WOULD_BLOCK` / `WOULD_UNBLOCK` (mode **dry-run**).

```env
REVOCATION_MODE=dry_run
ENFORCER_ENABLED=false
```

## Accès

| Quoi | Où |
|---|---|
| Interface d'administration | `https://ubuntu.tail14614.ts.net:8443` (tailnet uniquement) |
| Agent de l'Exit Node → backend | `http://100.126.200.88:8000` (direct, HMAC) |
| Application publique voisine (hors AKWABA) | Funnel 443, jamais modifié |

## Documentation

| Document | Pour |
|---|---|
| [Exploitation](docs/exploitation.md) | déployer, vérifier, mettre à jour l'agent, sauvegarder, restaurer, annuler, reprise après sinistre, **fiches incident** |
| [Architecture](docs/architecture.md) | schéma, conteneurs, données, agent, backend, frontend, phases |
| [Sécurité](docs/securite.md) | exposition réseau, HMAC, CSRF, en-têtes proxy, secrets, **limitations connues** |
| [Développement](docs/developpement.md) | environnement local, tests depuis Zed et VS Code, couverture, débogueur |
| [Historique](docs/historique.md) | notes de déploiement de chaque lot (11a → 13a) |
| [Interface](frontend/README.md) | organisation du frontend |

## L'essentiel

```bash
./deploy/deploy.sh --plan && ./deploy/deploy.sh      # déployer main (sauvegarde, migrations, vérification)
./deploy/verify-all.sh                               # état complet du serveur, lecture seule
./deploy/rollback.sh --plan                          # annuler le dernier déploiement
tests/run.sh                                         # toutes les suites de tests
```
