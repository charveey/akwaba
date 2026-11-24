/** Connexion, déconnexion, session expirée — via les endpoints existants du backend. */
import { api, ApiError, refreshSession } from "./api.js";
import { clearSession, setSession } from "./state.js";

/** Au démarrage : la session (cookie HttpOnly) est-elle encore valide ? */
export async function restoreSession() {
  return refreshSession();
}

export async function login(email, password) {
  const info = await api("/api/auth/login", {
    method: "POST",
    body: { email: email.trim(), password },
    auth: false,
  });
  setSession(info);
  return info;
}

/** Révoque la session côté serveur ; l'état local est vidé dans tous les cas. */
export async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST", auth: false });
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401) throw err;
  } finally {
    clearSession();
  }
}
