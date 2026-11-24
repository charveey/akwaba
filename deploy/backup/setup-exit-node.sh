#!/bin/sh
# AKWABA — réception des sauvegardes SUR L'EXIT NODE (Phase 14a). À lancer en root sur l'Exit Node :
#
#     sudo sh deploy/backup/setup-exit-node.sh 'ssh-ed25519 AAAA… akwaba-backup@serveur'
#
# La clé est celle affichée par setup-server.sh. Crée :
#   - l'utilisateur système akwaba-backup (aucun mot de passe) et /var/lib/akwaba-backup/archives (700) ;
#   - une autorisation SSH limitée à cette clé : restrict + rrsync -wo = ÉCRITURE SEULE dans archives/,
#     sans shell, sans redirection, sans lecture ni suppression (un serveur compromis ne peut ni relire ni
#     effacer les copies) ;
#   - la rotation locale (7 jours, 4 semaines, 6 mois) : akwaba-backup-prune.timer à 05:15 UTC.
# Les archives restent chiffrées (age) : l'Exit Node ne peut pas les lire. Rien n'est modifié dans l'agent
# Akwaba, Tailscale, sshd_config ni le reste du système.
set -eu

KEY="${1:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
USER_NAME=akwaba-backup
HOME_DIR=/var/lib/akwaba-backup
ARCHIVES="$HOME_DIR/archives"
RRSYNC="$(command -v rrsync || true)"

[ "$(id -u)" -eq 0 ] || { echo "à lancer en root (sudo)"; exit 1; }
case "$KEY" in
  "ssh-ed25519 "*) ;;
  *) echo "usage : sudo sh setup-exit-node.sh 'ssh-ed25519 AAAA… commentaire'"; exit 2 ;;
esac
printf '%s' "$KEY" | grep -qE '^ssh-ed25519 [A-Za-z0-9+/=]+( [A-Za-z0-9@._-]+)?$' || { echo "clé SSH invalide"; exit 2; }
[ -n "$RRSYNC" ] || { echo "rrsync absent : sudo apt install rsync (version 3.2.4 ou plus récente)"; exit 1; }
[ -x /usr/bin/python3 ] || { echo "/usr/bin/python3 introuvable"; exit 1; }
systemctl is-active --quiet ssh 2>/dev/null || systemctl is-active --quiet sshd 2>/dev/null \
  || echo "ATTENTION : le service ssh ne semble pas actif ; le serveur ne pourra pas déposer les copies."

if ! id -u "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$HOME_DIR" --shell /bin/sh "$USER_NAME"
fi
passwd -l "$USER_NAME" >/dev/null 2>&1 || true
install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR" "$ARCHIVES" "$HOME_DIR/.ssh"
TMP="$(mktemp)"
printf 'restrict,command="%s -wo %s" %s\n' "$RRSYNC" "$ARCHIVES" "$KEY" > "$TMP"
install -m 0600 -o "$USER_NAME" -g "$USER_NAME" "$TMP" "$HOME_DIR/.ssh/authorized_keys"
rm -f "$TMP"

install -d -m 0755 -o root -g root /usr/local/lib/akwaba-backup
install -m 0644 -o root -g root "$HERE/retention.py" /usr/local/lib/akwaba-backup/retention.py
install -m 0644 -o root -g root "$HERE/systemd/akwaba-backup-prune.service" "$HERE/systemd/akwaba-backup-prune.timer" \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now akwaba-backup-prune.timer

echo "prêt : $USER_NAME@$(hostname) reçoit les archives dans $ARCHIVES (écriture seule)."
echo "Vérifier depuis le serveur : ./deploy/backup/backup.sh   (étape 5/5 : copiée)"
