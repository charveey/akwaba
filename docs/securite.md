# Sécurité

## Exposition réseau

- AKWABA n'ajoute aucun port public : l'administration passe par le tailnet uniquement
  (`tailscale serve --https=8443` → `127.0.0.1:8080`, jamais de Funnel sur 8443).
- L'application Next.js voisine est exposée par Tailscale Funnel sur 443 : hors périmètre AKWABA, **jamais
  modifiée** (aucun script ne lance `tailscale serve reset` ni ne touche à 443).
- PostgreSQL n'a aucun port publié. Le backend écoute sur `127.0.0.1:8000` et sur l'IP tailnet (pour l'agent).
- À vérifier hors du dépôt : ACL Tailscale (8443 réservé aux appareils d'administration, 8000 à l'Exit Node)
  et règles OCI (aucune entrée ouverte à `0.0.0.0/0` sur 8000, 8080, 8443, 5432).

## Agent → backend

- Connexion directe sur `:8000`, **jamais via Nginx** (qui renvoie 404 sur `/api/agent/`).
- HMAC-SHA256 par requête, tolérance d'horloge ±120 s, nonce à usage unique, idempotence des lots
  (`batch_id`), identifiant individuel, secrets chiffrés en base (`FIELD_ENCRYPTION_KEY`).
- Liste d'IP (`AGENT_ALLOWED_IPS`) lue sur l'adresse TCP brute ; l'IP seule n'authentifie jamais.
- Taille des requêtes limitée. `/api/agent/*` est exclu du contrôle CSRF / origine (l'agent n'est pas un
  navigateur ; HMAC inchangé).
- Création, rotation et révocation de l'identifiant : `python -m app.cli create-agent | revoke-agent`.

## Administration (navigateur)

- Cookie de session `HttpOnly`, `Secure`, `SameSite=Strict`, chemin `/api` ; sessions serveur
  (12 h d'inactivité, 7 jours au maximum), révoquées à la déconnexion.
- Verrouillage du compte après 5 échecs (15 min) ; mauvais mot de passe, compte verrouillé et compte
  inconnu donnent la même réponse. Nginx limite aussi `/api/auth/login` (10 par minute, rafale de 5).
- CSRF, deux protections cumulées sur toute requête mutative : jeton de session `X-CSRF-Token`, et origine
  stricte (`ALLOWED_ORIGINS`) : `Origin` — ou à défaut `Referer` — doit être l'origine HTTPS du dashboard,
  `Sec-Fetch-Site` éventuel doit valoir `same-origin` ; **sans `Origin` ni `Referer` : refus (403)**,
  connexion comprise.
- Champs inattendus refusés (422) sur tous les corps de requête.
- Navigateur : CSP stricte (`script-src 'self'`, aucun inline, aucun `eval`, aucune ressource externe,
  `require-trusted-types-for 'script'`), `X-Frame-Options: DENY`, `nosniff`, `Permissions-Policy`
  restrictive. Pas de HSTS (il s'appliquerait aussi à l'hôte du Funnel). Tout contenu saisi est affiché
  comme du texte.

## En-têtes proxy et adresse du client

- Nginx **reconstruit** `X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Proto` : il ne fait confiance qu'au saut
  `tailscale serve` et ne garde que la dernière adresse de la chaîne (celle que Tailscale atteste).
- Le backend ne lit `X-Real-IP` que si la connexion vient de l'IP fixe de Nginx
  (`TRUSTED_PROXY_IPS=10.250.11.10`). uvicorn tourne avec `--no-proxy-headers` (issue #8, corrigée) :
  `X-Forwarded-For` ne réécrit jamais l'adresse, même depuis le loopback.

## Conteneurs et secrets

- Nginx et backend sans privilège : utilisateur non-root, `cap_drop: ALL`, `no-new-privileges`, racine en
  lecture seule. Images et dépendances figées (versions exactes, empreintes).
- `.env` en `600`, jamais suivi par git ; `deploy.sh` refuse de déployer sinon, `verify-all.sh` le contrôle,
  ainsi que l'absence de secret de `.env` dans les réponses publiques et dans les journaux.
- Sauvegardes chiffrées avec `age` pour une clé **publique** : la clé privée n'est jamais sur le serveur ni
  sur l'Exit Node. Copie hors serveur en **écriture seule** (`rrsync -wo`).
- Tailscale REST : lecture seule (`/users`, `/devices`), droits minimaux.
- Aucun bouton de l'interface ne permet de passer en `enforce`.

## Limitations connues

- **Anti-rejeu après redémarrage (issue #5, ouverte)** : les nonces HMAC déjà vus sont gardés **en mémoire**.
  Après un redémarrage du backend, une requête signée déjà acceptée peut être rejouée une fois tant que son
  horodatage reste dans la fenêtre de ±120 s. Impact limité : un lot rejoué est ignoré (`batch_id`), un
  heartbeat rejoué peut réenregistrer un état ancien. Reproduit par
  `test_known_limitation_replay_after_backend_restart` (résultat `xfail`) ; correction prévue : persistance
  atomique des nonces en base, compatible avec la reprise du spool (l'agent signe au moment de l'envoi).
- **Cookie et ports** : les cookies ignorent le port. Le cookie de session (`path=/api`) est donc aussi envoyé
  à `https://ubuntu.tail14614.ts.net/api/*` (application du Funnel). `deploy/verify-11a.sh` inventorie les
  routes `/api` de cette application (`NEXT_APP_DIR=…`).
- **Clé `key_id` de l'agent** : visible dans l'identifiant d'entité des événements d'audit
  `agent.credential_*` (événements append-only, jamais réécrits) ; masquée partout ailleurs.

## Ce que vérifient les tests

`tests/e2e/test_security_12c.py` balaie **toutes** les routes (inventaire lu dans l'application) : sans
session, CSRF, origines refusées, champs inattendus, sessions, verrouillage, limite de débit, en-têtes
falsifiés, secrets dans toutes les réponses JSON/CSV, et toutes les pages dans Chrome (CSP, Trusted Types,
contenu hostile, aucune requête externe). Voir [développement](developpement.md).
