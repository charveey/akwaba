#!/usr/bin/env bash
# AKWABA — base PostgreSQL JETABLE pour les tests, sur le poste de développement (Docker requis).
#
#     tests/test-db.sh up        # démarre (127.0.0.1:5433 uniquement, données en mémoire) + écrit .env.test
#     tests/test-db.sh down      # arrête et supprime le conteneur (aucune donnée conservée)
#     tests/test-db.sh status
#     tests/test-db.sh --plan up # affiche les commandes sans rien lancer
#
# Même image que la production (lue dans docker-compose.yml). Sans lien avec la base de production :
# conteneur séparé « akwaba-test-db », aucun volume, jamais publié ailleurs que sur 127.0.0.1.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=akwaba-test-db
PORT="${AKWABA_TEST_DB_PORT:-5433}"
URL="postgresql+psycopg://akwaba:akwaba@127.0.0.1:$PORT/akwaba_test"
PLAN=0
[[ "${1:-}" == --plan ]] && { PLAN=1; shift; }
do_() { if (( PLAN )); then echo "$*"; else "$@" >/dev/null; fi; }

IMAGE="$(grep -E '^\s*image:\s*postgres:' "$ROOT/docker-compose.yml" | head -n1 | awk '{print $2}')"
[[ -n "$IMAGE" ]] || { echo "image PostgreSQL introuvable dans docker-compose.yml" >&2; exit 1; }

case "${1:-}" in
  up)
    if (( ! PLAN )) && [[ -n "$(docker ps -q -f "name=^${NAME}$")" ]]; then
      echo "déjà démarrée : $URL"
    else
      do_ docker run -d --rm --name "$NAME" -p "127.0.0.1:$PORT:5432" \
        -e POSTGRES_USER=akwaba -e POSTGRES_PASSWORD=akwaba -e POSTGRES_DB=akwaba_test \
        --tmpfs /var/lib/postgresql/data:rw,size=512m "$IMAGE" || exit 1
      if (( ! PLAN )); then
        for _ in $(seq 1 60); do
          docker exec "$NAME" pg_isready -U akwaba -d akwaba_test >/dev/null 2>&1 && break
          sleep 1
        done
        docker exec "$NAME" pg_isready -U akwaba -d akwaba_test >/dev/null 2>&1 || { echo "base de test non prête" >&2; exit 1; }
      fi
    fi
    if [[ -f "$ROOT/.env.test" ]] && grep -q '^TEST_DATABASE_URL=' "$ROOT/.env.test"; then
      echo ".env.test existe déjà (non modifié)"
    else
      do_ sh -c "printf 'TEST_DATABASE_URL=%s\n' '$URL' >> '$ROOT/.env.test'"
      echo ".env.test : TEST_DATABASE_URL=$URL"
    fi
    echo "prête : $URL ($IMAGE)"
    ;;
  down)
    do_ docker rm -f "$NAME" 2>/dev/null || true
    echo "base de test arrêtée et supprimée"
    ;;
  status)
    if [[ -n "$(docker ps -q -f "name=^${NAME}$" 2>/dev/null)" ]]; then echo "démarrée : $URL"; else echo "arrêtée"; fi
    ;;
  *)
    echo "usage : $0 [--plan] up|down|status" >&2; exit 2 ;;
esac
