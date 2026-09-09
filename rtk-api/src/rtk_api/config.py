"""Settings for rtk-api, loaded from environment variables and
~/.config/rtk-api/env (if present). Real environment variables take
precedence over the file, so a launchd EnvironmentVariables block or an
ad-hoc export can always override the file for local testing.
"""

from __future__ import annotations

import json
import logging
import os
from functools import cached_property, lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_ENV_PATH = Path.home() / ".config" / "rtk-api" / "env"


def _load_config_file_env(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file. Lines starting with '#' or blank
    lines are ignored. Does not support multi-line values or quoting beyond
    stripping surrounding quotes.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


class ClientSpec(BaseModel):
    """One named API client (e.g. the `instinct` iMessage agent).

    `token` is the shared secret the client presents in `X-Rtk-Client-Token`
    (or `Authorization: Bearer`). `allow` and `require_approval` are fnmatch
    patterns over tool names -- `["things.*"]` grants all of Things.

    `require_approval` is reserved for the human-in-the-loop approval queue
    (Telegram Allow/Deny on a dedicated bot). Nothing implements it yet, so
    matching tools are refused; listing one here is how you mark a grant as
    "intended, but not until approvals ship".
    """

    token: str | None = None
    allow: list[str] = Field(default_factory=list)
    require_approval: list[str] = Field(default_factory=list)


def _parse_clients(raw: str) -> dict[str, ClientSpec]:
    """Parse RTK_API_CLIENTS.

    A malformed value fails closed (no clients) rather than taking the server
    down -- the owner credentials still work, so a bad edit to the env file
    doesn't lock Noah out of his own API.
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("RTK_API_CLIENTS must be a JSON object of name -> client")
        return {name: ClientSpec.model_validate(spec) for name, spec in parsed.items()}
    except (json.JSONDecodeError, ValidationError, ValueError):
        logging.getLogger("rtk_api.config").exception(
            "RTK_API_CLIENTS is malformed; no scoped clients loaded"
        )
        return {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
    )

    rtk_api_bearer_token: str | None = Field(default=None)
    rtk_api_clients_raw: str = Field(default="", alias="rtk_api_clients")
    rtk_api_mcp_secret: str | None = Field(default=None)
    cf_access_team_domain: str | None = Field(default=None)
    cf_access_aud: str | None = Field(default=None)
    cf_access_owner_common_name: str | None = Field(default=None)
    things_auth_token: str | None = Field(default=None)
    imessage_write_enabled: bool = Field(default=False)
    imessage_write_allowlist_raw: str = Field(default="", alias="imessage_write_allowlist")
    fairbridge_write_enabled: bool = Field(default=False)
    fairbridge_participants_raw: str = Field(default="", alias="fairbridge_participants")
    rtk_api_host: str = Field(default="127.0.0.1")
    rtk_api_port: int = Field(default=8787)

    @cached_property
    def clients(self) -> dict[str, ClientSpec]:
        """Parsed once per Settings instance -- and Settings itself is cached
        by `get_settings`, so editing RTK_API_CLIENTS in the env file requires
        a process restart to take effect.
        """
        return _parse_clients(self.rtk_api_clients_raw)

    @property
    def imessage_write_allowlist(self) -> list[str]:
        raw = self.imessage_write_allowlist_raw
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def fairbridge_participants(self) -> list[str]:
        """Phone numbers/emails of the Fairbridge group chat's members, comma
        separated. Deliberately **not** defaulted in code: these are three real
        people's phone numbers, and this repo is on GitHub. Set it in
        ~/.config/rtk-api/env (gitignored, rtk-local) -- with it unset the
        fairbridge tools refuse rather than guessing at a chat.
        """
        raw = self.fairbridge_participants_raw
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """Build Settings, merging ~/.config/rtk-api/env under the real
    process environment (real env vars win). Cached: call
    get_settings.cache_clear() in tests that need to reload.
    """
    file_values = _load_config_file_env(CONFIG_ENV_PATH)
    added_keys: list[str] = []
    try:
        for key, value in file_values.items():
            if key not in os.environ:
                os.environ[key] = value
                added_keys.append(key)
        return Settings()
    finally:
        for key in added_keys:
            os.environ.pop(key, None)
