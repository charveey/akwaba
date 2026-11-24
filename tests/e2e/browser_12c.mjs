/**
 * Lot 12c — chaque page réelle dans Chrome : CSP, Trusted Types, aucune requête externe, aucun
 * gestionnaire ni script inline, liens maîtrisés, contenu utilisateur jamais interprété comme du HTML.
 * node browser_12c.mjs <devtools-port> <base-url> <email> <mot-de-passe> <ids JSON>
 */
import { connect, scenario, sleep } from "./cdp.mjs";

const [port, BASE, EMAIL, PASSWORD, IDS] = process.argv.slice(2);
const ids = JSON.parse(IDS);
const s = scenario();
const { check } = s;

const PAGES = [
  ["#/", "Vue d'ensemble"], ["#/membres", "Membres"], [`#/membres/${ids.member}`, null],
  ["#/abonnements", "Abonnements"], ["#/abonnements?onglet=relances", "Abonnements"], ["#/paiements", "Paiements"],
  ["#/identites", "Identités"], [`#/identites/${ids.identity}`, null],
  ["#/quarantaine", "Quarantaine"], ["#/quarantaine?onglet=decisions", "Quarantaine"], [`#/quarantaine/${ids.record}`, null],
  ["#/exemptions", "Exemptions"], ["#/depenses", "Dépenses"], ["#/rapports", "Rapports"],
  ["#/rapports?onglet=operations", "Rapports"], ["#/audit", "Journal d'audit"], ["#/agent", "Agent"],
  ["#/parametres", "Paramètres"], ["#/page-inexistante", "Page introuvable"],
];

/** Contrôles du DOM rendu, évalués dans la page. */
const DOM_AUDIT = `(() => {
  const all = [...document.querySelectorAll("*")];
  const handlers = all.flatMap((el) => [...el.attributes].filter((a) => /^on/i.test(a.name)).map((a) => el.tagName + "@" + a.name));
  const inlineStyles = all.filter((el) => el.hasAttribute("style")).map((el) => el.tagName);
  const inlineScripts = [...document.scripts].filter((sc) => !sc.src || sc.textContent.trim()).length;
  const badLinks = [...document.querySelectorAll("a[href]")].map((a) => a) .filter((a) => {
    const href = a.getAttribute("href");
    if (href.startsWith("#/") || href.startsWith("/api/")) return false;
    if (href.startsWith("https://wa.me/")) return !(a.rel.includes("noopener") && a.rel.includes("noreferrer"));
    return true;
  }).map((a) => a.getAttribute("href"));
  const embeds = document.querySelectorAll("iframe, object, embed, frame, base").length;
  const xss = window.__xss !== undefined || !!document.querySelector("img[src=x]");
  return { handlers, inlineStyles, inlineScripts, badLinks, embeds, xss };
})()`;

async function main() {
  const requests = [];
  const cdp = await connect(port, s.problems, requests);
  const { evaluate: ev, waitFor } = cdp;
  const settled = (title) => waitFor(`(() => { const t = document.querySelector("#page-title")?.textContent || "";
    return t !== "" && ${title === null ? "true" : `t === ${JSON.stringify(title)}`}
      && !document.querySelector(".content .state .spinner"); })()`, 10000);
  const audit = async (where) => {
    const r = await ev(DOM_AUDIT);
    const clean = !r.handlers.length && !r.inlineStyles.length && !r.inlineScripts && !r.badLinks.length && !r.embeds && !r.xss;
    check(`${where} : DOM sans gestionnaire, style ni script inline, liens maîtrisés, aucune injection`, clean, r);
  };

  // 1. Page de connexion, avant toute session.
  await cdp.send("Page.navigate", { url: `${BASE}/#/` });
  check("page de connexion affichée", await waitFor(`!!document.querySelector("#login-email")`));
  await audit("connexion");
  const csp = await ev(`fetch("/", { cache: "no-store" }).then((r) => r.headers.get("content-security-policy"))`);
  check("CSP stricte sur le document (self, Trusted Types, aucun cadre)",
    ["default-src 'none'", "script-src 'self'", "style-src 'self'", "connect-src 'self'", "base-uri 'none'",
      "frame-ancestors 'none'", "require-trusted-types-for 'script'"].every((d) => (csp || "").includes(d))
    && !/unsafe-inline|unsafe-eval|https?:\/\//.test(csp || ""), csp);
  const cookieVisible = await ev(`document.cookie.includes("akwaba_session")`);

  // Nginx limite /api/auth/login (rafale de 5, puis 10 par minute) : en cas de 429, on attend et on réessaie.
  let loggedIn = false;
  for (let attempt = 0; attempt < 12 && !loggedIn; attempt += 1) {
    await ev(`(() => { document.querySelector("#login-email").value = ${JSON.stringify(EMAIL)};
      document.querySelector("#login-password").value = ${JSON.stringify(PASSWORD)};
      document.querySelector("form").requestSubmit(); })()`);
    loggedIn = await settled("Vue d'ensemble");
    if (!loggedIn) await sleep(6500);
  }
  check("connexion", loggedIn);
  check("cookie de session inaccessible au JavaScript (HttpOnly)",
    !cookieVisible && !(await ev(`document.cookie.includes("akwaba_session")`)));

  // 2. Toutes les pages de l'administration.
  for (const [hash, title] of PAGES) {
    await ev(`location.hash = ${JSON.stringify(hash)}`);
    await sleep(150);
    const ok = await settled(title);
    check(`${hash} affichée`, ok, await ev(`document.querySelector("#page-title")?.textContent`));
    await audit(hash);
  }

  // 3. Contenu utilisateur hostile rendu comme du texte.
  await ev(`location.hash = "#/membres/${ids.member}"`);
  await settled(null);
  check("nom de membre avec balise HTML affiché littéralement",
    await waitFor(`document.querySelector("#page-title").textContent.startsWith('<img src=x onerror="window.__xss=1">')`));
  await ev(`location.hash = "#/depenses"`);
  await settled("Dépenses");
  check("description de dépense affichée littéralement",
    await waitFor(`document.querySelector(".content").textContent.includes('<img src=x onerror="window.__xss=1">Serveur')`));
  await ev(`location.hash = "#/exemptions"`);
  await settled("Exemptions");
  check("note d'autorisation et motif de protection affichés littéralement",
    await waitFor(`(() => { const t = document.querySelector(".content").textContent;
      return t.includes('<img src=x onerror="window.__xss=1">invité') && t.includes('<img src=x onerror="window.__xss=1">motif'); })()`));
  check("aucun script injecté n'a été exécuté", await ev(`window.__xss === undefined && !document.querySelector("img[src=x]")`));

  // 4. Réseau : uniquement la même origine.
  const foreign = requests.filter((u) => !u.startsWith(`${BASE}/`) && !u.startsWith("data:"));
  check(`aucune requête vers un autre domaine (${requests.length} requêtes observées)`, foreign.length === 0, foreign);
  check("aucune requête vers l'API agent", !requests.some((u) => u.includes("/api/agent")));

  await sleep(300);
  check("aucune erreur console, exception ni violation CSP sur l'ensemble du parcours", s.problems.length === 0,
    s.problems.join(" | "));

  // 5. Sondes actives (après le contrôle ci-dessus : elles déclenchent volontairement des violations).
  // Elles s'exécutent depuis la file de tâches de la page, hors de l'évaluation DevTools, que Chrome
  // exempte de la CSP ; chaque refus est confirmé par l'événement « securitypolicyviolation ».
  const probes = await ev(`new Promise((done) => {
    const violations = [];
    document.addEventListener("securitypolicyviolation", (e) => violations.push(e.effectiveDirective + " " + (e.blockedURI || "")));
    const out = {};
    setTimeout(() => {
      try { out.innerHTML = (document.createElement("div").innerHTML = "<b>x</b>", "accepté"); } catch (e) { out.innerHTML = e.name; }
      try { out.fn = new Function("return 1")() === 1 ? "accepté" : "?"; } catch (e) { out.fn = e.name; }
      try { out.evalCall = eval("1 + 1") === 2 ? "accepté" : "?"; } catch (e) { out.evalCall = e.name; }
      fetch("https://example.com/").then(() => { out.fetch = "accepté"; }, (e) => { out.fetch = e.name; })
        .finally(() => setTimeout(() => done({ out, violations }), 300));
    }, 0);
  })`);
  const v = probes.violations.join(" | ");
  check("Trusted Types : innerHTML refusé", probes.out.innerHTML === "TypeError" && v.includes("require-trusted-types-for"), probes);
  // La compilation de chaînes est bloquée par Trusted Types (require-trusted-types-for), contrôlé avant script-src.
  check("eval et new Function refusés par la CSP (EvalError, violation signalée)",
    probes.out.fn === "EvalError" && probes.out.evalCall === "EvalError"
    && probes.violations.filter((x) => /require-trusted-types-for|script-src/.test(x)).length >= 3, probes);
  check("connexion vers un domaine externe refusée par la CSP (connect-src)",
    probes.out.fetch === "TypeError" && v.includes("connect-src https://example.com"), probes);
  cdp.close();
}

main().catch((err) => check("exécution du scénario", false, err.stack || String(err))).finally(() => s.finish());
