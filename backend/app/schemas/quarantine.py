import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def _note(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("doit être une chaîne")
        return v.strip() or None


class QuarantineOut(BaseModel):
    id: uuid.UUID
    exit_node_identity_id: uuid.UUID
    login_name: str
    status: str
    started_at: datetime
    expires_at: datetime
    resolved_at: Optional[datetime]
    resolved_by_admin_id: Optional[uuid.UUID]
    resolution_note: Optional[str]
    simulated_block: bool
    exempt: bool
    # Lot 11c (lecture, calculés par le backend à evaluated_at)
    current_verdict: Optional[str] = None
    current_reason: Optional[str] = None
    last_confirmed_activity_at: Optional[datetime] = None
    remaining_seconds: Optional[int] = None      # PENDING uniquement : délai avant blocage SIMULÉ
    suppressed_until: Optional[datetime] = None  # DISMISSED : aucune réouverture avant cette date (exclue)
    reopen_possible: Optional[bool] = None       # DISMISSED : borne atteinte (réouverture à la borne exacte)
    resolved_by_email: Optional[str] = None


class QuarantinePage(BaseModel):
    items: List[QuarantineOut]
    total: int
    counts: Dict[str, int]
    limit: int
    offset: int
    evaluated_at: Optional[datetime] = None


class DecisionOut(BaseModel):
    id: str
    quarantine_record_id: Optional[uuid.UUID]
    exit_node_identity_id: uuid.UUID
    login_name: str
    decision: str
    mode: str
    enforced: bool
    decided_at: datetime
    reason: Optional[str]


class DecisionPage(BaseModel):
    items: List[DecisionOut]
    total: int
    limit: int
    offset: int


class QuarantineDetail(BaseModel):
    record: QuarantineOut
    decisions: List[DecisionOut]
    evaluated_at: Optional[datetime] = None


class ExemptionOut(BaseModel):
    id: uuid.UUID
    exit_node_identity_id: uuid.UUID
    login_name: str
    quarantine_record_id: Optional[uuid.UUID]
    granted_at: datetime
    granted_by_admin_id: Optional[uuid.UUID]
    ended_at: Optional[datetime]
    ended_by_admin_id: Optional[uuid.UUID]
    active: bool
    # Lot 11c : motif = note de résolution du dossier autorisé ; e-mails des administrateurs.
    reason: Optional[str] = None
    granted_by_email: Optional[str] = None
    ended_by_email: Optional[str] = None


class ExemptionPage(BaseModel):
    items: List[ExemptionOut]
    total: int
    limit: int
    offset: int
