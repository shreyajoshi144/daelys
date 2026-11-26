"""The five typed actions (design rule 7): argument schemas, tiers, required permission, validation against
current state, and the exact preview a human approves."""

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import Role

ACTIONS: tuple[str, ...] = ("reassign", "move_due", "split", "notify", "comment_on_pr")

# Tier 2 = workspace change (reversible), tier 3 = external effect (never auto-approved, not reversible).
TIERS: dict[str, int] = {
    "reassign": 2,
    "move_due": 2,
    "split": 2,
    "notify": 3,
    "comment_on_pr": 3,
    "create_item": 2,
}
# Minimum rank the effective principal needs: min(agent rank, approver rank) >= required.
REQUIRED_RANK: dict[str, int] = {
    "reassign": 2,
    "move_due": 2,
    "split": 2,
    "notify": 1,
    "comment_on_pr": 2,
    "create_item": 2,
}
# `create_item` is not an agent action: only meeting ingestion creates it (an unmatched action item becomes a
# proposal, never a silent new work item). ACTIONS above stays the proposer's whole vocabulary.
ROLE_RANK: dict[Role, int] = {Role.viewer: 0, Role.member: 1, Role.agent: 2, Role.lead: 2, Role.owner: 3}
MAX_DUE_SHIFT_DAYS = 120
COMMENT_MARKER = "<!-- daelys:proposal:{id} -->"
_MENTION = re.compile(r"(^|[^\w`])@[A-Za-z0-9-]+")


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReassignArgs(_Args):
    to_user_id: UUID


class MoveDueArgs(_Args):
    new_due_date: date


class SplitPart(_Args):
    title: str = Field(min_length=1, max_length=200)
    estimate_days: float = Field(gt=0, le=365)


class SplitArgs(_Args):
    parts: list[SplitPart] = Field(min_length=2, max_length=5)


class NotifyArgs(_Args):
    to_user_ids: list[UUID] = Field(min_length=1, max_length=10)
    subject: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=2000)


class CommentArgs(_Args):
    body: str = Field(min_length=1, max_length=2000)


class CreateItemArgs(_Args):
    title: str = Field(min_length=1, max_length=200)
    lane: Literal["Product", "Engineering", "Marketing"] | None = None
    owner_user_id: UUID | None = None
    due_date: date | None = None
    project_id: UUID | None = None


ARG_MODELS: dict[str, type[_Args]] = {
    "reassign": ReassignArgs,
    "move_due": MoveDueArgs,
    "split": SplitArgs,
    "notify": NotifyArgs,
    "comment_on_pr": CommentArgs,
    "create_item": CreateItemArgs,
}


class InvalidAction(ValueError):
    pass


def parse_args(action_type: str, raw: dict[str, Any]) -> _Args:
    if action_type not in ARG_MODELS:
        raise InvalidAction(f"unknown action type {action_type!r}")
    try:
        return ARG_MODELS[action_type].model_validate(raw)
    except ValidationError as exc:
        raise InvalidAction(f"invalid {action_type} args: {exc.errors(include_url=False)}") from None


def comment_text(proposal_id: UUID, body: str) -> str:
    """Exactly what gets posted. The marker lets the executor detect an earlier post after a crash."""
    return body.rstrip() + "\n\n" + COMMENT_MARKER.format(id=proposal_id)


def new_due_at(current: datetime | None, day: date) -> datetime:
    """Keep the existing time of day and offset; default 18:00 UTC."""
    if current is None:
        return datetime.combine(day, time(18, 0), tzinfo=UTC)
    return datetime.combine(day, current.timetz())


async def human_members(session: AsyncSession, ids: list[UUID]) -> dict[UUID, Any]:
    rows = await session.execute(
        text("""
            SELECT u.id, u.display_name, u.email, u.github_login, m.role FROM users u
            JOIN memberships m ON m.user_id = u.id
            WHERE u.id = ANY(CAST(:ids AS uuid[])) AND m.role <> 'agent'
        """),
        {"ids": ids},
    )
    return {r.id: r for r in rows}


async def linked_pr(session: AsyncSession, item: Any) -> Any:
    """The open PR a comment would go on: the item itself if it is a PR, else the open PR that closes it."""
    if item.kind == "pr":
        return item if item.status not in ("done", "cancelled") else None
    return (
        await session.execute(
            text("""
                SELECT p.* FROM edges e JOIN work_items p ON p.id = e.src_id
                WHERE e.dst_id = :id AND e.kind = 'closes' AND p.kind = 'pr'
                  AND p.status NOT IN ('done', 'cancelled')
                ORDER BY p.last_activity_at DESC NULLS LAST LIMIT 1
            """),
            {"id": item.id},
        )
    ).first()


async def check_against_state(
    session: AsyncSession, action_type: str, args: _Args, item: Any, now: datetime
) -> list[str]:
    """Problems that make the action unsafe or meaningless right now. Empty list = OK."""
    problems: list[str] = []
    if isinstance(args, CreateItemArgs):
        if args.owner_user_id and args.owner_user_id not in await human_members(
            session, [args.owner_user_id]
        ):
            problems.append("owner is not a member of this workspace")
        if args.project_id and not await session.scalar(
            text("SELECT 1 FROM projects WHERE id = :p"), {"p": args.project_id}
        ):
            problems.append("project not found")
        if args.due_date and not now.date() <= args.due_date <= now.date() + timedelta(
            days=MAX_DUE_SHIFT_DAYS
        ):
            problems.append(f"due date must be within {MAX_DUE_SHIFT_DAYS} days from today")
        return problems
    if item.status in ("done", "cancelled") and action_type != "notify":
        problems.append(f"item is {item.status}")
    if isinstance(args, ReassignArgs):
        users = await human_members(session, [args.to_user_id])
        if args.to_user_id not in users:
            problems.append("target user is not a member of this workspace")
        elif args.to_user_id == item.owner_id:
            problems.append("item is already owned by that person")
    elif isinstance(args, MoveDueArgs):
        today = now.date()
        if not today <= args.new_due_date <= today + timedelta(days=MAX_DUE_SHIFT_DAYS):
            problems.append(f"new due date must be within {MAX_DUE_SHIFT_DAYS} days from today")
        if item.due_at is not None and args.new_due_date == item.due_at.date():
            problems.append("due date unchanged")
    elif isinstance(args, NotifyArgs):
        users = await human_members(session, args.to_user_ids)
        if set(args.to_user_ids) - users.keys():
            problems.append("every recipient must be a member of this workspace")
    elif isinstance(args, CommentArgs):
        if await linked_pr(session, item) is None:
            problems.append("no open pull request linked to this item")
        if _MENTION.search(args.body):
            problems.append("comments may not @mention anyone")
    return problems


async def preview(session: AsyncSession, proposal: Any, item: Any) -> dict[str, Any]:
    """The diff a human approves. For tier 3 this is the exact external text."""
    args = parse_args(proposal.action_type, proposal.args)
    if isinstance(args, CreateItemArgs):
        owner = await human_members(session, [args.owner_user_id]) if args.owner_user_id else {}
        return {
            "new_item": args.title,
            "lane": args.lane,
            "owner": owner[args.owner_user_id].display_name if args.owner_user_id in owner else None,
            "due_date": args.due_date.isoformat() if args.due_date else None,
        }
    if isinstance(args, ReassignArgs):
        current = await human_members(session, [item.owner_id]) if item.owner_id else {}
        target = await human_members(session, [args.to_user_id])
        before = (
            current[item.owner_id].display_name
            if item.owner_id in current
            else (item.owner_login or "unassigned")
        )
        after = target[args.to_user_id].display_name if args.to_user_id in target else "unknown"
        return {"field": "owner", "before": before, "after": after}
    if isinstance(args, MoveDueArgs):
        return {
            "field": "due date",
            "before": item.due_at.isoformat() if item.due_at else None,
            "after": new_due_at(item.due_at, args.new_due_date).isoformat(),
        }
    if isinstance(args, SplitArgs):
        return {"parent": item.title, "into": [p.model_dump() for p in args.parts]}
    if isinstance(args, NotifyArgs):
        users = await human_members(session, args.to_user_ids)
        return {
            "to": [f"{u.display_name} <{u.email}>" for u in users.values()],
            "subject": args.subject,
            "body": args.body,
            "channel": "email (Mailpit sandbox)",
        }
    assert isinstance(args, CommentArgs)
    pr = await linked_pr(session, item)
    return {
        "pull_request": pr.key if pr else None,
        "url": pr.url if pr else None,
        "text": comment_text(proposal.id, args.body),
    }
