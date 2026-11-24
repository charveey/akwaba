/**
 * Lot 11b — parcours navigateur réel : membres, abonnements, paiements, relances WhatsApp.
 * node browser_11b.mjs <devtools-port> <base-url> <email> <mot-de-passe> <starts_on_relance> <offset>
 */
import { writeFileSync } from "node:fs";

import { PAGE_HELPERS, connect, scenario, sleep } from "./cdp.mjs";

const [port, BASE, EMAIL, PASSWORD, REMINDER_START, OFFSET] = process.argv.slice(2);
const s = scenario();
const { check } = s;

async function main() {
  const cdp = await connect(port, s.problems);
  const { evaluate: ev, waitFor } = cdp;
  const helpers = () => ev(PAGE_HELPERS);
  const go = async (hash) => { await ev(`location.hash = ${JSON.stringify(hash)}`); await sleep(150); };
  const shot = async (name) => {
    if (!process.env.SCREENSHOT_DIR) return;
    await sleep(300);
    const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(`${process.env.SCREENSHOT_DIR}/11b-${name}.png`, Buffer.from(data, "base64"));
  };
  const dialogOpen = () => waitFor(`!!document.querySelector("dialog[open]")`);
  const dialogClosed = () => waitFor(`!document.querySelector("dialog[open]")`);

  // Connexion
  await cdp.send("Page.navigate", { url: `${BASE}/#/membres` });
  await waitFor(`!!document.querySelector("#login-email")`);
  await ev(`(() => { document.querySelector("#login-email").value = ${JSON.stringify(EMAIL)};
    document.querySelector("#login-password").value = ${JSON.stringify(PASSWORD)};
    document.querySelector("form").requestSubmit(); })()`);
  check("connexion puis page Membres", await waitFor(`document.querySelector("#page-title")?.textContent === "Membres"`));
  await helpers();
  check("liste vide : état « aucun membre »", await waitFor(`document.body.textContent.includes("Aucun membre pour le moment.")`));

  // 1. Création : validation serveur (téléphone invalide) puis succès
  await ev(`__t.click("Nouveau membre")`);
  await dialogOpen();
  await ev(`__t.fill({ full_name: "Awa Koné", phone_e164: "123", email: "awa@example.com", country_code: "ci" }); __t.submit()`);
  check("validation serveur affichée sous le champ (téléphone)",
    await waitFor(`document.querySelector('dialog[open] [data-field="phone_e164"] .field__error')?.textContent.startsWith("Téléphone invalide")`),
    await ev(`document.querySelector('dialog[open] [data-field="phone_e164"] .field__error')?.textContent`));
  await ev(`__t.fill({ phone_e164: "+225 01 02 03 04 05" }); __t.submit()`);
  check("membre créé → fiche membre",
    await waitFor(`location.hash.startsWith("#/membres/") && document.querySelector("#page-title")?.textContent === "Awa Koné"`, 10000),
    await ev("location.hash"));
  const memberId = await ev(`location.hash.split("/")[2]`);
  check("fiche : WhatsApp normalisé et pays", await waitFor(`document.body.textContent.includes("+2250102030405") && document.body.textContent.includes("Côte d’Ivoire")`));
  check("fiche : aucun abonnement, accès « Aucun accès »", (await ev(`__t.text(".member-head__plan")`)) === "Aucun abonnement."
    && (await ev(`document.querySelector(".member-head__badges").textContent.includes("Aucun accès")`)));

  // 2. Modification
  await ev(`__t.click("Modifier")`);
  await dialogOpen();
  await ev(`__t.fill({ country_code: "SN", notes: "Cliente fidèle" }); __t.submit()`);
  await dialogClosed();
  check("modification enregistrée", await waitFor(`document.body.textContent.includes("Sénégal") && document.body.textContent.includes("Cliente fidèle")`));

  // 3. Identité Tailscale
  await ev(`__t.click("Rattacher une identité")`);
  await dialogOpen();
  await ev(`__t.fill({ login_name: "Awa.Kone@Gmail.com" }); __t.submit()`);
  check("identité rattachée (normalisée par le serveur)", await waitFor(`document.querySelector(".id-list")?.textContent.includes("awa.kone@gmail.com")`));

  // 4. Abonnement Bronze payé
  await ev(`__t.click("Nouvel abonnement")`);
  await dialogOpen();
  await ev(`__t.fill({ plan_id: "Bronze", payment_method_id: "Wave", reference: "REF-1" }); __t.submit()`);
  await dialogClosed();
  check("abonnement créé : Bronze, Actif, Nouveau membre",
    await waitFor(`(() => { const t = document.querySelector(".member-head").textContent;
      return t.includes("Bronze") && t.includes("Actif") && t.includes("Nouveau membre"); })()`),
    await ev(`__t.text(".member-head")`));

  // 5. Renouvellement avec changement de niveau (Silver), payé avant l'échéance
  await ev(`__t.click("Renouveler")`);
  await dialogOpen();
  await ev(`__t.fill({ plan_id: "Silver" })`);
  check("renouvellement : avertissement « Changement de niveau »",
    await ev(`!document.querySelector("dialog[open] .alert--info").hidden`));
  await ev(`__t.fill({ payment_method_id: "Espèces" }); __t.submit()`);
  await dialogClosed();
  check("renouvelé : Silver, Membre renouvelé + badge Changement de niveau",
    await waitFor(`(() => { const t = document.querySelector(".member-head").textContent;
      return t.includes("Silver") && t.includes("Membre renouvelé") && t.includes("Changement de niveau"); })()`),
    await ev(`__t.text(".member-head")`));
  check("renouvellement anticipé : membre toujours Actif, Silver « à partir du … »",
    await ev(`(() => { const t = document.querySelector(".member-head").textContent; return t.includes("Actif") && !t.includes("Aucun accès") && t.includes("à partir du"); })()`),
    await ev(`__t.text(".member-head")`));
  check("abonnement futur affiché « À venir »",
    await ev(`document.querySelector("section[aria-labelledby=sec-subs] tbody tr").textContent.includes("À venir")`));
  const subRows = await ev(`[...document.querySelectorAll("section[aria-labelledby=sec-subs] tbody tr")].length`);
  check("historique : 2 abonnements", subRows === 2, subRows);
  const backend = await ev(`__t.api("/api/subscriptions?member_id=${memberId}&sort=-created").then((r) => r.body.items)`);
  check("dates affichées = dates calculées par le backend (aucun calcul JS)",
    await ev(`document.querySelector("section[aria-labelledby=sec-subs] tbody tr").textContent.includes(${JSON.stringify(backend[0].display_end.split("-").reverse().join("/"))})`),
    backend[0].display_end);
  await shot("1-fiche-membre");
  check("historique d'accès : trois sources distinctes et non exhaustives",
    await waitFor(`(() => { const t = document.querySelector("section[aria-labelledby=sec-history]").textContent;
      return t.includes("Périodes reconstituées à partir des abonnements") && t.includes("pas une traçabilité exhaustive")
        && t.includes("Événements enregistrés dans le journal d'audit") && t.includes("Abonnement renouvelé")
        && t.includes("Changements réels d'accès Tailscale") && t.includes("dry-run"); })()`));

  // 6. Paiements : totaux, filtres
  await go("#/paiements");
  await helpers();
  await waitFor(`document.querySelectorAll(".content .table tbody tr").length === 2`);
  const api = await ev(`__t.api("/api/payments").then((r) => r.body)`);
  const shown = await ev(`document.querySelector(".stat__value").textContent`);
  const expected = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "XOF" }).format(api.totals.collected_xof);
  check("total encaissé affiché = total du serveur", shown === expected, `${shown} / ${expected}`);
  await ev(`(() => { const s = document.querySelector('select[aria-label="Filtrer par statut métier"]'); s.value = "NEW_MEMBER"; s.dispatchEvent(new Event("change")); })()`);
  check("filtre statut métier : 1 paiement, total recalculé par le serveur",
    await waitFor(`document.querySelectorAll(".content .table tbody tr").length === 1 && location.hash.includes("statut=NEW_MEMBER")`));
  await ev(`(() => { const s = document.querySelector('select[aria-label="Filtrer par statut métier"]'); s.value = ""; s.dispatchEvent(new Event("change")); })()`);
  await waitFor(`document.querySelectorAll(".content .table tbody tr").length === 2`);

  // 7. Annulation d'un paiement (motif obligatoire), historique conservé
  await ev(`[...document.querySelectorAll(".content .table tbody tr")].find((tr) => tr.textContent.includes("Bronze")).querySelector("button").click()`);
  await dialogOpen();
  await ev(`__t.submit()`);
  check("annulation : motif obligatoire (contrôle de saisie)",
    await waitFor(`document.querySelector('dialog[open] [data-field="reason"] .field__error')?.textContent === "Champ obligatoire."`));
  await ev(`__t.fill({ reason: "Paiement saisi en double" }); __t.submit()`);
  await dialogClosed();
  check("paiement annulé : conservé avec statut et motif",
    await waitFor(`__t.rows().some((r) => r.includes("Annulé") && r.includes("Paiement saisi en double"))`)
    && (await ev(`__t.rows().length`)) === 2);
  const after = await ev(`__t.api("/api/payments").then((r) => r.body.totals)`);
  await shot("2-paiements");
  check("total encaissé hors annulés", after.collected_xof < api.totals.collected_xof && after.voided_count === 1, after);

  // 8. Relance WhatsApp : préparation ≠ envoi
  const second = await ev(`__t.api("/api/members", { method: "POST", body: JSON.stringify({ full_name: "Binta Relance", phone_e164: "+2250700000001" }) }).then((r) => r.body)`);
  const plans = await ev(`fetch("/api/plans").then((r) => r.json())`);
  const methods = await ev(`fetch("/api/payment-methods").then((r) => r.json())`);
  const sub2 = await ev(`__t.api("/api/subscriptions", { method: "POST", body: JSON.stringify({
    member_id: ${JSON.stringify(second.id)}, plan_id: ${JSON.stringify(plans.find((p) => p.code === "M1").id)},
    starts_on: ${JSON.stringify(REMINDER_START)}, payment: { payment_method_id: ${JSON.stringify(methods[0].id)} } }) })`);
  check("abonnement proche de l'échéance (préparation)", sub2.status === 201, sub2);

  await go("#/abonnements?onglet=relances");
  await helpers();
  await waitFor(`!!document.querySelector('[role="tab"][aria-selected="true"]') && document.querySelector('[role="tab"][aria-selected="true"]').textContent === "Relances WhatsApp"`);
  await ev(`__t.click("Générer les relances du jour")`);
  check("relance générée (À préparer)", await waitFor(`__t.rows().some((r) => r.includes("Binta Relance") && r.includes("À préparer") && r.includes("J${OFFSET}"))`),
    await ev(`__t.rows()`));
  await ev(`[...document.querySelectorAll(".content .table tbody tr")].find((tr) => tr.textContent.includes("Binta")).querySelector("button").click()`);
  await dialogOpen();
  const link = await ev(`(() => { const a = document.querySelector("dialog[open] a"); return a && { href: a.getAttribute("href"), target: a.target, rel: a.rel, text: a.textContent }; })()`);
  check("message prérempli + lien wa.me sécurisé",
    link && link.href.startsWith("https://wa.me/2250700000001?text=") && link.target === "_blank" && link.rel === "noopener noreferrer"
      && (await ev(`document.querySelector("dialog[open] .message-preview").textContent.includes("Akwaba VPN")`)), link);
  await shot("3-relance-dialogue");
  check("avertissement : ouvrir ≠ envoyer",
    await ev(`document.querySelector("dialog[open]").textContent.includes("ne vaut pas confirmation d'envoi")`));
  await ev(`__t.click("Fermer", document.querySelector("dialog[open]"))`);
  await dialogClosed();
  check("après préparation : « envoi non confirmé », jamais « envoyé »",
    await waitFor(`__t.rows().some((r) => r.includes("Binta") && r.includes("envoi non confirmé"))`)
    && !(await ev(`__t.rows().some((r) => r.includes("Binta") && r.includes("Envoi confirmé"))`)));
  const rem = await ev(`fetch("/api/reminders?member_id=${second.id}").then((r) => r.json()).then((p) => p.items[0])`);
  check("serveur : relance OPENED, sent_at vide", rem.status === "OPENED" && rem.sent_at === null, rem);
  await ev(`__t.click("Confirmer l'envoi")`);
  await waitFor(`!!document.querySelector("dialog[open]")`);
  check("confirmation explicite demandée", await ev(`document.querySelector("dialog[open]").textContent.includes("réellement envoyé")`));
  await ev(`__t.click("Oui, le message a été envoyé", document.querySelector("dialog[open]"))`);
  check("envoi confirmé après confirmation explicite", await waitFor(`__t.rows().some((r) => r.includes("Binta") && r.includes("Envoi confirmé"))`));

  // 9. Échéances : filtre « expire dans 7 jours »
  await go("#/abonnements");
  await helpers();
  await waitFor(`document.querySelectorAll(".content .table tbody tr").length >= 2`);
  await ev(`(() => { const s = document.querySelector('select[aria-label="Filtrer par échéance"]'); s.value = "7"; s.dispatchEvent(new Event("change")); })()`);
  await shot("4-echeances");
  check("échéances à 7 jours : seulement Binta", await waitFor(`__t.rows().length === 1 && __t.rows()[0].includes("Binta Relance")`), await ev(`__t.rows()`));

  // 10. Membres : recherche, tri, données Tailscale dans la liste
  await go("#/membres");
  await helpers();
  await waitFor(`__t.rows().length === 2`);
  await shot("5-membres");
  check("liste : e-mail Tailscale, formule, statut", await ev(`__t.rows().some((r) => r.includes("awa.kone@gmail.com") && r.includes("Silver"))`), await ev(`__t.rows()`));
  await ev(`(() => { const i = document.querySelector('input[type="search"]'); i.value = "binta"; i.dispatchEvent(new Event("input")); })()`);
  check("recherche serveur", await waitFor(`__t.rows().length === 1 && __t.rows()[0].includes("Binta")`));
  await ev(`(() => { const i = document.querySelector('input[type="search"]'); i.value = ""; i.dispatchEvent(new Event("input")); })()`);
  await waitFor(`__t.rows().length === 2`);
  await ev(`[...document.querySelectorAll("th button")].find((b) => b.textContent.startsWith("Nom")).click()`);
  check("tri par nom décroissant (serveur)", await waitFor(`location.hash.includes("tri=-name") && __t.rows()[0].includes("Binta")`), await ev(`__t.rows()`));

  // 11. Archivage définitif
  await go(`#/membres/${memberId}`);
  await waitFor(`document.querySelector("#page-title")?.textContent === "Awa Koné"`);
  await helpers();
  await ev(`__t.click("Archiver")`);
  await dialogOpen();
  check("archivage : caractère définitif annoncé",
    await ev(`(() => { const t = document.querySelector("dialog[open]").textContent; return t.includes("DÉFINITIF") && t.includes("aucun désarchivage n'est possible"); })()`));
  await ev(`__t.click("Archiver définitivement", document.querySelector("dialog[open]"))`);
  check("membre archivé, actions retirées",
    await waitFor(`document.querySelector(".member-head").textContent.includes("Archivé le") && !document.querySelector(".member-head .actions button")`));
  await go("#/membres");
  await helpers();
  check("archivés exclus par défaut de la liste", await waitFor(`__t.rows().length === 1 && __t.rows()[0].includes("Binta")`));

  // 12. Contrôles serveur toujours actifs
  const noCsrf = await ev(`fetch("/api/members", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then((r) => r.status)`);
  check("écriture sans jeton CSRF refusée (403)", noCsrf === 403, noCsrf);
  const bad = await ev(`__t.api("/api/payments?date_from=2026-03-01&date_to=2026-01-01")`);
  check("filtre incohérent refusé par le serveur (422)", bad.status === 422, bad.status);

  await sleep(300);
  check("aucune erreur console, exception ni violation CSP", s.problems.length === 0, s.problems.join(" | "));
  cdp.close();
}

main().catch((err) => check("exécution du scénario", false, err.stack || String(err))).finally(() => s.finish());
