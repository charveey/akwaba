#!/usr/bin/env bash
# AKWABA — lance toutes les suites de tests et affiche un récapitulatif (Phase 12d).
#
#     TEST_DATABASE_URL=postgresql+psycopg://akwaba:akwaba@127.0.0.1:5433/akwaba_test tests/run-all.sh
#     tests/run-all.sh --rapide      # sans les tests de bout en bout (Nginx réel, navigateur, agent réel)
#     tests/run-all.sh --plan        # affiche les commandes sans rien lancer
#
# Sans TEST_DATABASE_URL, les tests d'intégration (PostgreSQL réel) et de bout en bout sont IGNORÉS par
# pytest : le récapitulatif le signale, et le code de sortie reste non nul pour ne pas passer inaperçu.
# Aucun appel réseau externe, aucune action sur un serveur : tout tourne en local.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE=complet
case "${1:-}" in
  "") ;;
  --rapide) MODE=rapide ;;
  --plan) MODE=plan ;;
  *) echo "usage : $0 [--rapide|--plan]" >&2; exit 2 ;;
esac

SUITES=(                                   # nom|répertoire|commande (pytest.ini : -q, sans cache)
  "Backend|$ROOT/backend|python3 -m pytest"
  "Agent|$ROOT/agent|python3 -m pytest"
  "Frontend|$ROOT/frontend|node --test tests/*.test.mjs"
)
[[ "$MODE" != rapide ]] && SUITES+=("Nginx + déploiement + bout en bout|$ROOT|python3 -m pytest -q -p no:cacheprovider tests/nginx tests/deploy tests/e2e")

if [[ "$MODE" == plan ]]; then
  for s in "${SUITES[@]}"; do IFS='|' read -r name dir cmd <<<"$s"; printf '%-36s (cd %s && %s)\n' "$name" "$dir" "$cmd"; done
  exit 0
fi

[[ -n "${TEST_DATABASE_URL:-}" ]] || printf '\033[33mATTENTION\033[0m TEST_DATABASE_URL absent : les tests sur PostgreSQL réel seront ignorés.\n\n'

declare -a NAMES RESULTS STATUS DURATIONS
FAILED=0
for s in "${SUITES[@]}"; do
  IFS='|' read -r name dir cmd <<<"$s"
  printf '\033[1m== %s\033[0m\n' "$name"
  log="$(mktemp)"
  start=$SECONDS
  (cd "$dir" && eval "$cmd") >"$log" 2>&1
  rc=$?
  tail -n 3 "$log"
  if [[ "$cmd" == node* ]]; then
    pass="$(grep -E '^# pass' "$log" | awk '{print $3}')"; fail="$(grep -E '^# fail' "$log" | awk '{print $3}')"
    summary="${pass:-0} réussis, ${fail:-?} échecs"
  else
    summary="$(grep -E '(passed|failed|error|skipped|xfailed)' "$log" | tail -n 1 | sed -E 's/^=+ //; s/ =+$//; s/ in [0-9.]+s.*//' \
      | sed -E 's/ passed/ réussis/; s/ xfailed/ échecs attendus/; s/ failed/ échoués/; s/ skipped/ ignorés/; s/ errors?/ erreurs/; s/ warnings?/ avertissements/')"
  fi
  status=OK
  if (( rc != 0 )); then status=ÉCHEC; FAILED=1
  elif grep -qE '[0-9]+ ignorés' <<<"$summary"; then status=IGNORÉS; FAILED=1
  fi
  NAMES+=("$name"); RESULTS+=("${summary:-aucun résultat}"); STATUS+=("$status"); DURATIONS+=("$((SECONDS - start))s")
  rm -f "$log"
  echo
done

printf '\033[1m%-36s %-9s %-8s %s\033[0m\n' "Suite" "Statut" "Durée" "Résultat"
for i in "${!NAMES[@]}"; do
  color=32; [[ "${STATUS[$i]}" == OK ]] || color=31
  printf '%-36s \033[%sm%-9s\033[0m %-8s %s\n' "${NAMES[$i]}" "$color" "${STATUS[$i]}" "${DURATIONS[$i]}" "${RESULTS[$i]}"
done
[[ "$MODE" == rapide ]] && echo "(mode --rapide : tests de bout en bout non lancés)"
exit "$FAILED"
