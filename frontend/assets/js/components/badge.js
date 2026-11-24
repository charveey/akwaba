import { h } from "../dom.js";

const VARIANTS = new Set(["success", "warning", "danger", "info", "neutral"]);

/** badge("Actif", "success") — le texte vient toujours d'un nœud texte. */
export function badge(text, variant = "neutral") {
  const v = VARIANTS.has(variant) ? variant : "neutral";
  return h("span", { class: ["badge", v !== "neutral" && `badge--${v}`], text });
}
