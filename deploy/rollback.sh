#!/usr/bin/env bash
# AKWABA — annulation du dernier déploiement (Phase 14b). SUR LE SERVEUR, par l'utilisateur du dépôt.
#
#     ./deploy/rollback.sh --plan                     # diagnostic seulement : rien n'est modifié
#     ./deploy/rollback.sh                            # annule (saisir ANNULER)
#     ./deploy/rollback.sh --confirmation ANNULER
#
# Revient à l'image conservée par deploy.sh (akwaba-backend:precedent) ET au commit correspondant.
#   - Si le déploiement annulé n'a PAS migré la base : retour du code seul (aucune perte de données).
#   - S'il l'a migrée : la base doit revenir avec le code → restauration (restore.sh) du dump pris par
#     deploy.sh juste avant ce déploiement. Les données écrites DEPUIS ce déploiement sont alors perdues
#     (elles restent dans la base conservée par restore.sh et dans la sauvegarde de sécurité).
# L'image annulée est gardée sous akwaba-backend:annule-<commit>. Pour revenir en avant ensuite :
#   git checkout <branche> && ./deploy/deploy.sh
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
VERIFY_ALL="${VERIFY_ALL:-./deploy/verify-all.sh}"
RESTORE_SH="${RESTORE_SH:-./deploy/restore.sh}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
IMAGE=akwaba-backend:0.1.0
PLAN=0; CONFIRM=""
while (( $# )); do
  case "$1" in
    --plan) PLAN=1; shift ;;
    --confirmation) CONFIRM="${2:-}"; shift 2 ;;
    *) echo "usage : $0 [--plan] [--confirmation ANNULER]" >&2; exit 2 ;;
  esac
done
ok()   { printf '  \033[32mOK\033[0m     %s\n' "$*"; }
info() { printf '  \033[2m%s\033[0m\n' "$*"; }
refuse() { printf '\n\033[31mREFUS : %s\033[0m\nRien n%sa été modifié.\n' "$1" "'"; exit 1; }
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }
label() { docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$1" 2>/dev/null; }

mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
if (( ! PLAN )); then
  exec 9>"$BACKUP_DIR/.deploy.lock"
  flock -n 9 || { echo "un déploiement ou une restauration est déjà en cours" >&2; exit 1; }
fi

printf '\033[1m== Diagnostic\033[0m\n'
PREV="$(label akwaba-backend:precedent)"
[[ -n "$PREV" ]] || refuse "aucune image akwaba-backend:precedent : rien à annuler (elle est créée par deploy.sh)"
CUR="$(label "$IMAGE")"
HEAD_COMMIT="$(git rev-parse --short HEAD 2>/dev/null)"
info "image en service : commit ${CUR:-?} ; image précédente : commit $PREV ; dépôt : ${HEAD_COMMIT:-?}"
[[ -z "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]] || refuse "modifications locales non commitées (git status)"
git cat-file -e "$PREV^{commit}" 2>/dev/null || refuse "commit $PREV introuvable dans le dépôt (git fetch ?)"
[[ "$PREV" != "$CUR" ]] || refuse "l'image précédente est identique à l'image en service ($CUR)"

pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
REV_DB="$(docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -c 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '\r')"
REV_PREV="$(docker run --rm --network none --entrypoint alembic akwaba-backend:precedent heads 2>/dev/null | tr -d '\r' | grep -oE '^[0-9a-z_]+' | head -n1)"
[[ -n "$REV_DB" && -n "$REV_PREV" ]] || refuse "révisions Alembic illisibles (base : ${REV_DB:-?}, image précédente : ${REV_PREV:-?})"
DUMP=""
if [[ "$REV_DB" == "$REV_PREV" ]]; then
  ok "la base ($REV_DB) convient au code précédent : retour du code seul, aucune perte de données"
else
  DUMP="$(find "$BACKUP_DIR" -maxdepth 1 -name "akwaba-pre-deploy-${CUR}-*.dump" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -n1 | cut -d' ' -f2-)"
  [[ -n "$DUMP" ]] || refuse "la base est en $REV_DB mais le code précédent attend $REV_PREV, et aucun dump avant déploiement de $CUR n'est disponible (backups/akwaba-pre-deploy-$CUR-*.dump) — voir docs : restauration depuis une archive"
  info "la base est en $REV_DB, le code précédent attend $REV_PREV : restauration de $DUMP"
  info "ATTENTION : les données écrites depuis ce déploiement seront retirées de la base en service (conservées à part)"
fi

if (( PLAN )); then
  echo
  echo "Mode --plan : rien n'a été modifié. Étapes qui seraient exécutées :"
  echo "  image $IMAGE → akwaba-backend:annule-$CUR ; git checkout --detach $PREV ; akwaba-backend:precedent → $IMAGE"
  if [[ -n "$DUMP" ]]; then echo "  $RESTORE_SH $DUMP (base remise en $REV_PREV)"
  else echo "  redémarrage du backend, web recréé, $VERIFY_ALL"; fi
  exit 0
fi
if [[ -z "$CONFIRM" ]]; then
  read -r -p "Taper ANNULER pour revenir au commit $PREV : " CONFIRM || true
fi
[[ "$CONFIRM" == ANNULER ]] || refuse "confirmation absente"

printf '\n\033[1m== Annulation\033[0m\n'
docker image tag "$IMAGE" "akwaba-backend:annule-$CUR" && ok "image annulée conservée : akwaba-backend:annule-$CUR"
git checkout -q --detach "$PREV" || refuse "git checkout $PREV impossible"
ok "code : commit $PREV (HEAD détachée)"
docker image tag akwaba-backend:precedent "$IMAGE" && ok "image : $IMAGE = commit $PREV"
if [[ -n "$DUMP" ]]; then
  "$RESTORE_SH" "$DUMP" --confirmation RESTAURER
  rc=$?
else
  docker compose up -d --no-build backend && docker compose up -d --no-build --force-recreate web
  rc=$?
  end=$((SECONDS + HEALTH_TIMEOUT))
  for svc in backend web; do
    until [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$svc")" 2>/dev/null)" == healthy ]]; do
      (( SECONDS < end )) || { rc=1; echo "  $svc non sain"; break; }
      sleep 3
    done
  done
  (( rc == 0 )) && { "$VERIFY_ALL"; rc=$?; }
fi
if (( rc == 0 )); then
  printf '\n\033[32mDéploiement annulé : commit %s en service.\033[0m Pour revenir en avant : git checkout <branche> && ./deploy/deploy.sh\n' "$PREV"
else
  printf '\n\033[31mL%sannulation a échoué : voir ci-dessus.\033[0m Image annulée disponible : akwaba-backend:annule-%s\n' "'" "$CUR"
fi
exit "$rc"
