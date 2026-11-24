/**
 * Construction du DOM sans HTML sous forme de chaîne.
 *
 * Règle du projet : jamais d'innerHTML / outerHTML / insertAdjacentHTML.
 * La CSP (require-trusted-types-for 'script') les bloque de toute façon
 * dans Chromium ; tout texte venant de l'API passe par des nœuds texte.
 */

const SVG_NS = "http://www.w3.org/2000/svg";
const FORBIDDEN_ATTRS = new Set(["style", "innerhtml", "outerhtml", "srcdoc"]);

/**
 * h("button", { class: "btn", type: "button", onClick: fn }, "Texte", autreNoeud)
 *  - class       : chaîne ou tableau (valeurs falsy ignorées)
 *  - text        : textContent
 *  - dataset     : objet → data-*
 *  - onXxx       : fonction → addEventListener("xxx")
 *  - booléen     : true → attribut vide, false/null/undefined → absent
 */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

function applyAttrs(el, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") {
      const cls = Array.isArray(value) ? value.filter(Boolean).join(" ") : String(value);
      if (cls) el.setAttribute("class", cls);
    } else if (key === "text") {
      el.textContent = String(value);
    } else if (key === "dataset") {
      for (const [k, v] of Object.entries(value)) el.dataset[k] = String(v);
    } else if (/^on[A-Z]/.test(key)) {
      if (typeof value !== "function") throw new TypeError(`${key} doit être une fonction`);
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else {
      const lower = key.toLowerCase();
      if (FORBIDDEN_ATTRS.has(lower) || lower.startsWith("on")) {
        throw new Error(`attribut interdit : ${key}`);
      }
      if ((lower === "href" || lower === "src" || lower === "action") && !isSafeUrl(String(value))) {
        throw new Error(`URL refusée pour ${key}`);
      }
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }
}

/** Seulement des URL internes : "#/…", "/…" (pas "//hôte"). */
export function isSafeUrl(url) {
  return url.startsWith("#") || (url.startsWith("/") && !url.startsWith("//"));
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

export function mount(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

/* États génériques (chargement / vide / erreur) */

export function loadingState(message = "Chargement…") {
  return h("div", { class: "state", role: "status" },
    h("div", { class: "spinner", "aria-hidden": "true" }),
    h("p", { class: "state__title", text: message }));
}

export function emptyState(title, detail) {
  return h("div", { class: "state" },
    h("p", { class: "state__title", text: title }),
    detail ? h("p", { text: detail }) : null);
}

export function errorState(title, detail, onRetry) {
  return h("div", { class: "state state--error", role: "alert" },
    h("p", { class: "state__title", text: title }),
    detail ? h("p", { text: detail }) : null,
    onRetry ? h("button", { class: "btn", type: "button", onClick: onRetry, text: "Réessayer" }) : null);
}
