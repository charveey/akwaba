import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { THEME_KEY, applyTheme, getThemePreference } from "../assets/js/theme.js";

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

beforeEach(() => store.clear());

test("sans choix : suit le système (aucun data-theme)", () => {
  const root = { dataset: { theme: "dark" } };
  assert.equal(getThemePreference(), "system");
  applyTheme(undefined, root);
  assert.equal(root.dataset.theme, undefined);
});

test("choix explicite appliqué", () => {
  store.set(THEME_KEY, "dark");
  const root = { dataset: {} };
  applyTheme(undefined, root);
  assert.equal(root.dataset.theme, "dark");
});

test("valeur stockée invalide ignorée", () => {
  store.set(THEME_KEY, "<script>");
  assert.equal(getThemePreference(), "system");
});

test("stockage indisponible : pas d'exception", () => {
  const saved = globalThis.localStorage;
  globalThis.localStorage = { getItem() { throw new Error("SecurityError"); } };
  assert.equal(getThemePreference(), "system");
  globalThis.localStorage = saved;
});
