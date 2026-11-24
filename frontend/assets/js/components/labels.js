/**
 * Libellés français des valeurs CALCULÉES PAR LE BACKEND. Aucune règle ici :
 * uniquement la traduction d'un code en texte et en couleur de badge.
 */
import { badge } from "./badge.js";

export const ACCESS = {
  NONE: { label: "Aucun accès", variant: "neutral" },
  ACTIVE: { label: "Actif", variant: "success" },
  GRACE_PERIOD: { label: "Période de grâce", variant: "warning" },
  EXPIRED: { label: "Expiré", variant: "danger" },
  SUSPENDED: { label: "Suspendu", variant: "danger" },
};

export const RENEWAL = {
  NOT_APPLICABLE: { label: "Sans échéance", variant: "neutral" },
  UP_TO_DATE: { label: "À jour", variant: "success" },
  RENEWAL_OVERDUE: { label: "Renouvellement attendu", variant: "warning" },
  NON_RENEWAL: { label: "Non-renouvellement", variant: "danger" },
};

export const PAYMENT_KIND = {
  PENDING: { label: "En attente", variant: "warning" },
  NEW_MEMBER: { label: "Nouveau membre", variant: "info" },
  LATE_RENEWAL: { label: "Renouvellement en retard", variant: "warning" },
  RENEWED: { label: "Membre renouvelé", variant: "success" },
};

export const SUBSCRIPTION_PAYMENT = {
  PENDING: { label: "Non payé", variant: "warning" },
  PAID: { label: "Payé", variant: "success" },
  WAIVED: { label: "Offert", variant: "neutral" },
};

export const PAYMENT_STATUS = {
  CONFIRMED: { label: "Confirmé", variant: "success" },
  VOIDED: { label: "Annulé", variant: "danger" },
};

export const REMINDER_STATUS = {
  PENDING: { label: "À préparer", variant: "neutral" },
  OPENED: { label: "Ouvert dans WhatsApp — envoi non confirmé", variant: "warning" },
  SENT: { label: "Envoi confirmé", variant: "success" },
  CANCELED: { label: "Sans objet (abonnement renouvelé)", variant: "neutral" },
};

export const AUDIT_ACTIONS = {
  "member.created": "Membre créé",
  "member.updated": "Membre modifié",
  "member.archived": "Membre archivé",
  "identity.linked": "Identité Tailscale rattachée",
  "identity.unlinked": "Identité Tailscale détachée",
  "subscription.created": "Abonnement créé",
  "subscription.renewed": "Abonnement renouvelé",
  "subscription.canceled": "Abonnement résilié",
  "payment.recorded": "Paiement enregistré",
  "payment.voided": "Paiement annulé",
  "reminder.opened": "Relance WhatsApp ouverte",
  "reminder.sent": "Relance WhatsApp : envoi confirmé",
};

/** Options d'un <select> à partir d'une table de libellés. */
export function options(map, allLabel) {
  const opts = Object.entries(map).map(([value, { label }]) => ({ value, label }));
  return allLabel ? [{ value: "", label: allLabel }, ...opts] : opts;
}

export function statusBadge(map, value) {
  const entry = map[value];
  return badge(entry ? entry.label : value || "—", entry ? entry.variant : "neutral");
}

/** Statut métier + badge secondaire « Changement de niveau » (même en cas de retard). */
export function kindBadges(kind, planChanged) {
  return [
    kind ? statusBadge(PAYMENT_KIND, kind) : null,
    planChanged ? badge("Changement de niveau", "info") : null,
  ].filter(Boolean);
}

export function reminderMoment(offset) {
  if (offset === 0) return "J0 — jour d'expiration";
  return offset < 0 ? `J${offset} — avant expiration` : `J+${offset} — après expiration`;
}

/** État d'accès d'un abonnement ; « À venir » si le backend indique qu'il n'a pas encore commencé. */
export function accessBadge(status, startsInFuture = false) {
  return status === "NONE" && startsInFuture ? badge("À venir", "info") : statusBadge(ACCESS, status);
}
