/** Journal d'audit (lecture seule) : acteur normalisé par le serveur, détails masqués par le serveur. */
import { api, ApiError, withQuery } from "../api.js";
import { dateTime } from "../components/format.js";
import { ACTOR_KIND, AUDIT_ACTIONS, statusBadge } from "../components/labels.js";
import { pagination } from "../components/pagination.js";
import { dataTable } from "../components/table.js";
import { errorState, h, loadingState } from "../dom.js";
import { replaceQuery } from "../router.js";

const LIMIT = 50;

export function render(container, { query }) {
  const controller = new AbortController();
  const state = {
    action: query.get("action") || "", type: query.get("type") || "", entity: query.get("entite") || "",
    from: query.get("du") || "", to: query.get("au") || "",
    offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
  };
  const action = h("select", { class: "input", "aria-label": "Filtrer par action" },
    h("option", { value: "", text: "Toutes les actions" }),
    Object.entries(AUDIT_ACTIONS).sort((a, b) => a[1].localeCompare(b[1], "fr")).map(([k, v]) => h("option", { value: k, selected: k === state.action }, v)));
  action.addEventListener("change", () => { state.action = action.value; refresh(); });
  const text = (label, key, placeholder) => {
    const el = h("input", { class: "input", type: "search", "aria-label": label, placeholder, value: state[key], maxlength: "80" });
    el.addEventListener("change", () => { state[key] = el.value.trim(); refresh(); });
    return el;
  };
  const dateInput = (label, key) => {
    const el = h("input", { class: "input", type: "date", "aria-label": label, value: state[key] });
    el.addEventListener("change", () => { state[key] = el.value; refresh(); });
    return h("label", { class: "inline-field" }, h("span", { text: label }), el);
  };
  const results = h("div", { class: "section-gap" });
  container.replaceChildren(
    h("div", { class: "toolbar" }, h("div", { class: "toolbar__filters" },
      action, text("Type d'entité", "type", "Type d'entité (ex. member)"), text("Identifiant d'entité", "entity", "Identifiant de l'entité"),
      dateInput("Du", "from"), dateInput("Au", "to"))),
    h("p", { class: "field__hint", text: "Journal append-only : rien n'est modifié ni supprimé. L'acteur est celui de la session authentifiée ; les tentatives de connexion sont distinguées des actions d'administrateurs. Les clés sensibles éventuelles sont masquées." }),
    results);

  function refresh(reset = true) {
    if (reset) state.offset = 0;
    replaceQuery("/audit", { action: state.action, type: state.type, entite: state.entity, du: state.from, au: state.to, debut: state.offset || "" });
    load();
  }

  async function load() {
    results.replaceChildren(loadingState());
    try {
      const page = await api(withQuery("/api/audit", {
        action: state.action, entity_type: state.type, entity_id: state.entity, date_from: state.from, date_to: state.to,
        limit: LIMIT, offset: state.offset,
      }), { signal: controller.signal });
      results.replaceChildren(
        dataTable({ rows: page.items, caption: "Journal d'audit", empty: "Aucun événement ne correspond à ces critères.", columns: [
          { label: "Date", render: (e) => dateTime(e.occurred_at) },
          { label: "Action", render: (e) => h("span", { class: "stacked" }, AUDIT_ACTIONS[e.action] || e.action, h("small", { class: "mono", text: e.action })) },
          { label: "Acteur", render: (e) => h("span", { class: "stacked" }, statusBadge(ACTOR_KIND, e.actor_kind), h("small", { text: e.actor_display || "—" })) },
          { label: "Entité", render: (e) => e.entity_type },
          { label: "Identifiant", className: "mono", render: (e) => e.entity_id },
          { label: "Adresse IP", className: "mono", render: (e) => e.ip_address },
          { label: "Détails", render: (e) => (e.details && Object.keys(e.details).length
            ? h("details", {}, h("summary", { text: "Afficher" }), h("pre", { class: "audit-details", text: JSON.stringify(e.details, null, 2) }))
            : "—") },
        ] }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; refresh(false); } }));
    } catch (err) {
      if (err.name === "AbortError") return;
      results.replaceChildren(errorState("Impossible de charger le journal.", err instanceof ApiError ? err.message : String(err), load));
    }
  }
  load();
  return () => controller.abort();
}
