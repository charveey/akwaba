#!/usr/bin/env bash
# AKWABA — Phase 11c : identités, quarantaine, exemptions, décisions simulées.
#
#     ./deploy/deploy-11c.sh --check   # LECTURE SEULE : exemptions concernées par la nouvelle règle
#     ./deploy/deploy-11c.sh           # sauvegarde, reconstruction du backend, vérifications
#
# Aucune migration. Seul le backend redémarre. Aucune action réseau (dry_run, enforced=false).
#
# Changement de règle (validé) : une exemption ne couvre plus QUE UNKNOWN_IDENTITY.
# Transition : une identité exemptée dont le membre a un abonnement expiré (EXPIRED_CANDIDATE) sera,
# dès qu'elle est ACTIVE et au prochain passage du réconciliateur (s'il est activé), mise en dossier
# PENDING ; sans décision sous 24 h, un WOULD_BLOCK SIMULÉ est écrit. L'exemption n'est ni supprimée
# ni terminée : elle reste active et continue de couvrir l'identité si elle redevient inconnue.
set -euo pipefail

MODE="deploy"
case "${1:-}" in --check) MODE="check" ;; "") ;; *) echo "usage : $0 [--check]" >&2; exit 2 ;; esac

cd "$(dirname "$0")/.."
log()  { printf '\033[1m[11c]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[11c] ERREUR : %s\033[0m\n' "$*" >&2; exit 1; }
env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }

[[ -f .env ]] || fail ".env introuvable"
[[ "$(env_value REVOCATION_MODE)" == "dry_run" ]] || fail "REVOCATION_MODE doit valoir dry_run"
[[ "$(env_value ENFORCER_ENABLED)" == "false" ]] || fail "ENFORCER_ENABLED doit valoir false"
pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
psql_q() { docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -c "$1"; }

rev="$(psql_q "SELECT version_num FROM alembic_version")"
[[ "$rev" == "0011" ]] || fail "révision Alembic attendue 0011 (lot 11b), trouvée $rev"
log "réconciliateur : RECONCILER_ENABLED=$(env_value RECONCILER_ENABLED)"

impact() {
  # Lecture seule : évaluation calculée par le backend lui-même (aucune écriture, aucun commit).
  docker compose exec -T backend python - <<'PY'
from sqlalchemy import select
from app.db.session import get_sessionmaker
from app.domain.clock import SystemClock
from app.models.infra import IdentityExemption
from app.services.identities import evaluate_identities

with get_sessionmaker()() as db:
    active = set(db.execute(select(IdentityExemption.exit_node_identity_id).where(IdentityExemption.ended_at.is_(None))).scalars())
    page = evaluate_identities(db, SystemClock(), verdict=None, active_now=None, q=None, limit=100000, offset=0)
    hit = [e for e in page.items if e.id in active and e.verdict == "EXPIRED_CANDIDATE"]
    print(f"exemptions actives : {len(active)}")
    print(f"exemptions dont l'identité est aujourd'hui EXPIRED_CANDIDATE (concernées) : {len(hit)}")
    for e in hit:
        member = e.member.full_name if e.member else "-"
        print(f"  - {e.login_name} | membre : {member} | active maintenant : {e.active_now}")
    db.rollback()
PY
}

if [[ "$MODE" == "check" ]]; then
  log "Analyse (lecture seule) des exemptions concernées par la nouvelle règle :"
  impact
  log "Aucune modification effectuée."
  exit 0
fi

log "1/4 Analyse avant déploiement (lecture seule)"
impact

log "2/4 Sauvegarde pg_dump"
mkdir -p backups && chmod 700 backups
dump="backups/akwaba-pre-11c-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" -Fc > "$dump"
[[ -s "$dump" ]] || fail "sauvegarde vide"
docker compose exec -T postgres pg_restore --list < "$dump" > /dev/null || fail "sauvegarde illisible"
chmod 600 "$dump"
log "    $dump"

log "3/4 Reconstruction et redémarrage du backend (seul service touché)"
docker compose build backend
docker compose up -d backend
for _ in $(seq 1 40); do
  [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q backend)")" == "healthy" ]] && break
  sleep 3
done
[[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q backend)")" == "healthy" ]] || fail "backend non sain"

log "4/4 Vérifications"
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@" || echo 000; }
[[ "$(code http://127.0.0.1:8080/api/identities/unresolved)" == 401 ]] || fail "/api/identities/unresolved doit exiger une session"
[[ "$(code -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8080/api/auth/login)" == 403 ]] || fail "vérification d'origine inactive"
[[ "$(code -X POST http://127.0.0.1:8080/api/agent/ping)" == 404 ]] || fail "/api/agent/ via Nginx doit renvoyer 404"
bad="$(psql_q "SELECT count(*) FROM enforcement_decisions WHERE mode <> 'dry_run' OR enforced")"
[[ "$bad" == "0" ]] || fail "décision non simulée détectée ($bad)"
log "Lot 11c déployé. Lancer ensuite : ./deploy/verify-11a.sh"
