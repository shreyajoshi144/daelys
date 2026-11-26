"""Plan drafts and acceptance. Accepting writes `plan.item_created` / `plan.edge_created` events; work items
come from the projector, never from a direct write (design rule 1)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.plan_schema import Plan, PlanResult
from core.events import NewEvent, append_events
from rules.engine import tenant_now


class DraftNotFound(Exception):
    pass


class DraftAlreadyAccepted(Exception):
    pass


@dataclass(frozen=True)
class AcceptResult:
    items: int
    edges: int
    inserted_events: int


def item_key(draft_id: UUID, local_id: str) -> str:
    return f"plan:{draft_id}:{local_id}"


async def save_draft(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    artifact_id: UUID,
    result: PlanResult,
    created_by: UUID,
) -> UUID:
    draft_id = await session.scalar(
        text("""
            INSERT INTO plan_drafts
                (tenant_id, project_id, artifact_id, plan, dropped, repaired, agent_run_ids,
                created_by)
            VALUES (:t, :p, :a, CAST(:plan AS jsonb), CAST(:dropped AS jsonb), :repaired,
                CAST(:runs AS uuid[]), :by)
            RETURNING id
        """),
        {
            "t": tenant_id,
            "p": project_id,
            "a": artifact_id,
            "plan": result.plan.model_dump_json(),
            "dropped": "[" + ",".join(d.model_dump_json() for d in result.dropped) + "]",
            "repaired": result.repaired,
            "runs": result.agent_run_ids,
            "by": created_by,
        },
    )
    return UUID(str(draft_id))


def plan_events(draft_id: UUID, project_id: UUID, plan: Plan, at: datetime) -> list[NewEvent]:
    events: list[NewEvent] = []
    for item in plan.items:
        events.append(
            NewEvent(
                source="plan",
                type="plan.item_created",
                idempotency_key=f"plan:{draft_id}:item:{item.local_id}",
                occurred_at=at,
                subject_key=item_key(draft_id, item.local_id),
                payload={
                    "fields": {
                        "kind": "task",
                        "title": item.title,
                        "status": "open",
                        "lane": item.lane,
                        "owner_role": item.owner_role,
                        "estimate_days": item.estimate_days,
                        "project_id": str(project_id),
                        "source_span": item.source_span.model_dump(),
                    }
                },
            )
        )
    for item in plan.items:
        for dep in item.depends_on:
            events.append(
                NewEvent(
                    source="plan",
                    type="plan.edge_created",
                    idempotency_key=f"plan:{draft_id}:edge:{item.local_id}->{dep}",
                    occurred_at=at,
                    subject_key=item_key(draft_id, item.local_id),
                    payload={"depends_on": [item_key(draft_id, dep)]},
                )
            )
    return events


async def accept_draft(
    session: AsyncSession, tenant_id: UUID, project_id: UUID, draft_id: UUID, accepted_by: UUID
) -> AcceptResult:
    row = (
        await session.execute(
            text("SELECT plan, status FROM plan_drafts WHERE id = :d AND project_id = :p FOR UPDATE"),
            {"d": draft_id, "p": project_id},
        )
    ).first()
    if row is None:
        raise DraftNotFound
    if row.status == "accepted":
        raise DraftAlreadyAccepted
    plan = Plan.model_validate(row.plan)
    # The workspace clock, not wall time: accepting a plan must not move a replayed scenario's clock.
    events = plan_events(draft_id, project_id, plan, await tenant_now(session))
    appended = await append_events(session, tenant_id, events)
    await session.execute(
        text(
            "UPDATE plan_drafts SET status = 'accepted', accepted_by = :u, accepted_at = now() WHERE id = :d"
        ),
        {"u": accepted_by, "d": draft_id},
    )
    return AcceptResult(
        items=len(plan.items),
        edges=sum(len(i.depends_on) for i in plan.items),
        inserted_events=len(appended.inserted_ids),
    )
