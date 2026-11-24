/**
 * État de session EN MÉMOIRE uniquement.
 *
 * Le cookie de session est HttpOnly (illisible en JavaScript) ; le jeton CSRF
 * est conservé dans cette variable de module et relu via GET /api/auth/me au
 * chargement. Rien n'est écrit dans localStorage ni sessionStorage.
 */

const listeners = new Set();

let session = null; // { email, displayName } | null
let csrfToken = null;

export function getSession() {
  return session;
}

export function isAuthenticated() {
  return session !== null;
}

export function getCsrfToken() {
  return csrfToken;
}

/** info : réponse de /api/auth/login ou /api/auth/me. */
export function setSession(info) {
  if (!info || typeof info.email !== "string" || typeof info.csrf_token !== "string") {
    throw new Error("réponse de session invalide");
  }
  session = Object.freeze({ email: info.email, displayName: info.display_name || null });
  csrfToken = info.csrf_token;
  notify();
}

export function clearSession() {
  session = null;
  csrfToken = null;
  notify();
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify() {
  for (const fn of listeners) fn(session);
}
