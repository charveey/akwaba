/** Vue d'ensemble : 9 indicateurs et 5 graphiques, tous calculés par le serveur (GET /api/dashboard). */
import { api, ApiError } from "../api.js";
import { badge } from "../components/badge.js";
import { barList, columnChart } from "../components/charts.js";
import { dateTime, day, money } from "../components/format.js";
import { QUARANTINE_STATUS } from "../components/labels.js";
import { errorState, h, loadingState } from "../dom.js";

const xof = (v) => money(v, "XOF");
const int = (v) => new Intl.NumberFormat("fr-FR").format(v);

function stat(label, value, { href, hint, alert = false } = {}) {
  const inner = [h("span", { class: "stat__label", text: label }), h("span", { class: "stat__value", text: value }),
    hint ? h("span", { class: "stat__hint", text: hint }) : null];
  return href
    ? h("a", { class: ["card stat stat--link", alert && "stat--alert"], href }, inner)
    : h("div", { class: ["card stat", alert && "stat--alert"] }, inner);
}

export function render(container) {
  const controller = new AbortController();
  let alive = true;

  /** « État du système » (lot 11a) : preuve navigateur → Nginx → backend → PostgreSQL, chargée indépendamment. */
  function systemCard() {
    const card = h("section", { class: "card section-gap", "aria-labelledby": "status-title" },
      h("h2", { class: "card__title", id: "status-title", text: "État du système" }), loadingState("Vérification…"));
    api("/api/health/details", { signal: controller.signal })
      .then((health) => { if (alive) card.replaceChildren(card.firstChild, healthView(health)); })
      .catch((err) => {
        if (!alive || err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 503 && err.data) {
          card.replaceChildren(card.firstChild, healthView(err.data));
          return;
        }
        card.replaceChildren(card.firstChild, errorState("État indisponible", err instanceof ApiError ? err.message : String(err)));
      });
    return card;
  }

  async function load() {
    container.replaceChildren(loadingState("Calcul des indicateurs…"));
    try {
      const d = await api("/api/dashboard", { signal: controller.signal });
      if (!alive) return;
      const i = d.indicators;
      const agentHint = i.agent.exit_nodes
        ? `${i.agent.healthy_nodes}/${i.agent.exit_nodes} sain(s) selon le réconciliateur`
        : "Aucun Exit Node enregistré";
      container.replaceChildren(
        h("div", { class: "card-grid" },
          stat("Membres", int(i.members_total), { href: "#/membres", hint: "hors archivés" }),
          stat("Membres actifs", int(i.members_active), { href: "#/membres?acces=ACTIVE" }),
          stat("En période de grâce", int(i.members_grace), { href: "#/membres?acces=GRACE_PERIOD" }),
          stat("Abonnements expirés", int(i.subscriptions_expired), { href: "#/membres?acces=EXPIRED", hint: "grâce dépassée" }),
          stat("Paiements en attente", int(i.payments_pending), { href: "#/abonnements?statut=PENDING", alert: i.payments_pending > 0 }),
          stat("Recettes du mois", xof(i.revenue_month_xof), { href: "#/paiements", hint: `${i.revenue_month_count} paiement(s) depuis le ${day(d.month_start)} (UTC)` }),
          stat("Dossiers de quarantaine ouverts", int(i.quarantine_open), { href: "#/quarantaine", alert: i.quarantine_open > 0 }),
          stat("Identités non résolues", int(i.unresolved_sources_24h), { href: "#/identites", hint: "sources des dernières 24 h" }),
          stat("Dernière activité de l'agent", i.agent.last_heartbeat_at ? dateTime(i.agent.last_heartbeat_at) : "Jamais",
            { href: "#/agent", hint: agentHint, alert: i.agent.exit_nodes > 0 && i.agent.healthy_nodes < i.agent.exit_nodes })),
        h("p", { class: "field__hint", text: `Indicateurs calculés par le serveur le ${dateTime(d.evaluated_at)} sur l'ensemble des données (dates de calcul en UTC).` }),
        h("div", { class: "charts-grid section-gap" },
          h("section", { class: "card" }, columnChart({
            title: "Évolution des recettes (12 mois)", labels: d.revenue_by_month.map((r) => r.label),
            bars: [{ name: "Recettes (XOF)", values: d.revenue_by_month.map((r) => r.amount_xof) }], format: xof,
          })),
          h("section", { class: "card" }, barList({
            title: "Répartition des abonnements en vigueur", description: "Membres actifs ou en période de grâce, par formule.",
            items: d.subscriptions_by_plan.map((p) => ({ label: p.plan_name, value: p.count })), format: int,
            empty: "Aucun abonnement en vigueur.",
          })),
          h("section", { class: "card" }, columnChart({
            title: "Évolution des membres",
            description: "Nouveaux membres par mois et inscriptions cumulées (archivés compris). Ce n'est pas l'historique du nombre de membres actifs.",
            labels: d.members_by_month.map((r) => r.label),
            bars: [{ name: "Nouveaux membres", values: d.members_by_month.map((r) => r.new_members), className: "is-secondary" }],
            line: { name: "Inscriptions cumulées", values: d.members_by_month.map((r) => r.cumulative), className: "is-line" },
            format: int,
          })),
          h("section", { class: "card" }, barList({
            title: "Paiements par moyen de paiement (12 mois)",
            items: d.payments_by_method.map((r) => ({ label: `${r.label} (${r.count})`, value: r.amount_xof })), format: xof,
            empty: "Aucun paiement sur la période.",
          })),
          h("section", { class: "card" }, barList({
            title: "Dossiers de quarantaine par statut",
            items: d.quarantine_by_status.map((s) => ({ label: QUARANTINE_STATUS[s.status]?.label || s.status, value: s.count,
              className: { PENDING: "is-warning", EXPIRED: "is-danger", PENDING_REVOCATION: "is-danger", MANUAL_REVIEW: "is-warning", DISMISSED: "is-neutral", AUTHORIZED: "is-secondary" }[s.status] })),
            format: int,
          }))),
        systemCard());
    } catch (err) {
      if (!alive || err.name === "AbortError") return;
      container.replaceChildren(errorState("Impossible de calculer la vue d'ensemble.", err instanceof ApiError ? err.message : String(err), load));
    }
  }
  load();
  return () => { alive = false; controller.abort(); };
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
