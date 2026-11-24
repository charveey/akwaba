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
Exit Node "stray" (tailscaled, conntrack, LocalAPI)
        │  akwaba-agent (Python 3.9, systemd, hors Docker, aucun port entrant)
        │  HTTP sur le tailnet (chiffré WireGuard) + HMAC
        ▼
Serveur Oracle — 100.126.200.88:8000 ─────────────────────────┐  (agent : connexion directe)
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

Funnel public existant : https://ubuntu.tail14614.ts.net:443 → 127.0.0.1:3000 (hors AKWABA, inchangé)
```

| Point d'entrée | Liaison hôte | Usage |
|---|---|---|
| Backend | `127.0.0.1:8000` et `${TAILNET_BIND_IP}:8000` | Agent de l'Exit Node (HMAC + liste d'IP), diagnostics locaux |
| Nginx (`web`) | `127.0.0.1:8080` uniquement | Administration, exposée par `tailscale serve --https=8443` |
| PostgreSQL | aucune | Réseau Docker interne seulement |

Aucun port n'est exposé sur Internet. Nginx n'est joignable que par la boucle locale du serveur
(donc par `tailscaled`) ; le port 8443 n'est servi qu'aux appareils du tailnet (pas de Funnel).

Stack : Python, FastAPI, SQLAlchemy (sync), Alembic, PostgreSQL 16, APScheduler,
Pydantic, interface en JavaScript (modules ES natifs, sans framework, sans build) servie par
Nginx 1.30, Docker Compose (PostgreSQL, backend, web ; scheduler à venir).
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

Interface d'administration en français, en JavaScript natif (modules ES), **sans framework,
sans dépendance et sans étape de build** : voir [`frontend/README.md`](frontend/README.md).
Routage par fragment (`#/membres`), thème clair par défaut et thème sombre, responsive.

## Sécurité

- AKWABA n'ajoute aucun port public : accès uniquement via le tailnet.
  (L'application Next.js voisine, elle, est exposée via Tailscale Funnel sur 443 : hors périmètre AKWABA, jamais modifiée.)
- Agent → backend (`:8000`, direct) : HMAC-SHA256 par requête, tolérance ±300 s, anti-rejeu (`batch_id`),
  credential individuel, secrets chiffrés en base.
- `/api/agent/` : HMAC + allowlist de l'IP Tailscale de l'Exit Node + limite de taille
  (l'IP seule n'authentifie jamais). **Jamais servi par Nginx** (404).
- Admin : HTTPS via `tailscale serve --https=8443`, cookie `HttpOnly` / `Secure` / `SameSite=Strict`
  (chemin `/api`), sessions serveur (12 h d'inactivité, 7 jours max), verrouillage après échecs.
- CSRF, deux protections cumulées sur toute requête mutative administrative :
  1. jeton de session `X-CSRF-Token` (`require_csrf`) ;
  2. **origine stricte** (`ALLOWED_ORIGINS`, obligatoire) : `Origin` — ou à défaut `Referer` — doit être
     l'origine HTTPS du dashboard ; `Sec-Fetch-Site`, s'il est présent, doit valoir `same-origin` ;
     **sans `Origin` ni `Referer`, la requête est refusée (403)**, connexion comprise.
     `/api/agent/*` est exclu (l'agent n'est pas un navigateur ; HMAC inchangé).
- En-têtes proxy : Nginx **reconstruit** `X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Proto` (jamais repris
  du navigateur ; seule la dernière entrée ajoutée par `tailscaled` est retenue). Le backend ne lit
  `X-Real-IP` que si la connexion vient de l'IP fixe de Nginx (`TRUSTED_PROXY_IPS=10.250.11.10`).
  Le filtrage IP de l'agent lit toujours l'adresse TCP brute.
- Navigateur : CSP stricte (`script-src 'self'`, aucun inline, aucun `eval`, aucune ressource externe,
  `require-trusted-types-for 'script'`), `X-Frame-Options: DENY`, `nosniff`, `Permissions-Policy` restrictive.
  Pas de HSTS (il s'appliquerait aussi à l'hôte du Funnel).
- Nginx sans privilèges (utilisateur 101, `cap_drop: ALL`, racine en lecture seule).
- Tailscale REST : lecture seule (`/users`, `/devices`), droits minimaux.
- Aucun bouton web ne permet de passer en `enforce`.

> Limite connue : les cookies ne tiennent pas compte du port. Le cookie de session (`path=/api`) est
> donc aussi envoyé par le navigateur à `https://ubuntu.tail14614.ts.net/api/*` (application du Funnel).
> `deploy/verify-11a.sh` inventorie les routes `/api` de cette application (`NEXT_APP_DIR=...`).

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

## Déploiement de la Phase 11a (dashboard : socle)

Sur le serveur, depuis la racine du dépôt :

```bash
git fetch origin && git checkout phase-11a-dashboard-socle
# 1. Ajouter dans .env l'origine du dashboard (obligatoire, sinon le backend ne démarre pas) :
#      ALLOWED_ORIGINS=https://ubuntu.tail14614.ts.net:8443
./deploy/migrate-network-11a.sh --check   # contrôles seuls (chevauchement réseau, volume, .env)
./deploy/migrate-network-11a.sh           # sauvegarde pg_dump, down (SANS -v), up, vérifications
sudo ./deploy/tailscale/serve-8443.sh     # ajoute 8443 sans toucher à 443 / Funnel (jamais de reset)
./deploy/verify-11a.sh                    # ports, Nginx, agent, Funnel, mode dry-run
```

Retour arrière : `sudo ./deploy/tailscale/serve-8443.sh --remove` (retire uniquement 8443), puis
`git checkout main && docker compose down && docker compose up -d` (le volume `pgdata` est conservé ;
la sauvegarde est dans `backups/akwaba-pre-11a-*.dump`).

## Tests

```bash
cd backend && python3 -m pytest                              # unitaires (+ intégration si TEST_DATABASE_URL)
cd frontend && node --test tests/*.test.mjs                  # Node >= 22, aucune dépendance
python3 -m pytest tests/nginx tests/deploy                   # Nginx réel + invariants Compose / scripts
TEST_DATABASE_URL=... python3 -m pytest tests/e2e            # Chrome headless → TLS → Nginx → backend → PostgreSQL
```
