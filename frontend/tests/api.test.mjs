import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { api, ApiError, assertApiPath, errorMessage, setFetch, setUnauthorizedHandler } from "../assets/js/api.js";
import { clearSession, getCsrfToken, isAuthenticated, setSession } from "../assets/js/state.js";

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => (k.toLowerCase() === "content-type" && body !== undefined ? "application/json" : null) },
    json: async () => body,
  };
}

let calls;
function mockFetch(...responses) {
  calls = [];
  setFetch(async (url, init) => {
    calls.push({ url, init });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return next;
  });
}

beforeEach(() => {
  clearSession();
  setUnauthorizedHandler(() => {});
});

test("GET : cookie same-origin, pas de cache, pas de jeton CSRF", async () => {
  setSession({ email: "a@b.c", display_name: null, csrf_token: "tok" });
  mockFetch(response(200, { ok: 1 }));
  assert.deepEqual(await api("/api/members"), { ok: 1 });
  const { init } = calls[0];
  assert.equal(init.method, "GET");
  assert.equal(init.credentials, "same-origin");
  assert.equal(init.cache, "no-store");
  assert.equal(init.redirect, "error");
  assert.equal(init.headers["X-CSRF-Token"], undefined);
});

for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
  test(`${method} : en-tête X-CSRF-Token et corps JSON`, async () => {
    setSession({ email: "a@b.c", display_name: null, csrf_token: "tok" });
    mockFetch(response(200, {}));
    await api("/api/members", { method, body: { x: 1 } });
    assert.equal(calls[0].init.headers["X-CSRF-Token"], "tok");
    assert.equal(calls[0].init.headers["Content-Type"], "application/json");
    assert.equal(calls[0].init.body, '{"x":1}');
  });
}

test("401 → session vidée et gestionnaire de session expirée appelé", async () => {
  setSession({ email: "a@b.c", display_name: null, csrf_token: "tok" });
  let expired = 0;
  setUnauthorizedHandler(() => expired++);
  mockFetch(response(401, { detail: "Non authentifié" }));
  await assert.rejects(api("/api/members"), (e) => e instanceof ApiError && e.status === 401);
  assert.equal(expired, 1);
  assert.equal(isAuthenticated(), false);
  assert.equal(getCsrfToken(), null);
});

test("401 avec auth:false (connexion) → pas de redirection", async () => {
  let expired = 0;
  setUnauthorizedHandler(() => expired++);
  mockFetch(response(401, { detail: "Identifiants invalides" }));
  await assert.rejects(api("/api/auth/login", { method: "POST", body: {}, auth: false }), /Identifiants invalides/);
  assert.equal(expired, 0);
});

test("403 jeton CSRF invalide → relecture de /me puis un seul nouvel essai", async () => {
  setSession({ email: "a@b.c", display_name: null, csrf_token: "ancien" });
  mockFetch(
    response(403, { detail: "Jeton CSRF invalide" }),
    response(200, { email: "a@b.c", display_name: null, csrf_token: "nouveau" }),
    response(200, { ok: true }),
  );
  assert.deepEqual(await api("/api/members", { method: "POST", body: {} }), { ok: true });
  assert.equal(calls[1].url, "/api/auth/me");
  assert.equal(calls[2].init.headers["X-CSRF-Token"], "nouveau");
});

test("403 répété → pas de boucle", async () => {
  setSession({ email: "a@b.c", display_name: null, csrf_token: "t" });
  mockFetch(
    response(403, { detail: "Jeton CSRF invalide" }),
    response(200, { email: "a@b.c", display_name: null, csrf_token: "t2" }),
    response(403, { detail: "Jeton CSRF invalide" }),
  );
  await assert.rejects(api("/api/members", { method: "POST" }), (e) => e.status === 403);
  assert.equal(calls.length, 3);
});

test("403 origine refusée : pas de nouvel essai", async () => {
  mockFetch(response(403, { detail: "Origine de la requête refusée" }));
  await assert.rejects(api("/api/members", { method: "POST" }), /Origine de la requête refusée/);
  assert.equal(calls.length, 1);
});

test("erreur réseau → message français", async () => {
  mockFetch(new TypeError("Failed to fetch"));
  await assert.rejects(api("/api/members"), (e) => e.status === 0 && /Connexion au serveur impossible/.test(e.message));
});

test("204 → null", async () => {
  mockFetch(response(204));
  assert.equal(await api("/api/auth/logout", { method: "POST" }), null);
});

test("l'API de l'agent et les chemins invalides sont refusés côté client", () => {
  for (const p of ["/api/agent", "/api/agent/ping", "/api/agent?x", "/autre", "//evil/api/", "/api/../x", "https://x/api/"]) {
    assert.throws(() => assertApiPath(p), p);
  }
  assert.doesNotThrow(() => assertApiPath("/api/agentx"));
});

test("messages d'erreur", () => {
  assert.equal(errorMessage(422, { detail: [{ msg: "field required" }] }), "Certaines données sont invalides.");
  assert.equal(errorMessage(422, { detail: { message: "Requête invalide" } }), "Requête invalide");
  assert.equal(errorMessage(409, { detail: "Membre déjà archivé" }), "Membre déjà archivé");
  assert.match(errorMessage(429, null), /Trop de tentatives/);
  assert.match(errorMessage(502, null), /indisponible/);
});
