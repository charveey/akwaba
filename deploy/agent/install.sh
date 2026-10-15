#!/bin/sh
# Installe l'agent Akwaba : utilisateur dédié, code en lecture seule, règle sudoers, unité systemd.
# N'active ni ne démarre rien.   Usage : sudo sh install.sh CHEMIN/vers/akwaba_agent
set -eu

[ "$(id -u)" -eq 0 ] || { echo "à lancer en root (sudo)"; exit 1; }
SRC="${1:-}"
{ [ -d "$SRC" ] && [ -f "$SRC/__main__.py" ]; } || { echo "usage : install.sh CHEMIN/vers/akwaba_agent"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
CT="$(command -v conntrack)" || { echo "conntrack introuvable"; exit 1; }
[ -x /usr/bin/python3 ] || { echo "/usr/bin/python3 introuvable"; exit 1; }

if ! id -u akwaba-agent >/dev/null 2>&1; then
  useradd --system --user-group --no-create-home --home-dir /nonexistent \
          --shell /usr/sbin/nologin akwaba-agent
fi

install -d -m 0755 -o root -g root /opt/akwaba-agent
rm -rf /opt/akwaba-agent/akwaba_agent
cp -r "$SRC" /opt/akwaba-agent/akwaba_agent
find /opt/akwaba-agent -name __pycache__ -type d -prune -exec rm -rf {} +
chown -R root:root /opt/akwaba-agent
chmod -R u=rwX,go=rX /opt/akwaba-agent

TMP="$(mktemp)"
cat > "$TMP" <<EOF
Defaults:akwaba-agent !requiretty
Defaults:akwaba-agent !log_allowed
akwaba-agent ALL=(root) NOPASSWD: $CT -L -f ipv4, $CT -L -f ipv6
EOF
visudo -cf "$TMP" >/dev/null || { echo "règle sudoers invalide, abandon"; rm -f "$TMP"; exit 1; }
install -m 0440 -o root -g root "$TMP" /etc/sudoers.d/akwaba-agent
rm -f "$TMP"

install -m 0644 -o root -g root "$HERE/akwaba-agent.service" /etc/systemd/system/akwaba-agent.service
systemctl daemon-reload

echo "installé. Rien n'est activé ni démarré."
