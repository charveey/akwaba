# Alembic importe ce paquet pour découvrir les tables (voir alembic/env.py).
from app.models.admin import AdminSession, AdminUser
from app.models.audit import AuditLog
from app.models.billing import Payment, PaymentMethod, Subscription, SubscriptionPlan
from app.models.clients import ClientIdentity, Member
from app.models.finance import Expense, ExpenseCategory
from app.models.infra import (
    AgentBatch, AgentCredential, AgentHeartbeat, EnforcementDecision, ExitNode,
    ExitNodeIdentity, ExitNodeObservation, ExitNodePeer, ExitNodeSession,
    ProtectedIdentity, QuarantineRecord, TailnetDevice, TailnetMember,
)
from app.models.system import JobRun, Setting

__all__ = [
    "AdminSession", "AdminUser", "AgentBatch", "AgentCredential", "AgentHeartbeat", "AuditLog",
    "ClientIdentity", "EnforcementDecision", "ExitNode", "ExitNodeIdentity", "ExitNodeObservation",
    "ExitNodePeer", "ExitNodeSession", "Expense", "ExpenseCategory", "JobRun", "Member", "Payment",
    "PaymentMethod", "ProtectedIdentity", "QuarantineRecord", "Setting", "Subscription",
    "SubscriptionPlan", "TailnetDevice", "TailnetMember",
]
