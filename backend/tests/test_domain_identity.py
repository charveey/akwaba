import itertools

import pytest

from app.domain.identity import Evaluation, Verdict, best_access, evaluate_identity
from app.domain.subscriptions import AccessStatus as A

ALL = list(A)


def ev(**over):
    base = dict(resolved=True, active_confirmed=True, is_protected=False, member_linked=True, accesses=[A.ACTIVE])
    base.update(over)
    return evaluate_identity(**base)


def test_priority_1_unresolved_beats_everything():
    for protected, linked, acc in itertools.product((False, True), (False, True), ([A.ACTIVE], [A.EXPIRED], [])):
        assert ev(resolved=False, is_protected=protected, member_linked=linked, accesses=acc) == Evaluation(
            Verdict.NO_DECISION, "unresolved")


def test_priority_2_unconfirmed_activity_beats_protection_and_membership():
    for protected, linked, acc in itertools.product((False, True), (False, True), ([A.EXPIRED], [])):
        assert ev(active_confirmed=False, is_protected=protected, member_linked=linked, accesses=acc) == Evaluation(
            Verdict.NO_DECISION, "activity_not_confirmed")


def test_priority_3_protection_beats_membership_and_expiry():
    for linked, acc in itertools.product((False, True), ([A.EXPIRED], [A.NONE], [])):
        assert ev(is_protected=True, member_linked=linked, accesses=acc) == Evaluation(Verdict.PROTECTED, "protected")


@pytest.mark.parametrize("accesses, verdict, reason", [
    ([A.ACTIVE], Verdict.OK, "access_active"),
    ([A.GRACE_PERIOD], Verdict.OK_IN_GRACE, "in_grace"),
    ([A.EXPIRED], Verdict.EXPIRED_CANDIDATE, "expired_after_grace"),
    ([A.NONE], Verdict.MANUAL_REVIEW, "no_usable_subscription"),
    ([A.SUSPENDED], Verdict.MANUAL_REVIEW, "suspended"),
    ([], Verdict.MANUAL_REVIEW, "no_subscription"),
])
def test_member_verdicts(accesses, verdict, reason):
    assert ev(accesses=accesses) == Evaluation(verdict, reason)


def test_priority_8_resolved_identity_without_member():
    assert ev(member_linked=False, accesses=[]) == Evaluation(Verdict.UNKNOWN_IDENTITY, "no_member")
    assert ev(member_linked=False, accesses=[A.ACTIVE]).verdict is Verdict.UNKNOWN_IDENTITY   # sans lien, rien n'est lu


@pytest.mark.parametrize("accesses, expected", [
    ([A.EXPIRED, A.ACTIVE], A.ACTIVE),
    ([A.EXPIRED, A.GRACE_PERIOD], A.GRACE_PERIOD),
    ([A.EXPIRED, A.NONE], A.NONE),                 # renouvellement impayé en attente : revue, pas quarantaine
    ([A.EXPIRED, A.SUSPENDED], A.SUSPENDED),
    ([A.NONE, A.SUSPENDED], A.NONE),
    ([A.GRACE_PERIOD, A.NONE], A.GRACE_PERIOD),
    ([], None),
])
def test_best_access_is_the_most_favourable(accesses, expected):
    assert best_access(accesses) is expected


def test_best_access_does_not_depend_on_order():
    for perm in itertools.permutations(ALL):
        assert best_access(perm) is A.ACTIVE
    for perm in itertools.permutations([A.EXPIRED, A.SUSPENDED, A.NONE]):
        assert best_access(perm) is A.NONE


def test_best_access_accepts_plain_strings_and_refuses_unknown_ones():
    assert best_access(["GRACE_PERIOD", "EXPIRED"]) is A.GRACE_PERIOD
    with pytest.raises(ValueError):
        best_access(["INCONNU"])


def test_the_function_only_ever_returns_a_verdict_never_an_action():
    names = {v.value for v in Verdict}
    assert not {n for n in names if "BLOCK" in n or "QUARANTINE" in n}
