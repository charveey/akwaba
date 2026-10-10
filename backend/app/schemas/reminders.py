import uuid
from datetime import date, datetime

from pydantic import BaseModel


class ReminderOut(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    member_id: uuid.UUID
    member_name: str
    offset_days: int
    due_date: date
    message: str
    status: str            # statut effectif (CANCELED si l'abonnement a été renouvelé entre-temps)
    opened_at: datetime | None
    sent_at: datetime | None
    created_at: datetime


class ReminderLink(BaseModel):
    reminder: ReminderOut
    whatsapp_url: str


class GenerateResult(BaseModel):
    today: date
    created: int
    skipped_existing: int
    reminders: list[ReminderOut]


class ReminderPage(BaseModel):
    items: list[ReminderOut]
    total: int
    limit: int
    offset: int
