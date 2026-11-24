#!/usr/bin/env bash
# AKWABA — vérification globale de non-régression, SUR LE SERVEUR (Phase 12d).
#
#     ./deploy/verify-all.sh
#
# STRICTEMENT EN LECTURE SEULE : aucune commande ne modifie les conteneurs, la base, Tailscale ni les
# fichiers. Lectures (docker compose ps/exec/logs, SELECT, alembic current/heads/check, sha256sum) et
# requêtes HTTP locales seulement. En cas d'échec, la commande de correction est AFFICHÉE, jamais lancée.
#
# 1. Reprend ./deploy/verify-11a.sh : conteneurs, ports, Nginx, CSP, backend direct, HMAC, heartbeat,
#    dry_run, Funnel 443, Serve 8443.
# 2. Ajoute :
#    - code servi = code du dépôt : frontend monté dans Nginx (un changement de branche peut laisser le
#      conteneur « web » sur un ancien dossier vide) et code de l'image backend ;
#    - Alembic à jour, une seule tête, aucun écart modèle/base ; déclencheurs d'immuabilité présents ;
#    - aucune décision appliquée ; agent et HMAC actifs (lots et identifiant utilisés récemment) ;
#    - aucun secret de .env dans les réponses publiques ni dans les journaux des conteneurs ;
#      .env non lisible par les autres utilisateurs et non suivi par git.
# shellcheck disable=SC2015  # « test && ok || ko » : ok() renvoie toujours 0.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
VERIFY_11A="${VERIFY_11A:-./deploy/verify-11a.sh}"
WEB_URL="${WEB_URL:-http://127.0.0.1:8080}"
WEB_ROOT=/usr/share/nginx/akwaba          # ./frontend monté en lecture seule (docker-compose.yml)
APP_ROOT=/srv                             # alembic.ini, alembic/ et app/ copiés dans l'image (backend/Dockerfile)
TRIGGERS=(audit_logs_no_truncate audit_logs_no_update_delete enforcement_decisions_no_truncate
          enforcement_decisions_no_update_delete expenses_guard payments_guard subscriptions_guard)

PASS=0; FAILS=0
ok()   { printf '  \033[32mOK\033[0m     %s\n' "$*"; PASS=$((PASS+1)); }
ko()   { printf '  \033[31mÉCHEC\033[0m  %s\n' "$*"; FAILS=$((FAILS+1)); }
fix()  { printf '         \033[33m→ correction (à lancer soi-même) :\033[0m %s\n' "$*"; }
info() { printf '  \033[2m%s\033[0m\n' "$*"; }
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }
TMP="$(mktemp -d)"; chmod 700 "$TMP"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------------------------------- 1
printf '\033[1m######## Vérifications du lot 11a\033[0m\n'
"$VERIFY_11A"; rc11a=$?
echo
printf '\033[1m######## Vérifications globales (Phase 12)\033[0m\n'

pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
sql() { docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null; }
# Empreinte d'une arborescence : chemins relatifs + contenus, ordre d'octets, sans cache Python.
TREE_DIGEST='find "$@" -type f ! -path "*/__pycache__/*" ! -name "*.pyc" | LC_ALL=C sort | xargs -r sha256sum | sha256sum | cut -c1-16'

echo "== Code servi = code du dépôt"
host_front="$(cd frontend && sh -c "$TREE_DIGEST" _ .)"
nb_host="$(find frontend -type f | wc -l | tr -d ' ')"
nb_web="$(docker compose exec -T web sh -c "find $WEB_ROOT -type f | wc -l" 2>/dev/null | tr -d ' \r')"
web_front="$(docker compose exec -T web sh -c "cd $WEB_ROOT && $TREE_DIGEST" _ . 2>/dev/null | tr -d '\r')"
if [[ "${nb_web:-0}" == 0 ]]; then
  ko "frontend : le conteneur web ne voit AUCUN fichier ($nb_host dans ./frontend) — montage orphelin après un changement de branche"
  fix "docker compose up -d --force-recreate web"
elif [[ "$web_front" != "$host_front" ]]; then
  ko "frontend : fichiers servis par Nginx ($nb_web) ≠ ./frontend ($nb_host)"
  fix "docker compose up -d --force-recreate web"
else
  ok "frontend servi identique à ./frontend ($nb_host fichiers)"
fi
host_back="$(cd backend && sh -c "$TREE_DIGEST" _ alembic.ini alembic app)"
img_back="$(docker compose exec -T backend sh -c "cd $APP_ROOT && $TREE_DIGEST" _ alembic.ini alembic app 2>/dev/null | tr -d '\r')"
if [[ -n "$img_back" && "$img_back" == "$host_back" ]]; then ok "image backend construite depuis le code du dépôt"
else ko "image backend ≠ code du dépôt (branche changée sans reconstruction ?)"
     fix "docker compose build backend && docker compose up -d backend"; fi
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"; head="$(git rev-parse --short HEAD 2>/dev/null)"
info "dépôt : branche ${branch:-?} @ ${head:-?}$( [[ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]] && echo ' (modifications locales non commitées)')"

echo "== Base de données et migrations"
alembic() { docker compose exec -T backend alembic "$@" 2>&1 | tr -d '\r'; }
current="$(alembic current | grep -oE '^[0-9a-z_]+( \(head\))?' | tail -n1)"
heads="$(alembic heads | grep -oE '^[0-9a-z_]+' )"
if [[ "$(wc -l <<<"$heads" | tr -d ' ')" != 1 || -z "$heads" ]]; then ko "Alembic : têtes multiples ou illisibles : $(tr '\n' ' ' <<<"$heads")"
elif [[ "$current" == "$heads (head)" ]]; then ok "Alembic à jour : révision $heads (tête unique)"
else ko "Alembic : base en ${current:-?}, code en $heads"; fix "./deploy/deploy-<lot>.sh (sauvegarde puis migration)"; fi
check="$(alembic check)"
if grep -q "No new upgrade operations detected" <<<"$check"; then ok "alembic check : modèles et base identiques"
else ko "alembic check : écart entre les modèles et la base"; info "$(tail -n 3 <<<"$check")"; fi
missing=()
for t in "${TRIGGERS[@]}"; do
  [[ "$(sql "SELECT count(*) FROM pg_trigger WHERE tgname = '$t' AND tgenabled <> 'D'")" == 1 ]] || missing+=("$t")
done
(( ${#missing[@]} == 0 )) && ok "déclencheurs d'immuabilité actifs (${#TRIGGERS[@]}) : paiements, dépenses, abonnements, audit, décisions" \
  || ko "déclencheurs absents ou désactivés : ${missing[*]}"

echo "== Aucun blocage réel"
bad="$(sql "SELECT count(*) FROM enforcement_decisions WHERE mode <> 'dry_run' OR enforced")"
[[ "$bad" == 0 ]] && ok "toutes les décisions sont simulées (dry_run, enforced=false)" || ko "décisions appliquées ou hors dry_run : ${bad:-illisible}"
info "décisions simulées enregistrées : $(sql "SELECT count(*) FROM enforcement_decisions" || echo ?)"

echo "== Agent et HMAC"
age_batch="$(sql "SELECT COALESCE(EXTRACT(EPOCH FROM now() - max(received_at))::int, -1) FROM agent_batches")"
[[ "$age_batch" =~ ^[0-9]+$ ]] && (( age_batch <= 300 )) && ok "dernier lot d'observations reçu il y a ${age_batch}s" \
  || ko "aucun lot récent (âge : ${age_batch:-?} s) — vérifier akwaba-agent sur l'Exit Node"
age_cred="$(sql "SELECT COALESCE(EXTRACT(EPOCH FROM now() - max(last_used_at))::int, -1) FROM agent_credentials WHERE revoked_at IS NULL")"
[[ "$age_cred" =~ ^[0-9]+$ ]] && (( age_cred <= 300 )) && ok "signature HMAC acceptée il y a ${age_cred}s (identifiant actif)" \
  || ko "aucune requête signée acceptée récemment (âge : ${age_cred:-?} s)"
refused="$(docker compose logs --no-color --since 1h backend 2>/dev/null | grep -c 'agent refusé' || true)"
info "refus d'authentification agent sur la dernière heure : ${refused:-0}"

echo "== Secrets"
perm="$(stat -c '%a' .env 2>/dev/null)"
[[ -n "$perm" && "${perm: -1}" == 0 ]] && ok ".env non lisible par les autres utilisateurs ($perm)" \
  || { ko ".env lisible par les autres utilisateurs (${perm:-absent})"; fix "chmod 600 .env"; }
git ls-files --error-unmatch .env >/dev/null 2>&1 && ko ".env est suivi par git" || ok ".env non suivi par git"
patterns="$TMP/secrets"
: >"$patterns"; chmod 600 "$patterns"
for k in SECRET_KEY FIELD_ENCRYPTION_KEY POSTGRES_PASSWORD; do
  v="$(env_value "$k")"; (( ${#v} >= 8 )) && printf '%s\n' "$v" >>"$patterns"
done
if [[ ! -s "$patterns" ]]; then
  ko "secrets de .env illisibles : contrôle de fuite impossible"
else
  for p in / /api/health /api/auth/me /inexistant; do curl -s --max-time 8 "$WEB_URL$p"; echo; done >"$TMP/public"
  grep -qF -f "$patterns" "$TMP/public" && ko "un secret de .env apparaît dans une réponse publique" \
    || ok "aucun secret de .env dans les réponses publiques"
  docker compose logs --no-color --since 24h backend web postgres >"$TMP/logs" 2>/dev/null
  grep -qF -f "$patterns" "$TMP/logs" && ko "un secret de .env apparaît dans les journaux des conteneurs (24 h)" \
    || ok "aucun secret de .env dans les journaux des conteneurs (24 h, $(wc -l <"$TMP/logs" | tr -d ' ') lignes)"
fi

echo
echo "Vérifications globales : $PASS OK, $FAILS échec(s)."
(( rc11a == 0 )) && echo "Lot 11a : OK" || echo "Lot 11a : échec(s) — voir plus haut."
(( FAILS == 0 && rc11a == 0 ))
