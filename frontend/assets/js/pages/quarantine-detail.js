/** Détail d'un dossier de quarantaine : état, délai, historique, décisions simulées, actions confirmées. */
import { api, ApiError, withQuery } from "../api.js";
import { badge } from "../components/badge.js";
import { field, readForm } from "../components/form.js";
import { dateTime } from "../components/format.js";
import { AUDIT_ACTIONS, DECISION, OPEN_STATUSES, QUARANTINE_STATUS, VERDICT, reason, statusBadge } from "../components/labels.js";
import { formDialog } from "../components/modal.js";
import { dataTable } from "../components/table.js";
import { toast } from "../components/toast.js";
import { errorState, h, loadingState } from "../dom.js";
import { simulationNotice, simulationState } from "./identity-detail.js";
import { exceptionalNotice, timing } from "./quarantine.js";

const EXPLAIN = {
  PENDING: "Identité non autorisée active. Sans décision avant la fin du délai de 24 h, un blocage SIMULÉ sera enregistré.",
  AUTHORIZED: "Dossier autorisé. Pour une identité inconnue, l'autorisation crée une exemption ; elle ne couvre jamais un abonnement expiré.",
  EXPIRED: "Délai écoulé : un blocage simulé est enregistré. Aucun accès n'est réellement modifié.",
  PENDING_REVOCATION: "État exceptionnel réservé à la V2.",
  MANUAL_REVIEW: "Situation qui demande une décision humaine (membre sans abonnement utilisable, suspension…). Jamais de blocage automatique.",
  DISMISSED: "Dossier écarté sans exemption. Aucune réouverture pendant 7 jours ; réouverture possible exactement à la borne.",
};

/** Autoriser / écarter : confirmation explicite + note facultative (sert de motif à l'exemption). */
export function resolveDialog(r, action) {
  const authorize = action === "authorize";
  const note = field({ label: authorize ? "Motif (affiché sur l'exemption)" : "Note", name: "note", type: "textarea", attrs: { maxlength: "500" } });
  return formDialog({
    title: authorize ? "Autoriser ce dossier ?" : "Écarter ce dossier ?",
    intro: h("div", { class: "alert alert--warning", role: "note", text: authorize
      ? `${r.login_name} sera autorisée. Si l'identité est inconnue, une exemption est créée : elle ne s'applique qu'aux identités inconnues, jamais à un abonnement expiré.`
      : `${r.login_name} : le dossier sera écarté sans exemption. Aucun nouveau dossier ne sera ouvert pendant 7 jours.` }),
    fields: [note.el],
    submitLabel: authorize ? "Autoriser" : "Écarter",
    danger: !authorize,
    onSubmit: (form) => api(`/api/quarantine/${r.id}/${action}`, { method: "POST", body: { note: readForm(form).note } }),
  });
}

export function render(container, { params, setTitle }) {
  const controller = new AbortController();
  const signal = controller.signal;
  let alive = true;

  async function load() {
    container.replaceChildren(loadingState("Chargement du dossier…"));
    try {
      const [d, audit] = await Promise.all([
        api(`/api/quarantine/${params.id}`, { signal }),
        api(withQuery("/api/audit", { entity_type: "quarantine_record", entity_id: params.id, limit: 200 }), { signal }),
      ]);
      if (!alive) return;
      const r = d.record;
      setTitle(`Dossier — ${r.login_name}`);
      const act = async (promise, message) => {
        try {
          if (await promise) { toast(message, "success"); await load(); }
        } catch (err) {
          toast(err instanceof ApiError ? err.message : String(err), "danger");
        }
      };
      container.replaceChildren(h("div", { class: "stack" },
        r.status === "PENDING_REVOCATION" ? exceptionalNotice() : null,
        h("section", { class: "card member-head" },
          h("div", { class: "badges member-head__badges" }, statusBadge(QUARANTINE_STATUS, r.status),
            r.simulated_block ? badge("Blocage simulé en vigueur", "danger") : null, r.exempt ? badge("Identité exemptée", "info") : null),
          h("p", { class: "member-head__plan", text: EXPLAIN[r.status] || "" }),
          h("div", { class: "actions" },
            h("a", { class: "btn", href: `#/identites/${r.exit_node_identity_id}`, text: "Voir l'identité et ses observations" }),
            OPEN_STATUSES.has(r.status) ? h("button", { class: "btn btn--primary", type: "button", text: "Autoriser", onClick: () => act(resolveDialog(r, "authorize"), "Dossier autorisé.") }) : null,
            OPEN_STATUSES.has(r.status) ? h("button", { class: "btn btn--danger-outline", type: "button", text: "Écarter", onClick: () => act(resolveDialog(r, "dismiss"), "Dossier écarté.") }) : null)),
        h("section", { class: "card", "aria-labelledby": "sec-rec" },
          h("h2", { class: "card__title", id: "sec-rec", text: "Dossier" }),
          h("dl", { class: "dl" },
            h("dt", { text: "Identité" }), h("dd", { class: "mono", text: r.login_name }),
            h("dt", { text: "Ouvert le" }), h("dd", { text: dateTime(r.started_at) }),
            h("dt", { text: "Fin du délai" }), h("dd", { text: dateTime(r.expires_at) }),
            h("dt", { text: "Délai" }), h("dd", { text: timing(r) }),
            h("dt", { text: "Dernière activité" }), h("dd", { text: dateTime(r.last_confirmed_activity_at) }),
            h("dt", { text: "Verdict actuel" }), h("dd", {}, r.current_verdict ? statusBadge(VERDICT, r.current_verdict) : "—", " ", reason(r.current_reason)),
            h("dt", { text: "Résolu le" }), h("dd", { text: dateTime(r.resolved_at) }),
            h("dt", { text: "Par" }), h("dd", { text: r.resolved_by_email || "—" }),
            h("dt", { text: "Note" }), h("dd", { class: "pre", text: r.resolution_note || "—" })),
          h("p", { class: "field__hint", text: `Valeurs calculées par le serveur le ${dateTime(d.evaluated_at)}.` })),
        h("section", { class: "card", "aria-labelledby": "sec-hist" },
          h("h2", { class: "card__title", id: "sec-hist", text: "Historique des changements" }),
          dataTable({ rows: audit.items, empty: "Aucun événement.", columns: [
            { label: "Date", render: (e) => dateTime(e.occurred_at) },
            { label: "Événement", render: (e) => AUDIT_ACTIONS[e.action] || e.action },
            { label: "Transition", render: (e) => (e.details && e.details.from && e.details.to
              ? `${QUARANTINE_STATUS[e.details.from]?.label || e.details.from} → ${QUARANTINE_STATUS[e.details.to]?.label || e.details.to}`
              : e.details && e.details.status ? QUARANTINE_STATUS[e.details.status]?.label || e.details.status : "—") },
            { label: "Par", render: (e) => e.actor_email || "Automatique" },
          ] })),
        h("section", { class: "card", "aria-labelledby": "sec-dec" },
          h("h2", { class: "card__title", id: "sec-dec", text: "Décisions simulées" }),
          simulationNotice(),
          dataTable({ rows: d.decisions, empty: "Aucune décision simulée pour ce dossier.", columns: [
            { label: "Date", render: (x) => dateTime(x.decided_at) },
            { label: "Décision", render: (x) => statusBadge(DECISION, x.decision) },
            { label: "Motif", render: (x) => reason(x.reason) },
            { label: "Simulation", render: (x) => simulationState(x) },
          ] }))));
    } catch (err) {
      if (!alive || err.name === "AbortError") return;
      const notFound = err instanceof ApiError && (err.status === 404 || err.status === 422);
      container.replaceChildren(errorState(notFound ? "Dossier introuvable." : "Impossible de charger le dossier.",
        notFound ? null : err.message, notFound ? null : load));
    }
  }
  load();
  return () => { alive = false; controller.abort(); };
}
