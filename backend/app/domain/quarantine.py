"""Machine d'états de quarantaine. Pure : aucune base de données, aucune horloge implicite, aucune action.

Retourne une transition ; le service qui l'applique écrit le dossier, la décision (dry-run) et l'audit.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from app.domain.identity import Verdict

WINDOW = timedelta(hours=24)
DISMISS_SUPPRESSION = timedelta(days=7)

PENDING = "PENDING"
AUTHORIZED = "AUTHORIZED"
EXPIRED = "EXPIRED"
PENDING_REVOCATION = "PENDING_REVOCATION"     # réservé à la V2 : jamais produit ici
MANUAL_REVIEW = "MANUAL_REVIEW"
DISMISSED = "DISMISSED"

OPEN_STATUSES = (PENDING, EXPIRED, PENDING_REVOCATION, MANUAL_REVIEW)
# Statuts pour lesquels un WOULD_BLOCK a déjà été écrit.
BLOCKED_STATUSES = (EXPIRED, PENDING_REVOCATION)

WOULD_BLOCK = "WOULD_BLOCK"
WOULD_UNBLOCK = "WOULD_UNBLOCK"

NONE = "NONE"
OPEN = "OPEN"
CHANGE = "CHANGE"

_CLEAN = frozenset({Verdict.OK, Verdict.OK_IN_GRACE, Verdict.PROTECTED})
_UNAUTHORIZED = frozenset({Verdict.UNKNOWN_IDENTITY, Verdict.EXPIRED_CANDIDATE})


class InvalidTransition(Exception):
    pass


class AdminAction(str, Enum):
    AUTHORIZE = "AUTHORIZE"
    DISMISS = "DISMISS"


@dataclass(frozen=True)
class OpenRecord:
    status: str
    started_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class Transition:
    kind: str                                  # NONE | OPEN | CHANGE
    new_status: Optional[str] = None
    decision: Optional[str] = None             # WOULD_BLOCK | WOULD_UNBLOCK | None
    note: Optional[str] = None                 # code (jamais de texte libre)
    expires_at: Optional[datetime] = None      # seulement pour OPEN
    resolved: bool = False                     # True : AUTHORIZED / DISMISSED (resolved_at à renseigner)
    reason: str = ""


def _none(reason: str) -> Transition:
    return Transition(NONE, reason=reason)


def _aware(*values: Optional[datetime]) -> None:
    for v in values:
        if v is not None and v.tzinfo is None:
            raise ValueError("date sans fuseau horaire")


def suppressed_until(dismissed_at: Optional[datetime], span: timedelta = DISMISS_SUPPRESSION) -> Optional[datetime]:
    """Fin de la période pendant laquelle un dossier écarté ne se rouvre pas."""
    _aware(dismissed_at)
    return None if dismissed_at is None else dismissed_at + span


def next_step(
    *,
    now: datetime,
    active_now: bool,
    status_verdict: Verdict,
    record: Optional[OpenRecord],
    exempt: bool,
    suppressed_until: Optional[datetime],
    window: timedelta = WINDOW,
) -> Transition:
    """status_verdict : verdict calculé avec l'activité forcée à vrai (le statut décide).
    active_now : activité confirmée réelle, qui ne sert qu'à OUVRIR un dossier."""
    if window <= timedelta(0):
        raise ValueError("window doit être positif")
    _aware(now, suppressed_until)
    if record is not None:
        _aware(record.started_at, record.expires_at)
        if record.status not in OPEN_STATUSES:
            raise ValueError("dossier non ouvert : %s" % record.status)
        return _with_record(now, status_verdict, record)
    return _without_record(now, active_now, status_verdict, exempt, suppressed_until, window)


def _with_record(now: datetime, verdict: Verdict, rec: OpenRecord) -> Transition:
    blocked = rec.status in BLOCKED_STATUSES
    unblock = WOULD_UNBLOCK if blocked else None

    if verdict in _CLEAN:
        note = "auto_protected" if verdict is Verdict.PROTECTED else "auto_ok"
        return Transition(CHANGE, AUTHORIZED, unblock, note, resolved=True, reason=note)

    if verdict is Verdict.MANUAL_REVIEW:
        if rec.status == MANUAL_REVIEW:
            return _none("manual_review_pending")
        return Transition(CHANGE, MANUAL_REVIEW, unblock, reason="needs_review")

    if verdict in _UNAUTHORIZED:
        if rec.status == PENDING:
            if now >= rec.expires_at:
                return Transition(CHANGE, EXPIRED, WOULD_BLOCK, reason="window_elapsed")
            return _none("waiting")
        if rec.status == MANUAL_REVIEW:
            return _none("manual_review_kept")      # jamais de blocage automatique depuis une revue
        return _none("already_blocked")

    return _none("no_decision")


def _without_record(now: datetime, active_now: bool, verdict: Verdict, exempt: bool,
                    suppressed: Optional[datetime], window: timedelta) -> Transition:
    if verdict is Verdict.NO_DECISION:
        return _none("no_decision")
    if verdict in _CLEAN:
        return _none("clean")
    if not active_now:
        return _none("inactive")
    if exempt and verdict is Verdict.UNKNOWN_IDENTITY:
        return _none("exempt")
    if suppressed is not None and now < suppressed:
        return _none("dismissed_recently")
    if verdict in _UNAUTHORIZED:
        return Transition(OPEN, PENDING, expires_at=now + window, reason="unauthorized_active")
    if verdict is Verdict.MANUAL_REVIEW:
        return Transition(OPEN, MANUAL_REVIEW, expires_at=now + window, reason="needs_review")
    return _none("no_decision")


def admin_resolution(action: AdminAction, status: str) -> Transition:
    """Décision d'un administrateur sur un dossier ouvert. Un dossier résolu ne se modifie plus."""
    if status not in OPEN_STATUSES:
        raise InvalidTransition("dossier non ouvert : %s" % status)
    new = AUTHORIZED if AdminAction(action) is AdminAction.AUTHORIZE else DISMISSED
    unblock = WOULD_UNBLOCK if status in BLOCKED_STATUSES else None
    return Transition(CHANGE, new, unblock, resolved=True, reason="admin_" + new.lower())
