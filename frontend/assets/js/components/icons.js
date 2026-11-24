/** Icônes au trait (24×24), dessinées via createElementNS — aucune ressource externe. */
import { svg } from "../dom.js";

const PATHS = {
  home: ["M3 11 12 4l9 7", "M5 10v10h5v-6h4v6h5V10"],
  users: ["M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M2 21c0-4 3-6 7-6s7 2 7 6", "M16 3.5a4 4 0 0 1 0 7.5", "M18 15c2.4.6 4 2.4 4 6"],
  repeat: ["M17 2l4 4-4 4", "M3 11V9a3 3 0 0 1 3-3h15", "M7 22l-4-4 4-4", "M21 13v2a3 3 0 0 1-3 3H3"],
  wallet: ["M3 7h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z", "M3 7l12-4 1 4", "M16 13.5h2"],
  id: ["M3 5h18v14H3Z", "M9 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z", "M5.5 16c.5-1.8 1.8-2.5 3.5-2.5s3 .7 3.5 2.5", "M15 9h3", "M15 13h3"],
  shield: ["M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6Z", "M12 8v5", "M12 16h.01"],
  check: ["M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6Z", "m8.5 12 2.5 2.5 4.5-5"],
  receipt: ["M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2Z", "M9 8h6", "M9 12h6"],
  chart: ["M3 3v18h18", "M7 15l4-4 3 3 5-6"],
  list: ["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"],
  server: ["M3 4h18v6H3Z", "M3 14h18v6H3Z", "M7 7h.01", "M7 17h.01"],
  settings: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z", "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"],
  menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
  close: ["M6 6l12 12", "M18 6 6 18"],
};

export function icon(name) {
  const paths = PATHS[name] || PATHS.list;
  return svg("svg", {
    viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.8",
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false",
  }, paths.map((d) => svg("path", { d })));
}

export function logo() {
  return svg("svg", { viewBox: "0 0 32 32", class: "sidebar__logo", "aria-hidden": "true", focusable: "false" },
    svg("rect", { width: "32", height: "32", rx: "7", fill: "currentColor" }),
    svg("path", { d: "M9 24 16 8l7 16h-3.4l-1.4-3.4h-4.4L12.4 24Zm5.9-6.3h2.2L16 14.9Z", fill: "#fff" }));
}
