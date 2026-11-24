"""Lot 11c : fiche d'une identité observée (lecture seule)."""
from datetime import datetime
from typing import List

from pydantic import BaseModel

from app.schemas.identities import ContactMatch, IdentityEvaluationOut, ObservationOut
from app.schemas.protected import ProtectedOut
from app.schemas.quarantine import DecisionOut, ExemptionOut, QuarantineOut


class IdentityDetail(BaseModel):
    identity: IdentityEvaluationOut
    contact_matches: List[ContactMatch]      # suggestions CONTACT_EMAIL_MATCH, jamais appliquées
    observations: List[ObservationOut]       # 50 plus récentes
    records: List[QuarantineOut]
    decisions: List[DecisionOut]
    exemptions: List[ExemptionOut]
    protections: List[ProtectedOut]          # toutes sources, historique compris
    evaluated_at: datetime
    exit_node_reference_days: int
