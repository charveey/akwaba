/**
 * Lot 11a — parcours navigateur réel piloté par le protocole DevTools (Chrome headless).
 * Aucune dépendance : WebSocket et fetch natifs de Node >= 22.
 *
 * Appelé par test_dashboard_11a.py :
 *   node browser_11a.mjs <devtools-http-port> <base-url> <email> <password>
 * Protocole avec le test Python : la ligne "ATTENTE:revoquer" sur stdout demande
 * la révocation de la session côté base ; le test répond "ok" sur stdin.
 * Dernière ligne sur stdout : RESULTAT:<json>.
 */
import { writeFileSync } from "node:fs";
import { createInterface } from "node:readline";

const [devtoolsPort, BASE, EMAIL, PASSWORD] = process.argv.slice(2);
const stdin = createInterface({ input: process.stdin });
const lines = stdin[Symbol.asyncIterator]();

const results = [];
const problems = []; // erreurs console, exceptions, violations CSP / Trusted Types

function check(name, ok, detail = "") {
  results.push({ name, ok: Boolean(ok), detail: String(detail) });
}

async function cdpConnect() {
  const res = await fetch(`http://127.0.0.1:${devtoolsPort}/json/new?about:blank`, { method: "PUT" });
  const target = await res.json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((ok, ko) => {
    ws.onopen = ok;
    ws.onerror = ko;
  });
  let id = 0;
  const pending = new Map();
  const listeners = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    } else if (msg.method) {
      for (const fn of listeners) fn(msg);
    }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const n = ++id;
    pending.set(n, { resolve, reject });
    ws.send(JSON.stringify({ id: n, method, params }));
  });
  return { send, on: (fn) => listeners.push(fn), close: () => ws.close() };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const cdp = await cdpConnect();
  cdp.on((msg) => {
    if (msg.method === "Runtime.exceptionThrown") {
      problems.push(`exception: ${msg.params.exceptionDetails.text} ${msg.params.exceptionDetails.exception?.description || ""}`);
    } else if (msg.method === "Runtime.consoleAPICalled" && ["error", "warning", "assert"].includes(msg.params.type)) {
      problems.push(`console.${msg.params.type}: ${msg.params.args.map((a) => a.value ?? a.description).join(" ")}`);
    } else if (msg.method === "Log.entryAdded") {
      const e = msg.params.entry;
      // Les réponses 401/403/404 attendues apparaissent comme erreurs « network » : ignorées.
      if (e.source !== "network" && (e.level === "error" || e.level === "warning")) problems.push(`${e.source}: ${e.text}`);
    } else if (msg.method === "Audits.issueAdded") {
      const code = msg.params.issue.code;
      if (/ContentSecurityPolicy|TrustedTypes|MixedContent|Cookie/i.test(code)) problems.push(`issue: ${code} ${JSON.stringify(msg.params.issue.details).slice(0, 300)}`);
    }
  });
  await cdp.send("Runtime.enable");
  await cdp.send("Log.enable");
  await cdp.send("Audits.enable");
  await cdp.send("Network.enable");
  await cdp.send("Page.enable");

  const evaluate = async (expression) => {
    const r = await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const waitFor = async (expression, timeout = 8000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      try {
        if (await evaluate(expression)) return true;
      } catch { /* page en cours de chargement */ }
      await sleep(100);
    }
    return false;
  };
  const goto = async (url) => {
    await cdp.send("Page.navigate", { url });
    await sleep(300);
  };
  // Captures facultatives (revue visuelle) : SCREENSHOT_DIR=/chemin.
  const shot = async (name) => {
    if (!process.env.SCREENSHOT_DIR) return;
    await sleep(250);
    const { data } = await cdp.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${process.env.SCREENSHOT_DIR}/${name}.png`, Buffer.from(data, "base64"));
  };
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  const text = (sel) => evaluate(`document.querySelector(${JSON.stringify(sel)})?.textContent?.trim() ?? null`);
  const fillLogin = async (email, password) => {
    await waitFor(`!!document.querySelector("#login-email")`);
    await evaluate(`(() => {
      document.querySelector("#login-email").value = ${JSON.stringify(email)};
      document.querySelector("#login-password").value = ${JSON.stringify(password)};
      document.querySelector("form").requestSubmit();
    })()`);
  };

  // 1. Sans session : redirection vers la connexion, avec retour.
  await goto(`${BASE}/#/membres`);
  check("redirection vers la connexion sans session",
    await waitFor(`location.hash === "#/connexion?retour=%2Fmembres" && !!document.querySelector("#login-email")`),
    await evaluate("location.hash"));
  check("page de connexion en français", (await evaluate("document.documentElement.lang")) === "fr"
    && (await text("button[type=submit]")) === "Se connecter");

  // 2. Mauvais mot de passe.
  await fillLogin(EMAIL, "mauvais-mot-de-passe");
  check("identifiants invalides signalés",
    await waitFor(`document.querySelector(".alert--danger")?.textContent === "Identifiants invalides"`),
    await text(".alert--danger"));
  await shot("1-connexion-erreur");

  // 3. Connexion réussie → retour vers la page demandée.
  await fillLogin(EMAIL, PASSWORD);
  check("connexion puis retour vers Membres",
    await waitFor(`location.hash === "#/membres" && document.querySelector("#page-title")?.textContent === "Membres"`),
    await evaluate("location.hash"));
  check("sidebar : 12 entrées, entrée active", (await evaluate(`document.querySelectorAll(".nav-link").length`)) === 12
    && (await text(`.nav-link[aria-current="page"]`)) === "Membres");

  // 4. Cookie de session : HttpOnly, Secure, SameSite=Strict, chemin /api.
  const { cookies } = await cdp.send("Network.getCookies", { urls: [`${BASE}/api/auth/me`] });
  const sess = cookies.find((c) => c.name === "akwaba_session");
  check("cookie HttpOnly + Secure + SameSite=Strict + path=/api",
    sess && sess.httpOnly && sess.secure && sess.sameSite === "Strict" && sess.path === "/api", JSON.stringify(sess));
  check("cookie invisible pour JavaScript", (await evaluate("document.cookie")) === "");
  check("aucun jeton dans le stockage du navigateur",
    await evaluate(`sessionStorage.length === 0 && Object.keys(localStorage).every((k) => k === "akwaba.theme")`));

  // 5. Vue d'ensemble : API via Nginx, base et mode dry-run.
  await goto(`${BASE}/#/vue-ensemble`);
  check("vue d'ensemble : backend, base, mode observation",
    await waitFor(`document.body.textContent.includes("Opérationnelle") && document.body.textContent.includes("Observation (dry-run)")`),
    await evaluate(`document.querySelector(".dl")?.textContent`));
  await shot("2-vue-ensemble-clair");

  // 6. Requêtes depuis la page : origine acceptée par le backend, /api/agent bloqué.
  const probe = await evaluate(`(async () => {
    const me = await (await fetch("/api/auth/me")).json();
    const post = await fetch("/api/members", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token }, body: "{}" });
    const noCsrf = await fetch("/api/members", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const agent = await fetch("/api/agent/ping", { method: "POST", body: "{}" });
    const unknown = await fetch("/inexistant");
    return { post: post.status, noCsrf: noCsrf.status, agent: agent.status, unknown: unknown.status };
  })()`);
  check("POST navigateur même origine : accepté par l'origine (422 = validation métier)", probe.post === 422, JSON.stringify(probe));
  check("POST sans jeton CSRF : 403", probe.noCsrf === 403, JSON.stringify(probe));
  check("/api/agent/ depuis le navigateur : 404", probe.agent === 404, JSON.stringify(probe));
  check("chemin inconnu : 404", probe.unknown === 404, JSON.stringify(probe));

  // 7. Thème sombre persistant.
  await goto(`${BASE}/#/parametres`);
  await waitFor(`!!document.querySelector('input[value="dark"]')`);
  await evaluate(`document.querySelector('input[value="dark"]').click()`);
  check("thème sombre appliqué", (await evaluate("document.documentElement.dataset.theme")) === "dark");
  await cdp.send("Page.reload");
  await sleep(300);
  check("thème sombre conservé après rechargement",
    await waitFor(`document.documentElement.dataset.theme === "dark" && document.querySelector("#page-title")?.textContent === "Paramètres"`));
  await shot("3-parametres-sombre");

  // 8. Mobile : sidebar rétractable.
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 800, deviceScaleFactor: 2, mobile: true });
  await sleep(200);
  const hiddenBefore = await evaluate(`document.querySelector(".sidebar").getBoundingClientRect().right <= 0`);
  await shot("4-mobile-ferme");
  await evaluate(`document.querySelector(".header__menu").click()`);
  await sleep(300);
  await shot("5-mobile-menu");
  const shown = await evaluate(`document.querySelector(".sidebar").getBoundingClientRect().left >= 0
    && document.querySelector(".header__menu").getAttribute("aria-expanded") === "true"`);
  await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))`);
  await sleep(300);
  const hiddenAfter = await evaluate(`document.querySelector(".sidebar").getBoundingClientRect().right <= 0`);
  check("mobile : menu masqué, ouvert, refermé (Échap)", hiddenBefore && shown && hiddenAfter, `${hiddenBefore}/${shown}/${hiddenAfter}`);
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });

  // 9. Session expirée côté serveur → retour à la connexion avec message.
  process.stdout.write("ATTENTE:revoquer\n");
  await lines.next();
  await goto(`${BASE}/#/vue-ensemble`);
  await cdp.send("Page.reload");
  await sleep(300);
  check("session révoquée : redirection vers la connexion",
    await waitFor(`location.hash.startsWith("#/connexion") && !!document.querySelector("#login-email")`), await evaluate("location.hash"));

  // Expiration pendant l'utilisation (sans rechargement) : message explicite.
  await fillLogin(EMAIL, PASSWORD);
  await waitFor(`document.querySelector("#page-title")?.textContent === "Vue d'ensemble"`);
  process.stdout.write("ATTENTE:revoquer\n");
  await lines.next();
  await evaluate(`location.hash = "#/vue-ensemble?x=" + Date.now()`);
  check("expiration en cours d'utilisation : message de session expirée",
    await waitFor(`document.querySelector(".alert--info")?.textContent === "Votre session a expiré. Veuillez vous reconnecter."`),
    await text(".alert--info"));

  // 10. Déconnexion.
  await fillLogin(EMAIL, PASSWORD);
  await waitFor(`!!document.querySelector(".header__user button")`);
  await evaluate(`document.querySelector(".header__user button").click()`);
  check("déconnexion : retour à la connexion", await waitFor(`location.hash === "#/connexion" && !!document.querySelector("#login-email")`));
  check("déconnexion : session invalidée côté serveur",
    (await evaluate(`fetch("/api/auth/me").then((r) => r.status)`)) === 401);

  // 11. CSP / Trusted Types : aucune violation, exception ni erreur console sur tout le parcours.
  await sleep(300);
  const before = [...problems];
  check("aucune erreur console, exception ni violation CSP", before.length === 0, before.join(" | "));

  // Sonde volontaire : prouve que la CSP est active et que le détecteur ci-dessus fonctionne.
  const blocked = await evaluate(`(() => { try { document.body.innerHTML = "<b>x</b>"; return false; } catch (e) { return e instanceof TypeError; } })()`);
  await sleep(300);
  check("Trusted Types : innerHTML bloqué par la CSP", blocked);
  check("détecteur de violations CSP opérationnel",
    problems.slice(before.length).some((p) => /TrustedHTML|require-trusted-types-for/.test(p)), problems.slice(before.length).join(" | "));

  cdp.close();
}

main()
  .catch((err) => check("exécution du scénario", false, err.stack || err))
  .finally(() => {
    process.stdout.write(`RESULTAT:${JSON.stringify({ results, problems })}\n`);
    process.exit(0);
  });
