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


class QuarantinePage(BaseModel):
    items: List[QuarantineOut]
    total: int
    counts: Dict[str, int]
    limit: int
    offset: int


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


class ExemptionPage(BaseModel):
    items: List[ExemptionOut]
    total: int
    limit: int
    offset: int
