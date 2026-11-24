/**
 * Tableau de données : en-têtes triables (le tri est fait par le BACKEND, avant pagination),
 * état vide. Les cellules sont des nœuds DOM ou du texte (jamais du HTML).
 */
import { emptyState, h } from "../dom.js";

/** Prochaine valeur de tri quand on clique sur une colonne. */
export function nextSort(current, key, descFirst = false) {
  if (current === key) return `-${key}`;
  if (current === `-${key}`) return key;
  return descFirst ? `-${key}` : key;
}

export function sortState(current, key) {
  if (current === key) return "ascending";
  if (current === `-${key}`) return "descending";
  return "none";
}

/**
 * columns : [{ label, render(row), sortKey?, descFirst?, className? }]
 * sort / onSort : tri courant et rappel (facultatifs)
 */
export function dataTable({ columns, rows, sort = null, onSort = null, empty = "Aucun résultat.", caption = null }) {
  if (!rows.length) return emptyState(empty);
  const head = h("tr", {}, columns.map((col) => {
    if (!col.sortKey || !onSort) return h("th", { scope: "col", class: col.className, text: col.label });
    const state = sortState(sort, col.sortKey);
    const arrow = state === "ascending" ? " ▲" : state === "descending" ? " ▼" : "";
    return h("th", { scope: "col", class: col.className, "aria-sort": state },
      h("button", {
        type: "button", class: "th-sort",
        onClick: () => onSort(nextSort(sort, col.sortKey, col.descFirst)),
      }, col.label, h("span", { "aria-hidden": "true", text: arrow })));
  }));
  const body = rows.map((row) => h("tr", {}, columns.map((col) => {
    const value = col.render(row);
    return h("td", { class: col.className, "data-label": col.label }, value === null || value === undefined || value === "" ? "—" : value);
  })));
  return h("div", { class: "table-wrap" },
    h("table", { class: "table" },
      caption ? h("caption", { class: "visually-hidden", text: caption }) : null,
      h("thead", {}, head),
      h("tbody", {}, body)));
}
