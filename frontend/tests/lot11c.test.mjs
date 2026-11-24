import assert from "node:assert/strict";
import { test } from "node:test";

import { remaining } from "../assets/js/components/format.js";
import { AMBIGUITY, DECISION, OPEN_STATUSES, PROTECTION_SOURCE, QUARANTINE_STATUS, VERDICT, reason } from "../assets/js/components/labels.js";
import { resolve } from "../assets/js/router.js";

const ID = "3f2c1d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f";

test("routes de détail des identités et des dossiers", () => {
  const i = resolve(`#/identites/${ID}`, true);
  assert.equal(i.route.key, "/identites/:id");
  assert.equal(i.route.nav, "/identites");
  const q = resolve(`#/quarantaine/${ID}`, true);
  assert.equal(q.route.key, "/quarantaine/:id");
  assert.equal(q.route.nav, "/quarantaine");
  assert.equal(resolve("#/quarantaine/pas-un-id", true).type, "not-found");
  assert.equal(resolve("#/quarantaine?onglet=decisions", true).route.key, "/quarantaine");
});

test("libellés : les six statuts de dossier, dont l'état exceptionnel", () => {
  assert.deepEqual(Object.keys(QUARANTINE_STATUS), ["PENDING", "AUTHORIZED", "EXPIRED", "PENDING_REVOCATION", "MANUAL_REVIEW", "DISMISSED"]);
  assert.equal(QUARANTINE_STATUS.PENDING_REVOCATION.label, "État exceptionnel");
  assert.equal(QUARANTINE_STATUS.MANUAL_REVIEW.label, "Intervention humaine requise");
  assert.deepEqual([...OPEN_STATUSES].sort(), ["EXPIRED", "MANUAL_REVIEW", "PENDING", "PENDING_REVOCATION"]);
});

test("libellés : verdicts, décisions simulées, signaux, sources de protection", () => {
  assert.equal(Object.keys(VERDICT).length, 7);
  assert.equal(DECISION.WOULD_BLOCK.label, "Blocage simulé");
  assert.equal(DECISION.WOULD_UNBLOCK.label, "Déblocage simulé");
  assert.deepEqual(Object.keys(AMBIGUITY), ["ARCHIVED_MEMBER_LOGIN", "CONTACT_EMAIL_MATCH", "MULTIPLE_EXIT_NODES"]);
  assert.deepEqual(Object.keys(PROTECTION_SOURCE), ["MANUAL", "AUTO_MEMBER", "AUTO_DEVICE"]);
  assert.equal(reason("window_elapsed"), "Délai de 24 h écoulé");
  assert.equal(reason("code_inconnu"), "code_inconnu");
  assert.equal(reason(null), "—");
});

test("temps restant : formatage d'une durée fournie par le serveur", () => {
  assert.equal(remaining(86400), "24 h 00 min");
  assert.equal(remaining(3600 + 5 * 60), "1 h 05 min");
  assert.equal(remaining(59 * 60), "59 min");
  assert.equal(remaining(0), "Délai écoulé");
  assert.equal(remaining(null), "—");
});
