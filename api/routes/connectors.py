from fastapi import APIRouter, status

from api.deps import LeadOrOwner
from api.schemas import JobAccepted
from core import queue
from core.config import get_settings
from core.errors import NotConfigured

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.post("/github/backfill", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
async def github_backfill(ctx: LeadOrOwner) -> JobAccepted:
    settings = get_settings()
    if not settings.github_app_id or not settings.github_app_private_key_path:
        raise NotConfigured("GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_PATH are required for backfill")
    tenant = str(ctx.principal.tenant_id)
    pool = await queue.get_pool()
    job = await pool.enqueue_job("github_backfill", tenant, _job_id=f"github_backfill:{tenant}")
    return JobAccepted(job_id=job.job_id if job else None)
