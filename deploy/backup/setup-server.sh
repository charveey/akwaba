#!/usr/bin/env bash
# AKWABA — configuration des sauvegardes SUR LE SERVEUR (Phase 14a). À lancer par l'utilisateur du dépôt
# (celui qui lance docker compose), PAS en root ; sudo n'est demandé que pour installer le minuteur systemd.
#
#     ./deploy/backup/setup-server.sh --recipient age1… --exit-node <nom-tailnet-de-l-exit-node>
#     ./deploy/backup/setup-server.sh --recipient age1…                # sans copie sur l'Exit Node
#     ... --sans-systemd                                               # ne pas installer le minuteur
#
# Avant : sur VOTRE poste (jamais sur le serveur) :  age-keygen -o akwaba-backup.key
#         → garder ce fichier en lieu sûr (sans lui, aucune restauration possible) ;
#           sa ligne « public key: age1… » est le --recipient.
#
# Crée backups/config/ (700) : age-recipients.txt (clé PUBLIQUE), id_ed25519 (clé SSH dédiée à la copie),
# offsite.conf ; installe akwaba-backup.service/.timer (03:15 UTC). Ne lance aucune sauvegarde,
# ne modifie ni .env, ni docker-compose.yml, ni les conteneurs, ni Tailscale.
set -euo pipefail

cd "$(dirname "$0")/../.." || exit 1
REPO="$(pwd)"
CONFIG_DIR="${CONFIG_DIR:-backups/config}"
RECIPIENT=""; EXIT_NODE=""; REMOTE_USER="akwaba-backup"; SYSTEMD=1
while (( $# )); do
  case "$1" in
    --recipient) RECIPIENT="${2:-}"; shift 2 ;;
    --exit-node) EXIT_NODE="${2:-}"; shift 2 ;;
    --remote-user) REMOTE_USER="${2:-}"; shift 2 ;;
    --sans-systemd) SYSTEMD=0; shift ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
die() { echo "ERREUR : $*" >&2; exit 1; }

[[ "$(id -u)" != 0 ]] || die "à lancer par l'utilisateur du dépôt, pas en root"
[[ "$RECIPIENT" =~ ^age1[02-9ac-hj-np-z]{58}$ ]] || die "--recipient doit être une clé publique age (age1…, 62 caractères)"
command -v age >/dev/null || die "age absent : sudo apt install age"
command -v rsync >/dev/null || die "rsync absent : sudo apt install rsync"
if [[ -n "$EXIT_NODE" ]]; then
  { command -v ssh >/dev/null && command -v ssh-keygen >/dev/null; } || die "client OpenSSH absent : sudo apt install openssh-client"
fi
docker compose ps -q postgres >/dev/null 2>&1 || die "docker compose inaccessible pour $(id -un) (groupe docker ?)"
if [[ -n "$EXIT_NODE" && ! "$EXIT_NODE" =~ ^[A-Za-z0-9.-]+$ ]]; then die "--exit-node : nom d'hôte invalide"; fi
if [[ ! "$REMOTE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then die "--remote-user invalide"; fi

mkdir -p "$CONFIG_DIR"; chmod 700 backups "$CONFIG_DIR"
( umask 077; printf '%s\n' "$RECIPIENT" > "$CONFIG_DIR/age-recipients.txt" )
echo "clé publique age enregistrée : $CONFIG_DIR/age-recipients.txt"

if [[ -n "$EXIT_NODE" ]]; then
  if [[ ! -f "$CONFIG_DIR/id_ed25519" ]]; then
    ssh-keygen -q -t ed25519 -N "" -C "akwaba-backup@$(hostname)" -f "$CONFIG_DIR/id_ed25519"
  fi
  chmod 600 "$CONFIG_DIR/id_ed25519"
  ( umask 077; printf 'OFFSITE_HOST=%s\nOFFSITE_USER=%s\n' "$EXIT_NODE" "$REMOTE_USER" > "$CONFIG_DIR/offsite.conf" )
  echo
  echo "Copie sur l'Exit Node : $REMOTE_USER@$EXIT_NODE. SUR L'EXIT NODE, lancer :"
  echo "  sudo sh deploy/backup/setup-exit-node.sh '$(cat "$CONFIG_DIR/id_ed25519.pub")'"
else
  rm -f "$CONFIG_DIR/offsite.conf"
  echo "copie sur l'Exit Node : non configurée"
fi

if (( SYSTEMD )); then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  for unit in akwaba-backup.service akwaba-backup.timer; do
    sed -e "s|__USER__|$(id -un)|g" -e "s|__REPO__|$REPO|g" "deploy/backup/systemd/$unit" > "$tmp/$unit"
  done
  sudo install -m 0644 -o root -g root "$tmp/akwaba-backup.service" "$tmp/akwaba-backup.timer" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now akwaba-backup.timer
  echo "minuteur installé : $(systemctl list-timers akwaba-backup.timer --no-pager --no-legend | awk '{print $1, $2, $3}')"
fi
echo
echo "Première sauvegarde (avec test de restauration) : ./deploy/backup/backup.sh --test-restore"
