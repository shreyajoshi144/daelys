import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import text

from api.deps import SESSION_COOKIE, AnyMember
from api.schemas import LoginRequest, MeOut, TokenResponse
from core.config import get_settings
from core.db import resolve_tenant_slug, tenant_tx
from core.ratelimit import allow
from core.security import Principal, Role, issue_token, verify_password
from rules.engine import tenant_now

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)

_INVALID = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    """Bearer token for API clients."""
    token = await _authenticate(body, request)
    return TokenResponse(access_token=token, expires_in=get_settings().jwt_ttl_minutes * 60)


@router.post("/session", status_code=status.HTTP_204_NO_CONTENT)
async def create_session(body: LoginRequest, request: Request, response: Response) -> None:
    """Web app login: the token goes in an httpOnly cookie, so page scripts never see it."""
    settings = get_settings()
    token = await _authenticate(body, request)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.jwt_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeOut)
async def me(ctx: AnyMember) -> MeOut:
    row = (
        await ctx.session.execute(
            text("""
                SELECT u.display_name, u.email, t.name AS tenant_name, t.is_demo
                FROM users u JOIN tenants t ON t.id = u.tenant_id WHERE u.id = :u
            """),
            {"u": ctx.principal.user_id},
        )
    ).one()
    return MeOut(
        user_id=ctx.principal.user_id,
        display_name=row.display_name,
        email=row.email,
        tenant_name=row.tenant_name,
        role=ctx.principal.role.value,
        is_demo=row.is_demo,
        clock_now=await tenant_now(ctx.session),
    )


async def _authenticate(body: LoginRequest, request: Request) -> str:
    settings = get_settings()
    email = body.email.lower()
    client_ip = request.client.host if request.client else "unknown"
    limit = settings.login_rate_limit_per_minute
    if not (
        await allow(f"login:ip:{client_ip}", limit)
        and await allow(f"login:acct:{body.tenant}:{email}", limit)
    ):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too many login attempts")

    tenant_id = await resolve_tenant_slug(body.tenant)
    if tenant_id is None:
        verify_password(body.password, None)
        raise _INVALID
    async with tenant_tx(tenant_id) as session:
        row = (
            await session.execute(
                text("""
                    SELECT u.id, u.password_hash, m.role
                    FROM users u JOIN memberships m ON m.user_id = u.id
                    WHERE u.email = :email
                """),
                {"email": email},
            )
        ).first()
    if row is None or not verify_password(body.password, row.password_hash) or row.role == Role.agent:
        log.info("auth.login_failed", extra={"tenant": body.tenant, "ip": client_ip})
        raise _INVALID
    return issue_token(Principal(row.id, tenant_id, Role(row.role)))
