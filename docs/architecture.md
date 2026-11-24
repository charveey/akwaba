# Architecture

> **Principe fondateur de la V1 : AKWABA observe et décide, mais ne bloque rien.**
> Aucune action réseau n'est exécutée : ni nftables, ni commande de blocage, ni suspension Tailscale.
> Le système calcule uniquement des décisions `WOULD_BLOCK` / `WOULD_UNBLOCK` (mode **dry-run**,
> `REVOCATION_MODE=dry_run`, `ENFORCER_ENABLED=false`). Aucun bouton de l'interface ne permet d'en sortir.

## Vue d'ensemble

```text
Exit Node "stray" (tailscaled, conntrack, LocalAPI)
        │  akwaba-agent (Python 3.9+, systemd, hors Docker, aucun port entrant)
        │  HTTP sur le tailnet (chiffré WireGuard) + HMAC
        ▼
Serveur Oracle — 100.126.200.88:8000 ────────────────────────────────┐  (agent : connexion directe)
                                                              │
Navigateur admin (tailnet)                                     │
        │  https://ubuntu.tail14614.ts.net:8443                │
        ▼                                                      │
tailscale serve :8443 ──► 127.0.0.1:8080                       │
        ▼                                                      │
web / Nginx (10.250.11.10)  — /  : interface (fichiers statiques)
                            — /api/agent/ : 404                │
                            — /api/ : proxy ──────────┐        │
        ┌─────────────── réseau Docker internal 10.250.11.0/24 ▼
        │                  FastAPI backend :8000 ◄─────────────┘
        │                         ▼
        └────────────────► PostgreSQL 16 (aucun port publié)

Funnel public existant : https://ubuntu.tail14614.ts.net:443 → 127.0.0.1:3000 (hors AKWABA, jamais modifié)
```

| Point d'entrée | Liaison hôte | Usage |
|---|---|---|
| Backend | `127.0.0.1:8000` et `${TAILNET_BIND_IP}:8000` | agent de l'Exit Node (HMAC + liste d'IP), diagnostics locaux |
| Nginx (`web`) | `127.0.0.1:8080` uniquement | administration, exposée par `tailscale serve --https=8443` |
| PostgreSQL | aucune | réseau Docker interne seulement |

Aucun port n'est exposé sur Internet : Nginx n'est joignable que par la boucle locale du serveur (donc par
`tailscaled`), et 8443 n'est servi qu'aux appareils du tailnet (pas de Funnel).

## Conteneurs (Docker Compose)

| Service | Image (figée) | Limites | Particularités |
|---|---|---|---|
| `postgres` | `postgres:16.16-trixie` | 320 Mo, 128 processus | volume `pgdata` ; variante Debian **jamais changée** sans `REINDEX` (collations) |
| `backend` | `akwaba-backend:0.1.0` + `akwaba-backend:<commit>` (`python:3.12.16-slim-trixie`) | 256 Mo, 128 processus | racine en lecture seule, `/tmp` tmpfs, aucune capacité, `--no-proxy-headers`, un seul worker |
| `web` | `nginx:1.30.8-alpine` | 64 Mo, 64 processus | utilisateur 101, lecture seule, sert `./frontend` monté en lecture seule |

Journaux Docker bornés (10 Mo × 3 par conteneur). Dépendances Python figées avec empreintes
(`backend/requirements.lock`, installé avec `--require-hashes`). L'agent n'est **pas** conteneurisé.

Stack : Python, FastAPI, SQLAlchemy (synchrone), Alembic, PostgreSQL 16, Pydantic ; interface en JavaScript
natif (modules ES, sans framework, sans dépendance, sans build) servie par Nginx.

## Données

- **Infrastructure** : `exit_nodes`, `agent_*`, `exit_node_*`, `tailnet_*`, `protected_identities`,
  `enforcement_decisions`.
- **Clients AKWABA** : `members`, `client_identities`, `subscription_plans`, `subscriptions`, `payments`,
  `quarantine_records`, `reminders`, `expenses`, `audit_logs`, `settings`, `job_runs`, …

Pont : `exit_node_identities.client_identity_id`. Une identité technique Tailscale n'est **jamais**
automatiquement un membre.

Historique protégé par des déclencheurs PostgreSQL (aucune modification ni suppression) : `audit_logs`,
`enforcement_decisions`, `payments`, `expenses`, `subscriptions` (7 déclencheurs, contrôlés par
`deploy/verify-all.sh` et par chaque test de restauration).

## Agent (Exit Node)

Lit conntrack, la LocalAPI Tailscale (whois) et `tailscale status --json`. Produit des données **agrégées**
(ni IP de destination, ni port conservés). Envoie un heartbeat et ses observations toutes les 60 s, avec une
file locale (spool SQLite) si le backend est indisponible : rien n'est perdu pendant une coupure ou un
redémarrage. Un conntrack illisible produit un heartbeat dégradé, **jamais** une observation vide ; un whois
en échec donne `UNRESOLVED`, **jamais** une identité inconnue.

## Backend

Authentification admin (Argon2id, sessions serveur, CSRF), membres, abonnements immuables chaînés, paiements,
dépenses, rapports, relances WhatsApp (`wa.me`), audit append-only, ingestion des observations, réconciliation
(`evaluate_identity`, fonction pure) et machine de quarantaine (délai de 24 h) en dry-run. Tâches périodiques
(réconciliation, purge) intégrées au backend, désactivées par défaut (`RECONCILER_ENABLED`, `PURGE_ENABLED`).

## Frontend

Interface d'administration en français (voir [`frontend/README.md`](../frontend/README.md)) : routage par
fragment (`#/membres`), thème clair et sombre, responsive. Toutes les valeurs affichées sont calculées par le
serveur ; aucune règle métier n'est dupliquée dans le navigateur.

## Phases

0 audit · 1 backend minimal · 2 modèle de données · 3 authentification · 4 membres / abonnements / paiements ·
5 finance / relances / audit · 6 agent · 7 protocole HMAC · 8 ingestion · 9 réconciliation · 10 quarantaine ·
11 dashboard · 12 tests de bout en bout · 13 Docker / déploiement · 14 documentation / sauvegarde / retour arrière.
Notes de déploiement de chaque lot : [historique](historique.md).
