# Historique des déploiements par lot

Notes de déploiement **telles qu'elles ont été appliquées**, gardées pour mémoire. Pour déployer aujourd'hui,
utiliser la procédure standard ([exploitation](exploitation.md#déployer-une-nouvelle-version)) :
`./deploy/deploy.sh` fait tout ce qui suit (sauvegarde, migrations, recréation de `web`, vérification).
Les scripts propres à un lot (`deploy-11b.sh`, `deploy-11c.sh`, `deploy-11d.sh`, `migrate-network-11a.sh`)
restent dans le dépôt mais ne sont plus à relancer.

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

## Phase 12 (tests de bout en bout)

Aucun changement de production hors `deploy/verify-all.sh` (12d), contrôle global en lecture seule qui reprend
`deploy/verify-11a.sh`. Issue #8 (en-têtes proxy) découverte en 12c, corrigée en 13a (`--no-proxy-headers`).
Issue #5 (rejeu après redémarrage) documentée en 12a : voir [sécurité](securite.md#limitations-connues).

## Phase 13b et 13c

Procédures devenues standard : `deploy/deploy.sh` (13b) et `deploy/agent/update.sh` (13c), décrites dans
[exploitation](exploitation.md).

## Phase 14

14a : sauvegardes chiffrées (`deploy/backup/`) ; 14b : `deploy/restore.sh` et `deploy/rollback.sh` ;
14c : cette documentation. Mise en place : [exploitation](exploitation.md#sauvegardes).
