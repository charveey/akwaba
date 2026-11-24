/**
 * Lot 11d — parcours navigateur réel : vue d'ensemble, agent, dépenses, rapports (CSV), journal d'audit.
 * node browser_11d.mjs <devtools-port> <base-url> <email> <mot-de-passe> <aujourd'hui> <key_id> <secret>
 */
import { writeFileSync } from "node:fs";

import { PAGE_HELPERS, connect, scenario, sleep } from "./cdp.mjs";

const [port, BASE, EMAIL, PASSWORD, TODAY, KEY_ID, SECRET] = process.argv.slice(2);
const s = scenario();
const { check } = s;

async function main() {
  const cdp = await connect(port, s.problems);
  const { evaluate: ev, waitFor } = cdp;
  const helpers = () => ev(PAGE_HELPERS);
  const go = async (hash) => { await ev(`location.hash = ${JSON.stringify(hash)}`); await sleep(150); await helpers(); };
  const dialogOpen = () => waitFor(`!!document.querySelector("dialog[open]")`);
  const dialogsClosed = () => waitFor(`!document.querySelector("dialog[open]")`);
  const lastDialog = `[...document.querySelectorAll("dialog[open]")].pop()`;
  const shot = async (name) => {
    if (!process.env.SCREENSHOT_DIR) return;
    await sleep(300);
    const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(`${process.env.SCREENSHOT_DIR}/11d-${name}.png`, Buffer.from(data, "base64"));
  };

  await cdp.send("Page.navigate", { url: `${BASE}/#/vue-ensemble` });
  await waitFor(`!!document.querySelector("#login-email")`);
  await ev(`(() => { document.querySelector("#login-email").value = ${JSON.stringify(EMAIL)};
    document.querySelector("#login-password").value = ${JSON.stringify(PASSWORD)};
    document.querySelector("form").requestSubmit(); })()`);
  check("connexion puis vue d'ensemble", await waitFor(`document.querySelector("#page-title")?.textContent === "Vue d'ensemble"`));
  await helpers();

  // 1. Vue d'ensemble : 9 indicateurs + 5 graphiques, valeurs du serveur
  await waitFor(`document.querySelectorAll(".card-grid .stat").length === 9`, 10000);
  const api = await ev(`fetch("/api/dashboard").then((r) => r.json())`);
  const cards = await ev(`[...document.querySelectorAll(".card-grid .stat")].map((c) => [c.querySelector(".stat__label").textContent, c.querySelector(".stat__value").textContent])`);
  const fmt = (v) => new Intl.NumberFormat("fr-FR").format(v);
  const byLabel = Object.fromEntries(cards);
  check("9 indicateurs affichés = valeurs du serveur",
    cards.length === 9 && byLabel["Membres"] === fmt(api.indicators.members_total) && byLabel["Membres actifs"] === fmt(api.indicators.members_active)
      && byLabel["Paiements en attente"] === fmt(api.indicators.payments_pending)
      && byLabel["Recettes du mois"] === new Intl.NumberFormat("fr-FR", { style: "currency", currency: "XOF" }).format(api.indicators.revenue_month_xof),
    JSON.stringify({ cards, ind: api.indicators }));
  check("valeurs attendues (3 membres, 2 actifs, 1 en attente, agent sain)",
    api.indicators.members_total === 3 && api.indicators.members_active === 2 && api.indicators.payments_pending === 1
      && api.indicators.agent.healthy_nodes === 1, JSON.stringify(api.indicators));
  check("5 graphiques SVG avec tableau de données",
    await ev(`document.querySelectorAll("figure.chart").length === 5 && document.querySelectorAll("figure.chart svg[role=img]").length >= 4
      && document.querySelectorAll("figure.chart details.chart__data").length === 5`));
  check("évolution des membres : cumul non présenté comme effectif actif",
    await ev(`document.body.textContent.includes("Ce n'est pas l'historique du nombre de membres actifs")`));
  await shot("1-vue-ensemble");

  // 2. Agent
  await go("#/agent");
  await waitFor(`document.body.textContent.includes("stray-11d")`);
  check("diagnostic : sain, conntrack, saturation, spool, décalages, lots",
    await ev(`(() => { const t = document.querySelector(".content").textContent;
      return t.includes("Sain") && t.includes("42 %") && t.includes("2 lot(s) en attente") && t.includes("+1 s")
        && t.includes("Décalage émission → réception (serveur)") && t.includes("Derniers lots reçus") && t.includes("1 actif(s)"); })()`),
    await ev(`__t.text(".content")`));
  check("aucun key_id ni secret dans la page", !(await ev(`document.body.textContent.includes(${JSON.stringify(KEY_ID)}) || document.body.textContent.includes(${JSON.stringify(SECRET)})`)));
  const raw = await ev(`fetch("/api/exit-nodes").then((r) => r.text())`);
  check("aucun key_id ni secret dans l'API", !raw.includes(KEY_ID) && !raw.includes(SECRET) && !raw.includes("key_id"));
  await shot("2-agent");

  // 3. Dépenses : ajout, correction atomique, annulation, catégories, historique, CSV
  await go("#/depenses");
  await waitFor(`document.querySelector(".content").textContent.includes("Aucune dépense")`);
  const add = async (cat, amount, desc) => {
    await ev(`__t.click("Ajouter une dépense")`);
    await dialogOpen();
    await ev(`__t.fill({ category_id: ${JSON.stringify(cat)}, amount: "${amount}", expense_date: "${TODAY}", description: ${JSON.stringify(desc)} }); __t.submit()`);
    await dialogsClosed();
  };
  await add("Hébergement", 25000, "Serveur Oracle");
  await add("Autre", 1500, "Divers");
  check("dépenses ajoutées", await waitFor(`__t.rows().length === 2`));
  await ev(`[...document.querySelectorAll(".content .table tbody tr")].find((tr) => tr.textContent.includes("Serveur Oracle")).querySelector("button").click()`);
  await dialogOpen();
  check("correction : explication affichée", await ev(`document.querySelector("dialog[open]").textContent.includes("La correction annule l'ancienne")`));
  await ev(`__t.fill({ category_id: "Marketing", amount: "30000", description: "Serveur Oracle (corrigé)" }); __t.submit()`);
  await dialogsClosed();
  check("correction : ancienne annulée « Corrigée », nouvelle active, liens",
    await waitFor(`__t.rows().some((r) => r.includes("Serveur Oracle") && r.includes("Annulée") && r.includes("Motif : Corrigée") && r.includes("Remplacée par une correction"))
      && __t.rows().some((r) => r.includes("Serveur Oracle (corrigé)") && r.includes("Marketing") && r.includes("Remplace une dépense corrigée"))`),
    await ev(`__t.rows()`));
  await ev(`[...[...document.querySelectorAll(".content .table tbody tr")].find((tr) => tr.textContent.includes("Divers")).querySelectorAll("button")].find((b) => b.textContent === "Annuler").click()`);
  await dialogOpen();
  await ev(`__t.fill({ reason: "Doublon" }); __t.submit()`);
  await dialogsClosed();
  check("annulation avec motif", await waitFor(`__t.rows().some((r) => r.includes("Divers") && r.includes("Motif : Doublon"))`));
  check("total des dépenses actives", await ev(`document.querySelector(".stat__value").textContent.replace(/\\s/g, "").startsWith("30000")`),
    await ev(`document.querySelector(".stat__value").textContent`));
  await ev(`[...[...document.querySelectorAll(".content .table tbody tr")].find((tr) => tr.textContent.includes("(corrigé)")).querySelectorAll("button")].find((b) => b.textContent === "Historique").click()`);
  await dialogOpen();
  check("historique : correction journalisée avec l'administrateur",
    await waitFor(`document.querySelector("dialog[open]").textContent.includes("Dépense corrigée") && document.querySelector("dialog[open]").textContent.includes(${JSON.stringify(EMAIL)})`));
  await ev(`__t.click("Fermer", document.querySelector("dialog[open]"))`);
  await dialogsClosed();
  await ev(`__t.click("Catégories")`);
  await dialogOpen();
  await waitFor(`${lastDialog}.querySelectorAll("tbody tr").length === 5`);
  await ev(`[...${lastDialog}.querySelectorAll("tbody tr")].find((tr) => tr.textContent.startsWith("Hébergement")).querySelectorAll("button")[1].click()`);
  await waitFor(`document.querySelectorAll("dialog[open]").length === 2`);
  check("désactivation d'une catégorie utilisée : avertissement",
    await ev(`${lastDialog}.textContent.includes("1 dépense(s) utilisent « Hébergement »") && ${lastDialog}.textContent.includes("Aucune suppression n'est possible")`),
    await ev(`${lastDialog}.textContent`));
  await ev(`__t.click("Désactiver", ${lastDialog})`);
  check("catégorie désactivée, jamais supprimée",
    await waitFor(`[...${lastDialog}.querySelectorAll("tbody tr")].some((tr) => tr.textContent.startsWith("Hébergement") && tr.textContent.includes("Désactivée"))`));
  await ev(`__t.click("Fermer", ${lastDialog})`);
  await dialogsClosed();
  const href = await ev(`document.querySelector('a[download][href*="format=csv"]').getAttribute("href")`);
  const csv = await ev(`fetch(${JSON.stringify(href)}).then(async (r) => { const bytes = new Uint8Array(await r.arrayBuffer());
    return { status: r.status, type: r.headers.get("content-type"), disp: r.headers.get("content-disposition"),
      bom: bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf, body: new TextDecoder().decode(bytes) }; })`);
  check("export CSV des dépenses : en-têtes, BOM, ; , toutes les lignes",
    csv.status === 200 && csv.type.startsWith("text/csv") && csv.disp.startsWith("attachment") && csv.bom
      && csv.body.split("\r\n").filter(Boolean).length === 4 && csv.body.includes("date;categorie;montant_xof"), JSON.stringify(csv));
  await shot("3-depenses");

  // 4. Rapports financiers et opérationnels
  await go("#/rapports");
  await waitFor(`document.querySelectorAll(".content a[download]").length >= 5`, 10000);
  check("rapports financiers : recettes, dépenses, résultat net + répartitions",
    await ev(`["Résultat net", "Répartition par abonnement", "Répartition par moyen de paiement", "Dépenses par catégorie"].every((x) => document.querySelector(".content").textContent.includes(x))`));
  const links = await ev(`[...document.querySelectorAll(".content a[download]")].map((a) => a.getAttribute("href"))`);
  const statuses = await ev(`Promise.all(${JSON.stringify(links)}.map((u) => fetch(u).then((r) => r.status + " " + r.headers.get("content-type"))))`);
  check("tous les exports CSV financiers répondent en text/csv", statuses.every((x) => x.startsWith("200 text/csv")), JSON.stringify(statuses));
  await shot("4-rapports");
  await go("#/rapports?onglet=operations");
  check("rapport opérationnel : instantané et flux mensuels",
    await waitFor(`(() => { const t = document.querySelector(".content").textContent; return t.includes("Instantané au") && t.includes("aucun historique des effectifs n'est reconstitué")
      && t.includes("Flux mensuels") && t.includes("Identités observées (nouvelles)"); })()`, 10000));

  // 5. Journal d'audit : tentative de connexion distinguée, acteur de session
  await ev(`fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: ${JSON.stringify(EMAIL)}, password: "mauvais-mot-de-passe" }) })`);
  await go("#/audit");
  await waitFor(`__t.rows().length > 5`, 10000);
  check("tentative de connexion : acteur « Tentative », jamais l'administrateur ciblé",
    await ev(`__t.rows().some((r) => r.includes("Connexion échouée") && r.includes("Tentative") && r.includes("Tentative de connexion sur le compte"))`),
    await ev(`__t.rows().slice(0, 4)`));
  check("correction de dépense attribuée à l'administrateur de la session",
    await ev(`__t.rows().some((r) => r.includes("Dépense corrigée") && r.includes("Administrateur") && r.includes(${JSON.stringify(EMAIL)}))`));
  check("actions automatiques distinguées (aucune action réseau)",
    await ev(`!__t.rows().some((r) => /tailscale (up|down|set)|nft |iptables/.test(r))`));
  await ev(`(() => { const s = document.querySelector('select[aria-label="Filtrer par action"]'); s.value = "admin.login_failed"; s.dispatchEvent(new Event("change")); })()`);
  check("filtre par action", await waitFor(`__t.rows().length >= 1 && __t.rows().every((r) => r.includes("Connexion échouée"))`));
  const details = await ev(`fetch("/api/audit?action=admin.login_failed").then((r) => r.text())`);
  check("aucun mot de passe dans les détails d'audit", !details.includes("mauvais-mot-de-passe") && !details.includes("\"password\""));
  await shot("5-audit");

  await sleep(300);
  check("aucune erreur console, exception ni violation CSP", s.problems.length === 0, s.problems.join(" | "));
  cdp.close();
}

main().catch((err) => check("exécution du scénario", false, err.stack || String(err))).finally(() => s.finish());
