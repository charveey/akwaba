import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.agent_deps import require_agent
from app.api.deps import get_clock
from app.db.session import get_db
from app.domain.clock import Clock
from app.schemas.agent import BatchIn
from app.services.agent_auth import AgentContext
from app.services.ingest import BatchConflict, IngestRejected, ingest_batch

logger = logging.getLogger("akwaba.agent")
router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/ping")
async def ping(ctx: AgentContext = Depends(require_agent)) -> dict:
    """Valide toute la chaîne d'authentification. server_time permet à l'agent de mesurer son écart d'horloge."""
    return {"status": "ok", "exit_node": ctx.exit_node_name, "server_time": int(ctx.now)}


@router.post("/batches")
async def post_batch(
    ctx: AgentContext = Depends(require_agent),
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict:
    try:
        data = json.loads(ctx.body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "JSON invalide")
    try:
        batch = BatchIn.model_validate(data)
    except ValidationError as exc:
        # Emplacement et type seulement : jamais la valeur reçue (elle peut contenir un login).
        problems = [".".join(str(p) for p in e["loc"]) + " : " + e["type"] for e in exc.errors()[:5]]
        logger.warning("lot invalide %s", problems)
        raise HTTPException(422, {"message": "Lot invalide", "problems": problems})
    try:
        result = await run_in_threadpool(ingest_batch, db, ctx, clock, batch)
    except IngestRejected as exc:
        logger.warning("lot refusé : %s", exc)
        raise HTTPException(422, str(exc))
    except BatchConflict:
        logger.warning("lot refusé : batch_id déjà utilisé par un autre exit node")
        raise HTTPException(409, "Lot déjà reçu d'un autre agent")
    return {"status": result.status, "batch_id": str(batch.batch_id), "observations": result.observations}
