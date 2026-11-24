/** Point d'entrée : thème, session, coquille de l'application, routage. */
import { setUnauthorizedHandler } from "./api.js";
import { logout, restoreSession } from "./auth.js";
import { createHeader } from "./components/header.js";
import { createSidebar } from "./components/sidebar.js";
import { toast } from "./components/toast.js";
import { emptyState, errorState, h } from "./dom.js";
import { renderLogin } from "./pages/login.js";
import { HOME_PATH, LOGIN_PATH, loginHash, navigate, parseHash, resolve } from "./router.js";
import { getSession, isAuthenticated, subscribe } from "./state.js";
import { applyTheme } from "./theme.js";

/** Chargement à la demande des pages (import dynamique : autorisé par script-src 'self'). */
const PAGES = {
  "/vue-ensemble": () => import("./pages/dashboard.js"),
  "/membres": () => import("./pages/members.js"),
  "/membres/:id": () => import("./pages/member-detail.js"),
  "/abonnements": () => import("./pages/subscriptions.js"),
  "/paiements": () => import("./pages/payments.js"),
  "/identites": () => import("./pages/identities.js"),
  "/identites/:id": () => import("./pages/identity-detail.js"),
  "/quarantaine": () => import("./pages/quarantine.js"),
  "/quarantaine/:id": () => import("./pages/quarantine-detail.js"),
  "/exemptions": () => import("./pages/exemptions.js"),
  "/parametres": () => import("./pages/settings.js"),
};
const placeholder = () => import("./pages/placeholder.js");

const root = document.getElementById("app");
let shell = null;
let cleanup = null;
let renderToken = 0;
let loginNotice = null;

applyTheme();

setUnauthorizedHandler(() => {
  const { path } = parseHash(location.hash);
  if (path === LOGIN_PATH) return;
  loginNotice = "Votre session a expiré. Veuillez vous reconnecter.";
  navigate(loginHash(path));
});

subscribe((session) => {
  if (shell) shell.header.setUser(session);
});

function buildShell() {
  const sidebar = createSidebar({ onNavigate: () => setNavOpen(false) });
  const header = createHeader({ onToggleNav: () => setNavOpen(!el.classList.contains("is-nav-open")), onLogout });
  const content = h("main", { class: "content", id: "content", tabindex: "-1" });
  const backdrop = h("div", { class: "backdrop", onClick: () => setNavOpen(false) });
  const el = h("div", { class: "shell" }, sidebar.el, backdrop, h("div", { class: "main" }, header.el, content));

  function setNavOpen(open) {
    el.classList.toggle("is-nav-open", open);
    header.setNavOpen(open);
  }

  return { el, sidebar, header, content, setNavOpen };
}

async function onLogout() {
  shell.header.setLogoutBusy(true);
  try {
    await logout();
    toast("Vous êtes déconnecté.", "success");
  } catch (err) {
    toast(`Déconnexion côté serveur impossible : ${err.message}`, "danger");
  } finally {
    if (shell) shell.header.setLogoutBusy(false);
    navigate(`#${LOGIN_PATH}`);
  }
}

function runCleanup() {
  if (typeof cleanup === "function") {
    try {
      cleanup();
    } catch {
      /* ignoré */
    }
  }
  cleanup = null;
}

async function render() {
  const token = ++renderToken;
  runCleanup();
  const decision = resolve(location.hash, isAuthenticated());

  if (decision.type === "redirect") {
    navigate(decision.hash);
    return;
  }

  if (decision.type === "login") {
    shell = null;
    document.title = "Connexion — Akwaba";
    const notice = loginNotice;
    loginNotice = null;
    renderLogin(root, {
      returnTo: decision.returnTo,
      notice,
      onSuccess: (returnTo) => navigate(`#${returnTo || HOME_PATH}`),
    });
    return;
  }

  if (!shell) {
    shell = buildShell();
    root.replaceChildren(shell.el);
  }
  shell.setNavOpen(false);
  shell.header.setUser(getSession());

  if (decision.type === "not-found") {
    shell.sidebar.setActive(null);
    shell.header.setTitle("Page introuvable");
    document.title = "Page introuvable — Akwaba";
    shell.content.replaceChildren(emptyState("Cette page n'existe pas.", "Utilisez le menu pour naviguer."));
    return;
  }

  const { route, query, params } = decision;
  shell.sidebar.setActive(route.nav);
  shell.header.setTitle(route.title);
  document.title = `${route.title} — Akwaba`;
  try {
    const mod = await (PAGES[route.key] || placeholder)();
    if (token !== renderToken) return;
    cleanup = mod.render(shell.content, {
      route, query, params,
      setTitle: (title) => {
        shell.header.setTitle(title);
        document.title = `${title} — Akwaba`;
      },
    }) || null;
  } catch (err) {
    if (token !== renderToken) return;
    shell.content.replaceChildren(errorState("Impossible d'afficher cette page.", String(err && err.message), render));
  }
  shell.header.focusTitle();
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && shell) shell.setNavOpen(false);
});

window.addEventListener("hashchange", render);

async function start() {
  try {
    await restoreSession();
  } catch {
    /* pas de session : la connexion sera affichée */
  }
  if (!location.hash) history.replaceState(null, "", `#${HOME_PATH}`);
  await render();
}

start();
