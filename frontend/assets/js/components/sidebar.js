import { h } from "../dom.js";
import { NAV } from "../router.js";
import { icon, logo } from "./icons.js";

/** Sidebar : navigation principale. onNavigate est appelé après un clic (fermeture mobile). */
export function createSidebar({ onNavigate } = {}) {
  const links = new Map();
  const list = h("ul", {},
    NAV.map((route) => {
      const a = h("a", { class: "nav-link", href: `#${route.path}`, onClick: () => onNavigate && onNavigate() },
        icon(route.icon), h("span", { text: route.title }));
      links.set(route.path, a);
      return h("li", {}, a);
    }));

  const el = h("aside", { class: "sidebar", id: "sidebar", "aria-label": "Navigation principale" },
    h("div", { class: "sidebar__brand" },
      h("span", { class: "brand-mark" }, logo()),
      h("span", {}, "Akwaba", h("small", { text: "Administration" }))),
    h("nav", { class: "sidebar__nav" }, list),
    h("div", { class: "sidebar__footer", text: "Mode observation — aucune révocation réelle" }));

  function setActive(path) {
    for (const [p, a] of links) {
      if (p === path) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
  }

  return { el, setActive };
}
