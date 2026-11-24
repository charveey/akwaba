#!/usr/bin/env bash
# AKWABA — Phase 11a : expose l'administration en HTTPS sur le tailnet (port 8443).
#
#     sudo ./deploy/tailscale/serve-8443.sh             # ajoute 8443 → http://127.0.0.1:8080
#     sudo ./deploy/tailscale/serve-8443.sh --check     # affiche l'état, ne modifie rien
#     sudo ./deploy/tailscale/serve-8443.sh --remove    # retire UNIQUEMENT 8443
#
# Règles :
#   * N'exécute JAMAIS `tailscale serve reset` ni `tailscale funnel ...`.
#   * Sauvegarde `tailscale serve status --json` avant toute modification.
#   * Refuse de continuer si 8443 est déjà utilisé par autre chose.
#   * Vérifie après coup que la configuration du port 443 (Funnel) est STRICTEMENT
#     identique et que 8443 n'est PAS exposé par Funnel (tailnet uniquement).
set -euo pipefail

PORT=8443
TARGET="http://127.0.0.1:8080"
MODE="add"
case "${1:-}" in
  --check) MODE="check" ;;
  --remove) MODE="remove" ;;
  "") ;;
  *) echo "usage : $0 [--check|--remove]" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/../.."
log()  { printf '\033[1m[serve]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[serve] ERREUR : %s\033[0m\n' "$*" >&2; exit 1; }
command -v tailscale >/dev/null || fail "tailscale introuvable"
command -v python3 >/dev/null || fail "python3 introuvable"

status_json() { tailscale serve status --json 2>/dev/null || echo '{}'; }

# Résumé déterministe d'une configuration pour un port : TCP, handlers Web, AllowFunnel.
port_fingerprint() {
  PORT_ARG="$1" python3 -c '
import json, os, sys
cfg = json.load(sys.stdin) or {}
port = os.environ["PORT_ARG"]
out = {
    "tcp": (cfg.get("TCP") or {}).get(port),
    "web": {k: v for k, v in (cfg.get("Web") or {}).items() if k.rsplit(":", 1)[-1] == port},
    "funnel": {k: v for k, v in (cfg.get("AllowFunnel") or {}).items() if k.rsplit(":", 1)[-1] == port},
}
print(json.dumps(out, sort_keys=True))
'
}

# 8443 pointe-t-il déjà vers la cible attendue ? "absent" | "ours" | "other"
port_state() {
  PORT_ARG="$PORT" TARGET_ARG="$TARGET" python3 -c '
import json, os, sys
cfg = json.load(sys.stdin) or {}
port, target = os.environ["PORT_ARG"], os.environ["TARGET_ARG"]
tcp = (cfg.get("TCP") or {}).get(port)
web = {k: v for k, v in (cfg.get("Web") or {}).items() if k.rsplit(":", 1)[-1] == port}
if not tcp and not web:
    print("absent"); sys.exit()
handlers = [h for site in web.values() for h in (site.get("Handlers") or {}).values()]
ours = (tcp or {}).get("HTTPS") and handlers and all(
    h.get("Proxy", "").rstrip("/") in (target, target.replace("127.0.0.1", "localhost")) for h in handlers)
print("ours" if ours else "other")
'
}

before="$(status_json)"
mkdir -p backups && chmod 700 backups
backup="backups/tailscale-serve-$(date -u +%Y%m%dT%H%M%SZ).json"
printf '%s\n' "$before" > "$backup"
chmod 600 "$backup"
fp443_before="$(port_fingerprint 443 <<<"$before")"
state="$(port_state <<<"$before")"

log "Sauvegarde : $backup"
log "443 (Funnel, inchangé) : $fp443_before"
log "$PORT : $state"

if [[ "$MODE" == "check" ]]; then
  tailscale serve status || true
  exit 0
fi

if [[ "$MODE" == "remove" ]]; then
  [[ "$state" == "absent" ]] && { log "$PORT déjà absent."; exit 0; }
  [[ "$state" == "ours" ]] || fail "$PORT sert autre chose qu'Akwaba : retrait refusé"
  tailscale serve --https="$PORT" off
else
  case "$state" in
    ours)   log "$PORT pointe déjà vers $TARGET : rien à faire." ;;
    other)  fail "$PORT est déjà utilisé par une autre configuration : rien n'a été modifié" ;;
    absent)
      code="$(curl -s -o /dev/null -w '%{http_code}' "$TARGET/" || true)"
      [[ "$code" == "200" ]] || fail "Nginx ne répond pas sur $TARGET (code $code) : lancer d'abord deploy/migrate-network-11a.sh"
      tailscale serve --bg --https="$PORT" "$TARGET"
      ;;
  esac
fi

after="$(status_json)"
fp443_after="$(port_fingerprint 443 <<<"$after")"
if [[ "$fp443_after" != "$fp443_before" ]]; then
  fail "LA CONFIGURATION DU PORT 443 A CHANGÉ. Avant : $fp443_before / Après : $fp443_after.
Sauvegarde : $backup. Retirer 8443 avec : sudo $0 --remove"
fi
fp_port="$(port_fingerprint "$PORT" <<<"$after")"
if [[ "$MODE" == "add" ]]; then
  [[ "$(port_state <<<"$after")" == "ours" ]] || fail "$PORT n'est pas configuré comme attendu : $fp_port"
  PORT_ARG="$PORT" python3 -c '
import json, os, sys
cfg = json.load(sys.stdin) or {}
exposed = [k for k, v in (cfg.get("AllowFunnel") or {}).items() if v and k.rsplit(":", 1)[-1] == os.environ["PORT_ARG"]]
sys.exit(1 if exposed else 0)
' <<<"$after" || fail "$PORT est exposé par Funnel (Internet) : retirer immédiatement avec sudo $0 --remove"
fi

log "OK. 443 identique ; $PORT : $(port_state <<<"$after") ; Funnel non activé sur $PORT."
tailscale serve status || true
