import re
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_LOGIN_RE = re.compile(r"^\S+$")


class ProtectedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_name: str = Field(max_length=320)
    reason: Optional[str] = Field(default=None, max_length=500)

    @field_validator("login_name", mode="before")
    @classmethod
    def _login(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("doit être une chaîne")
        v = v.strip().lower()
        if not v or not _LOGIN_RE.match(v):
            raise ValueError("login invalide")
        return v

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("doit être une chaîne")
        return v.strip() or None


class ProtectedOut(BaseModel):
    id: uuid.UUID
    login_name: str
    source: str
    reason: Optional[str]
    created_at: datetime
    removed_at: Optional[datetime]
    active: bool
