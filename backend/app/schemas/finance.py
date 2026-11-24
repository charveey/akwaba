import re
import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class CategoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("doit être une chaîne")
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            raise ValueError("le nom est obligatoire")
        return v


class CategoryUpdate(CategoryIn):
    is_active: bool


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool
    sort_order: int
    expense_count: int = 0      # lot 11d : dépenses rattachées (toutes, annulées comprises)


class ExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID
    amount: StrictInt = Field(gt=0, le=1_000_000_000)  # francs CFA entiers
    expense_date: date
    description: str | None = Field(default=None, max_length=500)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("doit être une chaîne")
        return v.strip() or None


class ExpenseOut(BaseModel):
    id: uuid.UUID
    category_id: uuid.UUID
    category_name: str
    amount: int
    currency: str
    expense_date: date
    description: str | None
    status: str
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime
    # Lot 11d : liens de correction (d'après l'audit expense.corrected)
    replaces_id: uuid.UUID | None = None
    replaced_by_id: uuid.UUID | None = None


class ExpensePage(BaseModel):
    items: list[ExpenseOut]
    total: int
    total_amount: int  # somme des dépenses ACTIVE du filtre courant
    limit: int
    offset: int
