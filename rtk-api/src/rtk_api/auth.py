"""Auth middleware for rtk-api.

A request is authorized if ANY of:
  1. `Cf-Access-Jwt-Assertion` header verifies against the Cloudflare Access
     JWKS for the configured team domain, with `aud == CF_ACCESS_AUD`.
  2. `Authorization: Bearer <RTK_API_BEARER_TOKEN>` (constant-time compare).
  3. Path starts with `/{RTK_API_MCP_SECRET}/` (the MCP capability URL).

`/health` is always unauthenticated. Everything else -> 401.

Never log secrets. Auth failures may log source IP but not token/JWT values.
"""

from __future__ import annotations

import hmac
import logging

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from rtk_api.config import Settings

logger = logging.getLogger("rtk_api.auth")

UNAUTHENTICATED_PATHS = {"/health"}


class _JWKSCache:
    """Lazily builds and caches a PyJWKClient per team domain so we don't
    refetch the JWKS on every request.
    """

    def __init__(self) -> None:
        self._clients: dict[str, PyJWKClient] = {}

    def get(self, team_domain: str) -> PyJWKClient:
        client = self._clients.get(team_domain)
        if client is None:
            certs_url = f"https://{team_domain}/cdn-cgi/access/certs"
            client = PyJWKClient(certs_url)
            self._clients[team_domain] = client
        return client


_jwks_cache = _JWKSCache()


def _verify_cf_access_jwt(token: str, settings: Settings) -> bool:
    if not settings.cf_access_team_domain or not settings.cf_access_aud:
        return False
    try:
        client = _jwks_cache.get(settings.cf_access_team_domain)
        signing_key = client.get_signing_key_from_jwt(token)
        jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=settings.cf_access_aud,
        )
        return True
    except Exception:
        logger.warning("cf-access jwt verification failed", extra={"reason": "invalid_or_expired"})
        return False


def _verify_bearer(header_value: str, settings: Settings) -> bool:
    if not settings.rtk_api_bearer_token:
        return False
    if not header_value.startswith("Bearer "):
        return False
    presented = header_value[len("Bearer ") :]
    return hmac.compare_digest(presented, settings.rtk_api_bearer_token)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in UNAUTHENTICATED_PATHS:
            request.state.principal = None
            return await call_next(request)

        settings = self.settings

        # Path 3: MCP capability URL prefix.
        mcp_secret = settings.rtk_api_mcp_secret
        if mcp_secret and (path == f"/{mcp_secret}" or path.startswith(f"/{mcp_secret}/")):
            request.state.principal = "mcp-secret"
            return await call_next(request)

        # Path 1: Cloudflare Access JWT.
        cf_jwt = request.headers.get("Cf-Access-Jwt-Assertion")
        if cf_jwt and _verify_cf_access_jwt(cf_jwt, settings):
            request.state.principal = "cf-access"
            return await call_next(request)

        # Path 2: static bearer token.
        auth_header = request.headers.get("Authorization")
        if auth_header and _verify_bearer(auth_header, settings):
            request.state.principal = "bearer"
            return await call_next(request)

        client_ip = request.headers.get(
            "CF-Connecting-IP", request.client.host if request.client else "?"
        )
        logger.warning("unauthorized request", extra={"path": path, "ip": client_ip})
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
