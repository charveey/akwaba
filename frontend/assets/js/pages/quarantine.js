/** Quarantaine : onglets « Dossiers » et « Décisions simulées ». Aucune action réseau. */
import { api, ApiError, withQuery } from "../api.js";
import { badge } from "../components/badge.js";
import { dateTime, remaining } from "../components/format.js";
import { DECISION, OPEN_STATUSES, QUARANTINE_STATUS, VERDICT, options, reason, statusBadge } from "../components/labels.js";
import { pagination } from "../components/pagination.js";
import { dataTable } from "../components/table.js";
import { tabs } from "../components/tabs.js";
import { toast } from "../components/toast.js";
import { errorState, h, loadingState } from "../dom.js";
import { replaceQuery } from "../router.js";
import { simulationNotice, simulationState } from "./identity-detail.js";
import { resolveDialog } from "./quarantine-detail.js";

const LIMIT = 25;

export function render(container, { query }) {
  const controller = new AbortController();
  const tab = query.get("onglet") === "decisions" ? "decisions" : "dossiers";
  const body = h("div", { class: "section-gap" });
  container.replaceChildren(
    tabs({ label: "Vues de la quarantaine", active: tab,
      items: [{ id: "dossiers", label: "Dossiers" }, { id: "decisions", label: "Décisions simulées" }],
      onSelect: (id) => { location.hash = id === "decisions" ? "#/quarantaine?onglet=decisions" : "#/quarantaine"; } }),
    body);
  if (tab === "decisions") decisions(body, query, controller.signal);
  else records(body, query, controller.signal);
  return () => controller.abort();
}

/** Ce qu'il reste à savoir sur le délai, d'après les valeurs calculées par le serveur. */
export function timing(r) {
  if (r.status === "PENDING") return r.remaining_seconds > 0 ? `${remaining(r.remaining_seconds)} avant blocage simulé` : "Délai écoulé (traitement au prochain passage)";
  if (r.status === "MANUAL_REVIEW") return "Aucun blocage automatique";
  if (r.status === "DISMISSED" && r.suppressed_until) {
    return r.reopen_possible ? "Réouverture possible" : `Pas de réouverture avant le ${dateTime(r.suppressed_until)}`;
  }
  if (r.status === "EXPIRED") return "Blocage simulé en vigueur";
  if (r.status === "PENDING_REVOCATION") return "État exceptionnel (réservé V2)";
  return "—";
}

function records(box, query, signal) {
  const state = {
    status: query.get("statut") || "", open: query.get("ouverts") !== "0", q: query.get("q") || "",
    offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
  };
  const status = h("select", { class: "input", "aria-label": "Filtrer par statut" },
    options(QUARANTINE_STATUS, "Tous les statuts").map((o) => h("option", { value: o.value, selected: o.value === state.status }, o.label)));
  status.addEventListener("change", () => { state.status = status.value; refresh(); });
  const open = h("input", { type: "checkbox", checked: state.open });
  open.addEventListener("change", () => { state.open = open.checked; refresh(); });
  let timer = null;
  const search = h("input", { class: "input", type: "search", placeholder: "Login Tailscale…", "aria-label": "Rechercher un dossier", value: state.q });
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); refresh(); }, 300); });
  const counts = h("div", { class: "chips" });
  const exceptional = h("div");
  const results = h("div", { class: "section-gap" });
  box.replaceChildren(
    exceptional,
    h("div", { class: "toolbar" }, h("div", { class: "toolbar__filters" }, search, status,
      h("label", { class: "choice" }, open, h("span", { text: "Dossiers ouverts uniquement" })))),
    counts, results);

  function refresh(reset = true) {
    if (reset) state.offset = 0;
    replaceQuery("/quarantaine", { statut: state.status, ouverts: state.open ? "" : "0", q: state.q, debut: state.offset || "" });
    load();
  }

  async function act(promise, message) {
    try {
      if (await promise) { toast(message, "success"); load(); }
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  let seq = 0;
  async function load() {
    const mine = ++seq;
    results.replaceChildren(loadingState());
    try {
      const page = await api(withQuery("/api/quarantine", {
        status: state.status, open_only: state.open ? "true" : "", q: state.q, limit: LIMIT, offset: state.offset,
      }), { signal });
      if (mine !== seq) return;
      counts.replaceChildren(...Object.keys(QUARANTINE_STATUS).map((s) =>
        h("span", { class: "chip" }, statusBadge(QUARANTINE_STATUS, s), h("strong", { text: ` ${page.counts[s] || 0}` }))));
      exceptional.replaceChildren(page.counts.PENDING_REVOCATION ? exceptionalNotice() : "");
      results.replaceChildren(
        dataTable({
          rows: page.items, caption: "Dossiers de quarantaine", empty: "Aucun dossier ne correspond à ces critères.",
          columns: [
            { label: "Identité", render: (r) => h("a", { href: `#/identites/${r.exit_node_identity_id}`, class: "mono", text: r.login_name }) },
            { label: "Ouvert le", render: (r) => dateTime(r.started_at) },
            { label: "Dernière activité", render: (r) => dateTime(r.last_confirmed_activity_at) },
            { label: "Verdict actuel", render: (r) => h("span", { class: "stacked" }, r.current_verdict ? statusBadge(VERDICT, r.current_verdict) : "—", h("small", { text: reason(r.current_reason) })) },
            { label: "Statut", render: (r) => h("span", { class: "badges" }, statusBadge(QUARANTINE_STATUS, r.status), r.exempt ? badge("Exemptée", "info") : null) },
            { label: "Délai", render: (r) => timing(r) },
            { label: "Actions", render: (r) => h("span", { class: "actions" },
              h("a", { class: "btn btn--small", href: `#/quarantaine/${r.id}`, text: "Détail" }),
              OPEN_STATUSES.has(r.status) ? h("button", { class: "btn btn--small btn--primary", type: "button", text: "Autoriser", onClick: () => act(resolveDialog(r, "authorize"), "Dossier autorisé.") }) : null,
              OPEN_STATUSES.has(r.status) ? h("button", { class: "btn btn--small btn--danger-outline", type: "button", text: "Écarter", onClick: () => act(resolveDialog(r, "dismiss"), "Dossier écarté.") }) : null) },
          ],
        }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; refresh(false); } }));
    } catch (err) {
      if (err.name === "AbortError" || mine !== seq) return;
      results.replaceChildren(errorState("Impossible de charger les dossiers.", err instanceof ApiError ? err.message : String(err), load));
    }
  }
  load();
}

export function exceptionalNotice() {
  return h("div", { class: "alert alert--danger", role: "alert",
    text: "État exceptionnel PENDING_REVOCATION détecté. Cet état est réservé à la V2 et n'est jamais produit par le fonctionnement normal. "
      + "Règle existante : le dossier est traité comme un blocage simulé déjà en vigueur ; un administrateur peut l'autoriser ou l'écarter "
      + "(ce qui écrit un déblocage simulé). Aucune action réseau n'est effectuée." });
}

function decisions(box, query, signal) {
  const state = {
    decision: query.get("type") || "", q: query.get("q") || "", from: query.get("du") || "", to: query.get("au") || "",
    offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
  };
  const select = h("select", { class: "input", "aria-label": "Filtrer par décision" },
    options(DECISION, "Toutes les décisions").map((o) => h("option", { value: o.value, selected: o.value === state.decision }, o.label)));
  select.addEventListener("change", () => { state.decision = select.value; refresh(); });
  const date = (label, key) => {
    const el = h("input", { class: "input", type: "date", "aria-label": label, value: state[key] });
    el.addEventListener("change", () => { state[key] = el.value; refresh(); });
    return h("label", { class: "inline-field" }, h("span", { text: label }), el);
  };
  let timer = null;
  const search = h("input", { class: "input", type: "search", placeholder: "Login Tailscale…", "aria-label": "Rechercher une décision", value: state.q });
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); refresh(); }, 300); });
  const results = h("div", { class: "section-gap" });
  box.replaceChildren(simulationNotice(),
    h("div", { class: "toolbar" }, h("div", { class: "toolbar__filters" }, search, select, date("Du", "from"), date("Au", "to"))),
    results);

  function refresh(reset = true) {
    if (reset) state.offset = 0;
    replaceQuery("/quarantaine", { onglet: "decisions", type: state.decision, q: state.q, du: state.from, au: state.to, debut: state.offset || "" });
    load();
  }

  let seq = 0;
  async function load() {
    const mine = ++seq;
    results.replaceChildren(loadingState());
    try {
      const page = await api(withQuery("/api/enforcement-decisions", {
        decision: state.decision, q: state.q, date_from: state.from, date_to: state.to, limit: LIMIT, offset: state.offset,
      }), { signal });
      if (mine !== seq) return;
      results.replaceChildren(
        dataTable({
          rows: page.items, caption: "Décisions simulées", empty: "Aucune décision simulée.",
          columns: [
            { label: "Date", render: (d) => dateTime(d.decided_at) },
            { label: "Identité", render: (d) => h("a", { href: `#/identites/${d.exit_node_identity_id}`, class: "mono", text: d.login_name }) },
            { label: "Décision", render: (d) => statusBadge(DECISION, d.decision) },
            { label: "Motif", render: (d) => reason(d.reason) },
            { label: "Dossier", render: (d) => (d.quarantine_record_id ? h("a", { href: `#/quarantaine/${d.quarantine_record_id}`, text: "Ouvrir le dossier" }) : "—") },
            { label: "Simulation", render: (d) => simulationState(d) },
          ],
        }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; refresh(false); } }));
    } catch (err) {
      if (err.name === "AbortError" || mine !== seq) return;
      results.replaceChildren(errorState("Impossible de charger les décisions.", err instanceof ApiError ? err.message : String(err), load));
    }
  }
  load();
}
