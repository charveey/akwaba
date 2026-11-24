/** Paiements : historique filtrable, totaux encaissés (calculés par le serveur), enregistrement, annulation. */
import { api, ApiError, withQuery } from "../api.js";
import { loadMethods, methodNames, recordPaymentDialog, voidPaymentDialog } from "../components/billing-dialogs.js";
import { field, readForm } from "../components/form.js";
import { dateTime, day, money } from "../components/format.js";
import { PAYMENT_KIND, PAYMENT_STATUS, kindBadges, options, statusBadge } from "../components/labels.js";
import { formDialog } from "../components/modal.js";
import { pagination } from "../components/pagination.js";
import { dataTable } from "../components/table.js";
import { toast } from "../components/toast.js";
import { errorState, h, loadingState } from "../dom.js";
import { replaceQuery } from "../router.js";

const LIMIT = 25;
const CHANGED = [
  { value: "", label: "Avec ou sans changement de niveau" },
  { value: "oui", label: "Changement de niveau" },
  { value: "non", label: "Sans changement de niveau" },
];

export function render(container, { query }) {
  const controller = new AbortController();
  const signal = controller.signal;
  const state = {
    from: query.get("du") || "",
    to: query.get("au") || "",
    status: query.get("etat") || "",
    kind: query.get("statut") || "",
    changed: query.get("niveau") || "",
    method: query.get("moyen") || "",
    sort: query.get("tri") || "-paid_at",
    offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
  };

  const input = (label, type, key) => {
    const el = h("input", { class: "input", type, "aria-label": label, value: state[key] });
    el.addEventListener("change", () => { state[key] = el.value; refresh(); });
    return h("label", { class: "inline-field" }, h("span", { text: label }), el);
  };
  const select = (label, opts, key) => {
    const el = h("select", { class: "input", "aria-label": label }, opts.map((o) => h("option", { value: o.value, selected: o.value === state[key] }, o.label)));
    el.addEventListener("change", () => { state[key] = el.value; refresh(); });
    return el;
  };
  const method = select("Filtrer par moyen de paiement", [{ value: "", label: "Tous les moyens" }], "method");
  loadMethods().then((ms) => {
    for (const m of ms) method.append(h("option", { value: m.code, selected: m.code === state.method }, m.name));
  }).catch(() => {});

  const totals = h("div", { class: "card-grid section-gap", "aria-live": "polite" });
  const results = h("div", { class: "section-gap" });
  const record = h("button", { class: "btn btn--primary", type: "button", text: "Enregistrer un paiement", onClick: recordPending });

  container.replaceChildren(
    h("div", { class: "toolbar" },
      h("div", { class: "toolbar__filters" },
        input("Du", "date", "from"), input("Au", "date", "to"),
        select("Filtrer par état", options(PAYMENT_STATUS, "Tous les états"), "status"),
        select("Filtrer par statut métier", options(PAYMENT_KIND, "Tous les statuts"), "kind"),
        select("Filtrer par changement de niveau", CHANGED, "changed"),
        method),
      record),
    totals, results);

  function refresh(resetOffset = true) {
    if (resetOffset) state.offset = 0;
    replaceQuery("/paiements", {
      du: state.from, au: state.to, etat: state.status, statut: state.kind, niveau: state.changed,
      moyen: state.method, tri: state.sort !== "-paid_at" ? state.sort : "", debut: state.offset || "",
    });
    load();
  }

  let seq = 0;
  async function load() {
    const mine = ++seq;
    results.replaceChildren(loadingState());
    try {
      const page = await api(withQuery("/api/payments", {
        date_from: state.from, date_to: state.to, status: state.status, payment_kind: state.kind,
        plan_changed: state.changed === "oui" ? "true" : state.changed === "non" ? "false" : "",
        method: state.method, sort: state.sort, limit: LIMIT, offset: state.offset,
      }), { signal });
      const names = await methodNames();
      if (mine !== seq) return;
      const t = page.totals;
      totals.replaceChildren(
        stat("Total encaissé (hors annulés)", money(t.collected_xof, "XOF")),
        stat("Paiements confirmés", String(t.confirmed_count)),
        stat("Paiements annulés", String(t.voided_count)));
      results.replaceChildren(
        dataTable({
          rows: page.items, sort: state.sort, caption: "Historique des paiements",
          empty: "Aucun paiement ne correspond à ces critères.",
          onSort: (s) => { state.sort = s; refresh(false); },
          columns: [
            { label: "Date", sortKey: "paid_at", descFirst: true, render: (p) => dateTime(p.paid_at) },
            { label: "Membre", render: (p) => h("a", { href: `#/membres/${p.member_id}`, text: p.member_name || "—" }) },
            { label: "Formule", render: (p) => p.plan_name },
            { label: "Montant", render: (p) => money(p.amount, p.currency) },
            { label: "Encaissé (XOF)", render: (p) => (p.currency === "XOF" ? money(p.amount, "XOF") : money(p.settled_amount, p.settled_currency)) },
            { label: "Moyen", render: (p) => names.get(p.payment_method_code) || p.payment_method_code },
            { label: "Référence", render: (p) => p.reference },
            { label: "Statut métier", render: (p) => h("span", { class: "badges" }, kindBadges(p.payment_kind, p.plan_changed)) },
            { label: "État", render: (p) => h("span", {}, statusBadge(PAYMENT_STATUS, p.status),
              p.void_reason ? h("span", { class: "field__hint", text: ` (${p.void_reason})` }) : null) },
            { label: "Actions", render: (p) => (p.status === "CONFIRMED"
              ? h("button", { class: "btn btn--small btn--danger-outline", type: "button", text: "Annuler", onClick: () => act(voidPaymentDialog(p), "Paiement annulé.") })
              : null) },
          ],
        }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; refresh(false); } }));
    } catch (err) {
      if (err.name === "AbortError" || mine !== seq) return;
      totals.replaceChildren();
      results.replaceChildren(errorState("Impossible de charger les paiements.", err instanceof ApiError ? err.message : String(err), load));
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

  /** Un paiement se rattache toujours à un abonnement en attente de paiement. */
  async function recordPending() {
    try {
      const pending = await api(withQuery("/api/subscriptions", { payment_status: "PENDING", sort: "member", limit: 200 }), { signal });
      const open = pending.items.filter((s) => !s.canceled_at);
      if (!open.length) {
        toast("Aucun abonnement en attente de paiement. Pour un renouvellement, utilisez « Renouveler » (page Abonnements ou fiche membre).", "info");
        return;
      }
      const choice = field({ label: "Abonnement en attente", name: "subscription_id", type: "select", required: true,
        options: [{ value: "", label: "Choisir…" }, ...open.map((s) => ({
          value: s.id, label: `${s.member_name} — ${s.plan_name} — ${money(s.price_amount, s.currency)} — créé le ${day(s.created_at)}` }))] });
      const picked = await formDialog({
        title: "Enregistrer un paiement", fields: [choice.el], submitLabel: "Continuer",
        onSubmit: async (form) => open.find((s) => s.id === readForm(form).subscription_id),
      });
      if (picked && picked.id) await act(recordPaymentDialog(picked), "Paiement enregistré.");
    } catch (err) {
      if (err.name !== "AbortError") toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  load();
  return () => controller.abort();
}

function stat(label, value) {
  return h("div", { class: "card stat" }, h("span", { class: "stat__label", text: label }), h("span", { class: "stat__value", text: value }));
}
