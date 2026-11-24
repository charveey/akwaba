import { h } from "../dom.js";

/** Onglets accessibles (flèches gauche/droite). items : [{ id, label }] */
export function tabs({ items, active, onSelect, label }) {
  const buttons = items.map((item) => h("button", {
    type: "button", role: "tab", class: "tab", id: `tab-${item.id}`,
    "aria-selected": String(item.id === active), tabindex: item.id === active ? "0" : "-1",
    onClick: () => onSelect(item.id),
  }, item.label));
  const list = h("div", { class: "tabs", role: "tablist", "aria-label": label }, buttons);
  list.addEventListener("keydown", (event) => {
    const i = buttons.indexOf(document.activeElement);
    if (i < 0 || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const next = buttons[(i + (event.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length];
    next.focus();
    next.click();
  });
  return list;
}
