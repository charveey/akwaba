/**
 * Vue d'ensemble — lot 11a : état du système uniquement (preuve de bout en bout
 * navigateur → tailscale serve → Nginx → backend → PostgreSQL).
 * Les indicateurs métier arrivent au lot 11d.
 */
import { api, ApiError } from "../api.js";
import { badge } from "../components/badge.js";
import { errorState, h, loadingState } from "../dom.js";
import { getSession } from "../state.js";
import { render as renderPlaceholder } from "./placeholder.js";

export function render(container, ctx) {
  const status = h("div", { class: "card", "aria-labelledby": "status-title" },
    h("h2", { class: "card__title", id: "status-title", text: "État du système" }), loadingState("Vérification…"));
  const upcoming = h("div", { class: "section-gap" });
  const session = getSession();
  container.replaceChildren(
    h("p", { class: "page-intro", text: `Bienvenue${session ? ", " + (session.displayName || session.email) : ""}.` }),
    status, upcoming);
  renderPlaceholder(upcoming, ctx);

  let cancelled = false;
  const controller = new AbortController();

  async function load() {
    status.replaceChildren(status.firstChild, loadingState("Vérification…"));
    try {
      const health = await api("/api/health/details", { signal: controller.signal });
      if (cancelled) return;
      status.replaceChildren(status.firstChild, healthView(health));
    } catch (err) {
      if (cancelled || (err && err.name === "AbortError")) return;
      if (err instanceof ApiError && err.status === 503 && err.data) {
        status.replaceChildren(status.firstChild, healthView(err.data));
        return;
      }
      status.replaceChildren(status.firstChild,
        errorState("État indisponible", err instanceof ApiError ? err.message : String(err), load));
    }
  }

  load();
  return () => {
    cancelled = true;
    controller.abort();
  };
}

function healthView(health) {
  const dbOk = Boolean(health && health.database && health.database.ok);
  const mode = (health && health.mode) || {};
  const dryRun = mode.revocation_mode === "dry_run" && mode.enforcer_enabled === false;
  return h("dl", { class: "dl" },
    h("dt", { text: "Backend" }), h("dd", {}, badge("Disponible", "success")),
    h("dt", { text: "Base de données" }), h("dd", {}, badge(dbOk ? "Opérationnelle" : "Dégradée", dbOk ? "success" : "danger")),
    h("dt", { text: "Mode de révocation" }),
    h("dd", {}, badge(dryRun ? "Observation (dry-run)" : "Inattendu — vérifier la configuration", dryRun ? "info" : "danger")),
    h("dt", { text: "Version" }), h("dd", { class: "mono", text: (health && health.version) || "—" }));
}
