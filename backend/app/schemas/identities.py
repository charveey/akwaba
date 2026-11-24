import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class MemberRef(BaseModel):
    id: uuid.UUID
    full_name: str


class DeviceOut(BaseModel):
    stable_id: str
    hostname: Optional[str]
    os: Optional[str]
    tailscale_ip: str
    last_seen_at: datetime


class OpenRecordRef(BaseModel):
    id: uuid.UUID
    status: str


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
    # Lot 11c (lecture seule, informatif)
    display_name: Optional[str] = None
    tailscale_user_id: Optional[int] = None
    devices: List[DeviceOut] = []
    open_record: Optional[OpenRecordRef] = None
    exempt: bool = False
    protection_sources: List[str] = []
    # ARCHIVED_MEMBER_LOGIN | CONTACT_EMAIL_MATCH | MULTIPLE_EXIT_NODES — jamais d'effet automatique
    ambiguity: List[str] = []
    exit_node_count: int = 1       # Exit Nodes distincts dans les observations conservées


class IdentityEvaluationPage(BaseModel):
    items: List[IdentityEvaluationOut]
    total: int                     # après filtres verdict / activité
    counts: Dict[str, int]         # par verdict, avant ces deux filtres
    unresolved_recent_sources: int
    evaluated_at: datetime
    recency_seconds: int
    limit: int
    offset: int
    # Période de référence de MULTIPLE_EXIT_NODES : observations conservées (purge).
    exit_node_reference_days: int = 0


class ContactMatch(BaseModel):
    """Membre dont l'e-mail de contact correspond au login (suggestion, jamais appliquée)."""
    id: uuid.UUID
    full_name: str


class ObservationOut(BaseModel):
    observed_at: datetime
    window_seconds: int
    active_flows: int
    replied_flows: int
    new_flows: Optional[int]
    active_confirmed: bool
    hostname: Optional[str]


class UnresolvedSource(BaseModel):
    """Source non résolue (whois en échec) : aucune identité supposée, aucune action."""
    exit_node_id: uuid.UUID
    tailscale_ip: str
    observations: int
    first_observed_at: datetime
    last_observed_at: datetime


class UnresolvedPage(BaseModel):
    items: List[UnresolvedSource]
    hours: int
    evaluated_at: datetime
