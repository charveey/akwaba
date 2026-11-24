import { h } from "../dom.js";
import { getSession } from "../state.js";
import { getThemePreference, setThemePreference } from "../theme.js";

const OPTIONS = [
  { value: "light", label: "Clair" },
  { value: "dark", label: "Sombre" },
  { value: "system", label: "Selon le système" },
];

export function render(container) {
  const current = getThemePreference();
  const choices = OPTIONS.map(({ value, label }) => {
    const input = h("input", { type: "radio", name: "theme", value, checked: value === current });
    input.addEventListener("change", () => setThemePreference(value));
    return h("label", { class: "choice" }, input, h("span", { text: label }));
  });

  const session = getSession();
  container.replaceChildren(
    h("div", { class: "settings-grid" },
      h("section", { class: "card", "aria-labelledby": "theme-title" },
        h("h2", { class: "card__title", id: "theme-title", text: "Apparence" }),
        h("fieldset", { class: "field" },
          h("legend", { text: "Thème de l'interface" }),
          h("div", { class: "choice-group" }, choices),
          h("p", { class: "field__hint", text: "Préférence enregistrée sur cet appareil uniquement." }))),
      h("section", { class: "card", "aria-labelledby": "session-title" },
        h("h2", { class: "card__title", id: "session-title", text: "Session" }),
        h("dl", { class: "dl" },
          h("dt", { text: "Compte" }), h("dd", { text: session ? session.email : "—" }),
          h("dt", { text: "Nom affiché" }), h("dd", { text: (session && session.displayName) || "—" })),
        h("p", { class: "field__hint", text: "La session expire après 12 heures d'inactivité et au plus tard après 7 jours." }))));
}
