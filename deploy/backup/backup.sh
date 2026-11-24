#!/usr/bin/env bash
# AKWABA — sauvegarde chiffrée (Phase 14a). Lancée chaque jour par le minuteur systemd akwaba-backup.timer,
# ou à la main :
#
#     ./deploy/backup/backup.sh                    # test de restauration automatique le dimanche
#     ./deploy/backup/backup.sh --test-restore     # forcer le test de restauration
#
# 1. pg_dump (lecture seule de la base), vérifié par pg_restore --list.
# 2. Le dimanche : RESTAURATION RÉELLE de ce dump dans un conteneur temporaire (test-restore.sh).
# 3. Archive chiffrée avec age pour la clé PUBLIQUE de backups/config/age-recipients.txt :
#    dump + .env (indispensable : FIELD_ENCRYPTION_KEY déchiffre les secrets agent) + métadonnées.
#    Le clair ne quitte jamais un répertoire temporaire en 700, supprimé à la fin.
#    → backups/auto/akwaba-AAAAMMJJTHHMMSSZ.tar.age (600) + .sha256
# 4. Rotation locale (7 jours, 4 semaines, 6 mois) ; dumps avant déploiement : les 5 plus récents.
# 5. Copie sur l'Exit Node (rsync/SSH, clé dédiée en ÉCRITURE SEULE côté Exit Node), si configurée.
# 6. État dans backups/auto/status.json (lu par verify-all.sh).
#
# La clé PRIVÉE age n'est jamais sur le serveur : ni le serveur ni l'Exit Node ne peuvent relire les archives.
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1
BACKUP_DIR="${BACKUP_DIR:-backups}"
CONFIG_DIR="${CONFIG_DIR:-$BACKUP_DIR/config}"
AUTO="$BACKUP_DIR/auto"
TEST_RESTORE="${TEST_RESTORE:-auto}"           # auto = le dimanche (UTC)
case "${1:-}" in
  "") ;;
  --test-restore) TEST_RESTORE=yes ;;
  *) echo "usage : $0 [--test-restore]" >&2; exit 2 ;;
esac
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
log() { printf '[sauvegarde] %s\n' "$*"; }
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }

mkdir -p "$AUTO" && chmod 700 "$BACKUP_DIR" "$AUTO"
exec 9>"$BACKUP_DIR/.backup.lock"
flock -n 9 || { log "une sauvegarde est déjà en cours"; exit 1; }

STATUS="$AUTO/status.json"
# status.json : {"backup": {...}, "offsite": {...}, "restore_test": {...}} ; chaque bloc est conservé
# tant qu'il n'est pas remplacé (ex. : le dernier test de restauration reste visible en semaine).
set_status() {   # set_status BLOC JSON
  python3 - "$STATUS" "$1" "$2" <<'PY'
import json, os, sys
path, key, value = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {}
data[key] = value
tmp = path + ".tmp"
with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
os.replace(tmp, path)
PY
}
json_str() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }
fail() {
  log "ÉCHEC : $*"
  set_status backup "{\"at\": \"$NOW\", \"ok\": false, \"error\": $(json_str "$*")}"
  exit 1
}

RECIPIENTS="$CONFIG_DIR/age-recipients.txt"
[[ -s "$RECIPIENTS" ]] || fail "clé publique age absente ($RECIPIENTS) : lancer deploy/backup/setup-server.sh"
command -v age >/dev/null || fail "age non installé (sudo apt install age)"
[[ -f .env ]] || fail ".env absent"
pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"

WORK="$(mktemp -d "$BACKUP_DIR/.tmp-XXXXXX")"; chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT

# 1 ----------------------------------------------------------------------------------------------------
log "1/5 pg_dump"
( umask 077; docker compose exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" -Fc > "$WORK/akwaba.dump" ) \
  || fail "pg_dump a échoué"
[[ -s "$WORK/akwaba.dump" ]] || fail "dump vide"
docker compose exec -T postgres pg_restore --list < "$WORK/akwaba.dump" > /dev/null || fail "dump illisible (pg_restore --list)"
dump_sha="$(sha256sum "$WORK/akwaba.dump" | cut -d' ' -f1)"
revision="$(docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -c 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '\r')"
log "    $(du -h "$WORK/akwaba.dump" | cut -f1), révision ${revision:-?}"

# 2 ----------------------------------------------------------------------------------------------------
restore_failed=0
if [[ "$TEST_RESTORE" == yes || ( "$TEST_RESTORE" == auto && "$(date -u +%u)" == 7 ) ]]; then
  log "2/5 test de restauration de CE dump"
  out="$("$(dirname "$0")/test-restore.sh" "$WORK/akwaba.dump" 2>&1)"; rc=$?
  log "    $out"
  set_status restore_test "{\"at\": \"$NOW\", \"ok\": $( ((rc == 0)) && echo true || echo false), \"archive\": \"akwaba-$STAMP.tar.age\", \"details\": $(json_str "$out")}"
  (( rc == 0 )) || restore_failed=1
else
  log "2/5 test de restauration : le dimanche (ou --test-restore)"
fi

# 3 ----------------------------------------------------------------------------------------------------
log "3/5 archive chiffrée (age)"
( umask 077; cp .env "$WORK/env" )
python3 - "$WORK/meta.json" <<PY
import json, sys
json.dump({"created_at": "$NOW", "commit": "$(git rev-parse --short HEAD 2>/dev/null)",
           "alembic_revision": "$revision", "dump_sha256": "$dump_sha",
           "postgres_image": "$(docker compose config --images 2>/dev/null | grep -E '^postgres:' | head -n1)",
           "host": "$(hostname)"}, open(sys.argv[1], "w"), indent=2)
PY
ARCHIVE="$AUTO/akwaba-$STAMP.tar.age"
( umask 077; tar -C "$WORK" -cf - akwaba.dump env meta.json | age -R "$RECIPIENTS" -o "$ARCHIVE.part" ) \
  || { rm -f "$ARCHIVE.part"; fail "chiffrement impossible"; }
[[ "$(head -c 21 "$ARCHIVE.part")" == "age-encryption.org/v1" ]] || { rm -f "$ARCHIVE.part"; fail "archive non chiffrée"; }
mv "$ARCHIVE.part" "$ARCHIVE" && chmod 600 "$ARCHIVE"
( cd "$AUTO" && sha256sum "akwaba-$STAMP.tar.age" > "akwaba-$STAMP.tar.age.sha256" && chmod 600 "akwaba-$STAMP.tar.age.sha256" )
size="$(stat -c %s "$ARCHIVE")"
log "    $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
set_status backup "{\"at\": \"$NOW\", \"ok\": true, \"archive\": \"akwaba-$STAMP.tar.age\", \"size\": $size, \"alembic_revision\": \"$revision\", \"dump_sha256\": \"$dump_sha\"}"

# 4 ----------------------------------------------------------------------------------------------------
log "4/5 rotation"
python3 "$(dirname "$0")/retention.py" "$AUTO" --apply | sed 's/^/    /'
mapfile -t old_dumps < <(find "$BACKUP_DIR" -maxdepth 1 -name 'akwaba-pre-*.dump' -printf '%T@ %p\n' \
  | sort -rn | tail -n +6 | cut -d' ' -f2-)
if (( ${#old_dumps[@]} )); then rm -f -- "${old_dumps[@]}"; log "    dumps avant déploiement : ${#old_dumps[@]} ancien(s) supprimé(s), 5 gardés"; fi

# 5 ----------------------------------------------------------------------------------------------------
offsite_failed=0
if [[ -n "${OFFSITE_DEST:-}" || -s "$CONFIG_DIR/offsite.conf" ]]; then
  log "5/5 copie sur l'Exit Node"
  if [[ -n "${OFFSITE_DEST:-}" ]]; then                  # tests : destination locale
    dest="$OFFSITE_DEST"; rsh=()
  else
    # shellcheck disable=SC1091
    . "$CONFIG_DIR/offsite.conf"                          # OFFSITE_HOST, OFFSITE_USER
    dest="${OFFSITE_USER:?}@${OFFSITE_HOST:?}:"
    rsh=(-e "ssh -i $CONFIG_DIR/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=20 -o UserKnownHostsFile=$CONFIG_DIR/known_hosts -o StrictHostKeyChecking=accept-new")
  fi
  out="$(rsync -t --chmod=F600 --timeout=120 "${rsh[@]}" "$ARCHIVE" "$ARCHIVE.sha256" "$dest" 2>&1)" || offsite_failed=1
  [[ -n "$out" ]] && printf '    %s\n' "$out"
  if (( offsite_failed )); then
    log "    ÉCHEC de la copie (l'archive locale est conservée)"
    set_status offsite "{\"at\": \"$NOW\", \"ok\": false, \"archive\": \"akwaba-$STAMP.tar.age\", \"error\": $(json_str "$out")}"
  else
    log "    copiée"
    set_status offsite "{\"at\": \"$NOW\", \"ok\": true, \"archive\": \"akwaba-$STAMP.tar.age\"}"
  fi
else
  log "5/5 copie sur l'Exit Node : non configurée"
fi

if (( restore_failed || offsite_failed )); then
  log "TERMINÉE AVEC ERREUR(S) : test de restauration=$( ((restore_failed)) && echo échec || echo ok), copie=$( ((offsite_failed)) && echo échec || echo ok)"
  exit 1
fi
log "terminée : $ARCHIVE"
