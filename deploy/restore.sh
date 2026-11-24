#!/usr/bin/env bash
# AKWABA — restauration de la base (Phase 14b). SUR LE SERVEUR, par l'utilisateur du dépôt.
#
#     ./deploy/restore.sh backups/akwaba-pre-deploy-<commit>-<date>.dump          # dump en clair (serveur)
#     ./deploy/restore.sh akwaba-<date>.tar.age --identity /chemin/akwaba-backup.key
#     ... --restaurer-env        # remplacer aussi .env par celui de l'archive (ancien gardé à côté)
#     ... --confirmation RESTAURER   # sans question (sinon : saisir RESTAURER)
#
# La clé PRIVÉE age n'est pas conservée par ce script : l'apporter le temps de la restauration, puis l'effacer.
#
# 1. Vérifications (RIEN n'est modifié si l'une échoue) : archive déchiffrable, dump lisible, révision
#    Alembic du dump connue du code en place (une sauvegarde PLUS RÉCENTE que le code est refusée :
#    extraire d'abord le commit indiqué), .env cohérent, PostgreSQL démarré.
# 2. Confirmation explicite, puis sauvegarde de l'état actuel (backups/akwaba-pre-restore-<date>.dump).
# 3. Arrêt du backend et de web (PostgreSQL reste démarré ; l'agent garde ses lots dans son spool).
# 4. Restauration dans une base TEMPORAIRE, vérifiée (révision, 7 déclencheurs), puis échange par
#    renommage : la base actuelle est CONSERVÉE sous akwaba_avant_restauration_<date>, jamais supprimée.
#    En cas d'échec avant l'échange, la base de production n'a pas été touchée.
# 5. Migrations si la sauvegarde est plus ancienne que le code ; redémarrage ; ./deploy/verify-all.sh.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
VERIFY_ALL="${VERIFY_ALL:-./deploy/verify-all.sh}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
TRIGGERS="'audit_logs_no_truncate','audit_logs_no_update_delete','enforcement_decisions_no_truncate','enforcement_decisions_no_update_delete','expenses_guard','payments_guard','subscriptions_guard'"

SRC=""; IDENTITY=""; RESTORE_ENV=0; CONFIRM=""
while (( $# )); do
  case "$1" in
    --identity) IDENTITY="${2:-}"; shift 2 ;;
    --restaurer-env) RESTORE_ENV=1; shift ;;
    --confirmation) CONFIRM="${2:-}"; shift 2 ;;
    -*) echo "option inconnue : $1" >&2; exit 2 ;;
    *) SRC="$1"; shift ;;
  esac
done
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m     %s\n' "$*"; }
info() { printf '  \033[2m%s\033[0m\n' "$*"; }
refuse() { printf '\n\033[31mREFUS : %s\033[0m\nRien n%sa été modifié.\n' "$1" "'"; exit 1; }
env_file_value() { grep -E "^$2=" "$1" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }
env_value() { env_file_value .env "$1"; }

[[ -n "$SRC" && -s "$SRC" ]] || { echo "usage : $0 FICHIER.dump|FICHIER.tar.age [--identity CLÉ] [--restaurer-env] [--confirmation RESTAURER]" >&2; exit 2; }
mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.deploy.lock"                     # même verrou que deploy.sh : jamais en parallèle
flock -n 9 || { echo "un déploiement ou une restauration est déjà en cours" >&2; exit 1; }
WORK="$(mktemp -d "$BACKUP_DIR/.restore-XXXXXX")"; chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT

pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
[[ "$pg_db" =~ ^[a-z_][a-z0-9_]*$ && "$pg_user" =~ ^[a-z_][a-z0-9_]*$ ]] || refuse ".env : POSTGRES_USER / POSTGRES_DB absents ou invalides"
TMP_DB="${pg_db}_restauration_$STAMP"; TMP_DB="${TMP_DB,,}"
OLD_DB="${pg_db}_avant_restauration_$STAMP"; OLD_DB="${OLD_DB,,}"
sql() {   # sql BASE REQUÊTE
  docker compose exec -T postgres psql -U "$pg_user" -d "$1" -At -v ON_ERROR_STOP=1 -c "$2" 2>&1 | tr -d '\r'
}
alembic() { docker compose run --rm --no-deps -T backend alembic "$@" 2>&1 | tr -d '\r'; }

# ---------------------------------------------------------------------------------------------- 1
step "1/5 Vérifications"
[[ -n "$(docker compose ps -q postgres 2>/dev/null)" ]] || refuse "PostgreSQL ne tourne pas (docker compose up -d postgres)"
DUMP="$WORK/akwaba.dump"; ARCHIVE_ENV=""
if [[ "$SRC" == *.tar.age ]]; then
  [[ -n "$IDENTITY" && -r "$IDENTITY" ]] || refuse "archive chiffrée : --identity CHEMIN/akwaba-backup.key requis"
  ( umask 077; age -d -i "$IDENTITY" "$SRC" | tar -C "$WORK" -xf - akwaba.dump env meta.json ) 2>/dev/null \
    || refuse "archive illisible : mauvaise clé, fichier corrompu ou incomplet"
  ARCHIVE_ENV="$WORK/env"
  [[ "$(sha256sum "$DUMP" | cut -d' ' -f1)" == "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["dump_sha256"])' "$WORK/meta.json" 2>/dev/null)" ]] \
    || refuse "empreinte du dump différente de celle des métadonnées (archive altérée)"
  ok "archive déchiffrée et intègre (créée le $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["created_at"])' "$WORK/meta.json"))"
else
  ( umask 077; cp "$SRC" "$DUMP" )
fi
docker compose exec -T postgres pg_restore --list < "$DUMP" > /dev/null 2>&1 || refuse "dump illisible (pg_restore --list)"
REV_DUMP="$(docker compose exec -T postgres pg_restore --data-only --table=alembic_version -f - < "$DUMP" 2>/dev/null \
  | tr -d '\r' | awk '/^COPY public.alembic_version/ {getline; print; exit}')"
[[ "$REV_DUMP" =~ ^[0-9a-z_]+$ ]] || refuse "révision Alembic introuvable dans le dump"
HEAD="$(alembic heads | grep -oE '^[0-9a-z_]+' )"
[[ "$(wc -l <<<"$HEAD" | tr -d ' ')" == 1 && -n "$HEAD" ]] || refuse "têtes Alembic du code illisibles"
if [[ "$REV_DUMP" != "$HEAD" ]]; then
  alembic history -r "base:$HEAD" | grep -qE "(^|-> )$REV_DUMP(,| |$)" \
    || refuse "la sauvegarde est en révision $REV_DUMP, inconnue du code en place ($HEAD) : sauvegarde plus récente que le code — extraire d'abord le bon commit (meta.json)"
  info "sauvegarde en $REV_DUMP, code en $HEAD : migration après restauration"
fi
ok "dump lisible, révision $REV_DUMP compatible avec le code ($HEAD)"
if [[ -n "$ARCHIVE_ENV" ]]; then
  differ=()
  for k in FIELD_ENCRYPTION_KEY SECRET_KEY POSTGRES_USER POSTGRES_DB; do
    [[ "$(env_file_value "$ARCHIVE_ENV" "$k")" == "$(env_value "$k")" ]] || differ+=("$k")
  done
  if (( ${#differ[@]} )) && (( ! RESTORE_ENV )); then
    refuse ".env de l'archive différent du .env actuel (${differ[*]}) : sans la même FIELD_ENCRYPTION_KEY, les secrets agent restaurés sont illisibles. Relancer avec --restaurer-env pour reprendre celui de l'archive"
  fi
  (( ${#differ[@]} )) && info ".env de l'archive différent (${differ[*]}) : il remplacera l'actuel (--restaurer-env)"
fi
REV_LIVE="$(sql "$pg_db" "SELECT version_num FROM alembic_version" 2>/dev/null)"
info "base actuelle : révision ${REV_LIVE:-?}, $(sql "$pg_db" "SELECT count(*) FROM members" 2>/dev/null || echo ?) membre(s)"

# ---------------------------------------------------------------------------------------------- 2
step "2/5 Confirmation et sauvegarde de l'état actuel"
if [[ -z "$CONFIRM" ]]; then
  echo "  La base $pg_db sera REMPLACÉE par $SRC (l'actuelle sera conservée sous $OLD_DB)."
  read -r -p "  Taper RESTAURER pour continuer : " CONFIRM || true
fi
[[ "$CONFIRM" == RESTAURER ]] || refuse "confirmation absente"
SAFETY="$BACKUP_DIR/akwaba-pre-restore-$STAMP.dump"
if ! ( umask 077; docker compose exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" -Fc > "$SAFETY" ) || [[ ! -s "$SAFETY" ]]; then
  refuse "sauvegarde de l'état actuel impossible"
fi
ok "état actuel sauvegardé : $SAFETY"

# ---------------------------------------------------------------------------------------------- 3
step "3/5 Arrêt du backend et de web"
docker compose stop backend web >/dev/null 2>&1 || true
ok "backend et web arrêtés (PostgreSQL reste démarré)"
restart_services() {
  docker compose up -d --no-build backend >/dev/null 2>&1 && docker compose up -d --no-build --force-recreate web >/dev/null 2>&1
}
abort() {   # échec AVANT l'échange : production intacte
  printf '\n\033[31mÉCHEC : %s\033[0m\n' "$1"
  sql postgres "DROP DATABASE IF EXISTS $TMP_DB" >/dev/null 2>&1
  restart_services
  echo "La base de production n'a PAS été modifiée ; backend et web redémarrés."
  exit 1
}

# ---------------------------------------------------------------------------------------------- 4
step "4/5 Restauration dans une base temporaire, puis échange"
sql postgres "CREATE DATABASE $TMP_DB OWNER $pg_user" >/dev/null || abort "création de la base temporaire impossible"
out="$(docker compose exec -T postgres pg_restore -U "$pg_user" -d "$TMP_DB" --exit-on-error < "$DUMP" 2>&1)" \
  || abort "pg_restore : $(tail -n 2 <<<"$out")"
[[ "$(sql "$TMP_DB" "SELECT version_num FROM alembic_version")" == "$REV_DUMP" ]] || abort "révision restaurée inattendue"
[[ "$(sql "$TMP_DB" "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN ($TRIGGERS)")" == 7 ]] \
  || abort "déclencheurs d'immuabilité absents de la base restaurée"
ok "base temporaire restaurée et vérifiée ($(sql "$TMP_DB" "SELECT count(*) FROM members") membre(s))"
swap="SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$pg_db', '$TMP_DB') AND pid <> pg_backend_pid()"
sql postgres "$swap" >/dev/null
sql postgres "ALTER DATABASE $pg_db RENAME TO $OLD_DB" >/dev/null || abort "renommage de la base actuelle impossible"
if ! sql postgres "ALTER DATABASE $TMP_DB RENAME TO $pg_db" >/dev/null; then
  sql postgres "ALTER DATABASE $OLD_DB RENAME TO $pg_db" >/dev/null
  abort "échange impossible (base actuelle remise en place)"
fi
ok "échange effectué ; ancienne base conservée : $OLD_DB"
if (( RESTORE_ENV )) && [[ -n "$ARCHIVE_ENV" ]]; then
  cp -p .env ".env.avant-restauration-$STAMP" && install -m 600 "$ARCHIVE_ENV" .env
  ok ".env de l'archive installé (ancien : .env.avant-restauration-$STAMP)"
fi

# ---------------------------------------------------------------------------------------------- 5
step "5/5 Migrations, redémarrage, vérification"
back_out() {
  printf '\n\033[31mÉCHEC : %s\033[0m\n' "$1"
  echo "Pour revenir à la base d'avant la restauration (à lancer soi-même) :"
  echo "  docker compose stop backend web"
  echo "  docker compose exec -T postgres psql -U $pg_user -d postgres -c 'ALTER DATABASE $pg_db RENAME TO ${pg_db}_restauration_echouee_$STAMP'"
  echo "  docker compose exec -T postgres psql -U $pg_user -d postgres -c 'ALTER DATABASE $OLD_DB RENAME TO $pg_db'"
  echo "  docker compose up -d --no-build backend && docker compose up -d --no-build --force-recreate web"
  exit 1
}
if [[ "$REV_DUMP" != "$HEAD" ]]; then
  alembic upgrade head >/dev/null || back_out "migration $REV_DUMP → $HEAD en échec"
  [[ "$(sql "$pg_db" "SELECT version_num FROM alembic_version")" == "$HEAD" ]] || back_out "révision $HEAD non atteinte"
  ok "base migrée $REV_DUMP → $HEAD"
fi
restart_services || back_out "redémarrage impossible"
end=$((SECONDS + HEALTH_TIMEOUT))
for svc in backend web; do
  until [[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$svc")" 2>/dev/null)" == healthy ]]; do
    (( SECONDS < end )) || back_out "$svc non sain"
    sleep 3
  done
  ok "$svc : healthy"
done
"$VERIFY_ALL" || back_out "la vérification globale signale des échecs"
printf '\n\033[32mRestauration terminée.\033[0m Ancienne base : %s (à supprimer plus tard, une fois satisfait :\n' "$OLD_DB"
printf "  docker compose exec -T postgres psql -U %s -d postgres -c 'DROP DATABASE %s'). Sauvegarde : %s\n" "$pg_user" "$OLD_DB" "$SAFETY"
[[ -n "$IDENTITY" ]] && echo "Pensez à effacer la clé privée de ce serveur : shred -u $IDENTITY"
exit 0
