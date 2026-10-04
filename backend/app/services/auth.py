"""Authentification : verrouillage, sessions, audit dans la même transaction."""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import (
    dummy_hash, hash_password, hash_token, needs_rehash, new_token, verify_password,
)
from app.models.admin import AdminSession, AdminUser
from app.models.audit import AuditLog


@dataclass
class AuthContext:
    admin: AdminUser
    session: AdminSession


@dataclass
class LoginResult:
    admin: AdminUser
    session: AdminSession
    raw_token: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def audit(
    db: Session, action: str, *, admin_id=None, label: str | None = None,
    details: dict[str, Any] | None = None, ip: str | None = None,
) -> None:
    db.add(AuditLog(
        actor_admin_id=admin_id, actor_label=label, action=action,
        entity_type="admin_user" if admin_id else None,
        entity_id=str(admin_id) if admin_id else None,
        details=details, ip_address=ip,
    ))


def authenticate(
    db: Session, settings: Settings, email: str, password: str, ip: str | None, user_agent: str | None,
) -> LoginResult | None:
    now = _now()
    email_n = email.strip().lower()
    user = db.execute(
        select(AdminUser).where(AdminUser.email == email_n).with_for_update()
    ).scalar_one_or_none()

    # Toujours vérifier un hachage, même pour un compte inconnu.
    password_ok = verify_password(user.password_hash if user else dummy_hash(), password)

    if user is None:
        audit(db, "admin.login_failed", label="anonymous", ip=ip, details={
            "reason": "unknown_account",
            "email_sha256": hashlib.sha256(email_n.encode()).hexdigest()[:16],
        })
        db.commit()
        return None

    if not user.is_active:
        audit(db, "admin.login_failed", admin_id=user.id, ip=ip, details={"reason": "inactive"})
        db.commit()
        return None

    if user.locked_until is not None and user.locked_until > now:
        audit(db, "admin.login_blocked", admin_id=user.id, ip=ip, details={"reason": "locked"})
        db.commit()
        return None

    if not password_ok:
        user.failed_login_count += 1
        details: dict[str, Any] = {"reason": "bad_password", "failed_count": user.failed_login_count}
        if user.failed_login_count >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            details["locked_until"] = user.locked_until.isoformat()
        audit(db, "admin.login_failed", admin_id=user.id, ip=ip, details=details)
        db.commit()
        return None

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    raw = new_token()
    session = AdminSession(
        admin_user_id=user.id,
        token_hash=hash_token(raw),
        csrf_token=new_token(),
        expires_at=now + timedelta(days=settings.session_absolute_days),
        ip_address=ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(session)
    audit(db, "admin.login_success", admin_id=user.id, ip=ip)
    db.commit()
    return LoginResult(user, session, raw)


def load_session(db: Session, settings: Settings, raw_token: str) -> AuthContext | None:
    now = _now()
    row = db.execute(
        select(AdminSession, AdminUser)
        .join(AdminUser, AdminUser.id == AdminSession.admin_user_id)
        .where(AdminSession.token_hash == hash_token(raw_token))
    ).first()
    if row is None:
        return None
    session, admin = row
    if session.revoked_at is not None or session.expires_at <= now or not admin.is_active:
        return None
    if session.last_seen_at + timedelta(hours=settings.session_idle_hours) <= now:
        return None
    if now - session.last_seen_at > timedelta(seconds=60):  # limite les écritures
        session.last_seen_at = now
        db.commit()
    return AuthContext(admin, session)


def revoke_session(db: Session, ctx: AuthContext, ip: str | None) -> None:
    ctx.session.revoked_at = _now()
    audit(db, "admin.logout", admin_id=ctx.admin.id, ip=ip)
    db.commit()
