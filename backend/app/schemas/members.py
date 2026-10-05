import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_LOGIN_RE = re.compile(r"^\S+$")


def _clean(v: Any) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("doit être une chaîne")
    v = v.strip()
    return v or None


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone_e164: str | None = Field(default=None, max_length=32)
    country_code: str | None = Field(default=None, max_length=2)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("full_name", mode="before")
    @classmethod
    def _name(cls, v: Any) -> str:
        v = _clean(v)
        if v is None:
            raise ValueError("le nom est obligatoire")
        return v

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: Any) -> str | None:
        v = _clean(v)
        if v is None:
            return None
        v = v.lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("adresse e-mail invalide")
        return v

    @field_validator("phone_e164", mode="before")
    @classmethod
    def _phone(cls, v: Any) -> str | None:
        v = _clean(v)
        if v is None:
            return None
        v = re.sub(r"[ .\-()]", "", v)
        if not _PHONE_RE.match(v):
            raise ValueError("téléphone invalide : format international attendu (ex. +2250102030405)")
        return v

    @field_validator("country_code", mode="before")
    @classmethod
    def _country(cls, v: Any) -> str | None:
        v = _clean(v)
        if v is None:
            return None
        v = v.upper()
        if not _COUNTRY_RE.match(v):
            raise ValueError("code pays à 2 lettres attendu (ex. CI)")
        return v

    @field_validator("notes", mode="before")
    @classmethod
    def _notes(cls, v: Any) -> str | None:
        return _clean(v)


class IdentityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_name: str = Field(max_length=320)

    @field_validator("login_name", mode="before")
    @classmethod
    def _login(cls, v: Any) -> str:
        v = _clean(v)
        if v is None:
            raise ValueError("le login est obligatoire")
        v = v.lower()
        if not _LOGIN_RE.match(v):
            raise ValueError("le login ne doit pas contenir d'espace")
        return v


class IdentityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    login_name: str
    is_active: bool
    linked_at: datetime
    unlinked_at: datetime | None


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str | None
    phone_e164: str | None
    country_code: str | None
    notes: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    identities: list[IdentityOut] = []


class MemberPage(BaseModel):
    items: list[MemberOut]
    total: int
    limit: int
    offset: int


def member_out(member: Any, identities: list[Any]) -> MemberOut:
    out = MemberOut.model_validate(member)
    return out.model_copy(update={"identities": [IdentityOut.model_validate(i) for i in identities]})
