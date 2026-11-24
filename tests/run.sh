#!/usr/bin/env bash
# AKWABA — point d'entrée unique des tests, pour la ligne de commande, Zed et VS Code.
#
#     tests/run.sh                                   # toutes les suites (tests/run-all.sh)
#     tests/run.sh backend/tests/test_members.py     # un fichier (chemin relatif ou absolu)
#     tests/run.sh backend/tests/test_members.py -k test_archive    # un test (ou une classe)
#     tests/run.sh backend/app/services/members.py   # un fichier non-test : toute la suite du composant
#     tests/run.sh --plan …                          # affiche la commande sans la lancer
#
# Il choisit le bon répertoire et le bon lanceur (pytest dans backend/ ou agent/, pytest à la racine pour
# tests/nginx|deploy|e2e, node --test pour frontend/), charge .env.test (base de test) s'il existe, et
# utilise .venv/bin/python s'il existe. Un script navigateur tests/e2e/browser_*.mjs lance le test Python
# qui le pilote.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLAN=0
if [[ "${1:-}" == --plan ]]; then PLAN=1; shift; fi
TARGET=""; KEXPR=""; EXTRA=()
while (( $# )); do
  case "$1" in
    -k) KEXPR="${2:-}"; shift 2 ;;
    *) if [[ -z "$TARGET" && "$1" != -* ]]; then TARGET="$1"; else EXTRA+=("$1"); fi; shift ;;
  esac
done
KEXPR="${KEXPR##*> }"          # Zed : « TestClasse > test_methode » → test_methode

if [[ -f "$ROOT/.env.test" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env.test"
  set +a
fi
PY="${AKWABA_PYTHON:-}"
[[ -z "$PY" && -x "$ROOT/.venv/bin/python" ]] && PY="$ROOT/.venv/bin/python"
PY="${PY:-python3}"

run_in() {   # run_in RÉPERTOIRE commande…
  local dir="$1"; shift
  if (( PLAN )); then
    local shown="${dir#"$ROOT"/}"; [[ "$dir" == "$ROOT" ]] && shown="."
    printf 'cd %s && %s\n' "$shown" "${*//"$ROOT"\//}"
    [[ -n "${TEST_DATABASE_URL:-}" ]] && echo "(TEST_DATABASE_URL défini par .env.test)"
    exit 0
  fi
  [[ -n "${TEST_DATABASE_URL:-}" ]] || echo "info : TEST_DATABASE_URL absent → tests PostgreSQL ignorés (tests/test-db.sh up)" >&2
  cd "$dir" && exec "$@"
}

if [[ -z "$TARGET" ]]; then
  run_in "$ROOT" "$ROOT/tests/run-all.sh" "${EXTRA[@]}"
fi
[[ "$TARGET" = /* ]] || TARGET="$PWD/$TARGET"
[[ -e "$TARGET" ]] || { echo "introuvable : $TARGET" >&2; exit 2; }
ABS="$(cd "$(dirname "$TARGET")" && pwd)/$(basename "$TARGET")"
ABS="${ABS%/.}"
REL="${ABS#"$ROOT"/}"
[[ "$REL" != "$ABS" ]] || { echo "hors du dépôt : $ABS" >&2; exit 2; }
pyk=(); [[ -n "$KEXPR" ]] && pyk=(-k "$KEXPR")
is_test_file() { [[ -d "$ABS" || "$(basename "$ABS")" == test_*.py || "$(basename "$ABS")" == conftest.py ]]; }

case "$REL" in
  backend|backend/*|agent|agent/*)
    comp="${REL%%/*}"
    if is_test_file && [[ "$REL" == "$comp/tests"* ]]; then
      run_in "$ROOT/$comp" "$PY" -m pytest "${REL#"$comp"/}" "${pyk[@]}" "${EXTRA[@]}"
    fi
    run_in "$ROOT/$comp" "$PY" -m pytest "${pyk[@]}" "${EXTRA[@]}"
    ;;
  frontend|frontend/*)
    nk=(); [[ -n "$KEXPR" ]] && nk=(--test-name-pattern "$KEXPR")
    if [[ "$REL" == *.test.mjs ]]; then
      run_in "$ROOT/frontend" node --test "${nk[@]}" "${REL#frontend/}"
    fi
    run_in "$ROOT/frontend" node --test "${nk[@]}" tests/
    ;;
  tests/e2e/*.mjs)
    driver="$(grep -l -- "$(basename "$REL")" "$ROOT"/tests/e2e/test_*.py 2>/dev/null | head -n1)"
    [[ -n "$driver" ]] || driver="$ROOT/tests/e2e"
    run_in "$ROOT" "$PY" -m pytest -q -p no:cacheprovider "${driver#"$ROOT"/}" "${pyk[@]}" "${EXTRA[@]}"
    ;;
  tests/nginx*|tests/deploy*|tests/e2e*|tests/fixtures*)
    if is_test_file; then
      run_in "$ROOT" "$PY" -m pytest -q -p no:cacheprovider "$REL" "${pyk[@]}" "${EXTRA[@]}"
    fi
    run_in "$ROOT" "$PY" -m pytest -q -p no:cacheprovider tests/nginx tests/deploy tests/e2e "${pyk[@]}" "${EXTRA[@]}"
    ;;
  deploy/*)       # scripts d'exploitation : leurs tests sont dans tests/deploy
    run_in "$ROOT" "$PY" -m pytest -q -p no:cacheprovider tests/deploy "${pyk[@]}" "${EXTRA[@]}"
    ;;
  *)
    run_in "$ROOT" "$ROOT/tests/run-all.sh" "${EXTRA[@]}"
    ;;
esac
