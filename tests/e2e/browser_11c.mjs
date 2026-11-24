/**
 * Lot 11c — parcours navigateur réel : identités, association, réaffectation, quarantaine,
 * décisions simulées, exemptions et protections.
 * node browser_11c.mjs <devtools-port> <base-url> <email> <mot-de-passe>
 */
import { writeFileSync } from "node:fs";

import { PAGE_HELPERS, connect, scenario, sleep } from "./cdp.mjs";

const [port, BASE, EMAIL, PASSWORD] = process.argv.slice(2);
const s = scenario();
const { check } = s;

async function main() {
  const cdp = await connect(port, s.problems);
  const { evaluate: ev, waitFor } = cdp;
  const helpers = () => ev(PAGE_HELPERS);
  const go = async (hash) => { await ev(`location.hash = ${JSON.stringify(hash)}`); await sleep(150); await helpers(); };
  const dialogOpen = () => waitFor(`!!document.querySelector("dialog[open]")`);
  const dialogsClosed = () => waitFor(`!document.querySelector("dialog[open]")`);
  const shot = async (name) => {
    if (!process.env.SCREENSHOT_DIR) return;
    await sleep(300);
    const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(`${process.env.SCREENSHOT_DIR}/11c-${name}.png`, Buffer.from(data, "base64"));
  };
  const rowWith = (txt) => `[...document.querySelectorAll(".content .table tbody tr")].find((tr) => tr.textContent.includes(${JSON.stringify(txt)}))`;
  const lastDialog = `[...document.querySelectorAll("dialog[open]")].pop()`;

  await cdp.send("Page.navigate", { url: `${BASE}/#/identites` });
  await waitFor(`!!document.querySelector("#login-email")`);
  await ev(`(() => { document.querySelector("#login-email").value = ${JSON.stringify(EMAIL)};
    document.querySelector("#login-password").value = ${JSON.stringify(PASSWORD)};
    document.querySelector("form").requestSubmit(); })()`);
  check("connexion puis Identités", await waitFor(`document.querySelector("#page-title")?.textContent === "Identités"`));
  await helpers();

  // 1. Liste des identités, compteurs, sources non résolues
  await waitFor(`__t.rows().length >= 6`);
  check("identités observées listées avec verdicts",
    await ev(`__t.rows().some((r) => r.includes("inconnu@example.com") && r.includes("Identité inconnue"))
      && __t.rows().some((r) => r.includes("awa@example.com") && r.includes("Accès valide") && r.includes("Awa Active"))
      && __t.rows().some((r) => r.includes("revue@example.com") && r.includes("Revue manuelle"))`), await ev(`__t.rows()`));
  check("sources non résolues : informatives, jamais d'action",
    await waitFor(`(() => { const a = document.querySelector(".content .alert--info"); return a && a.textContent.includes("UNRESOLVED")
      && a.textContent.includes("aucune action n'est jamais déclenchée") && a.textContent.includes("100.99.9.9"); })()`));
  await shot("1-identites");
  await ev(`(() => { const s = document.querySelector('select[aria-label="Filtrer : signal d\\'ambiguïté"]'); s.value = "true"; s.dispatchEvent(new Event("change")); })()`);
  check("filtre « ambiguë » : suggestion d'e-mail de contact",
    await waitFor(`(() => { const r = __t.rows().filter((x) => x.includes("@example.com"));
      return r.length === 1 && r[0].includes("suggestion@example.com") && r[0].includes("Correspond à un e-mail de contact"); })()`),
    await ev(`__t.rows()`));

  // 2. Association à un membre (suggestion jamais appliquée automatiquement)
  await ev(`[...document.querySelectorAll(".content .table tbody a")].find((a) => a.textContent === "suggestion@example.com").click()`);
  await waitFor(`document.querySelector("#page-title")?.textContent === "suggestion@example.com"`);
  await helpers();
  check("fiche : signal informatif + membre suggéré, aucun membre associé",
    await waitFor(`(() => { const t = document.querySelector(".content").textContent; return t.includes("strictement informatifs")
      && t.includes("Contact Suggestion") && t.includes("Aucun membre associé."); })()`));
  check("fiche : profil, appareils, observations, dossier",
    await ev(`["Tailscale user ID", "Stable ID", "Observations récentes", "Dossiers de quarantaine", "En attente de décision"].every((x) => document.querySelector(".content").textContent.includes(x))`));
  await ev(`__t.click("Associer à un membre")`);
  await dialogOpen();
  await ev(`__t.fill({ member_id: "Contact Suggestion" }); __t.submit()`);
  check("association effectuée par l'administrateur, signal levé",
    await waitFor(`(() => { const t = document.querySelector(".content").textContent; return t.includes("Membre associé") && !t.includes("strictement informatifs")
      && document.querySelector("section[aria-labelledby=sec-member] a")?.textContent === "Contact Suggestion"; })()`, 10000));

  // 3. Réaffectation atomique avec double confirmation
  await go("#/identites?q=awa");
  await waitFor(`__t.rows().filter((x) => x.includes("awa@example.com")).length === 1`);
  await ev(`[...document.querySelectorAll(".content .table tbody a")].find((a) => a.textContent === "awa@example.com").click()`);
  await waitFor(`document.querySelector("#page-title")?.textContent === "awa@example.com"`);
  await helpers();
  await ev(`__t.click("Modifier l'association")`);
  await dialogOpen();
  check("réaffectation : association actuelle affichée", await ev(`${lastDialog}.textContent.includes("Membre actuel") && ${lastDialog}.textContent.includes("Awa Active")`));
  await ev(`(() => { const i = ${lastDialog}.querySelector('input[type="search"]'); i.value = "Binta"; i.dispatchEvent(new Event("input")); })()`);
  await waitFor(`[...${lastDialog}.querySelectorAll("option")].some((o) => o.textContent.startsWith("Binta Cible"))`);
  await ev(`__t.fill({ member_id: "Binta Cible" }, ${lastDialog}); ${lastDialog}.querySelector("form").requestSubmit()`);
  await waitFor(`document.querySelectorAll("dialog[open]").length === 2`);
  check("confirmation : actuel → cible", await ev(`${lastDialog}.textContent.includes("Awa Active → Binta Cible")`), await ev(`${lastDialog}.textContent`));
  await shot("2-reaffectation");
  await ev(`__t.click("Réaffecter", ${lastDialog})`);
  await dialogsClosed();
  check("identité réaffectée, verdict recalculé, historique journalisé",
    await waitFor(`(() => { const t = document.querySelector(".content").textContent;
      return document.querySelector("section[aria-labelledby=sec-member] a")?.textContent === "Binta Cible"
        && t.includes("Identité réaffectée à un autre membre") && t.includes("Membre sans abonnement"); })()`, 10000),
    await ev(`__t.text("section[aria-labelledby=sec-history]")`));

  // 4. Quarantaine : statuts, délais, actions confirmées
  await go("#/quarantaine?ouverts=0");
  await waitFor(`__t.rows().length >= 5`);
  check("dossiers : PENDING avec délai serveur, MANUAL_REVIEW sans blocage, EXPIRED simulé",
    await ev(`__t.rows().some((r) => r.includes("inconnu@example.com") && r.includes("En attente de décision") && /2[34] h \\d\\d min avant blocage simulé/.test(r))
      && __t.rows().some((r) => r.includes("revue@example.com") && r.includes("Intervention humaine requise") && r.includes("Aucun blocage automatique"))
      && __t.rows().some((r) => r.includes("expire@example.com") && r.includes("Délai expiré") && r.includes("Blocage simulé en vigueur"))`),
    await ev(`__t.rows()`));
  check("compteurs par statut (6 statuts)", await ev(`document.querySelectorAll(".chips .chip").length === 6`));
  await shot("3-quarantaine");
  await ev(`${rowWith("inconnu@example.com")}.querySelector("button").click()`);
  await dialogOpen();
  check("autorisation : confirmation, portée de l'exemption rappelée",
    await ev(`document.querySelector("dialog[open]").textContent.includes("jamais à un abonnement expiré")`));
  await ev(`__t.fill({ note: "Invité de passage" }); __t.submit()`);
  await dialogsClosed();
  check("dossier autorisé", await waitFor(`${rowWith("inconnu@example.com")}?.textContent.includes("Autorisé")`));
  await ev(`[...${rowWith("ecarte@example.com")}.querySelectorAll("button")].find((b) => b.textContent === "Écarter").click()`);
  await dialogOpen();
  await ev(`__t.fill({ note: "Test technique" }); __t.submit()`);
  await dialogsClosed();
  check("dossier écarté : pas de réouverture avant 7 jours",
    await waitFor(`${rowWith("ecarte@example.com")}?.textContent.includes("Pas de réouverture avant le")`));

  // 5. Détail d'un dossier : historique des changements
  await ev(`[...${rowWith("expire@example.com")}.querySelectorAll("a")].find((a) => a.textContent === "Détail").click()`);
  await waitFor(`document.querySelector("#page-title")?.textContent.startsWith("Dossier")`);
  check("détail : transitions et décision simulée",
    await waitFor(`(() => { const t = document.querySelector(".content").textContent;
      return t.includes("En attente de décision → Délai expiré") && t.includes("Blocage simulé") && t.includes("Simulée — non appliquée")
        && t.includes("Délai de 24 h écoulé"); })()`), await ev(`__t.text(".content")`));

  // 6. Décisions simulées
  await go("#/quarantaine?onglet=decisions");
  await waitFor(`__t.rows().length >= 1`);
  check("décisions : bandeau de simulation permanent",
    await ev(`document.querySelector(".simulation-banner").textContent.includes("aucune action réseau n'est effectuée")`));
  check("décisions : identité, verdict, motif, dossier, état de simulation",
    await ev(`__t.rows().some((r) => r.includes("expire@example.com") && r.includes("Blocage simulé") && r.includes("Délai de 24 h écoulé")
      && r.includes("Ouvrir le dossier") && r.includes("Simulée — non appliquée"))`), await ev(`__t.rows()`));
  await shot("4-decisions");

  // 7. Exemptions et protections
  await go("#/exemptions");
  await waitFor(`!!document.querySelector("section[aria-labelledby=sec-ex] tbody tr")`);
  check("exemption créée par l'autorisation, motif = note du dossier",
    await ev(`document.querySelector("section[aria-labelledby=sec-ex] tbody tr").textContent.includes("Invité de passage")`));
  check("règle affichée : jamais d'abonnement expiré",
    await ev(`document.querySelector("section[aria-labelledby=sec-ex]").textContent.includes("ne couvre jamais un membre dont l'abonnement a expiré")`));
  check("aucune création directe d'exemption", !(await ev(`[...document.querySelectorAll("button")].some((b) => /ajouter une exemption/i.test(b.textContent))`)));
  await ev(`__t.click("Terminer", document.querySelector("section[aria-labelledby=sec-ex]"))`);
  await dialogOpen();
  await ev(`__t.click("Terminer", document.querySelector("dialog[open]"))`);
  await ev(`(() => { const s = document.querySelector('select[aria-label="Filtrer les exemptions"]'); s.value = "toutes"; })()`);
  await ev(`(() => { const s = document.querySelector('select[aria-label="Filtrer les exemptions"]'); s.value = ""; s.dispatchEvent(new Event("change")); })()`);
  check("exemption terminée, conservée dans l'historique",
    await waitFor(`document.querySelector("section[aria-labelledby=sec-ex] tbody tr")?.textContent.includes("Terminée")`));
  check("protections automatiques séparées, lecture seule",
    await ev(`document.querySelector("section[aria-labelledby=sec-prot]").textContent.includes("le backend actuel n'en produit pas")`));
  await ev(`__t.click("Ajouter une protection manuelle")`);
  await dialogOpen();
  await ev(`__t.fill({ login_name: "Admin@Example.com", reason: "Compte d'administration" }); __t.submit()`);
  check("protection manuelle ajoutée (login normalisé)",
    await waitFor(`[...document.querySelectorAll("section[aria-labelledby=sec-prot] tbody tr")].some((tr) => tr.textContent.includes("admin@example.com") && tr.textContent.includes("Manuelle"))`));
  await ev(`__t.click("Retirer", document.querySelector("section[aria-labelledby=sec-prot]"))`);
  await dialogOpen();
  await ev(`__t.click("Retirer", document.querySelector("dialog[open]"))`);
  await dialogsClosed();
  await ev(`(() => { const c = document.querySelector("section[aria-labelledby=sec-prot] input[type=checkbox]"); c.click(); })()`);
  check("protection retirée, historique conservé",
    await waitFor(`[...document.querySelectorAll("section[aria-labelledby=sec-prot] tbody tr")].some((tr) => tr.textContent.includes("admin@example.com") && tr.textContent.includes("Retirée le"))`));
  await shot("5-exemptions");

  // 8. Contrôles serveur
  const noCsrf = await ev(`fetch("/api/quarantine/00000000-0000-0000-0000-000000000000/authorize", { method: "POST" }).then((r) => r.status)`);
  check("action sans jeton CSRF refusée (403)", noCsrf === 403, noCsrf);
  const agent = await ev(`fetch("/api/agent/ping", { method: "POST" }).then((r) => r.status)`);
  check("/api/agent/ bloqué par Nginx (404)", agent === 404, agent);

  await sleep(300);
  check("aucune erreur console, exception ni violation CSP", s.problems.length === 0, s.problems.join(" | "));
  cdp.close();
}

main().catch((err) => check("exécution du scénario", false, err.stack || String(err))).finally(() => s.finish());
