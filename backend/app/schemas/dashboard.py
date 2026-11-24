"""Lot 11d : vue d'ensemble et rapport opérationnel (lecture seule, calculés par le backend, UTC)."""
from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel

from app.schemas.reports import AmountRow


class AgentIndicator(BaseModel):
    exit_nodes: int
    healthy_nodes: int          # selon la fonction du réconciliateur (quarantine._health)
    last_heartbeat_at: Optional[datetime]


class Indicators(BaseModel):
    members_total: int          # membres non archivés
    members_active: int
    members_grace: int
    subscriptions_expired: int  # abonnement en vigueur expiré, grâce dépassée
    payments_pending: int       # abonnements non payés, non résiliés, membre non archivé
    revenue_month_xof: int
    revenue_month_count: int
    quarantine_open: int
    unresolved_sources_24h: int
    agent: AgentIndicator


class PlanCount(BaseModel):
    plan_code: str
    plan_name: str
    count: int


class MonthCount(BaseModel):
    month: date
    label: str
    new_members: int
    cumulative: int             # inscriptions cumulées (archivés compris) : PAS un nombre de membres actifs


class StatusCount(BaseModel):
    status: str
    count: int


class DashboardOut(BaseModel):
    evaluated_at: datetime
    today: date
    month_start: date
    range_start: date
    indicators: Indicators
    revenue_by_month: List[AmountRow]
    subscriptions_by_plan: List[PlanCount]
    members_by_month: List[MonthCount]
    payments_by_method: List[AmountRow]
    quarantine_by_status: List[StatusCount]


class OperationsRow(BaseModel):
    month: date
    label: str
    new_members: int
    new_member_subscriptions: int   # statut « Nouveau membre » (premier abonnement payé)
    renewals: int                   # « Membre renouvelé »
    late_renewals: int              # « Renouvellement en retard »
    plan_changes: int               # attribut « Changement de niveau »
    quarantine_opened: int
    identities_first_seen: int


class OperationsReport(BaseModel):
    evaluated_at: datetime
    today: date
    date_from: date
    date_to: date
    snapshot: Dict[str, int]        # accès en vigueur aujourd'hui : ACTIVE, GRACE_PERIOD, EXPIRED, SUSPENDED, NONE
    members_total: int
    rows: List[OperationsRow]
    totals: Dict[str, int]
