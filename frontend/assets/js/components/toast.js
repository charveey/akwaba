import { h } from "../dom.js";

const DURATION = { info: 5000, success: 4000, warning: 7000, danger: 9000 };

/** toast("Membre créé.", "success") */
export function toast(message, type = "info") {
  const container = document.getElementById("toasts");
  if (!container) return;
  const variant = DURATION[type] ? type : "info";
  const close = () => item.remove();
  const item = h("div", { class: ["toast", `toast--${variant}`], role: variant === "danger" ? "alert" : null },
    h("p", { class: "toast__message", text: message }),
    h("button", { class: "toast__close", type: "button", "aria-label": "Fermer la notification", onClick: close, text: "×" }));
  container.append(item);
  while (container.children.length > 3) container.firstElementChild.remove();
  setTimeout(close, DURATION[variant]);
}
