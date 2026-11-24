/**
 * Client HTTP de l'API d'administration (même origine, via Nginx).
 *
 * - Cookie de session HttpOnly envoyé automatiquement (credentials: same-origin).
 * - X-CSRF-Token ajouté à toute requête mutative.
 * - 401 → gestionnaire de session expirée (redirection vers la connexion).
 * - 403 « Jeton CSRF invalide » → relecture de /api/auth/me puis un seul nouvel essai.
 * - /api/agent/* n'est jamais appelé depuis le navigateur.
 */
import { clearSession, getCsrfToken, setSession } from "./state.js";

const SAFE_METHODS = new Set(["GET", "HEAD"]);
const CSRF_DETAIL = "Jeton CSRF invalide";

const STATUS_MESSAGES = {
  400: "La requête est invalide.",
  401: "Votre session a expiré. Veuillez vous reconnecter.",
  403: "Action non autorisée.",
  404: "Ressource introuvable.",
  409: "Conflit : la ressource a été modifiée entre-temps.",
  413: "Les données envoyées sont trop volumineuses.",
  422: "Certaines données sont invalides.",
  429: "Trop de tentatives. Veuillez patienter une minute avant de réessayer.",
};

export class ApiError extends Error {
  constructor(status, message, data = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

let fetchImpl = (...args) => globalThis.fetch(...args);
let onUnauthorized = () => {};

/** Pour les tests. */
export function setFetch(fn) {
  fetchImpl = fn;
}

export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

export function assertApiPath(path) {
  if (typeof path !== "string" || !path.startsWith("/api/") || path.includes("..") || path.includes("//")) {
    throw new Error("chemin d'API invalide");
  }
  if (path === "/api/agent" || path.startsWith("/api/agent/") || path.startsWith("/api/agent?")) {
    throw new Error("l'API de l'agent n'est pas accessible depuis l'interface");
  }
}

/** Message lisible en français pour une réponse en erreur. */
export function errorMessage(status, data) {
  const detail = data && data.detail;
  if (typeof detail === "string" && detail && status !== 422) return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  if (STATUS_MESSAGES[status]) return STATUS_MESSAGES[status];
  if (status >= 500) return "Le serveur est momentanément indisponible. Réessayez dans quelques instants.";
  return `Erreur inattendue (${status}).`;
}

/**
 * api("/api/members", { method: "POST", body: {...} })
 * options.auth === false : un 401 ne déclenche pas la redirection (connexion, lecture initiale).
 */
export async function api(path, { method = "GET", body, signal, auth = true, retryCsrf = true } = {}) {
  assertApiPath(path);
  method = method.toUpperCase();
  const headers = { Accept: "application/json" };
  const unsafe = !SAFE_METHODS.has(method);
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getCsrfToken();
  if (unsafe && token) headers["X-CSRF-Token"] = token;

  let res;
  try {
    res = await fetchImpl(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "strict-origin-when-cross-origin",
      signal,
    });
  } catch (err) {
    if (err && err.name === "AbortError") throw err;
    throw new ApiError(0, "Connexion au serveur impossible. Vérifiez votre accès au réseau Tailscale.");
  }

  const data = await readBody(res);

  if (res.ok) return data;

  if (res.status === 401 && auth) {
    clearSession();
    onUnauthorized();
  }

  if (res.status === 403 && unsafe && retryCsrf && data && data.detail === CSRF_DETAIL) {
    // Le jeton a pu changer (reconnexion dans un autre onglet) : on le relit une fois.
    const refreshed = await refreshSession();
    if (refreshed) return api(path, { method, body, signal, auth, retryCsrf: false });
  }

  throw new ApiError(res.status, errorMessage(res.status, data), data);
}

async function readBody(res) {
  if (res.status === 204) return null;
  const type = (res.headers && res.headers.get && res.headers.get("content-type")) || "";
  if (!type.includes("application/json")) return null;
  try {
    return await res.json();
  } catch {
    return null;
  }
}

/** Relit la session courante ; true si elle est valide. */
export async function refreshSession() {
  try {
    const info = await api("/api/auth/me", { auth: false });
    setSession(info);
    return true;
  } catch {
    return false;
  }
}
