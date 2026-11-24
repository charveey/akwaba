/**
 * Champs de formulaire et affichage des erreurs de validation RENVOYÉES PAR LE SERVEUR.
 * La validation métier reste côté backend ; le navigateur ne fait que des contrôles de saisie.
 */
import { h } from "../dom.js";

let seq = 0;

/**
 * field({ label, name, type, value, required, options, hint, attrs })
 * type : text | email | tel | date | datetime-local | number | textarea | select | checkbox
 */
export function field({ label, name, type = "text", value = "", required = false, options = [], hint = null, attrs = {} }) {
  const id = `f-${name}-${++seq}`;
  const hintId = hint ? `${id}-hint` : null;
  const errorId = `${id}-error`;
  const common = { id, name, required, "aria-describedby": [hintId, errorId].filter(Boolean).join(" "), ...attrs };
  let input;
  if (type === "textarea") {
    input = h("textarea", { class: "input", rows: "3", ...common });
    input.value = value ?? "";
  } else if (type === "select") {
    input = h("select", { class: "input", ...common },
      options.map((o) => h("option", { value: o.value, selected: String(o.value) === String(value ?? "") }, o.label)));
  } else if (type === "checkbox") {
    input = h("input", { type: "checkbox", ...common, checked: Boolean(value) });
    return {
      el: h("div", { class: "field field--inline", dataset: { field: name } },
        h("label", { class: "choice", for: id }, input, h("span", { text: label })),
        hint ? h("p", { class: "field__hint", id: hintId, text: hint }) : null,
        h("p", { class: "field__error", id: errorId, hidden: true })),
      input,
    };
  } else {
    input = h("input", { class: "input", type, ...common });
    input.value = value ?? "";
  }
  return {
    el: h("div", { class: "field", dataset: { field: name } },
      h("label", { for: id }, label, required ? h("span", { class: "required", "aria-hidden": "true", text: " *" }) : null),
      input,
      hint ? h("p", { class: "field__hint", id: hintId, text: hint }) : null,
      h("p", { class: "field__error", id: errorId, hidden: true })),
    input,
  };
}

/** Valeurs nommées d'un formulaire : texte nettoyé, vide → null, cases → booléen. */
export function readForm(form) {
  const out = {};
  for (const el of form.querySelectorAll("input[name], select[name], textarea[name]")) {
    if (el.type === "checkbox") out[el.name] = el.checked;
    else {
      const v = el.value.trim();
      out[el.name] = v === "" ? null : v;
    }
  }
  return out;
}

export function clearErrors(form) {
  for (const el of form.querySelectorAll("[aria-invalid]")) el.removeAttribute("aria-invalid");
  for (const p of form.querySelectorAll(".field__error")) {
    p.textContent = "";
    p.hidden = true;
  }
}

/** Traduit une erreur de validation Pydantic en message français. */
export function validationMessage(item) {
  if (!item) return "Valeur invalide.";
  if (item.type === "missing") return "Champ obligatoire.";
  if (typeof item.msg === "string" && item.msg.startsWith("Value error, ")) {
    const msg = item.msg.slice("Value error, ".length);
    return msg.charAt(0).toUpperCase() + msg.slice(1) + (msg.endsWith(".") ? "" : ".");
  }
  if (item.type === "greater_than" || item.type === "greater_than_equal") return "Valeur trop petite.";
  if (item.type === "string_too_long") return "Texte trop long.";
  return "Valeur invalide.";
}

/**
 * Affiche sous chaque champ les erreurs 422 du serveur (detail = [{ loc, msg, type }]).
 * prefix : chemin du corps à ignorer (ex. ["body"] ou ["body", "payment"]).
 * Renvoie les messages qui n'ont pas pu être rattachés à un champ.
 */
export function applyServerErrors(form, err) {
  const detail = err && err.data && err.data.detail;
  if (!Array.isArray(detail)) return [err && err.message ? err.message : "Erreur inconnue."];
  const unmatched = [];
  for (const item of detail) {
    const loc = Array.isArray(item.loc) ? item.loc : [];
    const name = loc.length ? String(loc[loc.length - 1]) : null;
    const wrap = name ? form.querySelector(`[data-field="${CSS.escape(name)}"]`) : null;
    if (!wrap) {
      unmatched.push(validationMessage(item));
      continue;
    }
    const input = wrap.querySelector("input, select, textarea");
    const p = wrap.querySelector(".field__error");
    if (input) input.setAttribute("aria-invalid", "true");
    if (p) {
      p.textContent = validationMessage(item);
      p.hidden = false;
    }
  }
  return unmatched;
}
