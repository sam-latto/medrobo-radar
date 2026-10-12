"""Signed-in user for each request.

The browser sends the Supabase session token as `Authorization: Bearer <jwt>`. We verify
its signature against the project's published signing keys (JWKS), its audience and
issuer, then require a profile row: profiles exist only for allowed school emails
(enforced by the database trigger on sign-up).
"""
import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request

from api.db import as_service
from api.deps import get_conn
from api.errors import ApiError
from config import SUPABASE_URL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str


class TokenVerifier:
    """Verifies Supabase access tokens. PyJWT is imported lazily."""

    algorithms = ["ES256", "RS256"]
    audience = "authenticated"

    def __init__(self, supabase_url: str = SUPABASE_URL, jwk_client=None):
        base = supabase_url.rstrip("/")
        self.issuer = f"{base}/auth/v1"
        self.jwks_url = f"{base}/auth/v1/.well-known/jwks.json"
        self._jwk_client = jwk_client

    def _client(self):
        if self._jwk_client is None:
            import jwt
            self._jwk_client = jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=3600)
        return self._jwk_client

    def verify(self, token: str) -> dict:
        import jwt
        try:
            key = self._client().get_signing_key_from_jwt(token)
            return jwt.decode(token, key.key, algorithms=self.algorithms, audience=self.audience,
                              issuer=self.issuer, options={"require": ["exp", "sub"]})
        except jwt.PyJWTError as e:
            raise ApiError(401, "invalid_token", "Your session has expired. Please sign in again.") from e


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "not_signed_in", "Please sign in.")
    return token.strip()


def current_user(request: Request, conn=Depends(get_conn)) -> CurrentUser:
    claims = request.app.state.verifier.verify(_bearer(request))
    try:
        user_id = UUID(claims["sub"])
    except (KeyError, ValueError):
        raise ApiError(401, "invalid_token", "Your session has expired. Please sign in again.")
    with as_service(conn):
        row = conn.execute("select id, email from public.profiles where id = %s", (user_id,)).fetchone()
    if row is None:
        raise ApiError(403, "account_not_allowed",
                       "Industry Radar is open to University of Michigan accounts only.")
    return CurrentUser(id=row["id"], email=row["email"])
