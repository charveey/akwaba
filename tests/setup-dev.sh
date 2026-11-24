#!/usr/bin/env bash
# AKWABA — préparation du poste de développement (une fois ; relançable sans risque).
# Lancé par la tâche « Akwaba : préparer l'environnement » de VS Code et de Zed, ou à la main :
#
#     tests/setup-dev.sh           # .venv + dépendances + base de test (si Docker est disponible)
#     tests/setup-dev.sh --plan    # affiche ce qui serait fait
#
# 1. .venv à la racine du dépôt, avec Python 3.12 (version de production) si disponible.
# 2. Dépendances figées (backend/requirements.lock, avec empreintes), PUIS les outils de test
#    (pip exige des empreintes pour tout si une seule ligne en a : deux commandes séparées).
# 3. Base de test jetable (tests/test-db.sh up) si Docker répond ; sinon, les tests PostgreSQL sont ignorés.
# Ne touche ni à .env, ni à la production, ni aux conteneurs de l'application.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLAN=0; [[ "${1:-}" == --plan ]] && PLAN=1
VENV="$ROOT/.venv"
TOOLS=("pytest>=8" "httpx>=0.27" "packaging" "pytest-cov>=5" "debugpy>=1.8")
say() { printf '\033[1m%s\033[0m\n' "$*"; }
do_() { if (( PLAN )); then echo "  $*"; else "$@" || { echo "ÉCHEC : $*" >&2; exit 1; }; fi; }

PY=""
for candidate in python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$(command -v "$candidate")"; break; fi
done
[[ -n "$PY" ]] || { echo "Python 3 introuvable : installer python3 (3.12 de préférence)" >&2; exit 1; }
version="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "Python $version trop ancien (3.11 minimum, 3.12 conseillé)" >&2; exit 1; }

say "1/3 Environnement Python ($VENV, Python $version)"
[[ "$version" == 3.12 ]] || echo "  note : Python $version (la production utilise 3.12 ; les tests restent valables)"
if [[ -x "$VENV/bin/python" ]]; then
  echo "  .venv existe déjà"
else
  "$PY" -c 'import venv' 2>/dev/null || { echo "module venv absent : sudo apt install python3-venv" >&2; exit 1; }
  do_ "$PY" -m venv "$VENV"
fi

say "2/3 Dépendances"
do_ "$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$ROOT/backend/requirements.lock"
do_ "$VENV/bin/python" -m pip install -q --disable-pip-version-check "${TOOLS[@]}"
(( PLAN )) || "$VENV/bin/python" -c 'import fastapi, sqlalchemy, pytest, httpx, pytest_cov, debugpy; print("  OK : fastapi", fastapi.__version__, "· pytest", pytest.__version__, "· couverture et débogueur prêts")'

say "3/3 Base de test"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if (( PLAN )); then echo "  $ROOT/tests/test-db.sh up"; else "$ROOT/tests/test-db.sh" up | sed 's/^/  /'; fi
else
  echo "  Docker indisponible : base de test non démarrée (tests PostgreSQL ignorés). Plus tard : tests/test-db.sh up"
fi

echo
say "Prêt."
echo "  VS Code : « Developer: Reload Window » (Ctrl+Maj+P), puis le panneau « Tests »."
echo "  Zed     : boutons ▶ dans la marge des tests, ou task: spawn → « Akwaba : … »."
