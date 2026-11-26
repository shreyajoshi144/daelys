"""Fresh copy of a tenant for scenario replays (time machine).

The copy gets the same people (same emails and password hashes, so the same sign-in works with the new
workspace slug), the same projects and briefs, and an event clock. It gets no events: the scenario is
replayed into it. Each side is touched only inside its own tenant transaction, so RLS is never bypassed.
"""

import uuid
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from core.briefs import chunk_text, store_brief
from core.db import tenant_tx
from core.seed import ensure_tenant, set_policy


@dataclass(frozen=True)
class Copy:
    tenant_id: UUID
    slug: str


async def copy_tenant(source_id: UUID) -> Copy:
    async with tenant_tx(source_id) as s:
        tenant = (await s.execute(text("SELECT slug, name FROM tenants"))).one()
        users = (
            await s.execute(
                text("""
                    SELECT u.id, u.email, u.display_name, u.department, u.github_login, u.password_hash,
                        m.role
                    FROM users u JOIN memberships m ON m.user_id = u.id
                """)
            )
        ).all()
        projects = (await s.execute(text("SELECT id, name, created_by FROM projects"))).all()
        leads = (await s.execute(text("SELECT project_id, user_id FROM project_members"))).all()
        briefs = (
            await s.execute(
                text("""
                    SELECT project_id, title, content, content_type, created_by FROM artifacts
                    WHERE kind = 'brief' AND project_id IS NOT NULL ORDER BY created_at
                """)
            )
        ).all()

    slug = f"{tenant.slug[:40]}-replay-{uuid.uuid4().hex[:6]}"
    new_id = await ensure_tenant(slug, f"{tenant.name} (replay)")
    user_map: dict[UUID, UUID] = {}
    project_map: dict[UUID, UUID] = {}
    async with tenant_tx(new_id) as s:
        for u in users:
            new_user = await s.scalar(
                text("""
                    INSERT INTO users (tenant_id, email, display_name, department, github_login,
                        password_hash)
                    VALUES (:t, :email, :name, :dept, :gh, :pw) RETURNING id
                """),
                {
                    "t": new_id,
                    "email": u.email,
                    "name": u.display_name,
                    "dept": u.department,
                    "gh": u.github_login,
                    "pw": u.password_hash,
                },
            )
            user_map[u.id] = new_user
            await s.execute(
                text("INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, :r)"),
                {"t": new_id, "u": new_user, "r": u.role},
            )
        for p in projects:
            project_map[p.id] = await s.scalar(
                text("INSERT INTO projects (tenant_id, name, created_by) VALUES (:t, :n, :u) RETURNING id"),
                {"t": new_id, "n": p.name, "u": user_map.get(p.created_by)},
            )
        for lead in leads:
            await s.execute(
                text("INSERT INTO project_members (tenant_id, project_id, user_id) VALUES (:t, :p, :u)"),
                {"t": new_id, "p": project_map[lead.project_id], "u": user_map[lead.user_id]},
            )
        for b in briefs:
            await store_brief(
                s,
                new_id,
                project_map[b.project_id],
                b.title,
                b.content,
                b.content_type,
                user_map.get(b.created_by),
                None,
                chunk_text(b.content),
            )
    await set_policy(new_id, clock="event")
    return Copy(new_id, slug)
