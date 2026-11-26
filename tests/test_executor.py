"""Executor guarantees: approved-only, unexpired, min(agent, approver), exactly once, audited, undoable."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from core.db import tenant_tx
from core.proposals import ProposalNotFound
from core.security import Principal, Role
from tests.actions_helpers import (
    approve_as,
    audit_actions,
    build_scenario,
    checkout,
    future_date,
    proposal,
    propose,
)
from tests.conftest import SeededTenant, auth_headers
from workers.execute import UndoRefused, execute, undo


async def action_events(tenant_id: uuid.UUID, proposal_id: uuid.UUID) -> int:
    async with tenant_tx(tenant_id) as s:
        return int(
            await s.scalar(
                text("SELECT count(*) FROM events WHERE idempotency_key LIKE :k"),
                {"k": f"exec:{proposal_id}:%"},
            )
            or 0
        )


async def test_unapproved_is_refused_and_audited(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    tier2 = await propose(sc, "move_due", {"new_due_date": future_date()})
    tier3 = await propose(sc, "comment_on_pr", {"body": "CI is red on the payment webhook tests."})
    before = await checkout(sc)
    for pid in (tier2, tier3):
        outcome = await execute(sc.tenant.id, pid)
        assert outcome.status == "pending" and outcome.detail.startswith("refused")
        assert (await proposal(sc, pid)).status == "pending"
        assert await action_events(sc.tenant.id, pid) == 0
        assert "execution.refused" in await audit_actions(sc, pid)
    assert (await checkout(sc)).due_at == before.due_at


async def test_expired_is_refused(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    pid = await propose(sc, "move_due", {"new_due_date": future_date()})
    assert (await approve_as(client, lead, pid)).status_code == 202
    async with tenant_tx(sc.tenant.id) as s:
        await s.execute(
            text("UPDATE proposals SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": pid}
        )
    outcome = await execute(sc.tenant.id, pid)
    assert outcome.status == "expired"
    assert await action_events(sc.tenant.id, pid) == 0
    # Approving an already-expired pending proposal is refused with a clear message too.
    other = await propose(sc, "move_due", {"new_due_date": future_date(11)})
    async with tenant_tx(sc.tenant.id) as s:
        await s.execute(
            text("UPDATE proposals SET expires_at = now() - interval '1 minute' WHERE id = :id"),
            {"id": other},
        )
    resp = await approve_as(client, lead, other)
    assert resp.status_code == 409 and "expired" in resp.json()["detail"]


async def test_double_run_has_one_effect(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    pid = await propose(sc, "move_due", {"new_due_date": future_date()})
    first, second = await asyncio.gather(approve_as(client, lead, pid), approve_as(client, lead, pid))
    assert sorted([first.status_code, second.status_code]) == [202, 409]
    outcomes = await asyncio.gather(*(execute(sc.tenant.id, pid) for _ in range(3)))
    assert sorted(o.status for o in outcomes).count("executed") >= 1
    assert await action_events(sc.tenant.id, pid) == 1
    assert (await audit_actions(sc, pid)).count("execute.move_due") == 1


async def test_move_due_then_undo_restores_state(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    original = await checkout(sc)
    new_day = future_date(20)
    pid = await propose(sc, "move_due", {"new_due_date": new_day})
    await approve_as(client, lead, pid)
    assert (await execute(sc.tenant.id, pid)).status == "executed"
    moved = await checkout(sc)
    assert moved.due_at.date().isoformat() == new_day and moved.due_at.timetz() == original.due_at.timetz()
    assert moved.risk_score < original.risk_score  # risk re-scored after the change (no longer due this week)

    resp = await client.post(f"/proposals/{pid}/undo", headers=lead)
    assert resp.status_code == 200 and resp.json() == {"status": "undone"}
    restored = await checkout(sc)
    assert restored.due_at == original.due_at and restored.risk_score == original.risk_score
    assert await audit_actions(sc, pid) == [
        "proposal.created",
        "proposal.approved",
        "execute.move_due",
        "undo.move_due",
    ]
    assert (await client.post(f"/proposals/{pid}/undo", headers=lead)).status_code == 409


async def test_reassign_and_split_with_undo(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    original = await checkout(sc)
    member = sc.user_ids[Role.member]
    reassign = await propose(sc, "reassign", {"to_user_id": str(member)})
    await approve_as(client, lead, reassign)
    await execute(sc.tenant.id, reassign)
    assert (await checkout(sc)).owner_id == member

    split = await propose(
        sc,
        "split",
        {
            "parts": [
                {"title": "Plan selection", "estimate_days": 2},
                {"title": "Razorpay payment", "estimate_days": 3},
            ]
        },
    )
    await approve_as(client, lead, split)
    await execute(sc.tenant.id, split)
    async with tenant_tx(sc.tenant.id) as s:
        children = (
            await s.execute(
                text("""
            SELECT c.title, c.status, c.lane, c.project_id FROM edges e JOIN work_items c ON c.id = e.dst_id
            WHERE e.src_id = :p AND e.kind = 'depends_on' ORDER BY c.title
        """),
                {"p": sc.checkout_id},
            )
        ).all()
    assert [(c.title, c.status) for c in children] == [
        ("Plan selection", "open"),
        ("Razorpay payment", "open"),
    ]
    assert all(c.lane == original.lane and c.project_id == sc.project_id for c in children)

    owner = Principal(sc.user_ids[Role.owner], sc.tenant.id, Role.owner)
    await undo(sc.tenant.id, split, owner)
    await undo(sc.tenant.id, reassign, owner)
    async with tenant_tx(sc.tenant.id) as s:
        statuses = set(
            await s.scalars(
                text("SELECT status FROM work_items WHERE key LIKE :k"), {"k": f"split:{split}:%"}
            )
        )
    assert statuses == {"cancelled"}
    after = await checkout(sc)
    assert (after.owner_id, after.owner_login) == (original.owner_id, original.owner_login)
    with pytest.raises(UndoRefused):
        await undo(sc.tenant.id, split, owner)


async def test_effective_permission_is_min_of_agent_and_approver(
    client: httpx.AsyncClient, database: None
) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    pid = await propose(sc, "move_due", {"new_due_date": future_date()})
    await approve_as(client, lead, pid)
    async with tenant_tx(sc.tenant.id) as s:  # the approver is demoted before the executor runs
        await s.execute(
            text("UPDATE memberships SET role = 'member' WHERE user_id = :u"), {"u": sc.user_ids[Role.lead]}
        )
    outcome = await execute(sc.tenant.id, pid)
    assert outcome.status == "failed" and "effective permission" in outcome.detail
    assert await action_events(sc.tenant.id, pid) == 0


async def test_who_may_decide(client: httpx.AsyncClient, decoy: SeededTenant, database: None) -> None:
    sc = await build_scenario(project_lead=False)  # the tenant's lead does NOT lead this project
    pid = await propose(sc, "move_due", {"new_due_date": future_date()})
    member = await auth_headers(client, sc.tenant, Role.member)
    lead = await auth_headers(client, sc.tenant, Role.lead)
    outsider = await auth_headers(client, decoy, Role.owner)
    assert (await approve_as(client, member, pid)).status_code == 403
    assert (await approve_as(client, lead, pid)).status_code == 403  # lead of a different project
    assert (await approve_as(client, outsider, pid)).status_code == 404
    with pytest.raises(ProposalNotFound):
        await execute(decoy.id, pid)  # cross-tenant execution attempt
    owner = await auth_headers(client, sc.tenant, Role.owner)
    assert (await approve_as(client, owner, pid)).status_code == 202


async def test_edit_then_approve_and_reject_with_reason(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    pid = await propose(sc, "move_due", {"new_due_date": future_date(5)})
    bad = await approve_as(client, lead, pid, {"new_due_date": "not-a-date"})
    assert bad.status_code == 422
    edited = future_date(9)
    assert (await approve_as(client, lead, pid, {"new_due_date": edited})).status_code == 202
    row = await proposal(sc, pid)
    assert row.args == {"new_due_date": edited} and row.original_args == {"new_due_date": future_date(5)}
    assert "proposal.edited_and_approved" in await audit_actions(sc, pid)

    other = await propose(sc, "move_due", {"new_due_date": future_date(30)})
    assert (
        await client.post(f"/proposals/{other}/reject", json={"reason": ""}, headers=lead)
    ).status_code == 422
    resp = await client.post(
        f"/proposals/{other}/reject", json={"reason": "Launch date is fixed"}, headers=lead
    )
    assert resp.status_code == 200 and (await proposal(sc, other)).rejection_reason == "Launch date is fixed"


async def test_every_executed_proposal_is_audited(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    pid = await propose(sc, "move_due", {"new_due_date": future_date()})
    await approve_as(client, lead, pid)
    await execute(sc.tenant.id, pid)
    async with tenant_tx(sc.tenant.id) as s:
        unaudited = await s.scalar(
            text("""
            SELECT count(*) FROM proposals p LEFT JOIN audit_log a ON a.proposal_id = p.id
                AND a.action LIKE 'execute.%'
            WHERE p.status = 'executed' AND a.id IS NULL
        """)
        )
        row = (
            await s.execute(
                text("""
            SELECT actor_type, actor_id, on_behalf_of, tier, before, after FROM audit_log
            WHERE proposal_id = :p AND action = 'execute.move_due'
        """),
                {"p": pid},
            )
        ).one()
        agent = await s.scalar(text("SELECT user_id FROM memberships WHERE role = 'agent'"))
    assert unaudited == 0
    assert (row.actor_type, row.actor_id, row.on_behalf_of, row.tier) == (
        "agent",
        agent,
        sc.user_ids[Role.lead],
        2,
    )
    assert row.before["due_at"] and row.after["due_at"]


async def test_state_is_rechecked_at_execution(client: httpx.AsyncClient, database: None) -> None:
    sc = await build_scenario()
    lead = await auth_headers(client, sc.tenant, Role.lead)
    pid = await propose(sc, "reassign", {"to_user_id": str(sc.user_ids[Role.member])})
    await approve_as(client, lead, pid)
    async with tenant_tx(sc.tenant.id) as s:  # the target leaves the workspace after approval
        await s.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": sc.user_ids[Role.member]})
    outcome = await execute(sc.tenant.id, pid)
    assert outcome.status == "failed" and "not a member" in outcome.detail


async def test_expired_sweep(database: None) -> None:
    from workers.execute import sweep

    sc = await build_scenario()
    pid = await propose(sc, "move_due", {"new_due_date": future_date()})
    async with tenant_tx(sc.tenant.id) as s:
        await s.execute(
            text("UPDATE proposals SET expires_at = :t WHERE id = :id"),
            {"t": datetime.now(UTC) - timedelta(hours=1), "id": pid},
        )
    assert (await sweep(sc.tenant.id))["expired"] == 1
    assert (await proposal(sc, pid)).status == "expired"
