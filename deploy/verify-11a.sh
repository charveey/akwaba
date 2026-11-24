#!/usr/bin/env bash
# AKWABA — Phase 11a : vérifications après déploiement, SUR LE SERVEUR.
# Aucune modification : lectures, requêtes GET/POST locales et `ss` uniquement.
#
#     ./deploy/verify-11a.sh
#     NEXT_APP_DIR=/chemin/vers/app-nextjs ./deploy/verify-11a.sh   # + inventaire des routes /api de Next.js
#
# Les contrôles depuis UN AUTRE appareil sont listés à la fin (à faire à la main).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
PASS=0; FAILS=0
ok()   { printf '  \033[32mOK\033[0m     %s\n' "$*"; PASS=$((PASS+1)); }
ko()   { printf '  \033[31mÉCHEC\033[0m  %s\n' "$*"; FAILS=$((FAILS+1)); }
info() { printf '  \033[2m%s\033[0m\n' "$*"; }
env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@" || echo 000; }

TAILNET_IP="$(env_value TAILNET_BIND_IP)"
HOST="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || echo ubuntu.tail14614.ts.net)"

echo "== Conteneurs"
for svc in postgres backend web; do
  cid="$(docker compose ps -q "$svc")"
  st="$( [[ -n "$cid" ]] && docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo absent)"
  [[ "$st" == "healthy" ]] && ok "$svc : healthy" || ko "$svc : $st"
done

echo "== Ports en écoute sur l'hôte"
listen="$(ss -Hltn 2>/dev/null | awk '{print $4}')"
web_binds="$(grep -E ':8080$' <<<"$listen" | sort -u | tr '\n' ' ')"
[[ "$web_binds" == "127.0.0.1:8080 " ]] && ok "8080 uniquement sur 127.0.0.1" || ko "8080 écoute sur : ${web_binds:-rien}"
grep -qE ':5432$' <<<"$listen" && ko "5432 publié sur l'hôte" || ok "PostgreSQL non publié sur l'hôte"
be_binds="$(grep -E ':8000$' <<<"$listen" | sort -u | tr '\n' ' ')"
[[ "$be_binds" == *"127.0.0.1:8000"* && "$be_binds" == *"$TAILNET_IP:8000"* ]] \
  && ok "8000 inchangé (127.0.0.1 + $TAILNET_IP) : $be_binds" || ko "8000 écoute sur : ${be_binds:-rien}"
grep -qE '^(0\.0\.0\.0|\*|\[::\]):(8000|8080)$' <<<"$listen" && ko "8000/8080 sur toutes les interfaces" || ok "ni 8000 ni 8080 sur 0.0.0.0"

echo "== Nginx (127.0.0.1:8080)"
[[ "$(code http://127.0.0.1:8080/)" == 200 ]] && ok "/ → 200" || ko "/ ne répond pas 200"
csp="$(curl -s -D - -o /dev/null http://127.0.0.1:8080/ | grep -i '^content-security-policy:' || true)"
[[ "$csp" == *"script-src 'self'"* && "$csp" != *unsafe-inline* && "$csp" != *unsafe-eval* ]] && ok "CSP stricte présente" || ko "CSP absente ou permissive"
[[ "$(code http://127.0.0.1:8080/api/health)" == 200 ]] && ok "/api/health via Nginx → 200" || ko "/api/health via Nginx"
[[ "$(code -X POST http://127.0.0.1:8080/api/agent/ping)" == 404 ]] && ok "/api/agent/ping via Nginx → 404" || ko "/api/agent/ via Nginx n'est pas bloqué"
[[ "$(code http://127.0.0.1:8080/inexistant)" == 404 ]] && ok "route inconnue → 404" || ko "route inconnue"
c="$(code -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8080/api/auth/login)"
[[ "$c" == 403 ]] && ok "connexion sans Origin → 403 (vérification d'origine active)" \
  || ko "connexion sans Origin → $c (attendu 403)"
if docker compose exec -T backend test -f /srv/app/core/origin.py 2>/dev/null; then
  ok "image backend à jour (Phase 11a)"
else
  ko "image backend antérieure à la Phase 11a : docker compose build backend && docker compose up -d backend"
fi


echo "== Backend direct (agent)"
[[ "$(code "http://$TAILNET_IP:8000/api/health")" == 200 ]] && ok "http://$TAILNET_IP:8000/api/health → 200" || ko "backend injoignable sur $TAILNET_IP:8000"
c="$(code -X POST "http://$TAILNET_IP:8000/api/agent/ping")"
[[ "$c" == 401 ]] && ok "/api/agent/ping direct sans signature → 401 (HMAC/liste d'IP inchangés, pas 403 d'origine)" || ko "/api/agent/ping direct → $c (attendu 401)"
pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
age="$(docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -c \
  "SELECT COALESCE(EXTRACT(EPOCH FROM now() - max(received_at))::int, -1) FROM agent_heartbeats" 2>/dev/null || echo -1)"
if [[ "$age" =~ ^[0-9]+$ ]] && (( age <= 180 )); then ok "dernier heartbeat de l'agent il y a ${age}s"
else ko "aucun heartbeat récent de l'agent (âge : $age s) — vérifier akwaba-agent sur l'Exit Node"; fi

echo "== Mode observation"
modes="$(docker compose exec -T backend sh -c 'echo "$REVOCATION_MODE/$ENFORCER_ENABLED"' 2>/dev/null)"
[[ "$modes" == "dry_run/false" ]] && ok "REVOCATION_MODE=dry_run, ENFORCER_ENABLED=false" || ko "mode inattendu : $modes"

echo "== Tailscale"
if command -v tailscale >/dev/null; then
  sj="$(tailscale serve status --json 2>/dev/null || echo '{}')"
  python3 -c '
import json, sys
c = json.load(sys.stdin) or {}
tcp = c.get("TCP") or {}
fun = [k for k, v in (c.get("AllowFunnel") or {}).items() if v]
print("443" in tcp, "8443" in tcp, any(k.endswith(":443") for k in fun), any(k.endswith(":8443") for k in fun))
' <<<"$sj" | { read -r s443 s8443 f443 f8443
    [[ "$s443" == True && "$f443" == True ]] && ok "443 servi et exposé par Funnel (inchangé)" || ko "443 / Funnel : servi=$s443 funnel=$f443"
    [[ "$s8443" == True ]] && ok "8443 servi (tailscale serve)" || ko "8443 non configuré : sudo ./deploy/tailscale/serve-8443.sh"
    [[ "$f8443" == False ]] && ok "8443 NON exposé par Funnel" || ko "8443 exposé sur Internet par Funnel !"
  }
  [[ "$(code "https://$HOST:8443/")" == 200 ]] && ok "https://$HOST:8443/ → 200" || ko "https://$HOST:8443/ injoignable"
  c443="$(code "https://$HOST/")"
  [[ "$c443" != 000 ]] && ok "Funnel https://$HOST/ répond ($c443)" || ko "Funnel https://$HOST/ ne répond pas"
else
  ko "commande tailscale absente"
fi

echo "== Application Next.js (port 443) et cookie /api — inventaire passif"
info "Le cookie akwaba_session (path=/api) est envoyé par le navigateur à https://$HOST/api/* (les cookies ignorent le port)."
for p in /api /api/ /api/auth/me /api/health; do
  info "GET https://$HOST$p → $(code "https://$HOST$p")  (404 partout = probablement aucune route /api)"
done
if [[ -n "${NEXT_APP_DIR:-}" && -d "$NEXT_APP_DIR" ]]; then
  routes="$(find "$NEXT_APP_DIR" -path '*/node_modules' -prune -o \( -path '*/app/api/*' -o -path '*/pages/api/*' -o -path '*/src/app/api/*' -o -path '*/src/pages/api/*' \) -type f -print 2>/dev/null)"
  if [[ -z "$routes" ]]; then ok "aucune route /api dans $NEXT_APP_DIR"
  else ko "routes /api trouvées dans l'application Next.js (elles recevront le cookie admin) :"; sed 's/^/           /' <<<"$routes"; fi
else
  info "NEXT_APP_DIR non fourni : inventaire du code Next.js non effectué."
fi

echo
echo "Résultat : $PASS OK, $FAILS échec(s)."
cat <<EOF

À vérifier depuis UN AUTRE appareil :
  1. Appareil du tailnet :   https://$HOST:8443/  → page de connexion Akwaba ; se connecter, se déconnecter.
  2. Appareil du tailnet :   curl -m5 http://$TAILNET_IP:8080/   → doit ÉCHOUER (8080 non exposé).
  3. Hors tailnet (4G) :     https://$HOST:8443/  → doit ÉCHOUER ;  https://$HOST/  → application publique OK.
  4. Navigateur (F12 > Console) sur https://$HOST:8443/ : aucune erreur CSP.
EOF
(( FAILS == 0 ))
