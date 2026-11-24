#!/usr/bin/env bash
# AKWABA — Phase 11d : vue d'ensemble, diagnostic agent, dépenses, rapports, journal d'audit.
#
#     ./deploy/deploy-11d.sh           # sauvegarde, reconstruction du backend, vérifications
#
# Aucune migration (révision Alembic attendue : 0011). Seul le backend redémarre ; le frontend est
# servi par Nginx depuis ./frontend (montage en lecture seule), aucun redémarrage de « web ».
# Aucune action réseau (dry_run, enforced=false), aucun appel Tailscale / nftables / iptables.
set -euo pipefail

[[ $# -eq 0 ]] || { echo "usage : $0" >&2; exit 2; }

cd "$(dirname "$0")/.."
log()  { printf '\033[1m[11d]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[11d] ERREUR : %s\033[0m\n' "$*" >&2; exit 1; }
env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }

[[ -f .env ]] || fail ".env introuvable"
[[ "$(env_value REVOCATION_MODE)" == "dry_run" ]] || fail "REVOCATION_MODE doit valoir dry_run"
[[ "$(env_value ENFORCER_ENABLED)" == "false" ]] || fail "ENFORCER_ENABLED doit valoir false"
pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
psql_q() { docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -c "$1"; }

rev="$(psql_q "SELECT version_num FROM alembic_version")"
[[ "$rev" == "0011" ]] || fail "révision Alembic attendue 0011 (aucune migration au lot 11d), trouvée $rev"

log "1/3 Sauvegarde pg_dump"
mkdir -p backups && chmod 700 backups
dump="backups/akwaba-pre-11d-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" -Fc > "$dump"
[[ -s "$dump" ]] || fail "sauvegarde vide"
docker compose exec -T postgres pg_restore --list < "$dump" > /dev/null || fail "sauvegarde illisible"
chmod 600 "$dump"
log "    $dump"

log "2/3 Reconstruction et redémarrage du backend (seul service touché)"
docker compose build backend
docker compose up -d backend
for _ in $(seq 1 40); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q backend)")" == "healthy" ]] && break
  sleep 3
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q backend)")" == "healthy" ]] || fail "backend non sain"

log "3/3 Vérifications"
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@" || echo 000; }
for path in /api/dashboard /api/exit-nodes /api/reports/operations "/api/expenses?format=csv"; do
  [[ "$(code "http://127.0.0.1:8080$path")" == 401 ]] || fail "$path doit exiger une session"
done
[[ "$(code -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8080/api/auth/login)" == 403 ]] || fail "vérification d'origine inactive"
[[ "$(code -X POST http://127.0.0.1:8080/api/agent/ping)" == 404 ]] || fail "/api/agent/ via Nginx doit renvoyer 404"
trig="$(psql_q "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'expenses'::regclass AND NOT tgisinternal")"
[[ "$trig" -ge 1 ]] || fail "déclencheur d'immuabilité des dépenses absent"
bad="$(psql_q "SELECT count(*) FROM enforcement_decisions WHERE mode <> 'dry_run' OR enforced")"
[[ "$bad" == "0" ]] || fail "décision non simulée détectée ($bad)"
log "Lot 11d déployé. Lancer ensuite : ./deploy/verify-11a.sh"
