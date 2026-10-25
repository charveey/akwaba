"""Évaluation d'une identité observée. Pure : aucune base de données, aucune horloge.

Retourne un verdict, jamais une action. Les transitions de quarantaine sont dans un autre module.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from app.domain.subscriptions import AccessStatus


class Verdict(str, Enum):
    NO_DECISION = "NO_DECISION"
    PROTECTED = "PROTECTED"
    OK = "OK"
    OK_IN_GRACE = "OK_IN_GRACE"
    EXPIRED_CANDIDATE = "EXPIRED_CANDIDATE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    UNKNOWN_IDENTITY = "UNKNOWN_IDENTITY"


@dataclass(frozen=True)
class Evaluation:
    verdict: Verdict
    reason: str


# Du plus favorable au moins favorable : le meilleur abonnement du membre décide.
_FAVORABILITY = {
    AccessStatus.ACTIVE: 0,
    AccessStatus.GRACE_PERIOD: 1,
    AccessStatus.NONE: 2,
    AccessStatus.SUSPENDED: 3,
    AccessStatus.EXPIRED: 4,
}


def best_access(accesses: Iterable[AccessStatus]) -> Optional[AccessStatus]:
    best: Optional[AccessStatus] = None
    for raw in accesses:
        status = AccessStatus(raw)
        if best is None or _FAVORABILITY[status] < _FAVORABILITY[best]:
            best = status
    return best


def evaluate_identity(
    *,
    resolved: bool,
    active_confirmed: bool,
    is_protected: bool,
    member_linked: bool,
    accesses: Iterable[AccessStatus] = (),
) -> Evaluation:
    # 1. Aucune décision sans identité certaine.
    if not resolved:
        return Evaluation(Verdict.NO_DECISION, "unresolved")
    # 2. Aucune décision sans activité confirmée.
    if not active_confirmed:
        return Evaluation(Verdict.NO_DECISION, "activity_not_confirmed")
    # 3. Une identité protégée n'est jamais mise en quarantaine.
    if is_protected:
        return Evaluation(Verdict.PROTECTED, "protected")
    # 8. Identité résolue sans membre associé.
    if not member_linked:
        return Evaluation(Verdict.UNKNOWN_IDENTITY, "no_member")
    access = best_access(accesses)
    if access is None:
        return Evaluation(Verdict.MANUAL_REVIEW, "no_subscription")
    if access is AccessStatus.ACTIVE:                      # 4. accès actif ou illimité
        return Evaluation(Verdict.OK, "access_active")
    if access is AccessStatus.GRACE_PERIOD:                # 5. grâce
        return Evaluation(Verdict.OK_IN_GRACE, "in_grace")
    if access is AccessStatus.EXPIRED:                     # 6. expiré, grâce terminée
        return Evaluation(Verdict.EXPIRED_CANDIDATE, "expired_after_grace")
    if access is AccessStatus.SUSPENDED:
        return Evaluation(Verdict.MANUAL_REVIEW, "suspended")
    return Evaluation(Verdict.MANUAL_REVIEW, "no_usable_subscription")   # 7. NONE
