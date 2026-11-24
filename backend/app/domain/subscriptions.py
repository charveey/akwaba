"""Règles métier pures des abonnements : aucune base de données, aucune horloge implicite."""
import calendar
from datetime import date, timedelta
from enum import Enum
from typing import Literal


class AccessStatus(str, Enum):
    NONE = "NONE"
    ACTIVE = "ACTIVE"
    GRACE_PERIOD = "GRACE_PERIOD"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"


class RenewalStatus(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UP_TO_DATE = "UP_TO_DATE"
    RENEWAL_OVERDUE = "RENEWAL_OVERDUE"
    NON_RENEWAL = "NON_RENEWAL"


RenewalRule = Literal["PREVIOUS_EXPIRY", "PAYMENT_DATE"]


def add_months(d: date, months: int) -> date:
    """Ajoute des mois en plafonnant au dernier jour du mois (31/01 + 1 -> 28/02)."""
    if months < 0:
        raise ValueError("months doit être positif")
    index = d.month - 1 + months
    year = d.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def compute_expires_on(starts_on: date, duration_months: int | None) -> date | None:
    """expires_on est exclusif. None = illimité."""
    if duration_months is None:
        return None
    if duration_months <= 0:
        raise ValueError("duration_months doit être > 0")
    return add_months(starts_on, duration_months)


def display_end(expires_on: date | None) -> date | None:
    """Dernier jour affiché au tableau de bord (17/12 -> 16/01)."""
    return None if expires_on is None else expires_on - timedelta(days=1)


def grace_end(expires_on: date, grace_period_days: int) -> date:
    """Premier jour APRÈS la grâce (exclusif)."""
    return expires_on + timedelta(days=grace_period_days)


def renewal_start(
    previous_expires_on: date,
    payment_date: date,
    grace_period_days: int,
    rule: RenewalRule = "PREVIOUS_EXPIRY",
) -> date:
    """Date de début du renouvellement (règle A par défaut)."""
    if payment_date < previous_expires_on:
        return previous_expires_on  # paiement anticipé : aucun jour perdu
    if payment_date < grace_end(previous_expires_on, grace_period_days):
        return previous_expires_on if rule == "PREVIOUS_EXPIRY" else payment_date
    return payment_date  # après la grâce


def access_status(
    *,
    today: date,
    starts_on: date,
    expires_on: date | None,
    is_unlimited: bool,
    grace_period_days: int,
    payment_status: str,
    canceled: bool,
    suspended: bool,
) -> AccessStatus:
    if suspended:
        return AccessStatus.SUSPENDED
    if canceled:
        return AccessStatus.EXPIRED
    if payment_status == "PENDING" or today < starts_on:
        return AccessStatus.NONE
    if is_unlimited:
        return AccessStatus.ACTIVE
    assert expires_on is not None
    if today < expires_on:
        return AccessStatus.ACTIVE
    if today < grace_end(expires_on, grace_period_days):
        return AccessStatus.GRACE_PERIOD
    return AccessStatus.EXPIRED


def renewal_status(
    *,
    today: date,
    expires_on: date | None,
    is_unlimited: bool,
    grace_period_days: int,
    canceled: bool,
    has_successor: bool,
) -> RenewalStatus:
    if is_unlimited:
        return RenewalStatus.NOT_APPLICABLE
    if has_successor:
        return RenewalStatus.UP_TO_DATE
    if canceled:
        return RenewalStatus.NON_RENEWAL
    assert expires_on is not None
    if today < expires_on:
        return RenewalStatus.UP_TO_DATE
    if today < grace_end(expires_on, grace_period_days):
        return RenewalStatus.RENEWAL_OVERDUE
    return RenewalStatus.NON_RENEWAL


class PaymentKind(str, Enum):
    """Statut métier d'un abonnement payé (Phase 11b). Calculé, jamais stocké."""
    PENDING = "PENDING"              # En attente : paiement non confirmé
    NEW_MEMBER = "NEW_MEMBER"        # Nouveau membre : premier abonnement payé du membre
    LATE_RENEWAL = "LATE_RENEWAL"    # Renouvellement en retard : payé à partir de l'expiration précédente
    RENEWED = "RENEWED"              # Membre renouvelé


def payment_kind(
    *,
    payment_status: str,
    has_predecessor: bool,
    predecessor_expires_on: date | None,
    paid_on: date | None,
) -> PaymentKind:
    """Hiérarchie validée : En attente > Nouveau membre > Renouvellement en retard > Membre renouvelé.

    - has_predecessor : il existe un abonnement payé (PAID/WAIVED) antérieur du même membre
      (le précédent de la chaîne, sinon le plus récent avant celui-ci — cas du réabonnement).
    - predecessor_expires_on : son expires_on (exclusif) ; None s'il était illimité.
    - paid_on : date UTC du premier paiement confirmé de cet abonnement (None si offert).

    Un paiement le jour même de expires_on est en retard (expires_on est le premier jour sans accès payé).
    Le changement de formule n'est PAS un statut : voir plan_changed().
    """
    if payment_status == "PENDING":
        return PaymentKind.PENDING
    if not has_predecessor:
        return PaymentKind.NEW_MEMBER
    if predecessor_expires_on is not None and paid_on is not None and paid_on >= predecessor_expires_on:
        return PaymentKind.LATE_RENEWAL
    return PaymentKind.RENEWED


def plan_changed(predecessor_plan_id: object | None, plan_id: object) -> bool:
    """Attribut secondaire « Changement de niveau » : formule différente de l'abonnement payé précédent."""
    return predecessor_plan_id is not None and predecessor_plan_id != plan_id
