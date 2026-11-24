import { h } from "../dom.js";
import { applyServerErrors, clearErrors } from "./form.js";

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

/**
 * Modale générique : contenu libre + boutons. Renvoie { dialog, close }.
 * actions : [{ label, variant, onClick(close) }] — un bouton « Fermer » est ajouté par défaut.
 */
export function openDialog({ title, body, actions = [], closeLabel = "Fermer", wide = false }) {
  const titleId = `modal-title-${Math.random().toString(36).slice(2, 9)}`;
  const close = () => dialog.close();
  const buttons = [
    h("button", { class: "btn", type: "button", text: closeLabel, onClick: close }),
    ...actions.map((a) => h("button", {
      class: ["btn", a.variant ? `btn--${a.variant}` : null], type: "button", text: a.label,
      onClick: () => a.onClick(close),
    })),
  ];
  const dialog = h("dialog", { class: ["modal", wide && "modal--wide"], "aria-labelledby": titleId },
    h("div", { class: "modal__body" }, h("h2", { class: "modal__title", id: titleId, text: title }), body),
    h("div", { class: "modal__actions" }, buttons));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return { dialog, close };
}

/**
 * Modale de formulaire. onSubmit(form) est asynchrone : s'il réussit, la modale se ferme et la
 * promesse renvoie son résultat ; s'il lève une erreur, elle reste ouverte et affiche les erreurs
 * du serveur (sous les champs, sinon en tête). Annuler → null.
 */
export function formDialog({ title, fields, intro = null, submitLabel = "Enregistrer", danger = false, onSubmit, wide = false }) {
  return new Promise((resolve) => {
    const titleId = `modal-title-${Math.random().toString(36).slice(2, 9)}`;
    const general = h("div", { class: "alert alert--danger", role: "alert", hidden: true });
    const submit = h("button", { class: ["btn", danger ? "btn--danger" : "btn--primary"], type: "submit", text: submitLabel });
    const cancel = h("button", { class: "btn", type: "button", text: "Annuler" });
    const form = h("form", { class: "modal-form", novalidate: true },
      h("div", { class: "modal__body" },
        h("h2", { class: "modal__title", id: titleId, text: title }),
        intro, general, fields),
      h("div", { class: "modal__actions" }, cancel, submit));
    const dialog = h("dialog", { class: ["modal", wide && "modal--wide"], "aria-labelledby": titleId }, form);
    let result = null;

    cancel.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(result);
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearErrors(form);
      general.hidden = true;
      if (!form.checkValidity()) {
        const first = form.querySelector(":invalid");
        if (first) {
          first.setAttribute("aria-invalid", "true");
          const p = first.closest(".field")?.querySelector(".field__error");
          if (p) {
            p.textContent = first.validity.valueMissing ? "Champ obligatoire." : "Valeur invalide.";
            p.hidden = false;
          }
          first.focus();
        }
        return;
      }
      submit.disabled = true;
      submit.setAttribute("aria-busy", "true");
      try {
        result = (await onSubmit(form)) ?? true;
        dialog.close();
      } catch (err) {
        const unmatched = applyServerErrors(form, err);
        if (unmatched.length) {
          general.textContent = unmatched.join(" ");
          general.hidden = false;
        }
      } finally {
        submit.disabled = false;
        submit.removeAttribute("aria-busy");
      }
    });
    document.body.append(dialog);
    dialog.showModal();
    form.querySelector("input:not([type=hidden]), select, textarea")?.focus();
  });
}
