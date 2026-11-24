import assert from "node:assert/strict";
import { test } from "node:test";

import { HOME_PATH, NAV, loginHash, parseHash, resolve, safeReturn } from "../assets/js/router.js";

test("la navigation contient les 12 sections, dans l'ordre", () => {
  assert.deepEqual(NAV.map((r) => r.title), [
    "Vue d'ensemble", "Membres", "Abonnements", "Paiements", "Identités", "Quarantaine",
    "Exemptions", "Dépenses", "Rapports", "Journal d'audit", "Agent", "Paramètres",
  ]);
  assert.equal(new Set(NAV.map((r) => r.path)).size, 12);
});

test("parseHash", () => {
  assert.equal(parseHash("").path, HOME_PATH);
  assert.equal(parseHash("#/").path, HOME_PATH);
  assert.equal(parseHash("#/membres/").path, "/membres");
  assert.equal(parseHash("#//membres").path, "/membres");
  assert.equal(parseHash("#membres").path, "/membres");
  const { path, query } = parseHash("#/connexion?retour=%2Fmembres");
  assert.equal(path, "/connexion");
  assert.equal(query.get("retour"), "/membres");
});

test("page privée sans session → connexion avec retour", () => {
  assert.deepEqual(resolve("#/paiements", false), { type: "redirect", hash: "#/connexion?retour=%2Fpaiements" });
  assert.deepEqual(resolve("#/vue-ensemble", false), { type: "redirect", hash: "#/connexion" });
});

test("route inconnue sans session → connexion (rien n'est révélé)", () => {
  assert.deepEqual(resolve("#/inconnue", false), { type: "redirect", hash: "#/connexion" });
});

test("route inconnue avec session → page introuvable", () => {
  assert.deepEqual(resolve("#/inconnue", true), { type: "not-found", path: "/inconnue" });
});

test("connexion affichée sans session", () => {
  const d = resolve("#/connexion?retour=%2Fmembres", false);
  assert.equal(d.type, "login");
  assert.equal(d.returnTo, "/membres");
});

test("connexion avec session → retour vers la page demandée", () => {
  assert.deepEqual(resolve("#/connexion?retour=%2Faudit", true), { type: "redirect", hash: "#/audit" });
  assert.deepEqual(resolve("#/connexion", true), { type: "redirect", hash: "#/vue-ensemble" });
});

test("le paramètre retour n'accepte que des pages connues (pas de redirection ouverte)", () => {
  for (const evil of ["https://evil.example", "//evil.example", "/connexion", "javascript:alert(1)", "/inconnue", null]) {
    assert.equal(safeReturn(evil), HOME_PATH);
  }
  assert.equal(loginHash("https://evil.example"), "#/connexion");
});

test("page privée avec session", () => {
  const d = resolve("#/parametres", true);
  assert.equal(d.type, "page");
  assert.equal(d.route.title, "Paramètres");
});
