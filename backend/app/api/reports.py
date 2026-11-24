import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_clock, require_admin
from app.core.csv_export import csv_response
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.dashboard import OperationsReport
from app.schemas.reports import AmountReport, AuditPage, ProfitReport
from app.services import operations
from app.services import reports as svc

reports_router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_admin)])
audit_router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_admin)])


Format = Literal["json", "csv"]
_GROUP_FR = {"month": "mois", "quarter": "trimestre", "year": "annee", "payment_method": "moyen_de_paiement",
             "category": "categorie", "plan": "formule"}


def _amount_csv(name: str, report: AmountReport):
    col = _GROUP_FR.get(report.group_by, report.group_by)
    return csv_response(f"{name}_{col}", [col, "nombre", "montant_xof"],
                        [(r.label, r.count, r.amount_xof) for r in report.rows])


@reports_router.get("/revenue", response_model=AmountReport)
def revenue(
    group_by: Literal["month", "quarter", "year", "payment_method"] = "month",
    date_from: date | None = None, date_to: date | None = None,
    format_: Format = Query(default="json", alias="format"), db: Session = Depends(get_db),
):
    report = svc.revenue_report(db, group_by=group_by, date_from=date_from, date_to=date_to)
    return _amount_csv("recettes", report) if format_ == "csv" else report


@reports_router.get("/revenue-by-plan", response_model=AmountReport)
def revenue_by_plan(
    date_from: date | None = None, date_to: date | None = None,
    format_: Format = Query(default="json", alias="format"), db: Session = Depends(get_db),
):
    report = svc.revenue_by_plan_report(db, date_from=date_from, date_to=date_to)
    return _amount_csv("recettes", report) if format_ == "csv" else report


@reports_router.get("/expenses", response_model=AmountReport)
def expenses(
    group_by: Literal["month", "quarter", "year", "category"] = "month",
    date_from: date | None = None, date_to: date | None = None,
    format_: Format = Query(default="json", alias="format"), db: Session = Depends(get_db),
):
    report = svc.expenses_report(db, group_by=group_by, date_from=date_from, date_to=date_to)
    return _amount_csv("depenses", report) if format_ == "csv" else report


@reports_router.get("/profit", response_model=ProfitReport)
def profit(
    group_by: Literal["month", "quarter", "year"] = "month",
    date_from: date | None = None, date_to: date | None = None,
    format_: Format = Query(default="json", alias="format"), db: Session = Depends(get_db),
):
    report = svc.profit_report(db, group_by=group_by, date_from=date_from, date_to=date_to)
    if format_ == "csv":
        col = _GROUP_FR[group_by]
        return csv_response(f"resultat_{col}", [col, "recettes_xof", "depenses_xof", "resultat_xof"],
                            [(r.label, r.revenue_xof, r.expenses_xof, r.profit_xof) for r in report.rows])
    return report


@reports_router.get("/operations", response_model=OperationsReport)
def operations_report(
    date_from: date | None = None, date_to: date | None = None,
    format_: Format = Query(default="json", alias="format"),
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
):
    """Lot 11d : instantané (aujourd'hui, UTC) + flux mensuels. Pas d'historique reconstruit des actifs."""
    report = operations.operations_report(db, clock, date_from=date_from, date_to=date_to)
    if format_ == "csv":
        return csv_response("rapport_operationnel", [
            "mois", "nouveaux_membres", "statut_nouveau_membre", "renouveles", "renouvellements_en_retard",
            "changements_de_niveau", "dossiers_quarantaine_ouverts", "nouvelles_identites_observees",
        ], [(r.label, r.new_members, r.new_member_subscriptions, r.renewals, r.late_renewals, r.plan_changes,
             r.quarantine_opened, r.identities_first_seen) for r in report.rows])
    return report


@audit_router.get("", response_model=AuditPage)
def audit(
    action: str | None = Query(default=None, max_length=80),
    entity_type: str | None = Query(default=None, max_length=80),
    entity_id: str | None = Query(default=None, max_length=64),
    actor_admin_id: uuid.UUID | None = None,
    date_from: date | None = None, date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> AuditPage:
    items, total = svc.list_audit(
        db, action=action, entity_type=entity_type, entity_id=entity_id, actor_admin_id=actor_admin_id,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    )
    return AuditPage(items=items, total=total, limit=limit, offset=offset)
