from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_clock, require_admin
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.identities import IdentityEvaluationPage
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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> IdentityEvaluationPage:
    return svc.evaluate_identities(db, clock, verdict=verdict, active_now=active_now, q=q, limit=limit, offset=offset)
