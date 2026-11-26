"""Seed demo tenants and replay the Tiffin Box baseline (week 1). Idempotent; runs as the app role.

python -m core.seed                      # tenants, users, week 1 replay + projection
python -m core.seed tenant <slug> <name> # an extra empty tenant (e.g. for out-of-order checks)
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.replay import SCENARIOS_DIR, ReplayConnector
from core.briefs import chunk_text, store_brief
from core.config import get_settings
from core.db import get_engine, tenant_tx
from core.logging import configure_logging
from core.security import Role, hash_password
from rules.engine import evaluate_tenant
from workers.projector import project_tenant

log = logging.getLogger(__name__)

_NAMESPACE = UUID("6f1c2a4e-8d3b-4f5a-9c7e-1b2d3e4f5a6b")


@dataclass(frozen=True)
class SeedUser:
    name: str
    email: str
    role: Role
    department: str | None
    github_login: str | None = None


TIFFIN_USERS = [
    SeedUser("Meera Iyer", "meera@tiffinbox.example", Role.owner, "Product"),
    SeedUser("Rohan Kapoor", "rohan@tiffinbox.example", Role.lead, "Product"),
    SeedUser("Ananya Rao", "ananya@tiffinbox.example", Role.member, "Product"),
    SeedUser("Arjun Mehta", "arjun@tiffinbox.example", Role.lead, "Engineering", "arjun-mehta-dev"),
    SeedUser("Kavya Nair", "kavya@tiffinbox.example", Role.member, "Engineering", "kavya-codes"),
    SeedUser("Vikram Singh", "vikram@tiffinbox.example", Role.member, "Engineering", "vik-builds"),
    SeedUser("Priya Sharma", "priya@tiffinbox.example", Role.lead, "Marketing"),
    SeedUser("Neha Gupta", "neha@tiffinbox.example", Role.member, "Marketing"),
    SeedUser("Sameer Khan", "sameer@tiffinbox.example", Role.viewer, "Marketing"),
]
DECOY_USERS = [SeedUser("Dana Decoy", "dana@decoy.example", Role.owner, None)]
AGENT = SeedUser("Daelys Agent", "agent@daelys.internal", Role.agent, None)


def tenant_uuid(slug: str) -> UUID:
    return uuid5(_NAMESPACE, f"tenant:{slug}")


async def ensure_tenant(slug: str, name: str) -> UUID:
    tenant_id = tenant_uuid(slug)
    async with tenant_tx(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, :name) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": tenant_id, "slug": slug, "name": name},
        )
    return tenant_id


async def _upsert_user(s: AsyncSession, tenant_id: UUID, user: SeedUser, password_hash: str | None) -> None:
    user_id = await s.scalar(
        text("""
            INSERT INTO users (tenant_id, email, display_name, department, github_login, password_hash)
            VALUES (:t, :email, :name, :dept, :gh, :pw)
            ON CONFLICT (tenant_id, email) DO UPDATE SET display_name = EXCLUDED.display_name,
                department = EXCLUDED.department, github_login = EXCLUDED.github_login,
                password_hash = EXCLUDED.password_hash
            RETURNING id
        """),
        {
            "t": tenant_id,
            "email": user.email,
            "name": user.name,
            "dept": user.department,
            "gh": user.github_login,
            "pw": password_hash,
        },
    )
    await s.execute(
        text("""
            INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, :r)
            ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = EXCLUDED.role
        """),
        {"t": tenant_id, "u": user_id, "r": user.role.value},
    )


async def seed_users(tenant_id: UUID, users: list[SeedUser], password: str) -> None:
    password_hash = hash_password(password)
    async with tenant_tx(tenant_id) as s:
        for user in users:
            await _upsert_user(s, tenant_id, user, password_hash)
        await _upsert_user(s, tenant_id, AGENT, None)


async def link_github(tenant_id: UUID, installation_id: str) -> None:
    async with tenant_tx(tenant_id) as s:
        await s.execute(
            text("""
                INSERT INTO integrations (tenant_id, provider, external_id) VALUES (:t, 'github', :e)
                ON CONFLICT (provider, external_id) DO NOTHING
            """),
            {"t": tenant_id, "e": installation_id},
        )


async def seed_project(tenant_id: UUID, name: str, brief_path: Path) -> UUID:
    """Demo project with its brief stored (no embeddings: seeding must not call paid APIs)."""
    project_id = uuid5(_NAMESPACE, f"project:{tenant_id}:{name}")
    content = await asyncio.to_thread(brief_path.read_text)
    async with tenant_tx(tenant_id) as s:
        owner = await s.scalar(text("SELECT user_id FROM memberships WHERE role = 'owner' LIMIT 1"))
        created = await s.scalar(
            text("""
                INSERT INTO projects (id, tenant_id, name, created_by) VALUES (:id, :t, :n, :u)
                ON CONFLICT (id) DO NOTHING RETURNING id
            """),
            {"id": project_id, "t": tenant_id, "n": name, "u": owner},
        )
        if created is not None:
            await s.execute(
                text("""
                    INSERT INTO project_members (tenant_id, project_id, user_id)
                    SELECT :t, :p, user_id FROM memberships WHERE role = 'lead'
                """),
                {"t": tenant_id, "p": project_id},
            )
            await store_brief(
                s,
                tenant_id,
                project_id,
                brief_path.name,
                content,
                "text/markdown",
                owner,
                None,
                chunk_text(content),
            )
    return project_id


async def set_policy(tenant_id: UUID, *, clock: str) -> None:
    async with tenant_tx(tenant_id) as s:
        await s.execute(
            text("""
                INSERT INTO policies (tenant_id, clock) VALUES (:t, :c)
                ON CONFLICT (tenant_id) DO UPDATE SET clock = EXCLUDED.clock, updated_at = now()
            """),
            {"t": tenant_id, "c": clock},
        )


async def seed_all() -> None:
    settings = get_settings()
    if settings.seed_user_password is None or len(settings.seed_user_password.get_secret_value()) < 8:
        raise SystemExit("SEED_USER_PASSWORD must be set (8+ chars) in .env")
    password = settings.seed_user_password.get_secret_value()

    tiffin = await ensure_tenant("tiffin-box", "Tiffin Box")
    decoy = await ensure_tenant("decoy-co", "Decoy Co")
    await seed_users(tiffin, TIFFIN_USERS, password)
    await seed_users(decoy, DECOY_USERS, password)
    if settings.seed_github_installation_id:
        await link_github(tiffin, settings.seed_github_installation_id)

    await seed_project(tiffin, "Subscriptions launch", SCENARIOS_DIR / "tiffin_box" / "brief.md")
    result = await ReplayConnector("tiffin_box", "week1").replay(tiffin)
    await set_policy(tiffin, clock="event")
    async with tenant_tx(tiffin) as s:  # the public demo can be reset by its owner
        await s.execute(
            text("UPDATE tenants SET is_demo = true")
        )  # replayed scenario: risk runs on the scenario's own clock
    projected = await project_tenant(tiffin)
    await evaluate_tenant(tiffin)
    log.info("seed.done", extra={"replay": result.__dict__, "projected": projected})


async def _run(args: argparse.Namespace) -> None:
    try:
        if args.command == "tenant":
            tenant_id = await ensure_tenant(args.slug, args.name)
            log.info("seed.tenant", extra={"slug": args.slug, "id": str(tenant_id)})
        else:
            await seed_all()
    finally:
        await get_engine().dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m core.seed")
    sub = parser.add_subparsers(dest="command")
    tenant = sub.add_parser("tenant", help="create an extra empty tenant")
    tenant.add_argument("slug")
    tenant.add_argument("name")
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
