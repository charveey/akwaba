"""Catégories et dépenses. Chaque écriture et son audit partagent la transaction."""
import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.clock import Clock
from app.models.finance import Expense, ExpenseCategory
from app.schemas.billing import VoidIn
from app.schemas.finance import CategoryIn, CategoryOut, CategoryUpdate, ExpenseCreate, ExpenseOut
from app.services.audit import record
from app.services.auth import AuthContext


def _err(code: int, msg: str) -> HTTPException:
    return HTTPException(code, msg)


def _expense_out(e: Expense, category_name: str) -> ExpenseOut:
    return ExpenseOut(
        id=e.id, category_id=e.category_id, category_name=category_name, amount=e.amount, currency=e.currency,
        expense_date=e.expense_date, description=e.description, status=e.status, voided_at=e.voided_at,
        void_reason=e.void_reason, created_at=e.created_at,
    )


# ---------------------------------------------------------------- catégories

def list_categories(db: Session, *, include_inactive: bool) -> list[CategoryOut]:
    stmt = select(ExpenseCategory)
    if not include_inactive:
        stmt = stmt.where(ExpenseCategory.is_active.is_(True))
    rows = db.execute(stmt.order_by(ExpenseCategory.sort_order, ExpenseCategory.name)).scalars()
    return [CategoryOut.model_validate(r) for r in rows]


def _name_taken(db: Session, name: str, *, exclude: uuid.UUID | None = None) -> bool:
    stmt = select(ExpenseCategory.id).where(func.lower(ExpenseCategory.name) == name.lower())
    if exclude is not None:
        stmt = stmt.where(ExpenseCategory.id != exclude)
    return db.execute(stmt.limit(1)).first() is not None


def create_category(db: Session, ctx: AuthContext, data: CategoryIn, ip: str | None) -> CategoryOut:
    if _name_taken(db, data.name):
        raise _err(409, "Une catégorie porte déjà ce nom")
    max_order = db.execute(select(func.coalesce(func.max(ExpenseCategory.sort_order), 0))).scalar_one()
    cat = ExpenseCategory(name=data.name, sort_order=max_order + 10)
    db.add(cat)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _err(409, "Une catégorie porte déjà ce nom")
    record(db, "expense_category.created", actor_admin_id=ctx.admin.id, entity_type="expense_category",
           entity_id=cat.id, details={"name": cat.name}, ip=ip)
    db.commit()
    return CategoryOut.model_validate(cat)


def update_category(
    db: Session, ctx: AuthContext, category_id: uuid.UUID, data: CategoryUpdate, ip: str | None,
) -> CategoryOut:
    cat = db.execute(
        select(ExpenseCategory).where(ExpenseCategory.id == category_id).with_for_update()
    ).scalar_one_or_none()
    if cat is None:
        raise _err(404, "Catégorie introuvable")
    if data.name != cat.name and _name_taken(db, data.name, exclude=cat.id):
        raise _err(409, "Une catégorie porte déjà ce nom")
    changed = [f for f, new in (("name", data.name), ("is_active", data.is_active)) if getattr(cat, f) != new]
    if changed:
        details: dict = {"changed_fields": changed}
        if "name" in changed:
            details.update(old_name=cat.name, new_name=data.name)
        if "is_active" in changed:
            details["is_active"] = data.is_active
        cat.name, cat.is_active = data.name, data.is_active
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise _err(409, "Une catégorie porte déjà ce nom")
        record(db, "expense_category.updated", actor_admin_id=ctx.admin.id, entity_type="expense_category",
               entity_id=cat.id, details=details, ip=ip)
    db.commit()
    return CategoryOut.model_validate(cat)


# ---------------------------------------------------------------- dépenses

def create_expense(
    db: Session, ctx: AuthContext, clock: Clock, data: ExpenseCreate, ip: str | None,
) -> ExpenseOut:
    cat = db.get(ExpenseCategory, data.category_id)
    if cat is None:
        raise _err(404, "Catégorie introuvable")
    if not cat.is_active:
        raise _err(409, "Catégorie désactivée")
    if data.expense_date > clock.today():
        raise _err(422, "La date de dépense ne peut pas être dans le futur")
    exp = Expense(
        category_id=cat.id, amount=data.amount, expense_date=data.expense_date,
        description=data.description, recorded_by_admin_id=ctx.admin.id,
    )
    db.add(exp)
    db.flush()
    # Ni la description ni aucun texte libre dans le journal append-only.
    record(db, "expense.recorded", actor_admin_id=ctx.admin.id, entity_type="expense", entity_id=exp.id,
           details={"category_id": str(cat.id), "amount": exp.amount,
                    "expense_date": exp.expense_date.isoformat()}, ip=ip)
    db.commit()
    return _expense_out(exp, cat.name)


def get_expense(db: Session, expense_id: uuid.UUID) -> ExpenseOut:
    row = db.execute(
        select(Expense, ExpenseCategory.name)
        .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .where(Expense.id == expense_id)
    ).first()
    if row is None:
        raise _err(404, "Dépense introuvable")
    return _expense_out(*row)


def list_expenses(
    db: Session, *, category_id: uuid.UUID | None, exp_status: str | None,
    date_from: date | None, date_to: date | None, limit: int, offset: int,
) -> tuple[list[ExpenseOut], int, int]:
    if date_from and date_to and date_from > date_to:
        raise _err(422, "date_from doit précéder date_to")
    conds = []
    if category_id is not None:
        conds.append(Expense.category_id == category_id)
    if exp_status is not None:
        conds.append(Expense.status == exp_status)
    if date_from is not None:
        conds.append(Expense.expense_date >= date_from)
    if date_to is not None:
        conds.append(Expense.expense_date <= date_to)
    total = db.execute(select(func.count()).select_from(Expense).where(*conds)).scalar_one()
    total_amount = db.execute(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(*conds, Expense.status == "ACTIVE")
    ).scalar_one()
    rows = db.execute(
        select(Expense, ExpenseCategory.name)
        .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
        .where(*conds)
        .order_by(Expense.expense_date.desc(), Expense.created_at.desc(), Expense.id)
        .limit(limit).offset(offset)
    ).all()
    return [_expense_out(e, n) for e, n in rows], total, int(total_amount)


def void_expense(
    db: Session, ctx: AuthContext, clock: Clock, expense_id: uuid.UUID, data: VoidIn, ip: str | None,
) -> ExpenseOut:
    exp = db.execute(select(Expense).where(Expense.id == expense_id).with_for_update()).scalar_one_or_none()
    if exp is None:
        raise _err(404, "Dépense introuvable")
    if exp.status == "VOIDED":
        raise _err(409, "Dépense déjà annulée")
    exp.status = "VOIDED"
    exp.voided_at = clock.now()
    exp.void_reason = data.reason
    record(db, "expense.voided", actor_admin_id=ctx.admin.id, entity_type="expense", entity_id=exp.id,
           details={"amount": exp.amount}, ip=ip)
    db.commit()
    name = db.execute(select(ExpenseCategory.name).where(ExpenseCategory.id == exp.category_id)).scalar_one()
    return _expense_out(exp, name)
