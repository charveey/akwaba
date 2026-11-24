/** Rapports financiers et opérationnels (calculés par le serveur), avec export CSV des mêmes filtres. */
import { api, ApiError, withQuery } from "../api.js";
import { barList, columnChart } from "../components/charts.js";
import { dateTime, day, money } from "../components/format.js";
import { ACCESS } from "../components/labels.js";
import { dataTable } from "../components/table.js";
import { tabs } from "../components/tabs.js";
import { errorState, h, loadingState } from "../dom.js";
import { replaceQuery } from "../router.js";

const xof = (v) => money(v, "XOF");
const int = (v) => new Intl.NumberFormat("fr-FR").format(v);
const UNITS = [{ value: "month", label: "Par mois" }, { value: "quarter", label: "Par trimestre" }, { value: "year", label: "Par année" }];

export function render(container, { query }) {
  const controller = new AbortController();
  const tab = query.get("onglet") === "operations" ? "operations" : "finances";
  const state = { from: query.get("du") || "", to: query.get("au") || "", unit: query.get("unite") || "month" };
  const body = h("div", { class: "section-gap" });
  const dateInput = (label, key) => {
    const el = h("input", { class: "input", type: "date", "aria-label": label, value: state[key] });
    el.addEventListener("change", () => { state[key] = el.value; sync(); load(); });
    return h("label", { class: "inline-field" }, h("span", { text: label }), el);
  };
  const unit = h("select", { class: "input", "aria-label": "Regroupement" }, UNITS.map((u) => h("option", { value: u.value, selected: u.value === state.unit }, u.label)));
  unit.addEventListener("change", () => { state.unit = unit.value; sync(); load(); });

  container.replaceChildren(
    tabs({ label: "Types de rapports", active: tab,
      items: [{ id: "finances", label: "Rapports financiers" }, { id: "operations", label: "Rapports opérationnels" }],
      onSelect: (id) => { location.hash = id === "operations" ? "#/rapports?onglet=operations" : "#/rapports"; } }),
    h("div", { class: "toolbar section-gap" }, h("div", { class: "toolbar__filters" },
      dateInput("Du", "from"), dateInput("Au", "to"), tab === "finances" ? unit : null)),
    h("p", { class: "field__hint", text: "Périodes en dates UTC (du … au … inclus). Les exports CSV reprennent exactement les mêmes filtres et l'ensemble des résultats." }),
    body);

  function sync() {
    replaceQuery("/rapports", { onglet: tab === "operations" ? "operations" : "", du: state.from, au: state.to, unite: state.unit !== "month" ? state.unit : "" });
  }

  const range = () => ({ date_from: state.from, date_to: state.to });
  const csv = (path, params) => h("a", { class: "btn btn--small", href: withQuery(path, { ...params, format: "csv" }), download: "", text: "Exporter en CSV" });
  const section = (title, path, params, content) => h("section", { class: "card" },
    h("div", { class: "card__header" }, h("h2", { class: "card__title", text: title }), csv(path, params)), content);
  const amountTable = (rows, first) => dataTable({ rows, empty: "Aucune donnée sur la période.", columns: [
    { label: first, render: (r) => r.label }, { label: "Nombre", render: (r) => int(r.count) }, { label: "Montant", render: (r) => xof(r.amount_xof) },
  ] });

  async function loadFinances() {
    const unitParams = { ...range(), group_by: state.unit };
    const [rev, byPlan, byMethod, exp, byCat, profit] = await Promise.all([
      api(withQuery("/api/reports/revenue", unitParams), { signal: controller.signal }),
      api(withQuery("/api/reports/revenue-by-plan", range()), { signal: controller.signal }),
      api(withQuery("/api/reports/revenue", { ...range(), group_by: "payment_method" }), { signal: controller.signal }),
      api(withQuery("/api/reports/expenses", unitParams), { signal: controller.signal }),
      api(withQuery("/api/reports/expenses", { ...range(), group_by: "category" }), { signal: controller.signal }),
      api(withQuery("/api/reports/profit", unitParams), { signal: controller.signal }),
    ]);
    const unitLabel = UNITS.find((u) => u.value === state.unit).label.replace("Par ", "");
    body.replaceChildren(
      h("div", { class: "card-grid" },
        h("div", { class: "card stat" }, h("span", { class: "stat__label", text: "Recettes" }), h("span", { class: "stat__value", text: xof(profit.revenue_xof) })),
        h("div", { class: "card stat" }, h("span", { class: "stat__label", text: "Dépenses" }), h("span", { class: "stat__value", text: xof(profit.expenses_xof) })),
        h("div", { class: ["card stat", profit.profit_xof < 0 && "stat--alert"] }, h("span", { class: "stat__label", text: "Résultat net" }), h("span", { class: "stat__value", text: xof(profit.profit_xof) }))),
      section("Résultat net", "/api/reports/profit", unitParams, h("div", {},
        columnChart({ title: `Recettes et dépenses (${unitLabel})`, labels: profit.rows.map((r) => r.label),
          bars: [{ name: "Recettes", values: profit.rows.map((r) => r.revenue_xof) }, { name: "Dépenses", values: profit.rows.map((r) => r.expenses_xof), className: "is-warning" }],
          format: xof }),
        dataTable({ rows: profit.rows, empty: "Aucune donnée.", columns: [
          { label: "Période", render: (r) => r.label }, { label: "Recettes", render: (r) => xof(r.revenue_xof) },
          { label: "Dépenses", render: (r) => xof(r.expenses_xof) }, { label: "Résultat", render: (r) => xof(r.profit_xof) },
        ] }))),
      h("div", { class: "charts-grid section-gap" },
        section(`Recettes (${unitLabel})`, "/api/reports/revenue", unitParams, amountTable(rev.rows, "Période")),
        section(`Dépenses (${unitLabel})`, "/api/reports/expenses", unitParams, amountTable(exp.rows, "Période")),
        section("Répartition par abonnement", "/api/reports/revenue-by-plan", range(), h("div", {},
          barList({ title: "Recettes par formule", items: byPlan.rows.map((r) => ({ label: r.label, value: r.amount_xof })), format: xof }),
          amountTable(byPlan.rows, "Formule"))),
        section("Répartition par moyen de paiement", "/api/reports/revenue", { ...range(), group_by: "payment_method" }, h("div", {},
          barList({ title: "Recettes par moyen de paiement", items: byMethod.rows.map((r) => ({ label: r.label, value: r.amount_xof })), format: xof }),
          amountTable(byMethod.rows, "Moyen"))),
        section("Dépenses par catégorie", "/api/reports/expenses", { ...range(), group_by: "category" }, amountTable(byCat.rows, "Catégorie"))));
  }

  async function loadOperations() {
    const r = await api(withQuery("/api/reports/operations", range()), { signal: controller.signal });
    body.replaceChildren(
      h("section", { class: "card" },
        h("h2", { class: "card__title", text: `Instantané au ${day(r.today)} (UTC)` }),
        h("div", { class: "card-grid" },
          ...["ACTIVE", "GRACE_PERIOD", "EXPIRED", "NONE"].map((k) => h("div", { class: "card stat" },
            h("span", { class: "stat__label", text: k === "NONE" ? "Sans accès (aucun abonnement en vigueur)" : `Membres — ${ACCESS[k].label.toLowerCase()}` }),
            h("span", { class: "stat__value", text: int(r.snapshot[k] || 0) })))),
        h("p", { class: "field__hint", text: `${int(r.members_total)} membres non archivés. Calculé le ${dateTime(r.evaluated_at)} ; aucun historique des effectifs n'est reconstitué.` })),
      section(`Flux mensuels du ${day(r.date_from)} au ${day(r.date_to)}`, "/api/reports/operations", range(), h("div", {},
        columnChart({ title: "Nouveaux membres et renouvellements par mois", labels: r.rows.map((x) => x.label),
          bars: [{ name: "Nouveaux membres", values: r.rows.map((x) => x.new_members), className: "is-secondary" },
            { name: "Renouvellements (à l'heure + en retard)", values: r.rows.map((x) => x.renewals + x.late_renewals) }], format: int }),
        dataTable({ rows: r.rows, empty: "Aucune donnée.", columns: [
          { label: "Mois", render: (x) => x.label },
          { label: "Nouveaux membres", render: (x) => int(x.new_members) },
          { label: "Statut « Nouveau membre »", render: (x) => int(x.new_member_subscriptions) },
          { label: "Renouvelés", render: (x) => int(x.renewals) },
          { label: "En retard", render: (x) => int(x.late_renewals) },
          { label: "Changements de niveau", render: (x) => int(x.plan_changes) },
          { label: "Dossiers ouverts", render: (x) => int(x.quarantine_opened) },
          { label: "Identités observées (nouvelles)", render: (x) => int(x.identities_first_seen) },
        ] }))));
  }

  async function load() {
    body.replaceChildren(loadingState("Calcul du rapport…"));
    try {
      await (tab === "operations" ? loadOperations() : loadFinances());
    } catch (err) {
      if (err.name === "AbortError") return;
      body.replaceChildren(errorState("Impossible de calculer le rapport.", err instanceof ApiError ? err.message : String(err), load));
    }
  }
  load();
  return () => controller.abort();
}
