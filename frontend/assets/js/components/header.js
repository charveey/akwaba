import { h } from "../dom.js";
import { icon } from "./icons.js";

/** En-tête : bouton menu (mobile), titre de page, utilisateur, déconnexion. */
export function createHeader({ onToggleNav, onLogout }) {
  const title = h("h1", { class: "header__title", id: "page-title", tabindex: "-1" });
  const email = h("span", { class: "header__user-email" });
  const menuBtn = h("button", {
    class: "btn btn--ghost btn--icon header__menu", type: "button",
    "aria-controls": "sidebar", "aria-expanded": "false", "aria-label": "Ouvrir le menu",
    onClick: () => onToggleNav(),
  }, icon("menu"));
  const logoutBtn = h("button", { class: "btn btn--ghost", type: "button", onClick: () => onLogout() },
    icon("logout"), h("span", { text: "Déconnexion" }));

  const el = h("header", { class: "header" },
    menuBtn, title, h("div", { class: "header__user" }, email, logoutBtn));

  return {
    el,
    setTitle(text) {
      title.textContent = text;
    },
    setUser(session) {
      email.textContent = session ? session.displayName || session.email : "";
    },
    setNavOpen(open) {
      menuBtn.setAttribute("aria-expanded", String(open));
      menuBtn.setAttribute("aria-label", open ? "Fermer le menu" : "Ouvrir le menu");
    },
    setLogoutBusy(busy) {
      logoutBtn.disabled = busy;
      logoutBtn.setAttribute("aria-busy", String(busy));
    },
    focusTitle() {
      title.focus({ preventScroll: true });
    },
  };
}
