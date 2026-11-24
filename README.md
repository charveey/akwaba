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
- Agent → backend (`:8000`, direct) : HMAC-SHA256 par requête, tolérance d'horloge ±120 s, nonce à usage
  unique, idempotence des lots (`batch_id`), credential individuel, secrets chiffrés en base.
- **Limitation connue (anti-rejeu)** : les nonces déjà vus sont conservés **en mémoire** par le processus
  backend. Après un redémarrage du backend, une requête signée déjà acceptée peut être rejouée une fois tant
  que son horodatage reste dans la fenêtre de validité (±120 s). Impact limité : un lot rejoué est ignoré
  (`batch_id`), un heartbeat rejoué peut réenregistrer un état ancien. Reproduit par `test_known_limitation_replay_after_backend_restart` (résultat `xfail`) ;
  correction suivie dans l'issue #5 (persistance atomique des nonces en base).
- **Limitation connue (en-têtes proxy, défense en profondeur)** : uvicorn active par défaut `--proxy-headers`
  et fait confiance à `X-Forwarded-For` venant de `127.0.0.1`/`::1`, avant le contrôle `TRUSTED_PROXY_IPS` de
  l'application. Sans effet en production (aucun client n'atteint le conteneur par son loopback : Nginx arrive
  de `10.250.11.10`, l'agent de son IP tailnet), mais reproduit par
  `test_known_limitation_uvicorn_trusts_forwarded_headers_from_loopback` (résultat `xfail`).
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
./deploy/migrate-network-11a.sh           # sauvegarde pg_dump, build backend, down (SANS -v), up, vérifications
sudo ./deploy/tailscale/serve-8443.sh     # ajoute 8443 sans toucher à 443 / Funnel (jamais de reset)
./deploy/verify-11a.sh                    # ports, Nginx, agent, Funnel, mode dry-run
```

Retour arrière : `sudo ./deploy/tailscale/serve-8443.sh --remove` (retire uniquement 8443), puis
`git checkout main && docker compose down && docker compose up -d` (le volume `pgdata` est conservé ;
la sauvegarde est dans `backups/akwaba-pre-11a-*.dump`).

## Déploiement du lot 11b (membres, abonnements, paiements, relances)

```bash
git fetch origin && git checkout phase-11b-membres-abonnements
./deploy/deploy-11b.sh --check   # révision Alembic attendue : 0010
./deploy/deploy-11b.sh           # pg_dump, build backend, alembic upgrade head (0011), vérifications
./deploy/verify-11a.sh           # non-régression infrastructure / sécurité
```

Migration `0011` : renomme uniquement les noms affichés des formules (1 mois → Bronze, 3 mois → Silver,
6 mois → Gold, 12 mois → Diamond ; Platinum inchangé). Un nom modifié à la main n'est jamais écrasé.
Retour arrière : `./deploy/deploy-11b.sh --rollback`.

## Déploiement du lot 11c (identités, quarantaine, exemptions, décisions simulées)

```bash
git fetch origin && git checkout phase-11c-identites-quarantaine
./deploy/deploy-11c.sh --check   # LECTURE SEULE : exemptions concernées par la nouvelle règle
./deploy/deploy-11c.sh           # pg_dump, build backend, vérifications (aucune migration)
./deploy/verify-11a.sh
```

**Règle de sécurité modifiée (validée)** : une exemption ne couvre plus que `UNKNOWN_IDENTITY`, jamais
`EXPIRED_CANDIDATE`. Une identité autorisée dont le membre a un abonnement expiré est de nouveau évaluée :
dossier PENDING dès qu'elle est active (au prochain passage du réconciliateur, s'il est activé), puis
`WOULD_BLOCK` **simulé** après 24 h sans décision. Les exemptions existantes ne sont ni supprimées ni
terminées.

Signaux d'ambiguïté (informatifs, sans aucun effet automatique) : `ARCHIVED_MEMBER_LOGIN`,
`CONTACT_EMAIL_MATCH`, `MULTIPLE_EXIT_NODES` (Exit Nodes distincts sur les observations conservées,
soit `RETENTION_OBSERVATIONS_DAYS`, 90 jours par défaut).

Réaffectation atomique d'une identité : `POST /api/members/{id}/identities/{identity_id}/reassign`
(une transaction, audit `identity.reassigned`).

## Déploiement du lot 11d (vue d'ensemble, agent, dépenses, rapports, audit)

```bash
git fetch origin && git checkout phase-11d-dashboard-rapports
./deploy/deploy-11d.sh           # pg_dump, build backend, vérifications (aucune migration)
./deploy/verify-11a.sh
```

Nouveaux points d'accès (session administrateur requise, lecture seule sauf mention) :

| Route | Rôle |
|---|---|
| `GET /api/dashboard` | 9 indicateurs et 5 séries calculés côté serveur |
| `GET /api/exit-nodes`, `GET /api/exit-nodes/{id}` | diagnostic agent ; « sain » = règle du réconciliateur ; jamais de secret HMAC ni de `key_id` |
| `GET /api/reports/operations` | instantané du jour + flux mensuels (UTC) |
| `POST /api/expenses/{id}/correct` | correction atomique : annule l'ancienne (motif « Corrigée »), crée la nouvelle, audit `expense.corrected` |
| `?format=csv` | dépenses et rapports financiers/opérationnels : `;`, UTF-8 avec BOM, mêmes filtres, toutes les lignes, formules neutralisées |

Le graphique « Évolution des membres » montre les nouveaux membres par mois et le cumul des inscriptions
(archivés compris) : ce n'est **pas** un historique du nombre de membres actifs. Le journal d'audit
affiche l'acteur issu de la session (`actor_display`, `actor_kind`) ; les tentatives de connexion
échouées ne sont jamais attribuées au compte visé ; les clés sensibles des détails sont masquées à la
lecture, sans réécriture des événements.

## Tests

```bash
cd backend && python3 -m pytest                              # unitaires (+ intégration si TEST_DATABASE_URL)
cd frontend && node --test tests/*.test.mjs                  # Node >= 22, aucune dépendance
python3 -m pytest tests/nginx tests/deploy                   # Nginx réel + invariants Compose / scripts
TEST_DATABASE_URL=... python3 -m pytest tests/e2e            # Chrome headless → TLS → Nginx → backend → PostgreSQL
TEST_DATABASE_URL=... python3 -m pytest tests/e2e/test_agent_backend_12a.py   # agent réel → HMAC → backend (Phase 12a)
TEST_DATABASE_URL=... python3 -m pytest tests/e2e/test_business_journeys_12b.py # parcours métier complets (Phase 12b)
TEST_DATABASE_URL=... python3 -m pytest tests/e2e/test_security_12c.py          # sécurité transversale, toutes routes et pages (Phase 12c)
```
