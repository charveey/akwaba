#!/bin/sh
# AKWABA — mise à jour de l'agent sur l'Exit Node (Phase 13c). À lancer en root SUR L'EXIT NODE.
#
#     sudo sh update.sh CHEMIN/vers/akwaba_agent            # met à jour
#     sudo sh update.sh CHEMIN/vers/akwaba_agent --plan     # vérifications seulement, rien n'est modifié
#
# Le code vient du dépôt (git pull sur l'Exit Node, ou copie : scp -r agent/akwaba_agent pi:/tmp/).
# Première installation : install.sh (utilisateur, sudoers, unité systemd) — ce script ne fait que le CODE.
#
# 1. Vérifications : installation existante, code nouveau complet et compilable par /usr/bin/python3.
# 2. Ping signé AVEC LE NOUVEAU CODE (copie temporaire, utilisateur de l'agent, configuration et secret
#    réels) : si la configuration ne se charge pas ou si le backend refuse, RIEN n'est modifié.
# 3. Bascule : code actuel conservé dans /opt/akwaba-agent/precedent, nouveau code installé (root, lecture seule).
# 4. Redémarrage du service, stabilité (actif, aucun redémarrage, aucune trace d'erreur), nouveau ping.
#    En cas d'échec : retour AUTOMATIQUE au code précédent et redémarrage.
#
# Ne modifie JAMAIS : agent.ini, le secret, la règle sudoers, l'unité systemd, le spool, la collecte
# (conntrack, whois, status), ni le backend. Les lots non envoyés restent dans le spool pendant le redémarrage.
set -eu

AGENT_HOME="${AGENT_HOME:-/opt/akwaba-agent}"
AGENT_CONF="${AGENT_CONF:-/etc/akwaba-agent/agent.ini}"
UNIT_FILE="${UNIT_FILE:-/etc/systemd/system/akwaba-agent.service}"
SERVICE="${SERVICE:-akwaba-agent}"
AGENT_USER="${AGENT_USER:-akwaba-agent}"
PYTHON="${PYTHON:-/usr/bin/python3}"
STABLE_SECONDS="${STABLE_SECONDS:-15}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CURRENT="$AGENT_HOME/akwaba_agent"
PREVIOUS="$AGENT_HOME/precedent/akwaba_agent"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  OK     %s\n' "$*"; }
fail() { printf '  ÉCHEC  %s\n' "$*"; }
stop() { printf '\nArrêt : %s\nRien n'"'"'a été modifié.\n' "$*"; cleanup; exit 1; }

SRC=""; PLAN=0
for a in "$@"; do
  case "$a" in
    --plan) PLAN=1 ;;
    -*) say "option inconnue : $a"; exit 2 ;;
    *) SRC="$a" ;;
  esac
done
STAGE=""
cleanup() { [ -n "$STAGE" ] && rm -rf "$STAGE"; return 0; }
trap cleanup EXIT

version_of() { sed -n 's/^__version__ = "\(.*\)"/\1/p' "$1/__init__.py" 2>/dev/null; }

# Ping signé, exécuté par l'utilisateur de l'agent avec le code situé dans $1 (répertoire parent du paquet).
ping_with() {
  sudo -u "$AGENT_USER" env PYTHONPATH="$1" PYTHONDONTWRITEBYTECODE=1 \
    "$PYTHON" -m akwaba_agent --config "$AGENT_CONF" --ping 2>&1
}

service_stable() {
  restarts_before="$(systemctl show -p NRestarts --value "$SERVICE" 2>/dev/null || echo 0)"
  sleep "$STABLE_SECONDS"
  [ "$(systemctl is-active "$SERVICE" 2>/dev/null)" = active ] || { fail "service inactif après redémarrage"; return 1; }
  restarts_after="$(systemctl show -p NRestarts --value "$SERVICE" 2>/dev/null || echo 0)"
  [ "$restarts_after" = "$restarts_before" ] || { fail "le service a redémarré tout seul ($restarts_before → $restarts_after)"; return 1; }
  if journalctl -u "$SERVICE" --since "@$RESTARTED_AT" --no-pager -q 2>/dev/null | grep -qE 'Traceback|configuration invalide'; then
    fail "erreurs dans le journal depuis le redémarrage (journalctl -u $SERVICE --since @$RESTARTED_AT)"; return 1
  fi
  ok "service actif et stable depuis ${STABLE_SECONDS}s, journal sans erreur"
}

say "== 1/4 Vérifications"
[ "$(id -u)" -eq 0 ] || { say "à lancer en root (sudo)"; exit 1; }
{ [ -n "$SRC" ] && [ -f "$SRC/__main__.py" ] && [ -f "$SRC/__init__.py" ]; } \
  || { say "usage : sudo sh update.sh CHEMIN/vers/akwaba_agent [--plan]"; exit 2; }
SRC="$(cd "$SRC" && pwd)"
if ! { [ -f "$CURRENT/__main__.py" ] && [ -f "$UNIT_FILE" ] && [ -f "$AGENT_CONF" ]; }; then
  stop "agent non installé sur cette machine : utiliser d'abord install.sh"
fi
id -u "$AGENT_USER" >/dev/null 2>&1 || stop "utilisateur $AGENT_USER absent : utiliser install.sh"
[ -x "$PYTHON" ] || stop "$PYTHON introuvable"
ok "installation existante : $CURRENT (version $(version_of "$CURRENT"))"

if diff -rq -x __pycache__ -x '*.pyc' "$CURRENT" "$SRC" >/dev/null 2>&1; then
  say "Le code installé est déjà identique à $SRC : rien à faire."
  exit 0
fi
changed="$(diff -rq -x __pycache__ -x '*.pyc' "$CURRENT" "$SRC" 2>/dev/null | wc -l | tr -d ' ')"
ok "nouveau code : version $(version_of "$SRC"), $changed fichier(s) différent(s)"

STAGE="$(mktemp -d "$AGENT_HOME/.maj-XXXXXX")"
chmod 0755 "$STAGE"
cp -r "$SRC" "$STAGE/akwaba_agent"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
chown -R root:root "$STAGE"
chmod -R u=rwX,go=rX "$STAGE"
"$PYTHON" - "$STAGE/akwaba_agent" <<'PY' || stop "le nouveau code ne compile pas avec $PYTHON"
import os, sys
for root, _, files in os.walk(sys.argv[1]):
    for f in files:
        if f.endswith(".py"):
            p = os.path.join(root, f)
            compile(open(p, encoding="utf-8").read(), p, "exec")
PY
ok "nouveau code compilable par $("$PYTHON" -V 2>&1)"

if ! cmp -s "$HERE/akwaba-agent.service" "$UNIT_FILE" 2>/dev/null; then
  say "  INFO   l'unité systemd du dépôt diffère de celle installée : elle n'est PAS modifiée ici (voir install.sh)."
fi

say "== 2/4 Ping signé avec le NOUVEAU code (configuration et secret réels)"
out="$(ping_with "$STAGE")" || true
say "  $out"
case "$out" in
  ping=ok*) ok "le nouveau code charge la configuration et s'authentifie auprès du backend" ;;
  *) stop "le nouveau code ne peut pas joindre ou s'authentifier auprès du backend" ;;
esac

if [ "$PLAN" -eq 1 ]; then
  say ""
  say "Mode --plan : vérifications réussies. Étapes qui seraient exécutées :"
  say "  3. $CURRENT → $PREVIOUS ; nouveau code installé"
  say "  4. systemctl restart $SERVICE ; stabilité ${STABLE_SECONDS}s ; ping ; retour automatique si échec"
  exit 0
fi

say "== 3/4 Bascule du code"
mkdir -p "$AGENT_HOME/precedent"
rm -rf "$PREVIOUS"
mv "$CURRENT" "$PREVIOUS"
mv "$STAGE/akwaba_agent" "$CURRENT"
rmdir "$STAGE"; STAGE=""
ok "code précédent conservé : $PREVIOUS"

rollback() {
  say ""
  say "== Retour automatique au code précédent"
  rm -rf "$AGENT_HOME/echec"; mkdir -p "$AGENT_HOME/echec"
  mv "$CURRENT" "$AGENT_HOME/echec/akwaba_agent"
  mv "$PREVIOUS" "$CURRENT"
  RESTARTED_AT="$(date +%s)"
  systemctl restart "$SERVICE"
  if service_stable && ping_with "$AGENT_HOME" | grep -q '^ping=ok'; then
    say "Code précédent rétabli et fonctionnel. Nouveau code conservé pour analyse : $AGENT_HOME/echec"
  else
    say "ATTENTION : le code précédent ne fonctionne pas non plus — vérifier : systemctl status $SERVICE"
  fi
  exit 1
}

say "== 4/4 Redémarrage et contrôle"
RESTARTED_AT="$(date +%s)"
systemctl restart "$SERVICE" || rollback
service_stable || rollback
out="$(ping_with "$AGENT_HOME")" || true
say "  $out"
case "$out" in
  ping=ok*) ok "ping signé avec le code installé" ;;
  *) fail "ping en échec après la bascule"; rollback ;;
esac
say ""
say "Agent mis à jour : version $(version_of "$CURRENT"). Code précédent : $PREVIOUS"
say "Côté serveur, ./deploy/verify-all.sh confirme la réception des lots (section « Agent et HMAC »)."
