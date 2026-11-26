"""Shared setup for proposal and executor tests: a replayed Tiffin Box tenant, a project and its lead."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text

from agents.proposer import Draft, ProposerResult
from connectors.replay import ReplayConnector
from core.actions import TIERS
from core.db import tenant_tx
from core.proposals import store
from core.security import Role
from core.seed import set_policy
from rules.engine import evaluate_tenant
from tests.conftest import SeededTenant, make_tenant
from workers.projector import project_tenant

CHECKOUT = "gh:tiffin-box/tiffin-box-app#12"


@dataclass(frozen=True)
class Scenario:
    tenant: SeededTenant
    project_id: UUID
    checkout_id: UUID
    user_ids: dict[Role, UUID]


async def build_scenario(*, project_lead: bool = True) -> Scenario:
    tenant = await make_tenant()
    async with tenant_tx(tenant.id) as s:
        users = {
            Role(r.role): r.user_id for r in await s.execute(text("SELECT user_id, role FROM memberships"))
        }
        project_id = await s.scalar(
            text("INSERT INTO projects (tenant_id, name) VALUES (:t, 'Subscriptions launch') RETURNING id"),
            {"t": tenant.id},
        )
        if project_lead:
            await s.execute(
                text("INSERT INTO project_members (tenant_id, project_id, user_id) VALUES (:t, :p, :u)"),
                {"t": tenant.id, "p": project_id, "u": users[Role.lead]},
            )
    await set_policy(tenant.id, clock="event")
    for week in ("week1", "week2"):
        await ReplayConnector("tiffin_box", week).replay(tenant.id)
    await project_tenant(tenant.id)
    await evaluate_tenant(tenant.id)
    async with tenant_tx(tenant.id) as s:
        checkout = await s.scalar(text("SELECT id FROM work_items WHERE key = :k"), {"k": CHECKOUT})
    return Scenario(tenant, UUID(str(project_id)), UUID(str(checkout)), users)


async def evidence_ids(scenario: Scenario, key: str = CHECKOUT) -> list[int]:
    async with tenant_tx(scenario.tenant.id) as s:
        return list(
            await s.scalars(text("SELECT id FROM events WHERE subject_key = :k ORDER BY id"), {"k": key})
        )


async def propose(
    scenario: Scenario, action_type: str, args: dict[str, Any], item_id: UUID | None = None
) -> UUID:
    """Store a proposal as if the proposer had produced and validated it (no model involved)."""
    ev = await evidence_ids(scenario)
    result = ProposerResult(
        item_id=item_id or scenario.checkout_id,
        drafts=[
            Draft(
                action_type=action_type,
                args=args,
                tier=TIERS[action_type],
                evidence_event_ids=ev[:2],
                expected_effect="test",
                reversible=TIERS[action_type] == 2,
            )
        ],
        rejected=[],
        agent_run_id=None,
        prompt_version="propose_v1",
        model=None,
    )
    async with tenant_tx(scenario.tenant.id) as s:
        created = await store(s, scenario.tenant.id, result)
    return created.ids[0]


async def approve_as(
    client: httpx.AsyncClient, headers: dict[str, str], proposal_id: UUID, args: dict[str, Any] | None = None
) -> httpx.Response:
    return await client.post(
        f"/proposals/{proposal_id}/approve", json={"args": args} if args else None, headers=headers
    )


async def proposal(scenario: Scenario, proposal_id: UUID) -> Any:
    async with tenant_tx(scenario.tenant.id) as s:
        return (await s.execute(text("SELECT * FROM proposals WHERE id = :id"), {"id": proposal_id})).one()


async def checkout(scenario: Scenario) -> Any:
    async with tenant_tx(scenario.tenant.id) as s:
        return (
            await s.execute(text("SELECT * FROM work_items WHERE id = :id"), {"id": scenario.checkout_id})
        ).one()


async def audit_actions(scenario: Scenario, proposal_id: UUID) -> list[str]:
    async with tenant_tx(scenario.tenant.id) as s:
        return list(
            await s.scalars(
                text("SELECT action FROM audit_log WHERE proposal_id = :p ORDER BY id"), {"p": proposal_id}
            )
        )


SCENARIO_TODAY = datetime(
    2026, 3, 13, tzinfo=UTC
)  # the replayed scenario's clock (event clock), not wall time


def future_date(days: int = 10) -> str:
    return (SCENARIO_TODAY + timedelta(days=days)).date().isoformat()


def mailpit_api() -> str:
    host = os.environ.get("SMTP_HOST", "localhost")
    port = "8025" if host == "mailpit" else os.environ.get("MAILPIT_UI_HOST_PORT", "8025")
    return f"http://{host}:{port}/api/v1"
