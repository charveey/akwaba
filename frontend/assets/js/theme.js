/**
 * Thème clair / sombre.
 *
 * Seule donnée stockée localement : la préférence d'affichage (aucun lien avec
 * la session ni les données métier). Sans choix explicite, la page suit
 * prefers-color-scheme (géré en CSS, sans attribut data-theme).
 */

export const THEME_KEY = "akwaba.theme";
export const THEMES = ["light", "dark", "system"];

function storage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

export function getThemePreference() {
  try {
    const v = storage()?.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function setThemePreference(value) {
  if (!THEMES.includes(value)) throw new Error("thème inconnu");
  try {
    if (value === "system") storage()?.removeItem(THEME_KEY);
    else storage()?.setItem(THEME_KEY, value);
  } catch {
    /* stockage indisponible (navigation privée) : le choix vaut pour la page courante */
  }
  applyTheme(value);
}

export function applyTheme(value = getThemePreference(), root = document.documentElement) {
  if (value === "light" || value === "dark") root.dataset.theme = value;
  else delete root.dataset.theme;
}
