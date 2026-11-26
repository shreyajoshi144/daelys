from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import tenant_tx
from core.security import InvalidToken, Principal, Role, decode_token

SESSION_COOKIE = "daelys_session"
# Cookie-authenticated writes must send this header. Browsers cannot add it cross-origin without a CORS
# preflight, and the API grants no CORS, so a forged cross-site form or fetch is rejected (CSRF defence).
CSRF_HEADER = "x-daelys-csrf"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_bearer = HTTPBearer(auto_error=False)
HUMAN_ROLES = (Role.owner, Role.lead, Role.member, Role.viewer)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid or missing credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class Context:
    principal: Principal
    session: AsyncSession


def _claimed_principal(request: Request, creds: HTTPAuthorizationCredentials | None) -> Principal:
    """Bearer token (API clients) or the httpOnly session cookie (the web app)."""
    token = creds.credentials if creds else request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _UNAUTHORIZED
    if creds is None and request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing CSRF header")
    try:
        return decode_token(token)
    except InvalidToken:
        raise _UNAUTHORIZED from None


async def _current_role(session: AsyncSession, user_id: UUID) -> Role:
    # The role comes from `memberships`, not the token, so revoking or downgrading takes effect at once.
    role = await session.scalar(text("SELECT role FROM memberships WHERE user_id = :u"), {"u": user_id})
    if role is None or Role(role) not in HUMAN_ROLES:
        raise _UNAUTHORIZED
    return Role(role)


async def get_context(
    request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> AsyncIterator[Context]:
    """Authenticate, then open one tenant-scoped transaction for the request. For read endpoints."""
    claimed = _claimed_principal(request, creds)
    async with tenant_tx(claimed.tenant_id) as session:
        role = await _current_role(session, claimed.user_id)
        yield Context(Principal(claimed.user_id, claimed.tenant_id, role), session)


async def get_principal(
    request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> Principal:
    """Authenticate without holding a transaction. For write endpoints (they commit before responding) and
    slow ones such as LLM calls, which must not pin a database connection."""
    claimed = _claimed_principal(request, creds)
    async with tenant_tx(claimed.tenant_id) as session:
        role = await _current_role(session, claimed.user_id)
    return Principal(claimed.user_id, claimed.tenant_id, role)


def _forbid_unless(role: Role, allowed: tuple[Role, ...]) -> None:
    if role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")


def require_role(*allowed: Role) -> Callable[..., Coroutine[Any, Any, Context]]:
    async def checker(ctx: Annotated[Context, Depends(get_context)]) -> Context:
        _forbid_unless(ctx.principal.role, allowed)
        return ctx

    return checker


def require_principal(*allowed: Role) -> Callable[..., Coroutine[Any, Any, Principal]]:
    async def checker(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        _forbid_unless(principal.role, allowed)
        return principal

    return checker


AnyMember = Annotated[Context, Depends(require_role(*HUMAN_ROLES))]
LeadOrOwner = Annotated[Context, Depends(require_role(Role.owner, Role.lead))]
LeadPrincipal = Annotated[Principal, Depends(require_principal(Role.owner, Role.lead))]
OwnerPrincipal = Annotated[Principal, Depends(require_principal(Role.owner))]
