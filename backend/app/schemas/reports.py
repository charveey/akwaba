import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class AmountRow(BaseModel):
    key: str
    label: str
    period_start: date | None
    count: int
    amount_xof: int


class AmountReport(BaseModel):
    date_from: date | None
    date_to: date | None
    group_by: str
    count: int
    total_xof: int
    rows: list[AmountRow]


class ProfitRow(BaseModel):
    key: str
    label: str
    period_start: date
    revenue_xof: int
    expenses_xof: int
    profit_xof: int


class ProfitReport(BaseModel):
    date_from: date | None
    date_to: date | None
    group_by: str
    revenue_xof: int
    expenses_xof: int
    profit_xof: int
    rows: list[ProfitRow]


class AuditEntry(BaseModel):
    id: int
    occurred_at: datetime
    actor_admin_id: uuid.UUID | None
    actor_email: str | None
    actor_label: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    details: Any | None
    ip_address: str | None
    # Lot 11d : acteur normalisé à la LECTURE (l'historique append-only n'est jamais réécrit)
    actor_display: str | None = None
    actor_kind: str | None = None    # admin | attempt | system | cli | anonymous | unknown


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int
    limit: int
    offset: int
