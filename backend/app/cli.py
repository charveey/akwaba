"""Administration en ligne de commande. Le mot de passe est saisi en mode masqué.

    python -m app.cli create-admin --email x@y.z [--name "Nom"]
    python -m app.cli set-password --email x@y.z
"""
import argparse
import getpass
import re
import sys
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models.admin import AdminSession, AdminUser
from app.services.auth import audit

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_LENGTH = 12


def _ask_password() -> str:
    pw = getpass.getpass(f"Mot de passe ({MIN_LENGTH} caractères minimum) : ")
    if len(pw) < MIN_LENGTH:
        sys.exit(f"Refusé : {MIN_LENGTH} caractères minimum.")
    if getpass.getpass("Confirmer : ") != pw:
        sys.exit("Refusé : les deux saisies diffèrent.")
    return pw


def _email(raw: str) -> str:
    email = raw.strip().lower()
    if not EMAIL_RE.match(email):
        sys.exit("Adresse e-mail invalide.")
    return email


def create_admin(email: str, name: str | None) -> None:
    email = _email(email)
    with get_sessionmaker()() as db:
        if db.execute(select(AdminUser).where(AdminUser.email == email)).scalar_one_or_none():
            sys.exit("Cet administrateur existe déjà (utiliser set-password).")
        password = _ask_password()
        user = AdminUser(email=email, display_name=name, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        audit(db, "admin.created", admin_id=user.id, label="cli")
        db.commit()
    print(f"Administrateur créé : {email}")


def set_password(email: str) -> None:
    email = _email(email)
    with get_sessionmaker()() as db:
        user = db.execute(select(AdminUser).where(AdminUser.email == email)).scalar_one_or_none()
        if user is None:
            sys.exit("Administrateur introuvable.")
        user.password_hash = hash_password(_ask_password())
        user.failed_login_count = 0
        user.locked_until = None
        db.execute(
            update(AdminSession)
            .where(AdminSession.admin_user_id == user.id, AdminSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        audit(db, "admin.password_reset", admin_id=user.id, label="cli")
        db.commit()
    print("Mot de passe modifié. Toutes les sessions ont été révoquées.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("create-admin")
    p1.add_argument("--email", required=True)
    p1.add_argument("--name")
    p2 = sub.add_parser("set-password")
    p2.add_argument("--email", required=True)
    args = parser.parse_args()
    if args.cmd == "create-admin":
        create_admin(args.email, args.name)
    else:
        set_password(args.email)


if __name__ == "__main__":
    main()
