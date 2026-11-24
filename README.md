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
- **En-têtes proxy (issue #8, corrigée en Phase 13a)** : uvicorn est lancé avec `--no-proxy-headers` ;
  `X-Forwarded-For` ne réécrit jamais l'IP client, même depuis le loopback. Seule l'application décide
  (`TRUSTED_PROXY_IPS` = Nginx, `X-Real-IP` reconstruit par Nginx) ; la liste d'IP de l'agent lit l'adresse TCP.
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

## Vérification globale du serveur (Phase 12d)

```bash
./deploy/verify-all.sh          # lecture seule ; reprend verify-11a.sh puis les contrôles ci-dessous
```

En plus des contrôles du lot 11a : code servi = code du dépôt (frontend monté dans Nginx, image
backend), Alembic à jour (tête unique, `alembic check` sans écart), déclencheurs d'immuabilité actifs,
aucune décision appliquée, agent et HMAC actifs (lot et requête signée acceptés depuis moins de 5 min),
aucun secret de `.env` dans les réponses publiques ni dans les journaux (24 h), `.env` en `600` et non
suivi par git. Le script n'exécute **aucune** correction : il affiche la commande à lancer.

**Après tout changement de branche sur le serveur** (`git checkout`, `git pull`), Nginx peut rester sur
l'ancien dossier `frontend` (montage orphelin : `/` ne répond plus, 8443 injoignable) :

```bash
docker compose up -d --force-recreate web       # conteneur sans données ; base, backend, 8000 et Tailscale intacts
docker compose build backend && docker compose up -d backend   # si le code backend a changé
./deploy/verify-all.sh
```

## Sauvegardes chiffrées (Phase 14a)

Chaque jour à 03:15 UTC (`akwaba-backup.timer`) : `pg_dump` vérifié + `.env` (indispensable :
`FIELD_ENCRYPTION_KEY` déchiffre les secrets agent) + métadonnées, dans une archive **chiffrée avec age**
pour votre clé **publique** → `backups/auto/akwaba-<date>.tar.age` (600). Le dimanche, le dump est
**réellement restauré** dans un conteneur PostgreSQL temporaire (sans réseau ni volume, détruit ensuite)
et comparé à la production (révision, tables, déclencheurs, lignes). Rotation : 7 jours, 4 semaines,
6 mois ; dumps avant déploiement : les 5 plus récents. Copie sur l'Exit Node par `rsync`/SSH avec une
clé dédiée en **écriture seule** (`rrsync -wo`) : un serveur compromis ne peut ni relire ni effacer les
copies. La clé **privée** age n'est jamais sur le serveur ni sur l'Exit Node.

```bash
# 1. Sur VOTRE poste (pas le serveur) — garder akwaba-backup.key en lieu sûr : sans elle, aucune restauration.
age-keygen -o akwaba-backup.key              # affiche « Public key: age1… »
# 2. Sur le serveur (utilisateur du dépôt, pas root) :
sudo apt install age rsync openssh-client
./deploy/backup/setup-server.sh --recipient age1… --exit-node <nom-tailnet-de-l-exit-node>
# 3. Sur l'Exit Node, avec la ligne « ssh-ed25519 … » affichée à l'étape 2 :
sudo sh deploy/backup/setup-exit-node.sh 'ssh-ed25519 AAAA… akwaba-backup@serveur'
# 4. Sur le serveur : première sauvegarde avec test de restauration, puis contrôle global
./deploy/backup/backup.sh --test-restore
./deploy/verify-all.sh                        # section « Sauvegardes (Phase 14a) »
```

État : `backups/auto/status.json` et `journalctl -u akwaba-backup`. `verify-all.sh` échoue si la dernière
sauvegarde ou copie a plus de 26 h, si le dernier test de restauration réussi a plus de 8 jours, ou si le
minuteur est inactif. Lire une archive (sur votre poste) :
`age -d -i akwaba-backup.key akwaba-<date>.tar.age | tar -xv` → `akwaba.dump`, `env`, `meta.json`.
La restauration en production est l'objet du lot 14b.

## Déploiement (Phase 13b) — procédure standard

```bash
git fetch origin && git checkout <branche>     # ou : git pull
./deploy/deploy.sh --plan                      # vérifications préalables seulement, rien n'est modifié
./deploy/deploy.sh                             # déploie le commit extrait
```

1. **Vérifications préalables** — si l'une échoue, rien n'est modifié : `.env` en `600` et complet,
   `REVOCATION_MODE=dry_run` / `ENFORCER_ENABLED=false`, cookie `Secure`, origines `https`, dépôt sans
   modification non commitée, 2 Go libres, PostgreSQL démarré avec la **même variante Debian** que l'image
   figée (sinon : collations, `REINDEX`), un seul déploiement à la fois.
2. **Sauvegarde** `pg_dump` vérifiée (`pg_restore --list`) : `backups/akwaba-pre-deploy-<commit>-<date>.dump` (600).
3. **Images** : `docker compose pull postgres web` ; l'image en place est conservée sous
   `akwaba-backend:precedent` ; nouvelle image `akwaba-backend:<commit>` (+ `0.1.0`).
4. **Migrations** avec la nouvelle image (`docker compose run --rm --no-deps`), seulement si la tête Alembic
   diffère de la base — toujours après la sauvegarde, avant le redémarrage du backend.
5. **Redémarrage** de postgres et backend, puis `web` **recréé** (jamais de montage orphelin du frontend) ;
   attente de l'état `healthy`.
6. **`./deploy/verify-all.sh`**. Journal complet : `backups/deploy-<date>.log` (600).

En cas d'échec, le script s'arrête et affiche le retour arrière (image `precedent`, commit précédent,
sauvegarde à restaurer si une migration a été appliquée). Il ne lance jamais `docker compose down`, ne
supprime ni volume ni image, ne touche ni à Tailscale (443/8443), ni au port 8000, ni à `.env`.
Les scripts `deploy-11b/11c/11d.sh` sont conservés pour l'historique.
## Mise à jour de l'agent sur l'Exit Node (Phase 13c)

Sur l'Exit Node, avec le code du dépôt (`git pull` sur l'Exit Node, ou `scp -r agent/akwaba_agent pi:/tmp/`) :

```bash
sudo sh deploy/agent/update.sh /chemin/vers/akwaba_agent --plan   # vérifications + ping, rien n'est modifié
sudo sh deploy/agent/update.sh /chemin/vers/akwaba_agent
```

1. Vérifications : agent déjà installé (sinon `install.sh`), nouveau code complet et compilable par
   `/usr/bin/python3` ; code identique → rien à faire.
2. **Ping signé avec le NOUVEAU code** (copie temporaire, utilisateur `akwaba-agent`, `agent.ini` et secret
   réels) : configuration illisible ou refus du backend → **rien n'est modifié**.
3. Bascule : code actuel conservé dans `/opt/akwaba-agent/precedent`, nouveau code installé (root, lecture seule).
4. `systemctl restart akwaba-agent`, puis contrôle : service actif, aucun redémarrage spontané, aucune
   trace d'erreur dans le journal, nouveau ping. **En cas d'échec, retour automatique** au code précédent
   (le code refusé est gardé dans `/opt/akwaba-agent/echec` pour analyse).

Le script ne modifie jamais `agent.ini`, le secret, la règle sudoers, l'unité systemd (il signale seulement
une différence avec celle du dépôt), le spool ni la collecte. Les lots produits pendant le redémarrage restent
dans le spool et partent au retour. Côté serveur, `./deploy/verify-all.sh` confirme la réception (« Agent et HMAC »).

## Déploiement du lot 13a (images figées, durcissement, journaux bornés)

Images figées : `postgres:16.16-trixie`, `python:3.12.16-slim-trixie` (backend), `nginx:1.30.8-alpine`.
Dépendances Python installées depuis `backend/requirements.lock` (versions exactes + empreintes, `--require-hashes`).
Backend : racine en lecture seule, `/tmp` en tmpfs, aucune capacité, `--no-proxy-headers` (issue #8).
Journaux Docker : 10 Mo × 3 par conteneur. Limites : postgres 320 Mo, backend 256 Mo, web 64 Mo.

```bash
# 1. Lecture seule — la variante Debian de PostgreSQL ne doit PAS changer (collations / index texte) :
docker compose exec postgres sh -c 'grep VERSION_CODENAME /etc/os-release; postgres --version'
#    attendu : VERSION_CODENAME=trixie. Si « bookworm » : NE PAS déployer, remplacer -trixie par -bookworm.
# 2. Sauvegarde puis déploiement (volume PostgreSQL conservé ; 443, 8443 et 8000 inchangés) :
mkdir -p backups && docker compose exec -T postgres pg_dump -U "$(grep ^POSTGRES_USER= .env | cut -d= -f2)" \
  -d "$(grep ^POSTGRES_DB= .env | cut -d= -f2)" -Fc > "backups/akwaba-pre-13a-$(date -u +%Y%m%dT%H%M%SZ).dump"
git fetch origin && git checkout phase-13a-images-compose
docker compose pull postgres
GIT_COMMIT="$(git rev-parse --short HEAD)" docker compose build backend
docker compose up -d                  # recrée postgres, backend et web (configuration modifiée)
./deploy/verify-all.sh                # reprend verify-11a.sh
```

## Développement : tests depuis Zed et VS Code

Une seule fois sur le poste, depuis l'éditeur : tâche **« Akwaba : préparer l'environnement (une fois) »**
(VS Code : `Terminal › Run Task` ; Zed : `task: spawn`), ou `tests/setup-dev.sh`. Elle crée `.venv`
(Python 3.12 de préférence), installe les dépendances figées puis les outils de test, et démarre la base de
test jetable si Docker est disponible (`tests/test-db.sh up` : PostgreSQL 16 sur 127.0.0.1:5433, en mémoire,
écrit `.env.test`). Relançable sans risque. Dans VS Code, ensuite : « Developer: Reload Window ».

- **Zed** : ouvrir le dossier du dépôt. Les boutons ▶ dans la marge des tests Python et JavaScript lancent
  le test sous le curseur (ou le fichier) dans le bon répertoire. Palette `task: spawn` → « Akwaba : … »
  (tous les tests, tests rapides, fichier courant, base de test).
- **VS Code** : ouvrir **`akwaba.code-workspace`** (pas le dossier seul). Le panneau « Tests » découvre
  backend, agent et infra/bout en bout, chacun dans son répertoire, avec `.env.test` ; ▶ et 🐞 par test.
  Tâches : `Terminal › Run Task` → « Akwaba : … » ; `Run Test Task` lance tous les tests.
- **Couverture** : tâche « Akwaba : couverture du fichier courant » ou « … complète » (`tests/run.sh
  --couverture …`) → résumé dans le terminal, `backend|agent/couverture/html/index.html`, et `lcov.info`
  par composant (affiché dans la marge par l'extension recommandée Coverage Gutters). VS Code : aussi
  « Run Tests with Coverage » dans le panneau « Tests ». Non mesurée pour `tests/nginx|deploy|e2e`.
- **Débogueur** : Zed (`debugger: start`, F4) et VS Code (Run and Debug, F5) proposent « Akwaba : déboguer
  le fichier de test courant / le test sous le curseur / les tests frontend ». Les points d'arrêt
  fonctionnent dans les tests et dans le code testé (`app/…`, `akwaba_agent/…`) : pytest tourne dans le
  processus débogué (`tests/debug_pytest.py`). VS Code : aussi le bouton 🐞 de chaque test.
- **Ligne de commande** : `tests/run.sh [fichier] [-k test]` choisit le bon lanceur (`--plan` pour voir
  la commande) ; `tests/run.sh` seul lance les quatre suites.

`.env.test` ne contient que l'URL de la base de test et n'est jamais commité ; `.env` (production) n'est
jamais lu par les tests. Sans base de test, les tests PostgreSQL sont simplement ignorés. Les tests de
bout en bout demandent en plus Nginx et Chrome/Chromium ; les tests de sauvegarde réels demandent root.

## Tests

```bash
cd backend && python3 -m pytest                              # unitaires (+ intégration si TEST_DATABASE_URL)
cd frontend && node --test tests/*.test.mjs                  # Node >= 22, aucune dépendance
python3 -m pytest tests/nginx tests/deploy                   # Nginx réel + invariants Compose / scripts
TEST_DATABASE_URL=... python3 -m pytest tests/e2e            # Chrome headless → TLS → Nginx → backend → PostgreSQL
TEST_DATABASE_URL=... python3 -m pytest tests/e2e/test_agent_backend_12a.py   # agent réel → HMAC → backend (Phase 12a)
TEST_DATABASE_URL=... python3 -m pytest tests/e2e/test_business_journeys_12b.py # parcours métier complets (Phase 12b)
TEST_DATABASE_URL=... python3 -m pytest tests/e2e/test_security_12c.py          # sécurité transversale, toutes routes et pages (Phase 12c)
TEST_DATABASE_URL=... tests/run-all.sh          # les quatre suites + récapitulatif (--rapide : sans bout en bout)
```
