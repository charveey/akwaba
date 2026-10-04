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

## Architecture

```text
Exit Node (tailscaled, conntrack, LocalAPI)
        │  akwaba-agent (Python, systemd, hors Docker, aucun port entrant)
        │  HTTPS + HMAC
        ▼
Reverse proxy (Nginx / existant)
        ▼
web / Nginx (SPA + /api)
        ▼
FastAPI backend (+ APScheduler)
        ▼
PostgreSQL 16
```

Stack : Python, FastAPI, SQLAlchemy (sync), Alembic, PostgreSQL 16, APScheduler,
Pydantic, SPA servie par Nginx, Docker Compose (PostgreSQL, backend, scheduler, web).
L'agent n'est **pas** conteneurisé.

## Séparation des données

- **Infrastructure** : `exit_nodes`, `agent_*`, `exit_node_*`, `tailnet_*`,
  `protected_identities`, `enforcement_decisions`.
- **Clients AKWABA** : `members`, `client_identities`, `subscription_plans`,
  `subscriptions`, `payments`, `quarantine_records`, `reminders`, `expenses`,
  `audit_logs`, `settings`, `job_runs`, …

Pont : `exit_node_identities.client_identity_id`.
Une identité technique Tailscale n'est **jamais** automatiquement un membre.

## Agent (Exit Node)

Lit conntrack, la LocalAPI Tailscale (whois) et `tailscale status --json`.
Produit des données **agrégées** (ni IP de destination, ni port conservés).
Envoie heartbeat (60 s) et observations, avec spool local si le backend est indisponible.
Un conntrack illisible produit un heartbeat dégradé, **jamais** une observation vide.
Un whois en échec = `UNRESOLVED`, **jamais** une identité inconnue.

## Backend

Authentification admin (Argon2id, sessions serveur, CSRF), membres, abonnements
immuables chaînés, paiements, finance, relances WhatsApp (`wa.me`), audit append-only,
ingestion des observations, réconciliation (`evaluate_identity`, fonction pure),
machine de quarantaine (24 h) en dry-run.

## Frontend

SPA sobre et fonctionnelle, servie par Nginx, proxy `/api`.

## Sécurité

- Agent → backend : HMAC-SHA256 par requête, tolérance ±300 s, anti-rejeu (`batch_id`),
  credential individuel, secrets chiffrés en base.
- `/api/agent/` : HMAC + allowlist IP + rate limiting (l'IP seule n'authentifie jamais).
- Admin : cookie HttpOnly / Secure / SameSite=Lax, CSRF, verrouillage après échecs.
- Tailscale REST : lecture seule (`/users`, `/devices`), droits minimaux.
- Aucun bouton web ne permet de passer en `enforce`.

## Mode V1 (dry-run)

```env
REVOCATION_MODE=dry_run
ENFORCER_ENABLED=false
```

## Phases d'implémentation

0. Audit environnement + initialisation
1. Backend minimal (FastAPI, config, logging, DB, Alembic, health)
2. Modèle de données + migrations
3. Auth admin
4. Membres / plans / abonnements / paiements
5. Finance / relances / audit
6. Agent (conntrack → whois → status → agrégation)
7. Protocole HMAC
8. Ingestion backend
9. Réconciliation
10. Quarantaine
11. Dashboard
12. Tests end-to-end
13. Docker / déploiement
14. Documentation / backup / rollback

## Démarrage (Phase 0)

```bash
cp .env.example .env      # puis renseigner les secrets
docker compose config     # valide le fichier
docker compose up -d postgres
docker compose ps
```
