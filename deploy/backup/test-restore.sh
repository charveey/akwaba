#!/usr/bin/env bash
# AKWABA — test de restauration d'un dump (Phase 14a). Lancé par backup.sh le dimanche, ou à la main :
#
#     ./deploy/backup/test-restore.sh FICHIER.dump
#
# Restaure RÉELLEMENT le dump dans un conteneur PostgreSQL TEMPORAIRE (même image figée que la production,
# aucun réseau, aucun port, aucun volume, détruit à la fin), puis compare avec la base en service :
#   - pg_restore sans erreur ;  - même révision Alembic ;  - mêmes tables ;
#   - 7 déclencheurs d'immuabilité présents ;  - tables métier non vides si elles le sont en production,
#     et jamais plus de lignes qu'en production (le dump est antérieur à l'instant présent).
# Ne touche à rien d'autre : la base de production n'est que LUE.
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 1
DUMP="${1:-}"
[[ -s "$DUMP" ]] || { echo "usage : $0 FICHIER.dump" >&2; exit 2; }
TRIGGERS=7
BUSINESS=(admin_users members subscription_plans subscriptions payments expenses expense_categories audit_logs)
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }
pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
live() { docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null | tr -d '\r'; }

image="$(docker compose config --images 2>/dev/null | grep -E '^postgres:' | head -n1)"
[[ -n "$image" ]] || { echo "ÉCHEC image PostgreSQL introuvable dans docker-compose.yml"; exit 1; }
name="akwaba-restore-test-$$"
password="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --rm --name "$name" --network none --memory 192m --pids-limit 128 \
  -e POSTGRES_PASSWORD="$password" -e POSTGRES_DB=restore_test "$image" >/dev/null \
  || { echo "ÉCHEC démarrage du conteneur de test ($image)"; exit 1; }
for _ in $(seq 1 60); do
  docker exec "$name" pg_isready -U postgres -d restore_test >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$name" pg_isready -U postgres -d restore_test >/dev/null 2>&1 || { echo "ÉCHEC PostgreSQL de test non prêt"; exit 1; }
test_q() { docker exec "$name" psql -U postgres -d restore_test -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null | tr -d '\r'; }

errors="$(docker exec -i "$name" pg_restore -U postgres -d restore_test --no-owner --no-privileges --exit-on-error \
  < "$DUMP" 2>&1)" || { echo "ÉCHEC pg_restore : $(tail -n 3 <<<"$errors")"; exit 1; }

fails=()
rev_dump="$(test_q "SELECT version_num FROM alembic_version")"; rev_live="$(live "SELECT version_num FROM alembic_version")"
[[ -n "$rev_dump" && "$rev_dump" == "$rev_live" ]] || fails+=("révision Alembic : sauvegarde ${rev_dump:-?}, production ${rev_live:-?}")
tables_sql="SELECT string_agg(tablename, ',' ORDER BY tablename) FROM pg_tables WHERE schemaname = 'public'"
tables_dump="$(test_q "$tables_sql")"; tables_live="$(live "$tables_sql")"
[[ -n "$tables_dump" && "$tables_dump" == "$tables_live" ]] || fails+=("tables différentes de la production")
trig="$(test_q "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN ('audit_logs_no_truncate','audit_logs_no_update_delete','enforcement_decisions_no_truncate','enforcement_decisions_no_update_delete','expenses_guard','payments_guard','subscriptions_guard')")"
[[ "$trig" == "$TRIGGERS" ]] || fails+=("déclencheurs d'immuabilité : $trig/$TRIGGERS")
rows=0
for t in "${BUSINESS[@]}"; do
  n_dump="$(test_q "SELECT count(*) FROM $t")"; n_live="$(live "SELECT count(*) FROM $t")"
  [[ "$n_dump" =~ ^[0-9]+$ && "$n_live" =~ ^[0-9]+$ ]] || { fails+=("$t : comptage impossible"); continue; }
  rows=$((rows + n_dump))
  if (( n_live > 0 && n_dump == 0 )); then fails+=("$t : vide dans la sauvegarde ($n_live en production)"); fi
  if (( n_dump > n_live )); then fails+=("$t : $n_dump lignes dans la sauvegarde > $n_live en production"); fi
done

ntables="$(tr ',' '\n' <<<"$tables_dump" | grep -c .)"
if (( ${#fails[@]} )); then
  printf 'ÉCHEC test de restauration : %s\n' "${fails[@]}"
  exit 1
fi
echo "RESTAURATION_OK revision=$rev_dump tables=$ntables lignes_metier=$rows image=$image"
