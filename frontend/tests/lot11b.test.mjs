import assert from "node:assert/strict";
import { test } from "node:test";

import { withQuery } from "../assets/js/api.js";
import { country, day, duration, localInputToIso, money } from "../assets/js/components/format.js";
import { validationMessage } from "../assets/js/components/form.js";
import { ACCESS, PAYMENT_KIND, REMINDER_STATUS, RENEWAL, reminderMoment } from "../assets/js/components/labels.js";
import { pageInfo } from "../assets/js/components/pagination.js";
import { nextSort, sortState } from "../assets/js/components/table.js";
import { isWhatsAppUrl } from "../assets/js/dom.js";
import { isPrivatePath, resolve, safeReturn } from "../assets/js/router.js";

const nbsp = (s) => s.replace(/[  ]/g, " ");
const ID = "3f2c1d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f";

test("montants en unités mineures, selon la devise", () => {
  assert.equal(nbsp(money(499, "EUR")), "4,99 €");
  assert.equal(nbsp(money(5399, "EUR")), "53,99 €");
  assert.match(nbsp(money(3274, "XOF")), /^3 274 F\s?CFA$/);
  assert.equal(money(null, "EUR"), "—");
});

test("dates du backend affichées sans conversion de fuseau", () => {
  assert.equal(day("2026-02-16"), "16/02/2026");
  assert.equal(day("2026-12-31T23:30:00+00:00"), "31/12/2026");
  assert.equal(day(null), "—");
  assert.equal(day("n'importe quoi"), "—");
  assert.equal(duration(3, false), "3 mois");
  assert.equal(duration(null, true), "Illimitée");
  assert.equal(country("CI"), "Côte d’Ivoire");
  assert.equal(localInputToIso(""), null);
  assert.match(localInputToIso("2026-01-17T10:30"), /^2026-01-1\dT\d\d:30:00\.000Z$/);
});

test("paramètres de requête : valeurs vides ignorées, booléens conservés", () => {
  assert.equal(withQuery("/api/members", { q: "awa", access_status: "", plan_code: null, sort: "name" }), "/api/members?q=awa&sort=name");
  assert.equal(withQuery("/api/payments", { plan_changed: "false", limit: 25, offset: 0 }), "/api/payments?plan_changed=false&limit=25&offset=0");
  assert.equal(withQuery("/api/payments", {}), "/api/payments");
  assert.equal(withQuery("/api/members", { q: "a&b=c" }), "/api/members?q=a%26b%3Dc");
});

test("tri : bascule ascendant / descendant", () => {
  assert.equal(nextSort("name", "name"), "-name");
  assert.equal(nextSort("-name", "name"), "name");
  assert.equal(nextSort("name", "expiry"), "expiry");
  assert.equal(nextSort("name", "paid_at", true), "-paid_at");
  assert.equal(sortState("-expiry", "expiry"), "descending");
  assert.equal(sortState("name", "expiry"), "none");
});

test("pagination", () => {
  assert.deepEqual(pageInfo(0, 25, 0), { text: "Aucun résultat", hasPrev: false, hasNext: false });
  assert.deepEqual(pageInfo(60, 25, 0), { text: "1–25 sur 60", hasPrev: false, hasNext: true });
  assert.deepEqual(pageInfo(60, 25, 50), { text: "51–60 sur 60", hasPrev: true, hasNext: false });
});

test("routes avec identifiant", () => {
  const d = resolve(`#/membres/${ID}`, true);
  assert.equal(d.type, "page");
  assert.equal(d.route.key, "/membres/:id");
  assert.equal(d.route.nav, "/membres");
  assert.deepEqual(d.params, { id: ID });
  assert.deepEqual(resolve(`#/membres/${ID}`, false), { type: "redirect", hash: `#/connexion?retour=%2Fmembres%2F${ID}` });
  assert.equal(safeReturn(`/membres/${ID}`), `/membres/${ID}`);
  for (const bad of ["/membres/abc", "/membres/../audit", `/membres/${ID}/x`, `/membres/${ID.toUpperCase()}`]) {
    assert.equal(isPrivatePath(bad), false, bad);
  }
  assert.equal(resolve("#/membres/abc", true).type, "not-found");
  assert.equal(resolve("#/membres?q=x", true).route.key, "/membres");
});

test("liens WhatsApp : seule exception externe, strictement validée", () => {
  assert.ok(isWhatsAppUrl("https://wa.me/2250102030405?text=Bonjour%2C%0A%0AVotre%20abonnement"));
  for (const bad of [
    "http://wa.me/2250102030405?text=x",
    "https://wa.me.evil.example/2250102030405?text=x",
    "https://evil.example/?u=https://wa.me/2250102030405?text=x",
    "https://wa.me/+2250102030405?text=x",
    "https://wa.me/2250102030405?text=<script>",
    "https://wa.me/2250102030405?text=x#frag",
    "javascript:alert(1)//https://wa.me/2250102030405?text=x",
    "https://wa.me/123?text=x",
    null,
  ]) {
    assert.equal(isWhatsAppUrl(bad), false, String(bad));
  }
});

test("messages de validation du serveur traduits", () => {
  assert.equal(validationMessage({ type: "missing", msg: "Field required" }), "Champ obligatoire.");
  assert.equal(validationMessage({ type: "value_error", msg: "Value error, adresse e-mail invalide" }), "Adresse e-mail invalide.");
  assert.equal(validationMessage({ type: "uuid_parsing", msg: "Input should be a valid UUID" }), "Valeur invalide.");
});

test("libellés : chaque valeur du backend a une traduction", () => {
  assert.deepEqual(Object.keys(ACCESS), ["NONE", "ACTIVE", "GRACE_PERIOD", "EXPIRED", "SUSPENDED"]);
  assert.deepEqual(Object.keys(RENEWAL), ["NOT_APPLICABLE", "UP_TO_DATE", "RENEWAL_OVERDUE", "NON_RENEWAL"]);
  assert.deepEqual(Object.keys(PAYMENT_KIND), ["PENDING", "NEW_MEMBER", "LATE_RENEWAL", "RENEWED"]);
  assert.equal(PAYMENT_KIND.LATE_RENEWAL.label, "Renouvellement en retard");
  assert.equal(RENEWAL.NON_RENEWAL.label, "Non-renouvellement");
  assert.match(REMINDER_STATUS.OPENED.label, /envoi non confirmé/);
  assert.equal(REMINDER_STATUS.SENT.label, "Envoi confirmé");
  assert.equal(reminderMoment(-7), "J-7 — avant expiration");
  assert.equal(reminderMoment(0), "J0 — jour d'expiration");
  assert.equal(reminderMoment(1), "J+1 — après expiration");
});
