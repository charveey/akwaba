from fastapi import APIRouter, Depends

from app.api.agent_deps import require_agent
from app.services.agent_auth import AgentContext

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/ping")
async def ping(ctx: AgentContext = Depends(require_agent)) -> dict:
    """Valide toute la chaîne d'authentification. server_time permet à l'agent de mesurer son écart d'horloge."""
    return {"status": "ok", "exit_node": ctx.exit_node_name, "server_time": int(ctx.now)}
