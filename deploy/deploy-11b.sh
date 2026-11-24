#!/usr/bin/env bash
# HISTORIQUE (Phase 13b) : utiliser désormais ./deploy/deploy.sh, procédure standard pour tout déploiement.
# AKWABA — Phase 11b : membres, abonnements, paiements, relances (backend + migration 0011).
#
#     ./deploy/deploy-11b.sh --check     # contrôles seuls
#     ./deploy/deploy-11b.sh             # sauvegarde, reconstruction du backend, migration 0011, vérifications
#     ./deploy/deploy-11b.sh --rollback  # migration 0011 annulée (noms d'origine des formules)
#
# Ne touche ni à Nginx, ni au réseau Docker, ni à Tailscale, ni à l'agent.
# La migration 0011 ne modifie QUE les noms affichés des formules (codes, durées, prix inchangés).
set -euo pipefail

MODE="deploy"
case "${1:-}" in --check) MODE="check" ;; --rollback) MODE="rollback" ;; "") ;; *) echo "usage : $0 [--check|--rollback]" >&2; exit 2 ;; esac

cd "$(dirname "$0")/.."
log()  { printf '\033[1m[11b]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[11b] ERREUR : %s\033[0m\n' "$*" >&2; exit 1; }
env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }

[[ -f .env ]] || fail ".env introuvable"
[[ "$(env_value REVOCATION_MODE)" == "dry_run" ]] || fail "REVOCATION_MODE doit valoir dry_run"
[[ "$(env_value ENFORCER_ENABLED)" == "false" ]] || fail "ENFORCER_ENABLED doit valoir false"
pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
psql_q() { docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -c "$1"; }

current="$(psql_q "SELECT version_num FROM alembic_version")" || fail "base inaccessible"
log "révision Alembic actuelle : $current"
log "formules : $(psql_q "SELECT string_agg(code || '=' || name, ', ' ORDER BY sort_order) FROM subscription_plans")"

if [[ "$MODE" == "check" ]]; then
  [[ "$current" == "0010" || "$current" == "0011" ]] || fail "révision attendue 0010 (ou 0011 si déjà appliquée), trouvée $current"
  log "Contrôles terminés : aucune modification."
  exit 0
fi

if [[ "$MODE" == "rollback" ]]; then
  [[ "$current" == "0011" ]] || fail "rien à annuler (révision $current)"
  docker compose exec -T backend alembic downgrade 0010
  log "0011 annulée : $(psql_q "SELECT string_agg(code || '=' || name, ', ' ORDER BY sort_order) FROM subscription_plans")"
  log "Revenir ensuite au code du lot 11a : git checkout phase-11a-dashboard-socle && docker compose build backend && docker compose up -d backend"
  exit 0
fi

[[ "$current" == "0010" || "$current" == "0011" ]] || fail "révision attendue 0010, trouvée $current"

log "1/4 Sauvegarde pg_dump"
mkdir -p backups && chmod 700 backups
dump="backups/akwaba-pre-11b-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" -Fc > "$dump"
[[ -s "$dump" ]] || fail "sauvegarde vide"
docker compose exec -T postgres pg_restore --list < "$dump" > /dev/null || fail "sauvegarde illisible"
chmod 600 "$dump"
log "    $dump"

log "2/4 Reconstruction et redémarrage du backend (seul service touché)"
docker compose build backend
docker compose up -d backend
for _ in $(seq 1 40); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q backend)")" == "healthy" ]] && break
  sleep 3
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q backend)")" == "healthy" ]] || fail "backend non sain"

log "3/4 Migration 0011 (noms affichés des formules)"
docker compose exec -T backend alembic upgrade head
[[ "$(psql_q "SELECT version_num FROM alembic_version")" == "0011" ]] || fail "révision 0011 non appliquée"

log "4/4 Vérifications"
plans="$(psql_q "SELECT string_agg(code || '=' || name || '/' || coalesce(duration_months::text, '-') || '/' || price_amount, ', ' ORDER BY sort_order) FROM subscription_plans")"
log "    $plans"
[[ "$plans" == *"M1=Bronze/1/"* && "$plans" == *"M3=Silver/3/"* && "$plans" == *"M6=Gold/6/"* && "$plans" == *"M12=Diamond/12/"* ]] \
  || log "    ATTENTION : un nom a été modifié manuellement auparavant et n'a pas été écrasé (comportement voulu)."
docker compose exec -T backend test -f /srv/app/core/origin.py || fail "image backend sans les protections du lot 11a"
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@" || echo 000; }
[[ "$(code http://127.0.0.1:8080/api/members)" == 401 ]] || fail "/api/members doit exiger une session (401)"
[[ "$(code -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8080/api/auth/login)" == 403 ]] || fail "vérification d'origine inactive"
[[ "$(code -X POST http://127.0.0.1:8080/api/agent/ping)" == 404 ]] || fail "/api/agent/ via Nginx doit renvoyer 404"
log "Lot 11b déployé. Lancer ensuite : ./deploy/verify-11a.sh"
