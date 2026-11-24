/**
 * Dialogues d'écriture partagés (membres, abonnements, paiements). Chaque action appelle un
 * endpoint EXISTANT du backend, qui reste seul juge (dates, montants, règles de renouvellement).
 */
import { api } from "../api.js";
import { h } from "../dom.js";
import { field, readForm } from "./form.js";
import { day, duration, localDay, localInputToIso, money } from "./format.js";
import { confirmDialog, formDialog } from "./modal.js";

let plansPromise = null;
let methodsPromise = null;

export function loadPlans() {
  plansPromise ||= api("/api/plans").catch((err) => {
    plansPromise = null;
    throw err;
  });
  return plansPromise;
}

export function loadMethods() {
  methodsPromise ||= api("/api/payment-methods").catch((err) => {
    methodsPromise = null;
    throw err;
  });
  return methodsPromise;
}

/** Nom lisible d'un moyen de paiement à partir de son code (référentiel du backend). */
export async function methodNames() {
  try {
    return new Map((await loadMethods()).map((m) => [m.code, m.name]));
  } catch {
    return new Map();
  }
}

export function planLabel(p) {
  return `${p.name} — ${duration(p.duration_months, p.is_unlimited)} — ${p.is_unlimited ? "offert" : money(p.price_amount, p.currency)}`;
}

/* ----------------------------------------------------------------- membres */

export function memberFields(m = {}) {
  return [
    field({ label: "Nom complet", name: "full_name", value: m.full_name, required: true, attrs: { maxlength: "200", autocomplete: "off" } }),
    field({ label: "Numéro WhatsApp", name: "phone_e164", type: "tel", value: m.phone_e164, hint: "Format international, ex. +2250102030405", attrs: { maxlength: "32" } }),
    field({ label: "E-mail de contact", name: "email", type: "email", value: m.email, attrs: { maxlength: "320" } }),
    field({ label: "Pays (code à 2 lettres)", name: "country_code", value: m.country_code, hint: "Ex. CI, SN, FR", attrs: { maxlength: "2" } }),
    field({ label: "Notes internes", name: "notes", type: "textarea", value: m.notes, attrs: { maxlength: "2000" } }),
  ].map((f) => f.el);
}

export function memberDialog(member = null) {
  return formDialog({
    title: member ? "Modifier le membre" : "Nouveau membre",
    fields: memberFields(member || {}),
    submitLabel: member ? "Enregistrer" : "Créer le membre",
    onSubmit: (form) => {
      const body = readForm(form);
      return member
        ? api(`/api/members/${member.id}`, { method: "PUT", body })
        : api("/api/members", { method: "POST", body });
    },
  });
}

export function archiveMember(member) {
  return confirmDialog({
    title: "Archiver ce membre ?",
    message: `L'archivage de « ${member.full_name} » est DÉFINITIF : aucun désarchivage n'est possible. `
      + "Ses identités Tailscale actives seront détachées ; l'historique (abonnements, paiements, audit) est conservé.",
    confirmLabel: "Archiver définitivement",
    danger: true,
  }).then((ok) => (ok ? api(`/api/members/${member.id}/archive`, { method: "POST" }) : null));
}

/* ----------------------------------------------------------------- paiements */

function paymentFields(methods, { currency = "EUR" } = {}) {
  const fields = [
    field({ label: "Moyen de paiement", name: "payment_method_id", type: "select", required: true,
      options: [{ value: "", label: "Choisir…" }, ...methods.map((m) => ({ value: m.id, label: m.name }))] }),
    field({ label: "Date et heure du paiement", name: "paid_at", type: "datetime-local", hint: "Vide = maintenant. Jamais dans le futur." }),
    field({ label: "Référence", name: "reference", hint: "Facultatif (n° de transaction…)", attrs: { maxlength: "120" } }),
  ];
  const settled = field({ label: "Montant réellement reçu en XOF", name: "settled_amount", type: "number",
    hint: "Facultatif. Vide = conversion à parité, calculée par le serveur.", attrs: { min: "1", step: "1", inputmode: "numeric" } });
  settled.el.hidden = currency === "XOF";
  settled.input.disabled = currency === "XOF";
  return { els: [...fields.map((f) => f.el), settled.el], settled };
}

function paymentBody(values) {
  return {
    payment_method_id: values.payment_method_id,
    paid_at: localInputToIso(values.paid_at),
    reference: values.reference,
    settled_amount: values.settled_amount === null || values.settled_amount === undefined ? null : Number(values.settled_amount),
  };
}

export async function recordPaymentDialog(sub) {
  const methods = await loadMethods();
  const { els } = paymentFields(methods, { currency: sub.currency });
  return formDialog({
    title: "Enregistrer le paiement",
    intro: h("p", { class: "page-intro", text: `${sub.member_name || "Membre"} — ${sub.plan_name}, montant dû : ${money(sub.price_amount, sub.currency)} (fixé par l'abonnement).` }),
    fields: els,
    submitLabel: "Enregistrer le paiement",
    onSubmit: (form) => api("/api/payments", { method: "POST", body: { subscription_id: sub.id, ...paymentBody(readForm(form)) } }),
  });
}

export function voidPaymentDialog(payment) {
  const reason = field({ label: "Motif de l'annulation", name: "reason", type: "textarea", required: true, attrs: { maxlength: "500" } });
  return formDialog({
    title: "Annuler ce paiement ?",
    intro: h("div", { class: "alert alert--warning", role: "note",
      text: `Paiement de ${money(payment.amount, payment.currency)} du ${localDay(payment.paid_at)}. L'annulation est irréversible ; `
        + "le paiement reste dans l'historique avec le statut « Annulé » et le motif saisi." }),
    fields: [reason.el],
    submitLabel: "Annuler le paiement",
    danger: true,
    onSubmit: (form) => api(`/api/payments/${payment.id}/void`, { method: "POST", body: { reason: readForm(form).reason } }),
  });
}

/* ----------------------------------------------------------------- abonnements */

export async function newSubscriptionDialog(member) {
  const [plans, methods] = await Promise.all([loadPlans(), loadMethods()]);
  const plan = field({ label: "Formule", name: "plan_id", type: "select", required: true,
    options: [{ value: "", label: "Choisir…" }, ...plans.map((p) => ({ value: p.id, label: planLabel(p) }))] });
  const start = field({ label: "Date de début", name: "starts_on", type: "date", hint: "Vide = date du paiement (ou aujourd'hui si non payé)." });
  const received = field({ label: "Paiement reçu", name: "received", type: "checkbox", value: true });
  const pay = paymentFields(methods);
  const payBox = h("fieldset", { class: "subform" }, h("legend", { text: "Paiement" }), pay.els);

  function sync() {
    const p = plans.find((x) => x.id === plan.input.value);
    const unlimited = Boolean(p && p.is_unlimited);
    received.el.hidden = unlimited;
    const show = !unlimited && received.input.checked;
    payBox.hidden = !show;
    for (const el of payBox.querySelectorAll("input, select, textarea")) el.disabled = !show;
    pay.settled.el.hidden = !show || (p && p.currency === "XOF");
    pay.settled.input.disabled = pay.settled.el.hidden;
  }
  plan.input.addEventListener("change", sync);
  received.input.addEventListener("change", sync);
  sync();

  return formDialog({
    title: `Nouvel abonnement — ${member.full_name}`,
    fields: [plan.el, start.el, received.el, payBox],
    submitLabel: "Créer l'abonnement",
    onSubmit: (form) => {
      const v = readForm(form);
      const body = { member_id: member.id, plan_id: v.plan_id, starts_on: v.starts_on };
      if (!payBox.hidden) body.payment = paymentBody(v);
      return api("/api/subscriptions", { method: "POST", body });
    },
  });
}

export async function renewDialog(sub) {
  const [plans, methods] = await Promise.all([loadPlans(), loadMethods()]);
  const choices = plans.filter((p) => !p.is_unlimited);
  const current = choices.find((p) => p.code === sub.plan_code);
  const plan = field({ label: "Formule du renouvellement", name: "plan_id", type: "select", required: true,
    value: current ? current.id : "", options: choices.map((p) => ({ value: p.id, label: planLabel(p) })) });
  const note = h("div", { class: "alert alert--info", role: "status", hidden: true,
    text: "Formule différente : ce renouvellement sera marqué « Changement de niveau »." });
  const pay = paymentFields(methods);
  plan.input.addEventListener("change", () => {
    note.hidden = !current || plan.input.value === current.id;
  });

  return formDialog({
    title: `Renouveler — ${sub.member_name || ""}`.trim(),
    intro: h("p", { class: "page-intro",
      text: `Abonnement actuel : ${sub.plan_name}, fin le ${day(sub.display_end)}. `
        + "Les dates du renouvellement sont calculées par le serveur selon la règle configurée." }),
    fields: [plan.el, note, h("fieldset", { class: "subform" }, h("legend", { text: "Paiement" }), pay.els)],
    submitLabel: "Renouveler",
    onSubmit: (form) => {
      const v = readForm(form);
      return api(`/api/subscriptions/${sub.id}/renew`, { method: "POST", body: { plan_id: v.plan_id, payment: paymentBody(v) } });
    },
  });
}

export function cancelSubscription(sub) {
  return confirmDialog({
    title: "Résilier cet abonnement ?",
    message: `L'abonnement ${sub.plan_name} de ${sub.member_name || "ce membre"} sera résilié immédiatement `
      + "(accès considéré comme expiré). La résiliation est irréversible ; l'historique est conservé.",
    confirmLabel: "Résilier",
    danger: true,
  }).then((ok) => (ok ? api(`/api/subscriptions/${sub.id}/cancel`, { method: "POST" }) : null));
}
