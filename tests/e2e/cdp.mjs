/**
 * Petit client du protocole DevTools de Chrome (aucune dépendance : WebSocket et fetch de Node >= 22)
 * et outils de scénario partagés par les tests de bout en bout.
 */
import { createInterface } from "node:readline";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function scenario() {
  const results = [];
  const problems = [];
  const stdin = createInterface({ input: process.stdin })[Symbol.asyncIterator]();
  return {
    results,
    problems,
    check(name, ok, detail = "") {
      results.push({ name, ok: Boolean(ok), detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
    },
    async waitHost(key) {
      process.stdout.write(`ATTENTE:${key}\n`);
      await stdin.next();
    },
    finish() {
      process.stdout.write(`RESULTAT:${JSON.stringify({ results, problems })}\n`);
      process.exit(0);
    },
  };
}

/** `requests` (facultatif) reçoit l'URL de chaque requête réseau émise par la page (lot 12c). */
export async function connect(devtoolsPort, problems, requests = null) {
  const res = await fetch(`http://127.0.0.1:${devtoolsPort}/json/new?about:blank`, { method: "PUT" });
  const target = await res.json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((ok, ko) => { ws.onopen = ok; ws.onerror = ko; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
      return;
    }
    if (requests && msg.method === "Network.requestWillBeSent") requests.push(msg.params.request.url);
    if (msg.method === "Runtime.exceptionThrown") {
      problems.push(`exception: ${msg.params.exceptionDetails.text} ${msg.params.exceptionDetails.exception?.description || ""}`);
    } else if (msg.method === "Runtime.consoleAPICalled" && ["error", "warning", "assert"].includes(msg.params.type)) {
      problems.push(`console.${msg.params.type}: ${msg.params.args.map((a) => a.value ?? a.description).join(" ")}`);
    } else if (msg.method === "Log.entryAdded") {
      const e = msg.params.entry;
      if (e.source !== "network" && (e.level === "error" || e.level === "warning")) problems.push(`${e.source}: ${e.text}`);
    } else if (msg.method === "Audits.issueAdded") {
      const code = msg.params.issue.code;
      if (/ContentSecurityPolicy|TrustedTypes|MixedContent|Cookie/i.test(code)) problems.push(`issue: ${code}`);
    }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const n = ++id;
    pending.set(n, { resolve, reject });
    ws.send(JSON.stringify({ id: n, method, params }));
  });
  for (const domain of ["Runtime", "Log", "Audits", "Network", "Page"]) await send(`${domain}.enable`);
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });

  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const waitFor = async (expression, timeout = 8000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      try {
        if (await evaluate(expression)) return true;
      } catch { /* chargement en cours */ }
      await sleep(100);
    }
    return false;
  };
  return { send, evaluate, waitFor, close: () => ws.close() };
}

/**
 * Fonctions injectées dans la page (via Runtime.evaluate) pour piloter l'interface comme un
 * utilisateur : remplir une modale, cliquer un bouton par son libellé.
 */
export const PAGE_HELPERS = `
window.__t = {
  dialog() { return document.querySelector("dialog[open]"); },
  fill(values, root = document.querySelector("dialog[open]")) {
    for (const [name, value] of Object.entries(values)) {
      const el = root.querySelector('[name="' + name + '"]');
      if (!el) throw new Error("champ introuvable : " + name);
      if (el.type === "checkbox") el.checked = Boolean(value);
      else if (el.tagName === "SELECT") {
        const opt = [...el.options].find((o) => o.value === value || o.textContent.startsWith(value));
        if (!opt) throw new Error("option introuvable : " + name + "=" + value);
        el.value = opt.value;
      } else el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
  },
  submit() { document.querySelector("dialog[open] form").requestSubmit(); },
  click(label, root = document) {
    const b = [...root.querySelectorAll("button, a")].find((x) => x.textContent.trim() === label && !x.disabled);
    if (!b) throw new Error("bouton introuvable : " + label);
    b.click();
  },
  text(sel) { return document.querySelector(sel)?.textContent.trim() ?? null; },
  rows() { return [...document.querySelectorAll(".content .table tbody tr")].map((tr) => tr.textContent.replace(/\\s+/g, " ").trim()); },
  async api(path, init = {}) {
    const me = await (await fetch("/api/auth/me")).json();
    const r = await fetch(path, { ...init, headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf_token, ...(init.headers || {}) } });
    return { status: r.status, body: r.headers.get("content-type")?.includes("json") ? await r.json() : null };
  },
};
true;
`;
