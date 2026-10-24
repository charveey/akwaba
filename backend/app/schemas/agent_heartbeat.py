"""Schéma du heartbeat envoyé par l'agent. Strict : aucun champ inconnu, aucune coercition."""
from typing import Optional, Union

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator,
)

Number = Union[StrictFloat, StrictInt]
CATEGORY = r"^[A-Za-z0-9_.-]{1,64}$"
BIG = 10**9


class HeartbeatDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: StrictInt = Field(ge=0, le=BIG)
    unparsed: StrictInt = Field(ge=0, le=BIG)
    timeouts_loaded: StrictInt = Field(ge=0, le=BIG)
    observations: StrictInt = Field(ge=0, le=BIG)
    conntrack_saturated: StrictBool
    dropped_batches: StrictInt = Field(ge=0, le=BIG)
    spool_recovered_corruption: StrictBool
    cycles: StrictInt = Field(ge=0, le=BIG)
    loop_errors: StrictInt = Field(ge=0, le=BIG)
    cycle_failed: StrictBool
    conntrack_saturation: Optional[Number] = None
    spool_oldest_age_seconds: Optional[Number] = None
    sent_batches: Optional[StrictInt] = Field(default=None, ge=0, le=BIG)
    rejected_batches: Optional[StrictInt] = Field(default=None, ge=0, le=BIG)
    send_failures: Optional[StrictInt] = Field(default=None, ge=0, le=BIG)
    last_send_error: Optional[str] = Field(default=None, pattern=CATEGORY)
    clock_skew_seconds: Optional[StrictInt] = Field(default=None, ge=-BIG, le=BIG)
    backend_reachable: Optional[StrictBool] = None

    @field_validator("conntrack_saturation")
    @classmethod
    def _saturation(cls, v):
        if v is not None and not 0 <= v <= 1:
            raise ValueError("saturation hors de [0, 1]")
        return v

    @field_validator("spool_oldest_age_seconds")
    @classmethod
    def _age(cls, v):
        if v is not None and v < 0:
            raise ValueError("âge négatif")
        return v


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: StrictInt = Field(alias="schema")
    agent_version: str = Field(pattern=r"^[A-Za-z0-9._+-]{1,32}$")
    generated_at: AwareDatetime
    conntrack_ok: StrictBool
    status_ok: StrictBool
    whois_ok: StrictBool
    bytes_available: StrictBool
    spool_depth: StrictInt = Field(ge=0, le=BIG)
    conntrack_count: Optional[StrictInt] = Field(default=None, ge=0, le=BIG)
    conntrack_max: Optional[StrictInt] = Field(default=None, ge=1, le=BIG)
    last_error: Optional[str] = Field(default=None, pattern=CATEGORY)
    details: HeartbeatDetails

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != 1:
            raise ValueError("version de schéma non prise en charge")
        return v
