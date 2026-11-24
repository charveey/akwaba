import { h } from "../dom.js";

/**
 * Modale de confirmation (élément <dialog> natif : focus piégé, Échap pour annuler).
 * const ok = await confirmDialog({ title, message, confirmLabel, danger: true });
 */
export function confirmDialog({ title, message, confirmLabel = "Confirmer", cancelLabel = "Annuler", danger = false }) {
  return new Promise((resolve) => {
    const titleId = `modal-title-${Math.random().toString(36).slice(2, 9)}`;
    const cancel = h("button", { class: "btn", type: "button", value: "cancel", text: cancelLabel });
    const confirm = h("button", { class: ["btn", danger ? "btn--danger" : "btn--primary"], type: "button", text: confirmLabel });
    const dialog = h("dialog", { class: "modal", "aria-labelledby": titleId },
      h("div", { class: "modal__body" },
        h("h2", { class: "modal__title", id: titleId, text: title }),
        message ? h("p", { text: message }) : null),
      h("div", { class: "modal__actions" }, cancel, confirm));

    let result = false;
    cancel.addEventListener("click", () => dialog.close());
    confirm.addEventListener("click", () => {
      result = true;
      dialog.close();
    });
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(result);
    });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}
