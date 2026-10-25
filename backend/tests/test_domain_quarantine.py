import itertools
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.identity import Verdict as V
from app.domain.quarantine import (
    AUTHORIZED, BLOCKED_STATUSES, CHANGE, DISMISSED, EXPIRED, MANUAL_REVIEW, NONE, OPEN, OPEN_STATUSES, PENDING,
    PENDING_REVOCATION, WINDOW, WOULD_BLOCK, WOULD_UNBLOCK, AdminAction, InvalidTransition, OpenRecord,
    admin_resolution, next_step, suppressed_until,
)

T0 = datetime(2040, 1, 1, 12, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
CLEAN = (V.OK, V.OK_IN_GRACE, V.PROTECTED)
UNAUTHORIZED = (V.UNKNOWN_IDENTITY, V.EXPIRED_CANDIDATE)


def step(**over):
    base = dict(now=T0, active_now=True, status_verdict=V.UNKNOWN_IDENTITY, record=None, exempt=False,
                suppressed_until=None)
    base.update(over)
    return next_step(**base)


def rec(status=PENDING, expired=False):
    return OpenRecord(status, T0 - 30 * H, T0 - H if expired else T0 + H)


def settle(record, t, now):
    """L'état du dossier après application de la transition (ce que fera le service)."""
    if t.kind == NONE:
        return record
    if t.kind == OPEN:
        return OpenRecord(t.new_status, now, t.expires_at)
    if t.resolved:
        return None
    return OpenRecord(t.new_status, record.started_at, record.expires_at)


# ----------------------------------------------------------------- ouverture

@pytest.mark.parametrize("verdict", UNAUTHORIZED)
def test_an_active_unauthorized_identity_opens_a_pending_record_for_24_hours(verdict):
    t = step(status_verdict=verdict)
    assert (t.kind, t.new_status, t.decision, t.resolved) == (OPEN, PENDING, None, False)
    assert t.expires_at == T0 + WINDOW == T0 + timedelta(hours=24)


def test_manual_review_opens_a_record_without_any_decision():
    t = step(status_verdict=V.MANUAL_REVIEW)
    assert (t.kind, t.new_status, t.decision) == (OPEN, MANUAL_REVIEW, None) and t.expires_at == T0 + WINDOW


def test_the_window_is_configurable():
    assert step(window=timedelta(hours=6)).expires_at == T0 + timedelta(hours=6)


@pytest.mark.parametrize("over, reason", [
    (dict(active_now=False), "inactive"),
    (dict(status_verdict=V.OK), "clean"),
    (dict(status_verdict=V.OK_IN_GRACE), "clean"),
    (dict(status_verdict=V.PROTECTED), "clean"),
    (dict(status_verdict=V.NO_DECISION), "no_decision"),
    (dict(exempt=True), "exempt"),
    (dict(suppressed_until=T0 + H), "dismissed_recently"),
])
def test_no_record_is_opened(over, reason):
    t = step(**over)
    assert (t.kind, t.reason) == (NONE, reason)


def test_the_exemption_only_covers_unknown_identities():
    assert step(exempt=True, status_verdict=V.UNKNOWN_IDENTITY).kind == NONE
    assert step(exempt=True, status_verdict=V.EXPIRED_CANDIDATE).kind == OPEN
    assert step(exempt=True, status_verdict=V.MANUAL_REVIEW).kind == OPEN


def test_dismissal_suppression_ends_exactly_at_its_boundary():
    until = suppressed_until(T0 - timedelta(days=7) + H)
    assert until == T0 + H
    assert step(suppressed_until=until).kind == NONE
    assert step(now=T0 + H, suppressed_until=until).kind == OPEN        # borne exclusive
    assert suppressed_until(None) is None


# --------------------------------------------------- dossiers ouverts : table

@pytest.mark.parametrize("status, expired, verdict, kind, new, decision, note", [
    (PENDING, False, V.UNKNOWN_IDENTITY, NONE, None, None, None),
    (PENDING, False, V.EXPIRED_CANDIDATE, NONE, None, None, None),
    (PENDING, True, V.UNKNOWN_IDENTITY, CHANGE, EXPIRED, WOULD_BLOCK, None),
    (PENDING, True, V.EXPIRED_CANDIDATE, CHANGE, EXPIRED, WOULD_BLOCK, None),
    (PENDING, False, V.OK, CHANGE, AUTHORIZED, None, "auto_ok"),
    (PENDING, True, V.OK_IN_GRACE, CHANGE, AUTHORIZED, None, "auto_ok"),
    (PENDING, False, V.PROTECTED, CHANGE, AUTHORIZED, None, "auto_protected"),
    (PENDING, False, V.MANUAL_REVIEW, CHANGE, MANUAL_REVIEW, None, None),
    (PENDING, True, V.MANUAL_REVIEW, CHANGE, MANUAL_REVIEW, None, None),          # jamais de WOULD_BLOCK ici
    (EXPIRED, True, V.UNKNOWN_IDENTITY, NONE, None, None, None),                  # pas de décision répétée
    (EXPIRED, True, V.EXPIRED_CANDIDATE, NONE, None, None, None),
    (EXPIRED, True, V.OK, CHANGE, AUTHORIZED, WOULD_UNBLOCK, "auto_ok"),
    (EXPIRED, True, V.OK_IN_GRACE, CHANGE, AUTHORIZED, WOULD_UNBLOCK, "auto_ok"),
    (EXPIRED, True, V.PROTECTED, CHANGE, AUTHORIZED, WOULD_UNBLOCK, "auto_protected"),
    (EXPIRED, True, V.MANUAL_REVIEW, CHANGE, MANUAL_REVIEW, WOULD_UNBLOCK, None),
    (PENDING_REVOCATION, True, V.OK, CHANGE, AUTHORIZED, WOULD_UNBLOCK, "auto_ok"),
    (PENDING_REVOCATION, True, V.UNKNOWN_IDENTITY, NONE, None, None, None),
    (MANUAL_REVIEW, False, V.OK, CHANGE, AUTHORIZED, None, "auto_ok"),
    (MANUAL_REVIEW, True, V.PROTECTED, CHANGE, AUTHORIZED, None, "auto_protected"),
    (MANUAL_REVIEW, True, V.UNKNOWN_IDENTITY, NONE, None, None, None),            # jamais de blocage depuis une revue
    (MANUAL_REVIEW, True, V.EXPIRED_CANDIDATE, NONE, None, None, None),
    (MANUAL_REVIEW, False, V.MANUAL_REVIEW, NONE, None, None, None),
])
def test_open_record_transitions(status, expired, verdict, kind, new, decision, note):
    for active in (True, False):                       # l'activité du moment ne change rien pour un dossier ouvert
        t = step(record=rec(status, expired), status_verdict=verdict, active_now=active)
        assert (t.kind, t.new_status, t.decision, t.note) == (kind, new, decision, note)
        assert t.resolved is (new == AUTHORIZED)


def test_the_expiry_boundary_is_inclusive():
    record = OpenRecord(PENDING, T0 - 24 * H, T0)
    assert step(record=record, now=T0 - timedelta(seconds=1)).kind == NONE
    assert step(record=record, now=T0).decision == WOULD_BLOCK


def test_a_long_outage_still_yields_a_single_block():
    record = OpenRecord(PENDING, T0 - timedelta(days=9), T0 - timedelta(days=8))
    t = step(record=record)
    assert (t.new_status, t.decision) == (EXPIRED, WOULD_BLOCK)
    assert step(record=settle(record, t, T0)).kind == NONE


# ------------------------------------------------------------ propriétés

def all_cases():
    return itertools.product(OPEN_STATUSES, list(V), (False, True), (False, True))


def test_exhaustive_safety_properties():
    for status, verdict, expired, active in all_cases():
        t = step(record=rec(status, expired), status_verdict=verdict, active_now=active)
        if t.decision == WOULD_BLOCK:
            assert status == PENDING and expired and verdict in UNAUTHORIZED and t.new_status == EXPIRED
        if t.decision == WOULD_UNBLOCK:
            assert status in BLOCKED_STATUSES and t.new_status in (AUTHORIZED, MANUAL_REVIEW)
        if verdict in CLEAN or verdict is V.MANUAL_REVIEW:
            assert t.decision != WOULD_BLOCK
        if t.resolved:
            assert t.new_status in (AUTHORIZED, DISMISSED)
        if t.kind == CHANGE:
            assert t.new_status != status and t.new_status != PENDING_REVOCATION
        if t.kind == NONE:
            assert t.new_status is None and t.decision is None
        assert t.kind != OPEN


def test_an_active_clean_or_protected_identity_never_gets_a_record():
    for verdict, active, exempt in itertools.product(CLEAN, (False, True), (False, True)):
        assert step(status_verdict=verdict, active_now=active, exempt=exempt).kind == NONE


def test_applying_a_transition_twice_changes_nothing_the_second_time():
    for status, verdict, expired, active in all_cases():
        record = rec(status, expired)
        first = step(record=record, status_verdict=verdict, active_now=active)
        second = step(record=settle(record, first, T0), status_verdict=verdict, active_now=active)
        assert second.kind == NONE, (status, verdict, expired, first)
    for verdict in list(V):
        first = step(status_verdict=verdict)
        if first.kind == OPEN:
            assert step(record=settle(None, first, T0), status_verdict=verdict).kind == NONE


def test_inputs_must_be_timezone_aware_and_records_open():
    with pytest.raises(ValueError):
        step(now=datetime(2040, 1, 1))
    with pytest.raises(ValueError):
        step(suppressed_until=datetime(2040, 1, 2))
    with pytest.raises(ValueError):
        step(record=OpenRecord(PENDING, datetime(2040, 1, 1), T0))
    with pytest.raises(ValueError):
        step(window=timedelta(0))
    for closed in (AUTHORIZED, DISMISSED, "INCONNU"):
        with pytest.raises(ValueError):
            step(record=OpenRecord(closed, T0 - H, T0 + H))
    with pytest.raises(ValueError):
        suppressed_until(datetime(2040, 1, 1))


# --------------------------------------------------------- cycle complet

def test_a_full_life_cycle_alternates_block_and_unblock_decisions():
    state = {"rec": None, "now": T0}
    decisions = []

    def tick(delta, verdict, active=True):
        state["now"] = state["now"] + delta
        t = next_step(now=state["now"], active_now=active, status_verdict=verdict, record=state["rec"],
                      exempt=False, suppressed_until=None)
        if t.decision:
            decisions.append(t.decision)
        state["rec"] = settle(state["rec"], t, state["now"])
        return t

    assert tick(timedelta(0), V.UNKNOWN_IDENTITY).kind == OPEN
    assert tick(H, V.UNKNOWN_IDENTITY).kind == NONE
    assert tick(24 * H, V.UNKNOWN_IDENTITY).decision == WOULD_BLOCK
    assert tick(H, V.UNKNOWN_IDENTITY, active=False).kind == NONE
    assert tick(H, V.OK).decision == WOULD_UNBLOCK and state["rec"] is None
    assert tick(H, V.OK).kind == NONE
    assert tick(H, V.EXPIRED_CANDIDATE).kind == OPEN
    assert tick(25 * H, V.EXPIRED_CANDIDATE).decision == WOULD_BLOCK
    assert decisions == [WOULD_BLOCK, WOULD_UNBLOCK, WOULD_BLOCK]


# ------------------------------------------------------------ administrateur

@pytest.mark.parametrize("status", OPEN_STATUSES)
@pytest.mark.parametrize("action, new", [(AdminAction.AUTHORIZE, AUTHORIZED), (AdminAction.DISMISS, DISMISSED)])
def test_admin_resolution_of_open_records(status, action, new):
    t = admin_resolution(action, status)
    assert (t.kind, t.new_status, t.resolved) == (CHANGE, new, True)
    assert t.decision == (WOULD_UNBLOCK if status in BLOCKED_STATUSES else None)


@pytest.mark.parametrize("status", [AUTHORIZED, DISMISSED, "INCONNU"])
def test_a_resolved_record_cannot_be_resolved_again(status):
    for action in AdminAction:
        with pytest.raises(InvalidTransition):
            admin_resolution(action, status)


def test_admin_actions_accept_plain_strings_and_refuse_unknown_ones():
    assert admin_resolution("DISMISS", PENDING).new_status == DISMISSED
    with pytest.raises(ValueError):
        admin_resolution("DETRUIRE", PENDING)
