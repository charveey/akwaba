/**
 * Routeur par fragment (#/membres). Nginx ne sert que "/" : aucune réécriture
 * côté serveur, et tout autre chemin HTTP reste un vrai 404.
 */

export const LOGIN_PATH = "/connexion";
export const HOME_PATH = "/vue-ensemble";

/** Navigation principale (ordre de la sidebar). `lot` : lot qui livrera la page. */
export const NAV = [
  { path: "/vue-ensemble", title: "Vue d'ensemble", icon: "home", lot: "11d" },
  { path: "/membres", title: "Membres", icon: "users", lot: "11b" },
  { path: "/abonnements", title: "Abonnements", icon: "repeat", lot: "11b" },
  { path: "/paiements", title: "Paiements", icon: "wallet", lot: "11b" },
  { path: "/identites", title: "Identités", icon: "id", lot: "11c" },
  { path: "/quarantaine", title: "Quarantaine", icon: "shield", lot: "11c" },
  { path: "/exemptions", title: "Exemptions", icon: "check", lot: "11c" },
  { path: "/depenses", title: "Dépenses", icon: "receipt", lot: "11d" },
  { path: "/rapports", title: "Rapports", icon: "chart", lot: "11d" },
  { path: "/audit", title: "Journal d'audit", icon: "list", lot: "11d" },
  { path: "/agent", title: "Agent", icon: "server", lot: "11d" },
  { path: "/parametres", title: "Paramètres", icon: "settings", lot: "11a" },
];

/** Pages de détail (non présentes dans la sidebar) : `nav` = entrée active de la sidebar. */
export const DETAIL_ROUTES = [
  { pattern: /^\/membres\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/, key: "/membres/:id", title: "Fiche membre", nav: "/membres", params: ["id"] },
  { pattern: /^\/identites\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/, key: "/identites/:id", title: "Fiche identité", nav: "/identites", params: ["id"] },
  { pattern: /^\/quarantaine\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/, key: "/quarantaine/:id", title: "Dossier de quarantaine", nav: "/quarantaine", params: ["id"] },
];

const PRIVATE_PATHS = new Set(NAV.map((r) => r.path));

function matchDetail(path) {
  for (const d of DETAIL_ROUTES) {
    const m = d.pattern.exec(path);
    if (m) {
      const params = Object.fromEntries(d.params.map((name, i) => [name, m[i + 1]]));
      return { route: { path, key: d.key, title: d.title, nav: d.nav, icon: null }, params };
    }
  }
  return null;
}

/** "#/membres?page=2" → { path: "/membres", query: URLSearchParams } */
export function parseHash(hash) {
  let raw = typeof hash === "string" ? hash : "";
  if (raw.startsWith("#")) raw = raw.slice(1);
  if (!raw.startsWith("/")) raw = "/" + raw;
  const q = raw.indexOf("?");
  const pathPart = q === -1 ? raw : raw.slice(0, q);
  const query = new URLSearchParams(q === -1 ? "" : raw.slice(q + 1));
  let path = pathPart.replace(/\/{2,}/g, "/");
  if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
  return { path: path === "/" ? HOME_PATH : path, query };
}

export function findRoute(path) {
  return NAV.find((r) => r.path === path) || null;
}

export function isPrivatePath(path) {
  return typeof path === "string" && (PRIVATE_PATHS.has(path) || matchDetail(path) !== null);
}

/** Destination après connexion : uniquement une page connue de l'application. */
export function safeReturn(value) {
  return isPrivatePath(value) ? value : HOME_PATH;
}

export function loginHash(returnPath) {
  return returnPath && isPrivatePath(returnPath) && returnPath !== HOME_PATH
    ? `#${LOGIN_PATH}?retour=${encodeURIComponent(returnPath)}`
    : `#${LOGIN_PATH}`;
}

/**
 * Décision de navigation (fonction pure, testée) :
 *  - { type: "login" }              afficher la connexion
 *  - { type: "redirect", hash }     changer d'adresse
 *  - { type: "page", route }        afficher une page privée
 *  - { type: "not-found", path }    page inconnue
 */
export function resolve(hash, authenticated) {
  const { path, query } = parseHash(hash);
  if (path === LOGIN_PATH) {
    return authenticated
      ? { type: "redirect", hash: `#${safeReturn(query.get("retour"))}` }
      : { type: "login", returnTo: safeReturn(query.get("retour")) };
  }
  const nav = findRoute(path);
  const detail = nav ? null : matchDetail(path);
  if (!nav && !detail) return authenticated ? { type: "not-found", path } : { type: "redirect", hash: loginHash(null) };
  if (!authenticated) return { type: "redirect", hash: loginHash(path) };
  return nav
    ? { type: "page", route: { ...nav, key: nav.path, nav: nav.path }, query, params: {} }
    : { type: "page", route: detail.route, query, params: detail.params };
}

/** Remplace les paramètres de l'adresse courante sans recharger la page (filtres, pagination). */
export function replaceQuery(path, params) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "" && v !== false) qs.set(k, String(v));
  }
  const str = qs.toString();
  globalThis.history.replaceState(null, "", `#${path}${str ? "?" + str : ""}`);
}

export function navigate(hash) {
  if (globalThis.location.hash === hash) {
    globalThis.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    globalThis.location.hash = hash;
  }
}
