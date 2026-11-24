"""Lot 11d : diagnostic administratif des Exit Nodes (lecture seule). Hors de /api/agent/ (bloqué par Nginx)."""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_clock, require_admin
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.exit_nodes import ExitNodeDetail, ExitNodePage
from app.services import exit_nodes as svc

router = APIRouter(prefix="/exit-nodes", tags=["exit-nodes"], dependencies=[Depends(require_admin)])


@router.get("", response_model=ExitNodePage)
def list_nodes(db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> ExitNodePage:
    return svc.list_nodes(db, clock)


@router.get("/{node_id}", response_model=ExitNodeDetail)
def detail(node_id: uuid.UUID, db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> ExitNodeDetail:
    return svc.node_detail(db, clock, node_id)
