import { h } from "../dom.js";

/** Texte et bornes de la page courante (fonction pure, testée). */
export function pageInfo(total, limit, offset) {
  if (!total) return { text: "Aucun résultat", hasPrev: false, hasNext: false };
  const from = Math.min(offset + 1, total);
  const to = Math.min(offset + limit, total);
  return {
    text: `${from}–${to} sur ${total}`,
    hasPrev: offset > 0,
    hasNext: offset + limit < total,
  };
}

/** onChange(nouvelOffset) */
export function pagination({ total, limit, offset, onChange }) {
  const info = pageInfo(total, limit, offset);
  return h("nav", { class: "pagination", "aria-label": "Pagination" },
    h("span", { class: "pagination__info", text: info.text }),
    h("div", { class: "pagination__buttons" },
      h("button", { class: "btn", type: "button", disabled: !info.hasPrev, onClick: () => onChange(Math.max(0, offset - limit)), text: "Précédent" }),
      h("button", { class: "btn", type: "button", disabled: !info.hasNext, onClick: () => onChange(offset + limit), text: "Suivant" })));
}
