#!/usr/bin/env bash
# AKWABA — déploiement unique (Phase 13b). Remplace les scripts par lot (deploy-11b/11c/11d.sh,
# conservés pour l'historique). Déploie le commit ACTUELLEMENT extrait :
#
#     git fetch origin && git checkout <branche>      # (ou git pull)
#     ./deploy/deploy.sh             # déploie
#     ./deploy/deploy.sh --plan      # vérifications préalables seulement : RIEN n'est modifié
#
# Étapes : 1. vérifications préalables (aucune modification si l'une échoue)
#          2. sauvegarde pg_dump vérifiée          3. images figées (pull) + image backend étiquetée par commit
#          4. migrations Alembic AVEC LA NOUVELLE IMAGE, seulement s'il y en a, toujours après la sauvegarde
#          5. backend redémarré, puis web recréé (évite le montage orphelin du frontend), contrôles de santé
#          6. ./deploy/verify-all.sh
#
# Ne fait JAMAIS : docker compose down, suppression de volume ou d'image, tailscale serve/funnel,
# modification de .env, du port 8000, de REVOCATION_MODE ou d'ENFORCER_ENABLED.
# shellcheck disable=SC2015  # « test && ok || bad » : ok() renvoie toujours 0.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
PLAN=0
case "${1:-}" in
  "") ;;
  --plan) PLAN=1 ;;
  *) echo "usage : $0 [--plan]" >&2; exit 2 ;;
esac
VERIFY_ALL="${VERIFY_ALL:-./deploy/verify-all.sh}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"     # secondes, par service
MIN_FREE_MB="${MIN_FREE_MB:-2048}"
IMAGE=akwaba-backend:0.1.0                 # = docker-compose.yml (services.backend.image)
REQUIRED=(POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB DATABASE_URL SECRET_KEY FIELD_ENCRYPTION_KEY
          TAILNET_BIND_IP AGENT_ALLOWED_IPS ALLOWED_ORIGINS)

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
LOG="$BACKUP_DIR/deploy-$STAMP.log"
if (( PLAN == 0 )); then ( umask 077; : > "$LOG" ); exec > >(tee -a "$LOG") 2>&1; fi   # journal créé en 600

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m     %s\n' "$*"; }
info() { printf '  \033[2m%s\033[0m\n' "$*"; }
ERRORS=()
bad()  { printf '  \033[31mREFUS\033[0m  %s\n' "$*"; ERRORS+=("$*"); }
die()  { printf '\n\033[31mÉCHEC : %s\033[0m\n' "$*"; rollback_help; exit 1; }
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }

DUMP=""; PREV_COMMIT=""; MIGRATED=0
rollback_help() {
  (( PLAN )) && return
  echo
  echo "Retour arrière (à lancer soi-même, après analyse) :"
  if [[ -n "$PREV_COMMIT" ]]; then
    echo "  image précédente : docker image tag akwaba-backend:precedent $IMAGE && docker compose up -d --no-build backend"
    echo "                     (commit $PREV_COMMIT ; revenir aussi au code : git checkout $PREV_COMMIT)"
  fi
  if (( MIGRATED )); then
    echo "  ATTENTION : des migrations ont été appliquées. Restaurer la base depuis $DUMP"
    echo "              selon la procédure de restauration documentée (Phase 14) — ne pas improviser."
  elif [[ -n "$DUMP" ]]; then
    echo "  base : aucune migration appliquée ; sauvegarde disponible : $DUMP"
  fi
  echo "  journal du déploiement : $LOG"
}

wait_healthy() {
  local svc="$1" end=$((SECONDS + HEALTH_TIMEOUT)) st=""
  while (( SECONDS < end )); do
    st="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$svc")" 2>/dev/null)"
    [[ "$st" == healthy ]] && { ok "$svc : healthy"; return 0; }
    sleep 3
  done
  die "$svc non sain après ${HEALTH_TIMEOUT}s (état : ${st:-absent}) — docker compose logs --tail=50 $svc"
}

# ---------------------------------------------------------------------------------------------- 1
exec 9>"$BACKUP_DIR/.deploy.lock"
flock -n 9 || { echo "un autre déploiement est en cours" >&2; exit 1; }

step "1/6 Vérifications préalables$( (( PLAN )) && echo ' (mode --plan : rien ne sera modifié)')"
command -v docker >/dev/null && docker compose version >/dev/null 2>&1 || bad "docker compose indisponible"
if [[ ! -f .env ]]; then
  bad ".env absent"
else
  perm="$(stat -c '%a' .env)"
  [[ "${perm: -2}" == 00 ]] && ok ".env en $perm" || bad ".env lisible par le groupe ou les autres ($perm) : chmod 600 .env"
  missing=(); for k in "${REQUIRED[@]}"; do [[ -n "$(env_value "$k")" ]] || missing+=("$k"); done
  (( ${#missing[@]} == 0 )) && ok "variables obligatoires présentes (${#REQUIRED[@]})" || bad "variables manquantes dans .env : ${missing[*]}"
  rm_="$(env_value REVOCATION_MODE)"; en_="$(env_value ENFORCER_ENABLED)"
  [[ "${rm_:-dry_run}" == dry_run && "${en_:-false}" == false ]] && ok "REVOCATION_MODE=dry_run, ENFORCER_ENABLED=false" \
    || bad "mode d'observation modifié (REVOCATION_MODE=${rm_:-?}, ENFORCER_ENABLED=${en_:-?})"
  [[ "$(env_value COOKIE_SECURE)" != false ]] && ok "cookie Secure" || bad "COOKIE_SECURE=false"
  bad_origins="$(tr ',' '\n' <<<"$(env_value ALLOWED_ORIGINS)" | sed 's/^ *//; s/ *$//' | grep -v '^$' | grep -v '^https://' || true)"
  [[ -z "$bad_origins" ]] && ok "ALLOWED_ORIGINS uniquement en https" || bad "origines non https : $(tr '\n' ' ' <<<"$bad_origins")"
fi
git rev-parse --git-dir >/dev/null 2>&1 || bad "pas un dépôt git"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null)"
[[ -z "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]] && ok "dépôt propre : $(git rev-parse --abbrev-ref HEAD 2>/dev/null) @ $COMMIT" \
  || bad "modifications locales non commitées (git status) : déployer uniquement un commit"
free_mb="$(df -Pm . | awk 'NR==2 {print $4}')"
(( free_mb >= MIN_FREE_MB )) && ok "espace disque libre : ${free_mb} Mo" || bad "espace disque insuffisant : ${free_mb} Mo (minimum $MIN_FREE_MB)"

pg_image="$(docker compose config --images 2>/dev/null | grep -E '^postgres:' | head -n1)"
want_variant="${pg_image##*-}"
pg_running="$(docker compose ps -q postgres 2>/dev/null)"
if [[ -z "$pg_image" ]]; then
  bad "image PostgreSQL introuvable dans docker-compose.yml"
elif [[ -z "$pg_running" ]]; then
  bad "PostgreSQL ne tourne pas : sauvegarde impossible (démarrer d'abord : docker compose up -d postgres)"
else
  have_variant="$(docker compose exec -T postgres sh -c '. /etc/os-release; echo "$VERSION_CODENAME"' 2>/dev/null | tr -d '\r')"
  [[ "$have_variant" == "$want_variant" ]] && ok "PostgreSQL : variante Debian inchangée ($have_variant → $pg_image)" \
    || bad "PostgreSQL : variante Debian ${have_variant:-?} → $want_variant (collations : REINDEX requis, déploiement refusé)"
fi
pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
psql_q() { docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null | tr -d '\r'; }
REV_BEFORE="$(psql_q "SELECT version_num FROM alembic_version")"
info "révision Alembic actuelle : ${REV_BEFORE:-?}"
PREV_COMMIT="$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE" 2>/dev/null)"
info "image backend actuelle : commit ${PREV_COMMIT:-inconnu} → à déployer : $COMMIT"

if (( ${#ERRORS[@]} )); then
  printf '\n\033[31m%d vérification(s) en échec : rien n%sa été modifié.\033[0m\n' "${#ERRORS[@]}" "'"
  exit 1
fi
if (( PLAN )); then
  echo
  echo "Mode --plan : vérifications préalables réussies. Étapes qui seraient exécutées :"
  echo "  2. pg_dump → $BACKUP_DIR/akwaba-pre-deploy-$COMMIT-<horodatage>.dump (vérifié)"
  echo "  3. docker compose pull postgres web ; GIT_COMMIT=$COMMIT docker compose build backend"
  echo "  4. migrations Alembic avec la nouvelle image (seulement si la tête diffère de ${REV_BEFORE:-?})"
  echo "  5. docker compose up -d --no-build postgres backend ; docker compose up -d --no-build --force-recreate web"
  echo "  6. $VERIFY_ALL"
  exit 0
fi

# ---------------------------------------------------------------------------------------------- 2
step "2/6 Sauvegarde"
DUMP="$BACKUP_DIR/akwaba-pre-deploy-$COMMIT-$STAMP.dump"
( umask 077; docker compose exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" -Fc > "$DUMP" ) || die "pg_dump a échoué"
[[ -s "$DUMP" ]] || die "sauvegarde vide"
docker compose exec -T postgres pg_restore --list < "$DUMP" > /dev/null || die "sauvegarde illisible (pg_restore --list)"
ok "$DUMP ($(du -h "$DUMP" | cut -f1))"

# ---------------------------------------------------------------------------------------------- 3
step "3/6 Images"
docker compose pull postgres web || die "téléchargement des images figées impossible"
if [[ -n "$PREV_COMMIT" ]]; then
  docker image tag "$IMAGE" akwaba-backend:precedent && ok "image actuelle conservée : akwaba-backend:precedent (commit $PREV_COMMIT)"
fi
GIT_COMMIT="$COMMIT" docker compose build backend || die "construction de l'image backend impossible (rien n'a été redémarré)"
ok "image construite : $IMAGE et akwaba-backend:$COMMIT"

# ---------------------------------------------------------------------------------------------- 4
step "4/6 Migrations"
HEAD_REV="$(docker compose run --rm --no-deps -T backend alembic heads 2>/dev/null | tr -d '\r' | grep -oE '^[0-9a-z_]+' )"
[[ "$(wc -l <<<"$HEAD_REV" | tr -d ' ')" == 1 && -n "$HEAD_REV" ]] || die "têtes Alembic multiples ou illisibles : $(tr '\n' ' ' <<<"$HEAD_REV")"
if [[ "$HEAD_REV" == "$REV_BEFORE" ]]; then
  ok "aucune migration en attente (révision $HEAD_REV)"
else
  info "migration ${REV_BEFORE:-?} → $HEAD_REV avec la nouvelle image (sauvegarde : $DUMP)"
  MIGRATED=1
  docker compose run --rm --no-deps -T backend alembic upgrade head || die "migration en échec"
  [[ "$(psql_q "SELECT version_num FROM alembic_version")" == "$HEAD_REV" ]] || die "révision $HEAD_REV non atteinte"
  ok "base migrée en $HEAD_REV"
fi

# ---------------------------------------------------------------------------------------------- 5
step "5/6 Redémarrage"
docker compose up -d --no-build postgres backend || die "démarrage de postgres/backend impossible"
wait_healthy postgres
wait_healthy backend
docker compose up -d --no-build --force-recreate web || die "recréation de web impossible"
wait_healthy web

# ---------------------------------------------------------------------------------------------- 6
step "6/6 Vérification globale"
if "$VERIFY_ALL"; then
  printf '\n\033[32mDéploiement du commit %s terminé.\033[0m Sauvegarde : %s — journal : %s\n' "$COMMIT" "$DUMP" "$LOG"
else
  die "la vérification globale signale des échecs (voir ci-dessus)"
fi
