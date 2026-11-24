/**
 * Graphiques SVG construits avec createElementNS (aucune bibliothèque, aucun style inline :
 * couleurs par classes CSS). Chaque graphique est accompagné d'un tableau de données accessible.
 * Les valeurs affichées sont celles calculées par le backend ; aucun calcul métier ici.
 */
import { h, svg } from "../dom.js";

const W = 640;
const H = 260;
const PAD = { top: 16, right: 16, bottom: 44, left: 64 };

const compact = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });

/** Libellé court de l'axe (« 12,5 k ») : les valeurs exactes restent dans les infobulles et le tableau. */
export function axisLabel(v) {
  return compact.format(v);
}

/** Pas d'affichage des libellés de l'axe horizontal pour éviter tout chevauchement (fonction pure, testée). */
export function labelStep(labels, slot) {
  const longest = labels.reduce((m, l) => Math.max(m, String(l).length), 0);
  const needed = longest * 7.5 + 10; // largeur approximative d'un libellé à 13 px
  return Math.max(1, Math.ceil(labels.length / 12), Math.ceil(needed / Math.max(1, slot)));
}

/** Graduation « ronde » de l'axe vertical (fonction pure, testée). */
export function niceMax(max) {
  if (!(max > 0)) return 1;
  const exp = 10 ** Math.floor(Math.log10(max));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (step * exp >= max) return step * exp;
  }
  return 10 * exp;
}

function dataTable(caption, columns, rows) {
  return h("details", { class: "chart__data" },
    h("summary", { text: "Voir les données" }),
    h("table", { class: "table" },
      h("caption", { class: "visually-hidden", text: caption }),
      h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col", text: c })))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((v) => h("td", { text: v })))))));
}

/**
 * Colonnes (une ou deux séries) + ligne facultative.
 * labels : ["2026-01", …] ; bars : [{ name, values, className }] ; line : { name, values, className } | null
 */
export function columnChart({ title, labels, bars = [], line = null, format = String, description = "" }) {
  const n = labels.length;
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const all = [...bars.flatMap((b) => b.values), ...(line ? line.values : [])];
  const top = niceMax(Math.max(0, ...all));
  const y = (v) => PAD.top + plotH - (Math.max(0, v) / top) * plotH;
  const slot = n ? plotW / n : plotW;
  const barW = Math.min(48, Math.max(2, (slot * 0.7) / Math.max(1, bars.length)));

  const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const v = top * f;
    return svg("g", { class: "chart__grid" },
      svg("line", { x1: PAD.left, x2: W - PAD.right, y1: y(v), y2: y(v) }),
      svg("text", { x: PAD.left - 8, y: y(v) + 4, "text-anchor": "end", class: "chart__tick" }, axisLabel(v)));
  });
  const columns = bars.flatMap((b, bi) => b.values.map((v, i) => svg("rect", {
    class: `chart__bar ${b.className || ""}`, x: PAD.left + i * slot + (slot - barW * bars.length) / 2 + bi * barW,
    y: y(v), width: barW, height: Math.max(0, PAD.top + plotH - y(v)),
  }, svg("title", {}, `${labels[i]} — ${b.name} : ${format(v)}`))));
  const every = labelStep(labels, slot);
  const xLabels = labels.map((l, i) => (i % every === 0 ? svg("text", {
    x: PAD.left + i * slot + slot / 2, y: H - PAD.bottom + 18, "text-anchor": "middle", class: "chart__tick",
  }, l) : null));
  let lineEl = null;
  if (line && n) {
    const pts = line.values.map((v, i) => `${PAD.left + i * slot + slot / 2},${y(v)}`).join(" ");
    lineEl = svg("g", {},
      svg("polyline", { class: `chart__line ${line.className || ""}`, points: pts, fill: "none" }),
      line.values.map((v, i) => svg("circle", { class: `chart__dot ${line.className || ""}`, cx: PAD.left + i * slot + slot / 2, cy: y(v), r: 3 },
        svg("title", {}, `${labels[i]} — ${line.name} : ${format(v)}`))));
  }
  const legend = [...bars, ...(line ? [line] : [])].map((s) =>
    h("span", { class: "chart__legend-item" }, h("span", { class: `chart__swatch ${s.className || ""}`, "aria-hidden": "true" }), s.name));
  return h("figure", { class: "chart" },
    h("figcaption", { class: "chart__title" }, title, description ? h("small", { class: "chart__note", text: description }) : null),
    svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title, class: "chart__svg" },
      grid, svg("line", { class: "chart__axis", x1: PAD.left, x2: W - PAD.right, y1: PAD.top + plotH, y2: PAD.top + plotH }),
      columns, lineEl, xLabels),
    h("div", { class: "chart__legend" }, legend),
    dataTable(title, ["Période", ...bars.map((b) => b.name), ...(line ? [line.name] : [])],
      labels.map((l, i) => [l, ...bars.map((b) => format(b.values[i])), ...(line ? [format(line.values[i])] : [])])));
}

/** Barres horizontales (répartitions). items : [{ label, value }] */
export function barList({ title, items, format = String, empty = "Aucune donnée.", description = "" }) {
  const max = Math.max(0, ...items.map((i) => i.value));
  const total = items.reduce((s, i) => s + i.value, 0);
  const rowH = 30;
  const height = Math.max(rowH, items.length * rowH);
  const labelW = 190;
  const barArea = W - labelW - 120;
  const body = items.length
    ? svg("svg", { viewBox: `0 0 ${W} ${height}`, role: "img", "aria-label": title, class: "chart__svg" },
      items.map((it, i) => svg("g", {},
        svg("text", { x: labelW - 8, y: i * rowH + 19, "text-anchor": "end", class: "chart__label" }, it.label),
        svg("rect", { class: `chart__bar ${it.className || ""}`, x: labelW, y: i * rowH + 6,
          width: max ? Math.max(2, (it.value / max) * barArea) : 0, height: rowH - 12 },
        svg("title", {}, `${it.label} : ${format(it.value)}`)),
        svg("text", { x: labelW + (max ? (it.value / max) * barArea : 0) + 8, y: i * rowH + 19, class: "chart__value" }, format(it.value)))))
    : h("p", { class: "chart__empty", text: empty });
  return h("figure", { class: "chart" },
    h("figcaption", { class: "chart__title" }, title, description ? h("small", { class: "chart__note", text: description }) : null),
    body,
    items.length ? dataTable(title, ["Élément", "Valeur", "Part"],
      items.map((i) => [i.label, format(i.value), total ? `${Math.round((i.value / total) * 100)} %` : "—"])) : null);
}
