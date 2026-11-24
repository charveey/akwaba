/** Abonnements : onglets « Échéances » et « Relances WhatsApp ». */
import { api, ApiError, withQuery } from "../api.js";
import { badge } from "../components/badge.js";
import { cancelSubscription, recordPaymentDialog, renewDialog } from "../components/billing-dialogs.js";
import { dateTime, day } from "../components/format.js";
import {
  ACCESS, accessBadge, PAYMENT_KIND, REMINDER_STATUS, RENEWAL, kindBadges, options, reminderMoment, statusBadge,
} from "../components/labels.js";
import { confirmDialog, openDialog } from "../components/modal.js";
import { pagination } from "../components/pagination.js";
import { dataTable } from "../components/table.js";
import { tabs } from "../components/tabs.js";
import { toast } from "../components/toast.js";
import { errorState, h, loadingState, whatsAppLink } from "../dom.js";
import { replaceQuery } from "../router.js";

const LIMIT = 25;

export function render(container, { query }) {
  const controller = new AbortController();
  const tab = query.get("onglet") === "relances" ? "relances" : "echeances";
  const body = h("div", { class: "section-gap" });
  container.replaceChildren(
    tabs({
      label: "Vues des abonnements", active: tab,
      items: [{ id: "echeances", label: "Échéances" }, { id: "relances", label: "Relances WhatsApp" }],
      onSelect: (id) => { location.hash = id === "relances" ? "#/abonnements?onglet=relances" : "#/abonnements"; },
    }),
    body);
  if (tab === "relances") reminders(body, query, controller.signal);
  else deadlines(body, query, controller.signal);
  return () => controller.abort();
}

/* ----------------------------------------------------------------- Échéances */

const EXPIRING = [
  { value: "", label: "Toutes les échéances" },
  { value: "7", label: "Expire dans 7 jours" },
  { value: "15", label: "Expire dans 15 jours" },
  { value: "30", label: "Expire dans 30 jours" },
  { value: "90", label: "Expire dans 90 jours" },
];

function deadlines(box, query, signal) {
  const state = {
    access: query.get("acces") || "",
    renewal: query.get("renouvellement") || "",
    kind: query.get("statut") || "",
    within: query.get("expire") || "",
    all: query.get("historique") === "1",
    sort: query.get("tri") || "expiry",
    offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
  };
  const sel = (label, opts, value, key) => {
    const s = h("select", { class: "input", "aria-label": label }, opts.map((o) => h("option", { value: o.value, selected: o.value === value }, o.label)));
    s.addEventListener("change", () => { state[key] = s.value; refresh(); });
    return s;
  };
  const history = h("input", { type: "checkbox", checked: state.all });
  history.addEventListener("change", () => { state.all = history.checked; refresh(); });
  const results = h("div", { class: "section-gap" });

  box.replaceChildren(
    h("div", { class: "toolbar" }, h("div", { class: "toolbar__filters" },
      sel("Filtrer par accès", options(ACCESS, "Tous les accès"), state.access, "access"),
      sel("Filtrer par renouvellement", options(RENEWAL, "Tous les renouvellements"), state.renewal, "renewal"),
      sel("Filtrer par statut", options(PAYMENT_KIND, "Tous les statuts"), state.kind, "kind"),
      sel("Filtrer par échéance", EXPIRING, state.within, "within"),
      h("label", { class: "choice" }, history, h("span", { text: "Inclure les abonnements renouvelés (historique)" })))),
    results);

  function refresh(resetOffset = true) {
    if (resetOffset) state.offset = 0;
    replaceQuery("/abonnements", {
      acces: state.access, renouvellement: state.renewal, statut: state.kind, expire: state.within,
      historique: state.all ? "1" : "", tri: state.sort !== "expiry" ? state.sort : "", debut: state.offset || "",
    });
    load();
  }

  let seq = 0;
  async function load() {
    const mine = ++seq;
    results.replaceChildren(loadingState());
    try {
      const page = await api(withQuery("/api/subscriptions", {
        access_status: state.access, renewal_status: state.renewal, payment_kind: state.kind,
        expiring_within_days: state.within, current_only: state.all ? "" : "true",
        sort: state.sort, limit: LIMIT, offset: state.offset,
      }), { signal });
      if (mine !== seq) return;
      results.replaceChildren(
        dataTable({
          rows: page.items, sort: state.sort, caption: "Échéances des abonnements",
          empty: "Aucun abonnement ne correspond à ces critères.",
          onSort: (s) => { state.sort = s; refresh(false); },
          columns: [
            { label: "Membre", sortKey: "member", render: (s) => h("a", { href: `#/membres/${s.member_id}`, text: s.member_name || "—" }) },
            { label: "Formule", render: (s) => h("span", {}, s.plan_name, s.plan_changed ? h("span", { class: "cell-badge" }, badge("Changement de niveau", "info")) : null) },
            { label: "Début", render: (s) => day(s.starts_on) },
            { label: "Fin (incluse)", sortKey: "expiry", render: (s) => (s.is_unlimited ? "Illimité" : day(s.display_end)) },
            { label: "Accès coupé le", render: (s) => (s.is_unlimited ? "—" : day(s.grace_end)) },
            { label: "Accès", render: (s) => accessBadge(s.access_status, s.starts_in_future) },
            { label: "Renouvellement", render: (s) => statusBadge(RENEWAL, s.renewal_status) },
            { label: "Statut", render: (s) => h("span", { class: "badges" }, kindBadges(s.payment_kind, false)) },
            { label: "Actions", render: (s) => h("span", { class: "actions" },
              s.is_current && !s.is_unlimited && s.payment_status === "PAID" && !s.canceled_at
                ? h("button", { class: "btn btn--small", type: "button", text: "Renouveler", onClick: () => act(renewDialog(s), "Abonnement renouvelé.") }) : null,
              s.payment_status === "PENDING" && !s.canceled_at
                ? h("button", { class: "btn btn--small", type: "button", text: "Payer", onClick: () => act(recordPaymentDialog(s), "Paiement enregistré.") }) : null,
              s.is_current && !s.canceled_at
                ? h("button", { class: "btn btn--small btn--danger-outline", type: "button", text: "Résilier", onClick: () => act(cancelSubscription(s), "Abonnement résilié.") }) : null) },
          ],
        }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; refresh(false); } }));
    } catch (err) {
      if (err.name === "AbortError" || mine !== seq) return;
      results.replaceChildren(errorState("Impossible de charger les abonnements.", err instanceof ApiError ? err.message : String(err), load));
    }
  }

  async function act(promise, message) {
    try {
      if (await promise) {
        toast(message, "success");
        load();
      }
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  load();
}

/* ----------------------------------------------------------------- Relances WhatsApp */

function reminders(box, query, signal) {
  const state = {
    status: query.get("etat") || "",
    offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
  };
  const status = h("select", { class: "input", "aria-label": "Filtrer par état de la relance" },
    options(REMINDER_STATUS, "Toutes les relances").map((o) => h("option", { value: o.value, selected: o.value === state.status }, o.label)));
  status.addEventListener("change", () => { state.status = status.value; refresh(); });
  const generate = h("button", { class: "btn btn--primary", type: "button", text: "Générer les relances du jour", onClick: async () => {
    generate.disabled = true;
    try {
      const r = await api("/api/reminders/generate", { method: "POST" });
      toast(r.created ? `${r.created} relance(s) préparée(s) pour le ${day(r.today)}.` : `Aucune nouvelle relance pour le ${day(r.today)}.`, "success");
      load();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    } finally {
      generate.disabled = false;
    }
  } });
  const results = h("div", { class: "section-gap" });
  box.replaceChildren(
    h("div", { class: "alert alert--info", role: "note",
      text: "Ouvrir WhatsApp ne confirme pas l'envoi. Une relance n'est indiquée « envoi confirmé » qu'après votre confirmation explicite, une fois le message réellement envoyé." }),
    h("div", { class: "toolbar" }, h("div", { class: "toolbar__filters" }, status), generate),
    results);

  function refresh(resetOffset = true) {
    if (resetOffset) state.offset = 0;
    replaceQuery("/abonnements", { onglet: "relances", etat: state.status, debut: state.offset || "" });
    load();
  }

  let seq = 0;
  async function load() {
    const mine = ++seq;
    results.replaceChildren(loadingState());
    try {
      const page = await api(withQuery("/api/reminders", { status: state.status, limit: LIMIT, offset: state.offset }), { signal });
      if (mine !== seq) return;
      results.replaceChildren(
        dataTable({
          rows: page.items, caption: "Relances WhatsApp", empty: "Aucune relance. Utilisez « Générer les relances du jour ».",
          columns: [
            { label: "Membre", render: (r) => h("a", { href: `#/membres/${r.member_id}`, text: r.member_name }) },
            { label: "Échéance", render: (r) => day(r.due_date) },
            { label: "Moment", render: (r) => reminderMoment(r.offset_days) },
            { label: "État", render: (r) => statusBadge(REMINDER_STATUS, r.status) },
            { label: "Envoi confirmé le", render: (r) => dateTime(r.sent_at) },
            { label: "Actions", render: (r) => h("span", { class: "actions" },
              r.status === "PENDING" || r.status === "OPENED"
                ? h("button", { class: "btn btn--small", type: "button", text: "Préparer le message", onClick: () => prepare(r) }) : null,
              r.status === "OPENED"
                ? h("button", { class: "btn btn--small btn--primary", type: "button", text: "Confirmer l'envoi", onClick: () => confirmSent(r) }) : null) },
          ],
        }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; refresh(false); } }));
    } catch (err) {
      if (err.name === "AbortError" || mine !== seq) return;
      results.replaceChildren(errorState("Impossible de charger les relances.", err instanceof ApiError ? err.message : String(err), load));
    }
  }

  /** Le backend prépare le lien wa.me (et note l'ouverture) ; l'administrateur clique lui-même. */
  async function prepare(r) {
    try {
      const { reminder, whatsapp_url: url } = await api(`/api/reminders/${r.id}/open`, { method: "POST" });
      openDialog({
        title: `Relance WhatsApp — ${reminder.member_name}`,
        body: h("div", { class: "stack" },
          h("p", { class: "field__hint", text: `${reminderMoment(reminder.offset_days)} · échéance du ${day(reminder.due_date)}` }),
          h("blockquote", { class: "message-preview", text: reminder.message }),
          h("div", { class: "alert alert--warning", role: "note",
            text: "L'ouverture de WhatsApp ne vaut pas confirmation d'envoi. Après avoir réellement envoyé le message, revenez cliquer sur « Confirmer l'envoi »." }),
          whatsAppLink(url, "Ouvrir dans WhatsApp", { class: "btn btn--primary" })),
      });
      load();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  async function confirmSent(r) {
    const ok = await confirmDialog({
      title: "Confirmer l'envoi ?",
      message: `Confirmez-vous avoir réellement envoyé la relance à ${r.member_name} dans WhatsApp ?`,
      confirmLabel: "Oui, le message a été envoyé",
    });
    if (!ok) return;
    try {
      await api(`/api/reminders/${r.id}/mark-sent`, { method: "POST" });
      toast("Envoi confirmé.", "success");
      load();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  load();
}

