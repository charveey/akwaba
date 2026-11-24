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

/* ----------------------------------------------------------------- Lot 11c */

export const VERDICT = {
  NO_DECISION: { label: "Aucune décision", variant: "neutral" },
  PROTECTED: { label: "Protégée", variant: "info" },
  OK: { label: "Accès valide", variant: "success" },
  OK_IN_GRACE: { label: "En période de grâce", variant: "warning" },
  EXPIRED_CANDIDATE: { label: "Abonnement expiré", variant: "danger" },
  MANUAL_REVIEW: { label: "Revue manuelle", variant: "warning" },
  UNKNOWN_IDENTITY: { label: "Identité inconnue", variant: "danger" },
};

export const QUARANTINE_STATUS = {
  PENDING: { label: "En attente de décision", variant: "warning" },
  AUTHORIZED: { label: "Autorisé", variant: "success" },
  EXPIRED: { label: "Délai expiré", variant: "danger" },
  PENDING_REVOCATION: { label: "État exceptionnel", variant: "danger" },
  MANUAL_REVIEW: { label: "Intervention humaine requise", variant: "warning" },
  DISMISSED: { label: "Dossier écarté", variant: "neutral" },
};

export const OPEN_STATUSES = new Set(["PENDING", "EXPIRED", "PENDING_REVOCATION", "MANUAL_REVIEW"]);

export const DECISION = {
  WOULD_BLOCK: { label: "Blocage simulé", variant: "danger" },
  WOULD_UNBLOCK: { label: "Déblocage simulé", variant: "success" },
};

export const AMBIGUITY = {
  ARCHIVED_MEMBER_LOGIN: { label: "Login d'un membre archivé", variant: "warning",
    help: "Ce login a appartenu à un membre archivé et n'est rattaché à aucun membre actif." },
  CONTACT_EMAIL_MATCH: { label: "Correspond à un e-mail de contact", variant: "warning",
    help: "Le login correspond à l'e-mail de contact d'un membre auquel l'identité n'est pas rattachée. Simple suggestion." },
  MULTIPLE_EXIT_NODES: { label: "Plusieurs Exit Nodes", variant: "warning",
    help: "Le login a été observé sur au moins deux Exit Nodes distincts." },
};

export const PROTECTION_SOURCE = {
  MANUAL: { label: "Manuelle", variant: "info" },
  AUTO_MEMBER: { label: "Automatique (membre)", variant: "neutral" },
  AUTO_DEVICE: { label: "Automatique (appareil)", variant: "neutral" },
};

/** Codes de motif renvoyés par le backend (évaluation et transitions). */
export const REASONS = {
  unresolved: "Identité non résolue",
  activity_not_confirmed: "Activité non confirmée",
  protected: "Identité protégée",
  no_member: "Aucun membre associé",
  no_subscription: "Membre sans abonnement",
  access_active: "Abonnement actif",
  in_grace: "Période de grâce",
  expired_after_grace: "Abonnement expiré, grâce terminée",
  suspended: "Abonnement suspendu",
  no_usable_subscription: "Aucun abonnement utilisable",
  unauthorized_active: "Identité non autorisée active",
  needs_review: "Revue manuelle nécessaire",
  window_elapsed: "Délai de 24 h écoulé",
  auto_ok: "Accès redevenu valide",
  auto_protected: "Identité protégée",
  admin_authorized: "Autorisé par un administrateur",
  admin_dismissed: "Écarté par un administrateur",
};

export function reason(code) {
  return code ? REASONS[code] || code : "—";
}

export function ambiguityBadges(codes = []) {
  return codes.map((c) => {
    const entry = AMBIGUITY[c] || { label: c, variant: "warning" };
    const b = badge(entry.label, entry.variant);
    if (entry.help) b.setAttribute("title", entry.help);
    return b;
  });
}

AUDIT_ACTIONS["identity.reassigned"] = "Identité réaffectée à un autre membre";
AUDIT_ACTIONS["quarantine.opened"] = "Dossier ouvert (automatique)";
AUDIT_ACTIONS["quarantine.changed"] = "Statut du dossier modifié (automatique)";
AUDIT_ACTIONS["quarantine.authorized"] = "Dossier autorisé par un administrateur";
AUDIT_ACTIONS["quarantine.dismissed"] = "Dossier écarté par un administrateur";
AUDIT_ACTIONS["identity_exemption.ended"] = "Exemption terminée";
AUDIT_ACTIONS["protected_identity.added"] = "Protection manuelle ajoutée";
AUDIT_ACTIONS["protected_identity.removed"] = "Protection manuelle retirée";
