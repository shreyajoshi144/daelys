from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

import bcrypt
import jwt

from core.config import get_settings

ISSUER = "daelys"
AUDIENCE = "daelys-api"
ALGORITHM = "HS256"
# Verified against when the account does not exist, so response time does not reveal valid emails.
_DUMMY_HASH = bcrypt.hashpw(b"daelys-timing-equaliser", bcrypt.gensalt()).decode()


class Role(StrEnum):
    owner = "owner"
    lead = "lead"
    member = "member"
    viewer = "viewer"
    agent = "agent"


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    tenant_id: UUID
    role: Role


class InvalidToken(Exception):
    pass


def hash_password(password: str) -> str:
    if len(password.encode()) > 72:
        raise ValueError("password longer than 72 bytes")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate = password.encode()[:72]
    if password_hash is None:
        bcrypt.checkpw(candidate, _DUMMY_HASH.encode())
        return False
    return bcrypt.checkpw(candidate, password_hash.encode())


def issue_token(principal: Principal, now: datetime | None = None) -> str:
    settings = get_settings()
    issued = now or datetime.now(UTC)
    claims = {
        "sub": str(principal.user_id),
        "tenant_id": str(principal.tenant_id),
        "role": principal.role.value,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": issued,
        "exp": issued + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def decode_token(token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            get_settings().jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "tenant_id", "role", "exp", "iat"]},
        )
        return Principal(UUID(claims["sub"]), UUID(claims["tenant_id"]), Role(claims["role"]))
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidToken(str(exc)) from exc
