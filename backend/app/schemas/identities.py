import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class MemberRef(BaseModel):
    id: uuid.UUID
    full_name: str


class IdentityEvaluationOut(BaseModel):
    id: uuid.UUID
    exit_node_id: uuid.UUID
    login_name: str
    is_sharee: bool
    first_seen_at: datetime
    last_seen_at: datetime
    last_confirmed_activity_at: Optional[datetime]
    active_now: bool
    verdict: str
    reason: str
    protected: bool
    member: Optional[MemberRef]
    client_identity_id: Optional[uuid.UUID]
    access: Optional[str]          # meilleur état d'accès parmi les abonnements du membre


class IdentityEvaluationPage(BaseModel):
    items: List[IdentityEvaluationOut]
    total: int                     # après filtres verdict / activité
    counts: Dict[str, int]         # par verdict, avant ces deux filtres
    unresolved_recent_sources: int
    evaluated_at: datetime
    recency_seconds: int
    limit: int
    offset: int
