# Développement et tests

## Démarrage local

```bash
cp .env.example .env      # puis renseigner les secrets
docker compose config     # valide le fichier
docker compose up -d postgres
docker compose ps
```

## Tests depuis Zed et VS Code

Une seule fois sur le poste, depuis l'éditeur : tâche **« Akwaba : préparer l'environnement (une fois) »**
(VS Code : `Terminal › Run Task` ; Zed : `task: spawn`), ou `tests/setup-dev.sh`. Elle crée `.venv`
(Python 3.12 de préférence), installe les dépendances figées puis les outils de test, et démarre la base de
test jetable si Docker est disponible (`tests/test-db.sh up` : PostgreSQL 16 sur 127.0.0.1:5433, en mémoire,
écrit `.env.test`). Relançable sans risque. Dans VS Code, ensuite : « Developer: Reload Window ».

- **Zed** : ouvrir le dossier du dépôt. Les boutons ▶ dans la marge des tests Python et JavaScript lancent
  le test sous le curseur (ou le fichier) dans le bon répertoire. Palette `task: spawn` → « Akwaba : … »
  (tous les tests, tests rapides, fichier courant, base de test).
- **VS Code** : ouvrir **`akwaba.code-workspace`** (pas le dossier seul). Le panneau « Tests » découvre
  backend, agent et infra/bout en bout, chacun dans son répertoire, avec `.env.test` ; ▶ et 🐞 par test.
  Tâches : `Terminal › Run Task` → « Akwaba : … » ; `Run Test Task` lance tous les tests.
- **Couverture** : tâche « Akwaba : couverture du fichier courant » ou « … complète » (`tests/run.sh
  --couverture …`) → résumé dans le terminal, `backend|agent/couverture/html/index.html`, et `lcov.info`
  par composant (affiché dans la marge par l'extension recommandée Coverage Gutters). VS Code : aussi
  « Run Tests with Coverage » dans le panneau « Tests ». Non mesurée pour `tests/nginx|deploy|e2e`.
- **Débogueur** : Zed (`debugger: start`, F4) et VS Code (Run and Debug, F5) proposent « Akwaba : déboguer
  le fichier de test courant / le test sous le curseur / les tests frontend ». Les points d'arrêt
  fonctionnent dans les tests et dans le code testé (`app/…`, `akwaba_agent/…`) : pytest tourne dans le
  processus débogué (`tests/debug_pytest.py`). VS Code : aussi le bouton 🐞 de chaque test.
- **Ligne de commande** : `tests/run.sh [fichier] [-k test]` choisit le bon lanceur (`--plan` pour voir
  la commande) ; `tests/run.sh` seul lance les quatre suites.

`.env.test` ne contient que l'URL de la base de test et n'est jamais commité ; `.env` (production) n'est
jamais lu par les tests. Sans base de test, les tests PostgreSQL sont simplement ignorés. Les tests de
bout en bout demandent en plus Nginx et Chrome/Chromium ; les tests de sauvegarde réels demandent root.

## Tests en ligne de commande

```bash
cd backend && python3 -m pytest                              # unitaires (+ intégration si TEST_DATABASE_URL)
cd frontend && node --test tests/*.test.mjs                  # Node >= 22, aucune dépendance
python3 -m pytest tests/nginx tests/deploy                   # Nginx réel + invariants Compose / scripts
TEST_DATABASE_URL=... python3 -m pytest tests/e2e            # Chrome headless → TLS → Nginx → backend → PostgreSQL
TEST_DATABASE_URL=... python3 -m pytest tests/e2e/test_agent_backend_12a.py   # agent réel → HMAC → backend (Phase 12a)
TEST_DATABASE_URL=... python3 -m pytest tests/e2e/test_business_journeys_12b.py # parcours métier complets (Phase 12b)
TEST_DATABASE_URL=... python3 -m pytest tests/e2e/test_security_12c.py          # sécurité transversale, toutes routes et pages (Phase 12c)
TEST_DATABASE_URL=... tests/run-all.sh          # les quatre suites + récapitulatif (--rapide : sans bout en bout)
```

Tout lancer avec un récapitulatif : `tests/run.sh` (équivalent de `tests/run-all.sh`). Résultat attendu :
tout passe, sauf **1 `xfail`** volontaire (limitation connue de l'issue #5, voir [sécurité](securite.md)).

## Organisation du dépôt

| Dossier | Contenu |
|---|---|
| `backend/` | FastAPI (`app/`), migrations Alembic (`alembic/`), tests (`tests/`) |
| `agent/` | agent de l'Exit Node (`akwaba_agent/`), tests |
| `frontend/` | interface (modules ES), tests Node — voir [`frontend/README.md`](../frontend/README.md) |
| `deploy/` | scripts d'exploitation — voir [exploitation](exploitation.md) ; configuration Nginx (`deploy/nginx/`) |
| `tests/` | Nginx réel, invariants des scripts (`deploy/`), bout en bout (`e2e/`), lanceurs |
| `docs/` | cette documentation (un test vérifie que les scripts et options cités existent) |
