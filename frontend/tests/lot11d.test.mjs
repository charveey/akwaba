import assert from "node:assert/strict";
import { test } from "node:test";

import { niceMax } from "../assets/js/components/charts.js";
import { ACTOR_KIND, AUDIT_ACTIONS } from "../assets/js/components/labels.js";

test("graduation de l'axe : valeur ronde au-dessus du maximum", () => {
  assert.equal(niceMax(0), 1);
  assert.equal(niceMax(-5), 1);
  assert.equal(niceMax(7), 10);
  assert.equal(niceMax(12), 20);
  assert.equal(niceMax(240), 250);
  assert.equal(niceMax(3274), 5000);
  assert.equal(niceMax(100), 100);
});

test("audit : types d'acteurs et actions traduits", () => {
  assert.deepEqual(Object.keys(ACTOR_KIND), ["admin", "attempt", "system", "cli", "anonymous", "unknown"]);
  assert.equal(ACTOR_KIND.attempt.label, "Tentative");
  for (const a of ["admin.login_failed", "admin.login_success", "expense.corrected", "identity.reassigned",
    "quarantine.opened", "agent.credential_created", "payment.voided"]) {
    assert.ok(AUDIT_ACTIONS[a], a);
  }
});

test("les graphiques ne posent aucun attribut style ni HTML (compatibles CSP)", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../assets/js/components/charts.js", import.meta.url), "utf8");
  assert.doesNotMatch(src, /style\s*[:=]/);
  assert.doesNotMatch(src, /innerHTML|insertAdjacentHTML/);
  assert.match(src, /createElementNS|svg\(/);
});

test("axe : libellés compacts (les valeurs exactes restent dans le tableau)", async () => {
  const { axisLabel } = await import("../assets/js/components/charts.js");
  assert.equal(axisLabel(0), "0");
  assert.match(axisLabel(12500).replace(/\s/g, " "), /^12,5 k$/);
});

test("axe horizontal : un libellé sur deux quand les mois se chevaucheraient", async () => {
  const { labelStep } = await import("../assets/js/components/charts.js");
  const months = Array.from({ length: 12 }, (_, i) => `2026-${String(i + 1).padStart(2, "0")}`);
  assert.equal(labelStep(months, 120), 1);
  assert.ok(labelStep(months, 38) >= 2);
  assert.equal(labelStep(["15:43"], 600), 1);
});
