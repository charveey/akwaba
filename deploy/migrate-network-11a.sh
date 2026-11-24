#!/usr/bin/env bash
# AKWABA — Phase 11a : bascule vers le réseau Docker à sous-réseau fixe + service web (Nginx).
#
# À lancer SUR LE SERVEUR, depuis la racine du dépôt, après `git pull` de la branche :
#
#     ./deploy/migrate-network-11a.sh --check     # contrôles seuls, aucune modification
#     ./deploy/migrate-network-11a.sh             # migration
#
# Garanties :
#   * le volume PostgreSQL n'est JAMAIS supprimé (`docker compose down` sans -v) ;
#   * sauvegarde pg_dump vérifiée AVANT tout arrêt ;
#   * même volume (nom + date de création) et mêmes comptages après redémarrage ;
#   * aucune action sur Tailscale (Funnel 443 / Serve) ni sur l'agent ;
#   * REVOCATION_MODE=dry_run et ENFORCER_ENABLED=false exigés.
#
# Interruption : l'agent met ses envois en file d'attente locale (spool) pendant
# la coupure (~1 min) et les renvoie ensuite.
set -euo pipefail

SUBNET="10.250.11.0/24"
WEB_IP="10.250.11.10"
PROJECT_NETWORK="akwaba_internal"
CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

cd "$(dirname "$0")/.."
log()  { printf '\033[1m[11a]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[11a] ERREUR : %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f docker-compose.yml ]] || fail "docker-compose.yml introuvable"
[[ -f .env ]] || fail ".env introuvable"
command -v docker >/dev/null || fail "docker introuvable"
command -v python3 >/dev/null || fail "python3 introuvable (contrôle de chevauchement)"

env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | tr -d '"'"'"; }

# --- 1. Configuration ---------------------------------------------------------
log "1/8 Contrôle de .env"
[[ "$(env_value REVOCATION_MODE)" == "dry_run" ]] || fail "REVOCATION_MODE doit valoir dry_run"
[[ "$(env_value ENFORCER_ENABLED)" == "false" ]] || fail "ENFORCER_ENABLED doit valoir false"
origins="$(env_value ALLOWED_ORIGINS)"
[[ -n "$origins" ]] || fail "ALLOWED_ORIGINS manquant dans .env (ex. https://ubuntu.tail14614.ts.net:8443) : le backend ne démarrerait pas"
[[ "$origins" =~ ^https:// ]] || fail "ALLOWED_ORIGINS doit commencer par https://"
docker compose config --quiet || fail "docker compose config invalide"
log "    ALLOWED_ORIGINS=$origins"

# --- 2. Chevauchement de sous-réseau -------------------------------------------
log "2/8 Chevauchement avec $SUBNET (routes de l'hôte, Tailscale, réseaux Docker)"
other_nets="$(docker network ls -q | while read -r id; do
  name="$(docker network inspect -f '{{.Name}}' "$id")"
  [[ "$name" == "$PROJECT_NETWORK" ]] && continue
  docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$id" | tr ' ' '\n' | sed "s|^|$name |"
done)"
host_routes="$(ip -4 route show table all 2>/dev/null | awk '{print $1}' | grep -E '^[0-9]' || true)"
SUBNET="$SUBNET" OTHER="$other_nets" ROUTES="$host_routes" python3 - <<'PY' || fail "chevauchement détecté : choisir un autre sous-réseau dans docker-compose.yml ET deploy/nginx/conf.d/akwaba.conf"
import ipaddress, os, sys
target = ipaddress.ip_network(os.environ["SUBNET"])
bad = []
for line in os.environ["OTHER"].splitlines():
    parts = line.split()
    if len(parts) == 2:
        try:
            if ipaddress.ip_network(parts[1], strict=False).overlaps(target):
                bad.append(f"réseau Docker {parts[0]} ({parts[1]})")
        except ValueError:
            pass
for r in os.environ["ROUTES"].split():
    try:
        net = ipaddress.ip_network(r, strict=False)
    except ValueError:
        continue
    if net.prefixlen > 0 and net.overlaps(target) and not net.subnet_of(target):
        bad.append(f"route hôte {r}")
for b in bad:
    print("   chevauchement :", b, file=sys.stderr)
sys.exit(1 if bad else 0)
PY
log "    aucun chevauchement"

# --- 3. Volume PostgreSQL ---------------------------------------------------------
log "3/8 Volume PostgreSQL"
pg_cid="$(docker compose ps -q postgres || true)"
[[ -n "$pg_cid" ]] || fail "le conteneur postgres ne tourne pas : rien à migrer en sécurité"
volume="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$pg_cid")"
[[ -n "$volume" ]] || fail "volume de données PostgreSQL introuvable"
vol_created="$(docker volume inspect -f '{{.CreatedAt}}' "$volume")"
log "    volume=$volume créé=$vol_created"

pg_user="$(env_value POSTGRES_USER)"; pg_db="$(env_value POSTGRES_DB)"
COUNT_SQL="SELECT 'admin_users', count(*) FROM admin_users UNION ALL SELECT 'members', count(*) FROM members
  UNION ALL SELECT 'payments', count(*) FROM payments UNION ALL SELECT 'audit_logs', count(*) FROM audit_logs
  UNION ALL SELECT 'agent_heartbeats', count(*) FROM agent_heartbeats ORDER BY 1"
counts() { docker compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -At -c "$COUNT_SQL"; }
before_counts="$(counts)" || fail "lecture de la base impossible"
echo "$before_counts" | sed 's/^/    /'

if (( CHECK_ONLY )); then
  log "Contrôles terminés (--check) : aucune modification effectuée."
  exit 0
fi

# --- 4. Sauvegarde -------------------------------------------------------------------
log "4/8 Sauvegarde pg_dump"
mkdir -p backups && chmod 700 backups
dump="backups/akwaba-pre-11a-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" -Fc > "$dump"
[[ -s "$dump" ]] || fail "sauvegarde vide"
docker compose exec -T postgres pg_restore --list < "$dump" > /dev/null || fail "sauvegarde illisible"
chmod 600 "$dump"
log "    $dump ($(du -h "$dump" | cut -f1))"

# --- 5. Image Nginx --------------------------------------------------------------------
log "5/8 Téléchargement de l'image web"
docker compose pull web

# --- 6. Arrêt / redémarrage (SANS -v) ---------------------------------------------------
log "6/8 Arrêt de la pile (le volume est conservé)"
docker compose down --remove-orphans
docker volume inspect "$volume" > /dev/null || fail "VOLUME DISPARU — restaurer depuis $dump"

log "7/8 Démarrage"
docker compose up -d
for _ in $(seq 1 60); do
  unhealthy=0
  for svc in postgres backend web; do
    cid="$(docker compose ps -q "$svc")"
    [[ -n "$cid" ]] || { unhealthy=1; continue; }
    [[ "$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)" == "healthy" ]] || unhealthy=1
  done
  (( unhealthy == 0 )) && break
  sleep 3
done
(( unhealthy == 0 )) || fail "services non sains après 3 min : docker compose ps / docker compose logs backend web"

# --- 8. Vérifications ---------------------------------------------------------------------
log "8/8 Vérifications"
pg_cid="$(docker compose ps -q postgres)"
[[ "$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$pg_cid")" == "$volume" ]] \
  || fail "postgres n'utilise plus le volume $volume"
[[ "$(docker volume inspect -f '{{.CreatedAt}}' "$volume")" == "$vol_created" ]] || fail "le volume a été recréé"
after_counts="$(counts)"
[[ "$after_counts" == "$before_counts" ]] || {
  # Seuls les battements de cœur de l'agent peuvent avoir augmenté (envois en attente rejoués).
  diff <(echo "$before_counts" | grep -v agent_heartbeats) <(echo "$after_counts" | grep -v agent_heartbeats) \
    || fail "comptages différents après redémarrage"
}
subnet_now="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$PROJECT_NETWORK")"
[[ "$subnet_now" == "$SUBNET" ]] || fail "sous-réseau inattendu : $subnet_now"
web_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(docker compose ps -q web)")"
[[ "$web_ip" == "$WEB_IP" ]] || fail "IP de web inattendue : $web_ip"

log "Migration réussie : volume $volume conservé, données identiques, web=$WEB_IP."
log "Étapes suivantes :"
log "  sudo ./deploy/tailscale/serve-8443.sh"
log "  ./deploy/verify-11a.sh"
