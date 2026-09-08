"""Auth middleware for rtk-api.

Two questions, answered in that order: *who* is calling (authentication) and
*what may they call* (authorization). Authentication resolves a `Principal`;
`app.call_tool` enforces the principal's allowlist.

A request resolves to a principal via the first of these that matches:
  1. Path starts with `/{RTK_API_MCP_SECRET}/` (the MCP capability URL).
  2. A named client credential -- `X-Rtk-Client-Token`, or
     `Authorization: Bearer <token>` -- matching an entry in RTK_API_CLIENTS.
     Checked *before* the Cloudflare JWT so a scoped identity always beats
     the generic one when a client presents both (which external clients
     must: the Access headers get them past the edge, the client token
     tells us who they are).
  3. `Cf-Access-Jwt-Assertion` verifying against the Cloudflare Access JWKS
     for the configured team domain, with `aud == CF_ACCESS_AUD` *and* the
     JWT's `common_name` claim (the authenticating service token's client
     id) matching `CF_ACCESS_OWNER_COMMON_NAME`. A JWT is proof someone
     passed *some* service token valid for this Access app -- not proof of
     *which* one -- so without this check any scoped client's Access
     credentials alone (no X-Rtk-Client-Token) would resolve to unscoped
     owner, silently defeating RTK_API_CLIENTS. A valid JWT for a
     non-owner common_name falls through to check 4 rather than granting
     anything.
  4. `Authorization: Bearer <RTK_API_BEARER_TOKEN>` (constant-time compare).

3 and 4 are the owner: unscoped, all tools. `/health` is always
unauthenticated. Everything else -> 401.

Never log secrets. Auth failures may log source IP but not token/JWT values.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from fnmatch import fnmatch

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from rtk_api.config import Settings

logger = logging.getLogger("rtk_api.auth")

UNAUTHENTICATED_PATHS = {"/health"}

#: Header an external client uses to identify itself. `Authorization: Bearer`
#: works too and is checked as a fallback, but Cloudflare Access has a history
#: of being fussy about forwarding `Authorization` to the origin -- a custom
#: header passes through cleanly, so prefer it for anything behind Access.
CLIENT_TOKEN_HEADER = "X-Rtk-Client-Token"


@dataclass(frozen=True)
class Principal:
    """Who is calling, and what they may call.

    `allow` and `require_approval` are fnmatch patterns over tool names, e.g.
    "things.*" or "imessage.send".

    `require_approval` is checked *before* `allow` and overrides it, so a
    broad grant like "imessage.*" can be narrowed by gating one tool out of
    it. It is the placeholder for the human-in-the-loop approval queue;
    nothing implements it yet, so a tool that matches it is refused rather
    than silently allowed.
    """

    name: str
    kind: str
    allow: tuple[str, ...] = ()
    require_approval: tuple[str, ...] = ()

    def permits(self, tool_name: str) -> bool:
        return any(fnmatch(tool_name, pattern) for pattern in self.allow)

    def needs_approval(self, tool_name: str) -> bool:
        return any(fnmatch(tool_name, pattern) for pattern in self.require_approval)


MCP_SECRET_PLACEHOLDER = "<mcp-secret>"


def _first_path_segment(path: str) -> str:
    """'/abc/def' -> 'abc'; '/abc' -> 'abc'; '/' -> ''."""
    return path.lstrip("/").split("/", 1)[0]


def is_mcp_path(path: str, settings: Settings) -> bool:
    """True if `path` is `/{RTK_API_MCP_SECRET}` or `/{RTK_API_MCP_SECRET}/...`.

    The first segment is compared with `hmac.compare_digest` rather than
    `str.startswith` so a probing client can't learn the secret one byte at
    a time from response timing.
    """
    secret = settings.rtk_api_mcp_secret
    if not secret:
        return False
    segment = _first_path_segment(path)
    return hmac.compare_digest(segment.encode("utf-8"), secret.encode("utf-8"))


def redact_path(path: str, settings: Settings) -> str:
    """Replace the MCP secret in `path` with `<mcp-secret>` before it is
    logged, so the capability URL never lands in /tmp/rtk-api.log.

    Substring replacement (not just the exact first segment) on purpose: a
    near-miss probe like `/{secret}x` is a 401 that would otherwise log the
    full secret alongside it.
    """
    secret = settings.rtk_api_mcp_secret
    if secret and secret in path:
        return path.replace(secret, MCP_SECRET_PLACEHOLDER)
    return path


def _owner(kind: str) -> Principal:
    """The unscoped principal: Noah, via Cloudflare Access or the legacy
    bearer token. Named explicitly rather than left as an implicit bypass so
    every request goes through the same allowlist check.
    """
    return Principal(name="owner", kind=kind, allow=("*",))


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


def _verify_cf_access_jwt(token: str, settings: Settings) -> dict | None:
    """Verify signature/audience and return the decoded claims, or None."""
    if not settings.cf_access_team_domain or not settings.cf_access_aud:
        return None
    try:
        client = _jwks_cache.get(settings.cf_access_team_domain)
        signing_key = client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=settings.cf_access_aud,
        )
    except Exception:
        logger.warning("cf-access jwt verification failed", extra={"reason": "invalid_or_expired"})
        return None


def _bearer_value(header_value: str | None) -> str | None:
    if not header_value or not header_value.startswith("Bearer "):
        return None
    return header_value[len("Bearer ") :]


def _verify_bearer(header_value: str | None, settings: Settings) -> bool:
    if not settings.rtk_api_bearer_token:
        return False
    presented = _bearer_value(header_value)
    if presented is None:
        return False
    return hmac.compare_digest(presented, settings.rtk_api_bearer_token)


def _resolve_client(request: Request, settings: Settings) -> Principal | None:
    """Match a presented client token against RTK_API_CLIENTS.

    Compares against every configured client (no early exit) so the work done
    doesn't depend on which client was presented.
    """
    presented = request.headers.get(CLIENT_TOKEN_HEADER) or _bearer_value(
        request.headers.get("Authorization")
    )
    if not presented:
        return None

    matched: Principal | None = None
    for name, spec in settings.clients.items():
        if not spec.token:
            continue
        if hmac.compare_digest(presented, spec.token):
            matched = Principal(
                name=name,
                kind="client",
                allow=tuple(spec.allow),
                require_approval=tuple(spec.require_approval),
            )
    return matched


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

        # 1. MCP capability URL prefix. NOTE: this short-circuits every check
        # below, and the MCP mount registers the *whole* registry -- holding
        # this secret means holding every tool. Per-client scoping does not
        # apply to the MCP surface; see README.
        if is_mcp_path(path, settings):
            request.state.principal = Principal(name="mcp", kind="mcp-secret", allow=("*",))
            return await call_next(request)

        # 2. Named client credential -- before the JWT, so a scoped identity
        # wins over the generic one when both are presented.
        client_principal = _resolve_client(request, settings)
        if client_principal is not None:
            request.state.principal = client_principal
            return await call_next(request)

        # 3. Cloudflare Access JWT -- owner only if it's *the owner's*
        # service token that authenticated at the edge. A valid JWT from a
        # scoped client's own service token (e.g. instinct's) must not grant
        # owner just because no client token happened to be presented.
        cf_jwt = request.headers.get("Cf-Access-Jwt-Assertion")
        if cf_jwt:
            claims = _verify_cf_access_jwt(cf_jwt, settings)
            if claims is not None:
                common_name = claims.get("common_name")
                if settings.cf_access_owner_common_name and hmac.compare_digest(
                    common_name or "", settings.cf_access_owner_common_name
                ):
                    request.state.principal = _owner("cf-access")
                    return await call_next(request)
                logger.warning(
                    "cf-access jwt valid but common_name is not the owner's",
                    extra={"common_name": common_name},
                )

        # 4. Static bearer token.
        if _verify_bearer(request.headers.get("Authorization"), settings):
            request.state.principal = _owner("bearer")
            return await call_next(request)

        client_ip = request.headers.get(
            "CF-Connecting-IP", request.client.host if request.client else "?"
        )
        logger.warning(
            "unauthorized request",
            extra={"path": redact_path(path, settings), "ip": client_ip},
        )
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
