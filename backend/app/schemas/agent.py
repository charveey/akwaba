"""Schéma du lot envoyé par l'agent. Strict : aucun champ inconnu, aucune coercition de type."""
import ipaddress
import re
import uuid
from typing import Literal, Optional

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator,
)

_TAILSCALE = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
_LOGIN_RE = re.compile(r"^\S+$")
Count = StrictInt
STRICT = ConfigDict(extra="forbid")


class AgentIdentityIn(BaseModel):
    model_config = STRICT

    login_name: str = Field(max_length=320)
    tailscale_user_id: Optional[StrictInt] = Field(default=None, ge=0, le=2**63 - 1)
    stable_id: Optional[str] = Field(default=None, max_length=64)
    is_sharee: StrictBool

    @field_validator("login_name")
    @classmethod
    def _login(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or not _LOGIN_RE.match(v):
            raise ValueError("login invalide")
        return v

    @field_validator("stable_id")
    @classmethod
    def _stable(cls, v: Optional[str]) -> Optional[str]:
        return (v.strip() or None) if v is not None else None


class PeerIn(BaseModel):
    """Informatif : accepté, validé, non stocké."""
    model_config = STRICT

    known: Optional[StrictBool] = None
    online: Optional[StrictBool] = None
    expired: Optional[StrictBool] = None
    tagged: Optional[StrictBool] = None


class ObservationIn(BaseModel):
    model_config = STRICT

    src_ip: str
    total_flows: Count = Field(ge=0, le=10_000_000)
    recent_flows: Count = Field(ge=0, le=10_000_000)
    recent_replied_flows: Count = Field(ge=0, le=10_000_000)
    unknown_age_flows: Count = Field(ge=0, le=10_000_000)
    new_flows: Optional[Count] = Field(default=None, ge=0, le=10_000_000)
    active_window: StrictBool
    consecutive_active_windows: Count = Field(ge=0, le=100_000)
    active_confirmed: StrictBool
    identity_status: Literal["RESOLVED", "UNRESOLVED"]
    identity_technical_error: StrictBool
    identity: Optional[AgentIdentityIn] = None
    peer: Optional[PeerIn] = None

    @field_validator("src_ip")
    @classmethod
    def _ip(cls, v: str) -> str:
        try:
            ip = ipaddress.ip_address(v.strip())
        except ValueError:
            raise ValueError("adresse IP invalide")
        if not any(ip in net for net in _TAILSCALE):
            raise ValueError("adresse hors du réseau Tailscale")
        return str(ip)

    @model_validator(mode="after")
    def _consistent(self) -> "ObservationIn":
        if self.identity_status == "RESOLVED" and self.identity is None:
            raise ValueError("RESOLVED sans identité")
        if self.identity_status == "UNRESOLVED" and self.identity is not None:
            raise ValueError("UNRESOLVED avec une identité")
        if not self.recent_replied_flows <= self.recent_flows <= self.total_flows:
            raise ValueError("compteurs de flux incohérents")
        if self.recent_flows + self.unknown_age_flows > self.total_flows:
            raise ValueError("compteurs de flux incohérents")
        return self


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    batch_id: uuid.UUID
    schema_version: StrictInt = Field(alias="schema")
    sampled_at: AwareDatetime
    window_seconds: StrictInt = Field(ge=1, le=3600)
    conntrack_ok: StrictBool
    status_ok: StrictBool
    whois_ok: StrictBool
    observations: list[ObservationIn] = Field(max_length=500)

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != 1:
            raise ValueError("version de schéma non prise en charge")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "BatchIn":
        if not self.conntrack_ok:
            raise ValueError("un lot ne peut pas être produit sans conntrack")
        ips = [o.src_ip for o in self.observations]
        if len(set(ips)) != len(ips):
            raise ValueError("une même adresse source apparaît deux fois")
        return self
