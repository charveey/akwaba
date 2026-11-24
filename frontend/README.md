# Akwaba — interface d'administration

JavaScript natif (modules ES), **aucun framework, aucune dépendance, aucune étape de build**.
Les fichiers de ce dossier sont servis tels quels par Nginx (`deploy/nginx/`).

## Ce que Nginx sert

| Chemin | Contenu |
|---|---|
| `/` | `index.html` |
| `/assets/*.{css,js,svg,png,ico,woff2}` | fichiers statiques |
| tout le reste (dont `README.md`, `tests/`) | 404 |

Le routage est fait **par fragment** (`#/membres`, `#/parametres`…) : aucune réécriture côté serveur.

## Structure

```text
index.html                 un seul <script type="module">, aucun inline
assets/css/                variables (thèmes), layout, components, pages ; main.css les importe
assets/js/app.js           démarrage : thème, session, coquille, routage
assets/js/router.js        table de navigation (12 sections), garde d'authentification (fonction pure)
assets/js/api.js           fetch same-origin, X-CSRF-Token, 401 → session expirée, 403 CSRF → un nouvel essai
assets/js/auth.js          connexion / déconnexion via /api/auth/*
assets/js/state.js         session et jeton CSRF EN MÉMOIRE (jamais dans localStorage/sessionStorage)
assets/js/theme.js         clair / sombre / système (seule donnée en localStorage : la préférence)
assets/js/dom.js           h() / svg() : construction du DOM sans HTML en chaîne
assets/js/components/      sidebar, header, toast, modal (<dialog>), badge, icônes
assets/js/pages/           login, dashboard, settings, placeholder (sections des lots 11b–11d)
tests/                     node --test (aucune dépendance)
```

## Règles

- **Sécurité** : CSP stricte servie par Nginx (`script-src 'self'`, `style-src 'self'`,
  `require-trusted-types-for 'script'`). Donc : aucun script ni style inline, aucun attribut `style`
  ni `on*`, **jamais `innerHTML`** — tout passe par `h()` / `textContent`. Aucune URL externe.
- **Session** : cookie `HttpOnly` géré par le backend ; le jeton CSRF est relu par `GET /api/auth/me`
  au chargement et conservé en mémoire. `/api/agent/*` n'est jamais appelé.
- **Langue** : tous les textes visibles sont en français.
- **Nouvelle page** : créer `assets/js/pages/<nom>.js` exportant `render(container, { route, query })`
  (peut renvoyer une fonction de nettoyage), puis l'ajouter à `PAGES` dans `app.js`.

## Tests

```bash
node --test tests/*.test.mjs     # Node >= 22
```

`tests/static.test.mjs` vérifie automatiquement les règles ci-dessus (inline, `innerHTML`, URL externes,
stockage, imports, textes anglais). Le parcours complet dans un vrai Chrome est dans `tests/e2e/` à la racine.
