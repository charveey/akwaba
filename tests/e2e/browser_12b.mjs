/**
 * Lot 12b — navigateur réel sur les parcours métier de bout en bout.
 * node browser_12b.mjs <devtools-port> <base-url> <email> <mot-de-passe> <avant|apres> <note>
 *
 *   avant : après la première réconciliation (deux dossiers PENDING) ; autorise l'identité inconnue.
 *   apres : après la réconciliation à T0 + 24 h (blocage SIMULÉ du membre expiré).
 */
import { writeFileSync } from "node:fs";

import { PAGE_HELPERS, connect, scenario, sleep } from "./cdp.mjs";

const [port, BASE, EMAIL, PASSWORD, PHASE, NOTE] = process.argv.slice(2);
const s = scenario();
const { check } = s;

async function main() {
  const cdp = await connect(port, s.problems);
  const { evaluate: ev, waitFor } = cdp;
  const helpers = () => ev(PAGE_HELPERS);
  const title = (t) => waitFor(`document.querySelector("#page-title")?.textContent === ${JSON.stringify(t)}`);
  const go = async (hash, t) => { await ev(`location.hash = ${JSON.stringify(hash)}`); await sleep(150); await helpers(); if (t) await title(t); };
  const shot = async (name) => {
    if (!process.env.SCREENSHOT_DIR) return;
    await sleep(300);
    const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(`${process.env.SCREENSHOT_DIR}/12b-${PHASE}-${name}.png`, Buffer.from(data, "base64"));
  };
  const rowWith = (txt) => `[...document.querySelectorAll(".content .table tbody tr")].find((tr) => tr.textContent.includes(${JSON.stringify(txt)}))`;
  const openCases = `[...document.querySelectorAll(".stat")].find((e) => e.textContent.includes("Dossiers de quarantaine ouverts"))?.querySelector(".stat__value")?.textContent`;

  // Connexion (la seconde phase réutilise la session du même navigateur).
  await cdp.send("Page.navigate", { url: `${BASE}/#/` });
  await waitFor(`!!document.querySelector("#login-email") || document.querySelector("#page-title")?.textContent === "Vue d'ensemble"`);
  if (await ev(`!!document.querySelector("#login-email")`)) {
    await ev(`(() => { document.querySelector("#login-email").value = ${JSON.stringify(EMAIL)};
      document.querySelector("#login-password").value = ${JSON.stringify(PASSWORD)};
      document.querySelector("form").requestSubmit(); })()`);
  }
  check(`[${PHASE}] session administrateur`, await title("Vue d'ensemble"));
  await helpers();

  if (PHASE === "avant") {
    check("vue d'ensemble : 2 dossiers ouverts", await waitFor(`${openCases} === "2"`), await ev(openCases));

    await go("#/identites", "Identités");
    await waitFor(`__t.rows().length >= 4`);
    check("identités observées par l'agent, verdicts calculés par le serveur",
      await ev(`__t.rows().some((r) => r.includes("actif@example.com") && r.includes("Accès valide") && r.includes("Awa Active"))
        && __t.rows().some((r) => r.includes("expire@example.com") && r.includes("Abonnement expiré") && r.includes("Eli Expiré"))
        && __t.rows().some((r) => r.includes("inconnu@example.com") && r.includes("Identité inconnue"))
        && __t.rows().some((r) => r.includes("ops@example.com") && r.includes("Protégée"))`), await ev(`__t.rows()`));
    await shot("1-identites");

    await go("#/quarantaine?ouverts=0", "Quarantaine");
    await waitFor(`__t.rows().length >= 2`);
    check("dossiers PENDING pour le membre expiré et l'inconnu, avec délai serveur",
      await ev(`["expire@example.com", "inconnu@example.com"].every((l) => __t.rows().some((r) => r.includes(l)
        && r.includes("En attente de décision") && /2[34] h \\d\\d min avant blocage simulé/.test(r)))`), await ev(`__t.rows()`));
    check("aucun dossier pour le membre actif ni l'identité protégée",
      await ev(`!__t.rows().some((r) => r.includes("actif@example.com") || r.includes("ops@example.com"))`));
    await shot("2-quarantaine");

    await ev(`${rowWith("inconnu@example.com")}.querySelector("button").click()`);
    await waitFor(`!!document.querySelector("dialog[open]")`);
    check("autorisation confirmée, portée de l'exemption rappelée",
      await ev(`document.querySelector("dialog[open]").textContent.includes("jamais à un abonnement expiré")`));
    await ev(`__t.fill({ note: ${JSON.stringify(NOTE)} }); __t.submit()`);
    await waitFor(`!document.querySelector("dialog[open]")`);
    check("dossier de l'inconnu autorisé", await waitFor(`${rowWith("inconnu@example.com")}?.textContent.includes("Autorisé")`));
    check("le membre expiré reste en attente",
      await ev(`${rowWith("expire@example.com")}.textContent.includes("En attente de décision")`));

    await go("#/", "Vue d'ensemble");
    check("vue d'ensemble : 1 dossier ouvert après l'autorisation", await waitFor(`${openCases} === "1"`), await ev(openCases));
  } else {
    await go("#/quarantaine?ouverts=0", "Quarantaine");
    await waitFor(`__t.rows().length >= 2`);
    check("24 h plus tard : délai expiré, blocage SIMULÉ en vigueur",
      await ev(`${rowWith("expire@example.com")}.textContent.includes("Délai expiré")
        && ${rowWith("expire@example.com")}.textContent.includes("Blocage simulé en vigueur")`), await ev(`__t.rows()`));
    check("l'inconnu autorisé reste autorisé", await ev(`${rowWith("inconnu@example.com")}.textContent.includes("Autorisé")`));
    await shot("1-quarantaine");

    await ev(`[...${rowWith("expire@example.com")}.querySelectorAll("a")].find((a) => a.textContent === "Détail").click()`);
    await waitFor(`document.querySelector("#page-title")?.textContent.startsWith("Dossier")`);
    check("détail : transition, motif et décision simulée",
      await waitFor(`(() => { const t = document.querySelector(".content").textContent;
        return t.includes("En attente de décision → Délai expiré") && t.includes("Délai de 24 h écoulé")
          && t.includes("Simulée — non appliquée"); })()`), await ev(`__t.text(".content")`));

    await go("#/quarantaine?onglet=decisions", "Quarantaine");
    await waitFor(`__t.rows().length >= 1`);
    check("décisions : une seule, simulée, jamais appliquée",
      await ev(`__t.rows().length === 1 && __t.rows()[0].includes("expire@example.com") && __t.rows()[0].includes("Blocage simulé")
        && __t.rows()[0].includes("Simulée — non appliquée")`), await ev(`__t.rows()`));
    check("bandeau de simulation", await ev(`document.querySelector(".simulation-banner").textContent.includes("aucune action réseau n'est effectuée")`));
    await shot("2-decisions");

    await go("#/exemptions", "Exemptions");
    check("exemption issue de l'autorisation, motif = note saisie",
      await waitFor(`[...document.querySelectorAll("section[aria-labelledby=sec-ex] tbody tr")].some((tr) =>
        tr.textContent.includes("inconnu@example.com") && tr.textContent.includes(${JSON.stringify(NOTE)}))`));

    await go("#/", "Vue d'ensemble");
    check("vue d'ensemble : 1 dossier ouvert (délai expiré)", await waitFor(`${openCases} === "1"`), await ev(openCases));

    await go("#/audit?action=quarantine.opened", "Journal d'audit");
    check("audit : ouvertures attribuées au réconciliateur (automatique)",
      await waitFor(`__t.rows().length === 2 && __t.rows().every((r) => r.includes("Réconciliateur (automatique)") && r.includes("Automatique"))`),
      await ev(`__t.rows()`));
    await go("#/audit?action=quarantine.changed", "Journal d'audit");
    check("audit : passage en délai expiré par le réconciliateur",
      await waitFor(`__t.rows().length === 1 && __t.rows()[0].includes("Réconciliateur (automatique)")`), await ev(`__t.rows()`));
    await go("#/audit?action=quarantine.authorized", "Journal d'audit");
    check("audit : autorisation attribuée à l'administrateur de la session",
      await waitFor(`__t.rows().length === 1 && __t.rows()[0].includes(${JSON.stringify(EMAIL)}) && __t.rows()[0].includes("Administrateur")`),
      await ev(`__t.rows()`));
    await shot("3-audit");
  }

  await sleep(300);
  check(`[${PHASE}] aucune erreur console, exception ni violation CSP`, s.problems.length === 0, s.problems.join(" | "));
  cdp.close();
}

main().catch((err) => check("exécution du scénario", false, err.stack || String(err))).finally(() => s.finish());
