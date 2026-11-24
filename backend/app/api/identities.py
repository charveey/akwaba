import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_clock, require_admin
from app.db.session import get_db
from app.domain.clock import Clock
from app.core.config import get_settings
from app.schemas.identities import IdentityEvaluationPage, UnresolvedPage
from app.schemas.identity_detail import IdentityDetail
from app.services import protected as protected_svc
from app.services import quarantine_views as qviews
from app.services import identities as svc

router = APIRouter(prefix="/identities", tags=["identities"], dependencies=[Depends(require_admin)])

VerdictName = Literal[
    "NO_DECISION", "PROTECTED", "OK", "OK_IN_GRACE", "EXPIRED_CANDIDATE", "MANUAL_REVIEW", "UNKNOWN_IDENTITY",
]


@router.get("", response_model=IdentityEvaluationPage)
def list_(
    verdict: Optional[VerdictName] = None,
    active_now: Optional[bool] = None,
    q: Optional[str] = Query(default=None, max_length=100),
    protected: Optional[bool] = None,
    has_member: Optional[bool] = None,
    ambiguous: Optional[bool] = None,
    open_record: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> IdentityEvaluationPage:
    return svc.evaluate_identities(db, clock, verdict=verdict, active_now=active_now, q=q, limit=limit, offset=offset,
                                   is_protected=protected, has_member=has_member, ambiguous=ambiguous,
                                   open_record=open_record)


@router.get("/unresolved", response_model=UnresolvedPage)
def unresolved(
    hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
) -> UnresolvedPage:
    """Sources UNRESOLVED récentes : information seulement, jamais d'identité supposée ni d'action."""
    return svc.unresolved_sources(db, clock, hours)


@router.get("/{identity_id}", response_model=IdentityDetail)
def detail(identity_id: uuid.UUID, db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> IdentityDetail:
    ev = svc.evaluate_one(db, clock, identity_id)
    if ev is None:
        raise HTTPException(404, "Identité introuvable")
    return IdentityDetail(
        identity=ev,
        contact_matches=svc.contact_matches(db, ev.login_name, ev.member.id if ev.member else None),
        observations=svc.recent_observations(db, identity_id),
        records=qviews.list_records(db, status=None, q=None, open_only=False, limit=200, offset=0,
                                    clock=clock, identity_id=identity_id).items,
        decisions=qviews.list_decisions(db, decision=None, q=None, date_from=None, date_to=None, limit=200,
                                        offset=0, identity_id=identity_id).items,
        exemptions=qviews.list_exemptions(db, active=None, q=None, limit=200, offset=0,
                                          identity_id=identity_id).items,
        protections=[p for p in protected_svc.list_protected(db, include_removed=True) if p.login_name == ev.login_name],
        evaluated_at=clock.now(),
        exit_node_reference_days=get_settings().retention_observations_days,
    )
