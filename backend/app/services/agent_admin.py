"""Création, rotation et révocation des identifiants d'agent. Le secret n'est jamais journalisé."""
import ipaddress
import secrets
from datetime import datetime
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.agent_protocol import new_secret
from app.core.crypto import encrypt_field
from app.models.audit import AuditLog
from app.models.infra import AgentCredential, ExitNode


class AgentAdminError(Exception):
    pass


def _audit(db: Session, action: str, key_id: str, details: dict) -> None:
    db.add(AuditLog(actor_label="cli", action=action, entity_type="agent_credential",
                    entity_id=key_id, details=details))


def create_agent(db: Session, *, name: str, ip: str, rotate: bool, now: datetime) -> Tuple[str, str]:
    """Retourne (key_id, secret en clair). À afficher une seule fois."""
    try:
        ip = str(ipaddress.ip_address(ip))
    except ValueError:
        raise AgentAdminError("adresse IP invalide")
    name = name.strip()
    if not name:
        raise AgentAdminError("nom obligatoire")
    node = db.execute(select(ExitNode).where(ExitNode.name == name)).scalar_one_or_none()
    if node is None:
        node = ExitNode(name=name, tailscale_ip=ip)
        db.add(node)
        db.flush()
    elif node.tailscale_ip != ip:
        raise AgentAdminError("cet exit node existe avec une autre adresse IP")
    active = db.execute(
        select(AgentCredential).where(AgentCredential.exit_node_id == node.id, AgentCredential.revoked_at.is_(None))
    ).scalars().all()
    if active and not rotate:
        raise AgentAdminError("un identifiant actif existe déjà (utiliser --rotate pour le remplacer)")
    for cred in active:
        cred.revoked_at = now
    key_id = "agt_" + secrets.token_hex(8)
    secret = new_secret()
    db.add(AgentCredential(exit_node_id=node.id, key_id=key_id, secret_encrypted=encrypt_field(secret)))
    _audit(db, "agent.credential_created", key_id, {"exit_node": node.name, "revoked_previous": len(active)})
    db.commit()
    return key_id, secret


def revoke_agent(db: Session, key_id: str, *, now: datetime) -> None:
    cred = db.execute(select(AgentCredential).where(AgentCredential.key_id == key_id)).scalar_one_or_none()
    if cred is None:
        raise AgentAdminError("identifiant introuvable")
    if cred.revoked_at is not None:
        raise AgentAdminError("identifiant déjà révoqué")
    cred.revoked_at = now
    _audit(db, "agent.credential_revoked", key_id, {})
    db.commit()
