"""Lot 11d : vue d'ensemble (indicateurs et séries calculés côté serveur, sur la base complète)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_clock, require_admin
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.dashboard import DashboardOut
from app.services import operations

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_admin)])


@router.get("", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> DashboardOut:
    return operations.dashboard(db, clock)
