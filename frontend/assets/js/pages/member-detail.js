/**
 * Fiche membre : informations, identités, abonnements, paiements, historique d'accès, relances.
 * Toutes les dates, échéances et statuts affichés viennent du backend.
 */
import { api, ApiError, withQuery } from "../api.js";
import { badge } from "../components/badge.js";
import {
  archiveMember, cancelSubscription, memberDialog, methodNames, newSubscriptionDialog, recordPaymentDialog,
  renewDialog, voidPaymentDialog,
} from "../components/billing-dialogs.js";
import { field, readForm } from "../components/form.js";
import { country, dateTime, day, localDay, money } from "../components/format.js";
import {
  ACCESS, accessBadge, AUDIT_ACTIONS, PAYMENT_STATUS, REMINDER_STATUS, RENEWAL, SUBSCRIPTION_PAYMENT, kindBadges,
  reminderMoment, statusBadge,
} from "../components/labels.js";
import { confirmDialog, formDialog } from "../components/modal.js";
import { dataTable } from "../components/table.js";
import { toast } from "../components/toast.js";
import { emptyState, errorState, h, loadingState } from "../dom.js";

export function render(container, { params, setTitle }) {
  const controller = new AbortController();
  const signal = controller.signal;
  let alive = true;
  let names = new Map();

  async function load() {
    container.replaceChildren(loadingState("Chargement de la fiche…"));
    try {
      const id = params.id;
      const [member, subs, pays, reminders, memberAudit] = await Promise.all([
        api(`/api/members/${id}`, { signal }),
        api(withQuery("/api/subscriptions", { member_id: id, sort: "-created", limit: 200 }), { signal }),
        api(withQuery("/api/payments", { member_id: id, limit: 200 }), { signal }),
        api(withQuery("/api/reminders", { member_id: id, limit: 200 }), { signal }),
        api(withQuery("/api/audit", { entity_type: "member", entity_id: id, limit: 200 }), { signal }),
      ]);
      names = await methodNames();
      if (!alive) return;
      setTitle(member.full_name);
      container.replaceChildren(view(member, subs.items, pays.items, reminders.items, memberAudit.items));
      loadRelatedAudit(member, subs.items, pays.items, reminders.items);
    } catch (err) {
      if (!alive || err.name === "AbortError") return;
      const notFound = err instanceof ApiError && (err.status === 404 || err.status === 422);
      container.replaceChildren(errorState(notFound ? "Membre introuvable." : "Impossible de charger la fiche.",
        notFound ? "Ce membre n'existe pas ou plus." : err.message, notFound ? null : load));
    }
  }

  /** Action d'écriture → message, puis rechargement complet (source de vérité : le backend). */
  async function act(promise, success) {
    try {
      const result = await promise;
      if (result) {
        toast(success, "success");
        await load();
      }
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  function view(m, subs, pays, reminders, audit) {
    const current = m.current;
    const archived = Boolean(m.archived_at);
    const head = subs.find((s) => current && s.id === current.subscription_id) || null;

    const actions = archived ? [] : [
      h("button", { class: "btn", type: "button", text: "Modifier", onClick: () => act(memberDialog(m), "Membre modifié.") }),
      (!current || current.access_status === "EXPIRED")
        ? h("button", { class: "btn btn--primary", type: "button", text: "Nouvel abonnement", onClick: () => act(newSubscriptionDialog(m), "Abonnement créé.") })
        : null,
      head && !head.is_unlimited && head.payment_status === "PAID" && !head.canceled_at
        ? h("button", { class: "btn btn--primary", type: "button", text: "Renouveler", onClick: () => act(renewDialog(head), "Abonnement renouvelé.") })
        : null,
      head && head.payment_status === "PENDING" && !head.canceled_at
        ? h("button", { class: "btn btn--primary", type: "button", text: "Enregistrer le paiement", onClick: () => act(recordPaymentDialog(head), "Paiement enregistré.") })
        : null,
      h("button", { class: "btn btn--danger-outline", type: "button", text: "Archiver", onClick: () => act(archiveMember(m), "Membre archivé.") }),
    ];

    return h("div", { class: "stack" },
      h("section", { class: "card member-head" },
        h("div", { class: "member-head__badges badges" },
          archived ? badge(`Archivé le ${localDay(m.archived_at)}`, "neutral") : null,
          statusBadge(ACCESS, current ? current.access_status : "NONE"),
          current ? statusBadge(RENEWAL, current.renewal_status) : null,
          current ? kindBadges(current.payment_kind, current.plan_changed) : null),
        h("p", { class: "member-head__plan", text: current
          ? `${current.plan_name} — ${current.is_unlimited ? "illimité" : `${current.upcoming_starts_on ? `à partir du ${day(current.upcoming_starts_on)}, ` : ""}jusqu'au ${day(current.display_end)} inclus`}`
          : "Aucun abonnement." }),
        h("div", { class: "actions" }, actions)),

      h("div", { class: "detail-grid" },
        h("section", { class: "card", "aria-labelledby": "sec-info" },
          h("h2", { class: "card__title", id: "sec-info", text: "Informations" }),
          h("dl", { class: "dl" },
            h("dt", { text: "WhatsApp" }), h("dd", { class: "mono", text: m.phone_e164 || "—" }),
            h("dt", { text: "E-mail de contact" }), h("dd", { text: m.email || "—" }),
            h("dt", { text: "Pays" }), h("dd", { text: m.country_code ? country(m.country_code) : "—" }),
            h("dt", { text: "Inscrit le" }), h("dd", { text: localDay(m.created_at) }),
            h("dt", { text: "Notes" }), h("dd", { class: "pre", text: m.notes || "—" }))),
        identitiesCard(m, archived)),

      section("sec-subs", "Abonnements", dataTable({
        rows: subs, empty: "Aucun abonnement.", caption: "Historique des abonnements",
        columns: [
          { label: "Formule", render: (s) => h("span", {}, s.plan_name, s.plan_changed ? h("span", { class: "cell-badge" }, badge("Changement de niveau", "info")) : null) },
          { label: "Début", render: (s) => day(s.starts_on) },
          { label: "Fin (incluse)", render: (s) => (s.is_unlimited ? "Illimité" : day(s.display_end)) },
          { label: "Accès coupé le", render: (s) => (s.is_unlimited ? "—" : day(s.grace_end)) },
          { label: "Accès", render: (s) => accessBadge(s.access_status, s.starts_in_future) },
          { label: "Renouvellement", render: (s) => statusBadge(RENEWAL, s.renewal_status) },
          { label: "Statut", render: (s) => h("span", { class: "badges" }, kindBadges(s.payment_kind, false), statusBadge(SUBSCRIPTION_PAYMENT, s.payment_status)) },
          { label: "Montant", render: (s) => (s.is_unlimited ? "Offert" : money(s.price_amount, s.currency)) },
          { label: "Actions", render: (s) => h("span", { class: "actions" },
            !archived && s.payment_status === "PENDING" && !s.canceled_at
              ? h("button", { class: "btn btn--small", type: "button", text: "Payer", onClick: () => act(recordPaymentDialog(s), "Paiement enregistré.") }) : null,
            !archived && s.is_current && !s.canceled_at
              ? h("button", { class: "btn btn--small btn--danger-outline", type: "button", text: "Résilier", onClick: () => act(cancelSubscription(s), "Abonnement résilié.") }) : null,
            s.canceled_at ? badge(`Résilié le ${localDay(s.canceled_at)}`, "neutral") : null) },
        ],
      })),

      section("sec-pays", "Paiements", paymentsTable(pays)),

      section("sec-history", "Historique d'accès", h("div", { class: "stack" },
        h("div", {},
          h("h3", { text: "Périodes reconstituées à partir des abonnements" }),
          h("p", { class: "field__hint", text: "Reconstitution des droits d'accès prévus par les abonnements enregistrés. "
            + "Ce n'est pas une traçabilité exhaustive des changements réels d'accès." }),
          periods(subs)),
        h("div", { id: "audit-events" },
          h("h3", { text: "Événements enregistrés dans le journal d'audit" }),
          auditTable(audit)),
        h("div", {},
          h("h3", { text: "Changements réels d'accès Tailscale" }),
          h("div", { class: "alert alert--info", role: "note",
            text: "Aucun changement réel : AKWABA fonctionne en mode observation (dry-run) et ne modifie aucun accès Tailscale. "
              + "Les décisions simulées (quarantaine) seront présentées au lot 11c." })))),

      section("sec-wa", "Notifications WhatsApp", dataTable({
        rows: reminders, empty: "Aucune relance WhatsApp pour ce membre.",
        columns: [
          { label: "Échéance", render: (r) => day(r.due_date) },
          { label: "Moment", render: (r) => reminderMoment(r.offset_days) },
          { label: "Statut", render: (r) => statusBadge(REMINDER_STATUS, r.status) },
          { label: "Ouverte le", render: (r) => dateTime(r.opened_at) },
          { label: "Envoi confirmé le", render: (r) => dateTime(r.sent_at) },
        ],
      })));
  }

  function identitiesCard(m, archived) {
    const link = archived ? null : h("button", { class: "btn btn--small", type: "button", text: "Rattacher une identité", onClick: () => {
      const login = field({ label: "Login Tailscale", name: "login_name", type: "email", required: true, hint: "Ex. prenom.nom@gmail.com", attrs: { maxlength: "320" } });
      act(formDialog({
        title: "Rattacher une identité Tailscale", fields: [login.el], submitLabel: "Rattacher",
        onSubmit: (form) => api(`/api/members/${m.id}/identities`, { method: "POST", body: readForm(form) }),
      }), "Identité rattachée.");
    } });
    return h("section", { class: "card", "aria-labelledby": "sec-ids" },
      h("div", { class: "card__header" }, h("h2", { class: "card__title", id: "sec-ids", text: "Identités Tailscale" }), link),
      m.identities.length
        ? h("ul", { class: "id-list" }, m.identities.map((i) => h("li", {},
          h("span", { class: "mono", text: i.login_name }), " ",
          badge(i.is_active ? "Rattachée" : "Détachée", i.is_active ? "success" : "neutral"),
          h("span", { class: "field__hint", text: i.is_active ? ` depuis le ${localDay(i.linked_at)}` : ` le ${localDay(i.unlinked_at)}` }),
          i.is_active && !archived ? h("button", { class: "btn btn--small btn--ghost", type: "button", text: "Détacher", onClick: () => act(
            confirmDialog({ title: "Détacher cette identité ?", message: `${i.login_name} ne sera plus associée à ce membre.`, confirmLabel: "Détacher", danger: true })
              .then((ok) => (ok ? api(`/api/members/${m.id}/identities/${i.id}/unlink`, { method: "POST" }) : null)),
            "Identité détachée.") }) : null)))
        : emptyState("Aucune identité rattachée."));
  }

  function paymentsTable(pays) {
    return dataTable({
      rows: pays, empty: "Aucun paiement.", caption: "Historique des paiements",
      columns: [
        { label: "Date", render: (p) => dateTime(p.paid_at) },
        { label: "Formule", render: (p) => p.plan_name },
        { label: "Montant", render: (p) => money(p.amount, p.currency) },
        { label: "Encaissé (XOF)", render: (p) => (p.currency === "XOF" ? money(p.amount, "XOF") : money(p.settled_amount, p.settled_currency)) },
        { label: "Moyen", render: (p) => names.get(p.payment_method_code) || p.payment_method_code },
        { label: "Référence", render: (p) => p.reference },
        { label: "Statut métier", render: (p) => h("span", { class: "badges" }, kindBadges(p.payment_kind, p.plan_changed)) },
        { label: "Statut", render: (p) => h("span", {}, statusBadge(PAYMENT_STATUS, p.status),
          p.void_reason ? h("span", { class: "field__hint", text: ` (${p.void_reason})` }) : null) },
        { label: "Actions", render: (p) => (p.status === "CONFIRMED"
          ? h("button", { class: "btn btn--small btn--danger-outline", type: "button", text: "Annuler", onClick: () => act(voidPaymentDialog(p), "Paiement annulé.") })
          : null) },
      ],
    });
  }

  /** Événements d'audit des abonnements et paiements du membre, ajoutés après l'affichage. */
  async function loadRelatedAudit(m, subs, pays, reminders) {
    const targets = [
      ...subs.slice(0, 30).map((s) => ["subscription", s.id]),
      ...pays.slice(0, 30).map((p) => ["payment", p.id]),
      ...m.identities.slice(0, 20).map((i) => ["client_identity", i.id]),
      ...reminders.slice(0, 30).map((r) => ["reminder", r.id]),
    ];
    try {
      const pages = await Promise.all(targets.map(([type, id]) =>
        api(withQuery("/api/audit", { entity_type: type, entity_id: id, limit: 50 }), { signal })));
      if (!alive) return;
      const box = document.getElementById("audit-events");
      if (!box) return;
      const own = await api(withQuery("/api/audit", { entity_type: "member", entity_id: m.id, limit: 200 }), { signal });
      const all = [...own.items, ...pages.flatMap((p) => p.items)];
      const unique = [...new Map(all.map((e) => [e.id, e])).values()].sort((a, b) => b.id - a.id);
      box.replaceChildren(h("h3", { text: "Événements enregistrés dans le journal d'audit" }), auditTable(unique));
    } catch {
      /* l'affichage de base (événements du membre) reste valable */
    }
  }

  load();
  return () => {
    alive = false;
    controller.abort();
  };
}

function section(id, title, content) {
  return h("section", { class: "card", "aria-labelledby": id },
    h("h2", { class: "card__title", id, text: title }), content);
}

function periods(subs) {
  if (!subs.length) return emptyState("Aucune période.");
  const ordered = [...subs].reverse();
  return h("ol", { class: "timeline" }, ordered.map((s) => h("li", {},
    h("strong", { text: s.plan_name }),
    h("span", { text: s.is_unlimited
      ? ` — à partir du ${day(s.starts_on)}, sans échéance`
      : ` — du ${day(s.starts_on)} au ${day(s.display_end)} inclus ; accès coupé le ${day(s.grace_end)} sans renouvellement` }),
    s.payment_status === "PENDING" ? h("span", { class: "field__hint", text: " (non payé : aucun accès prévu)" }) : null,
    s.canceled_at ? h("span", { class: "field__hint", text: ` (résilié le ${localDay(s.canceled_at)})` }) : null)));
}

function auditTable(items) {
  return dataTable({
    rows: items, empty: "Aucun événement enregistré.",
    columns: [
      { label: "Date", render: (e) => dateTime(e.occurred_at) },
      { label: "Événement", render: (e) => AUDIT_ACTIONS[e.action] || e.action },
      { label: "Par", render: (e) => e.actor_email || e.actor_label || "—" },
    ],
  });
}
