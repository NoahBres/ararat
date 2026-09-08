"""Settings for hometools, loaded from environment variables and
~/.config/hometools/env (if present). Real environment variables take
precedence over the file, so a launchd EnvironmentVariables block or an
ad-hoc export can always override the file for local testing.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_ENV_PATH = Path.home() / ".config" / "hometools" / "env"


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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
    )

    hometools_bearer_token: str | None = Field(default=None)
    hometools_mcp_secret: str | None = Field(default=None)
    cf_access_team_domain: str | None = Field(default=None)
    cf_access_aud: str | None = Field(default=None)
    things_auth_token: str | None = Field(default=None)
    imessage_write_enabled: bool = Field(default=False)
    imessage_write_allowlist_raw: str = Field(default="", alias="imessage_write_allowlist")
    hometools_host: str = Field(default="127.0.0.1")
    hometools_port: int = Field(default=8787)

    @property
    def imessage_write_allowlist(self) -> list[str]:
        raw = self.imessage_write_allowlist_raw
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """Build Settings, merging ~/.config/hometools/env under the real
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
