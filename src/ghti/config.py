"""Settings, read from environment variables first and then from a local file.

On GitHub Actions everything comes from repository secrets (environment
variables). On a computer you can either export the same variables or write
them in `config.env` inside the user configuration folder (see `config_dir`).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ghti.errors import ConfigError

APP_NAME = "ghti"
REQUIRED = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "INTERVALS_API_KEY")
OPTIONAL = ("INTERVALS_ATHLETE_ID", "GHTI_TOKEN_FILE", "GHTI_TIMEZONE")


def utcnow() -> datetime:
    """Current time in UTC. Tests replace this function to freeze the clock."""
    return datetime.now(UTC)


def config_dir() -> Path:
    """Folder for the local config file and token (per operating system)."""
    override = os.environ.get("GHTI_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a tiny KEY=VALUE file. Blank lines and # comments are ignored."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def raw_values() -> dict[str, str]:
    values = read_env_file(config_dir() / "config.env")
    for key in REQUIRED + OPTIONAL:
        env_value = os.environ.get(key, "").strip()
        if env_value:
            values[key] = env_value
    return values


@dataclass(frozen=True)
class Settings:
    google_client_id: str
    google_client_secret: str
    intervals_api_key: str
    intervals_athlete_id: str
    token_file: Path
    timezone: str | None

    def secrets(self) -> tuple[str, ...]:
        """Values that must never appear in output."""
        return (self.google_client_secret, self.intervals_api_key)

    def today(self) -> date:
        """Today's date in the user's time zone (GHTI_TIMEZONE or the system one)."""
        now = utcnow()
        if self.timezone:
            return now.astimezone(ZoneInfo(self.timezone)).date()
        return now.astimezone().date()


def is_configured() -> bool:
    """True when at least one required setting is present."""
    values = raw_values()
    return any(values.get(key) for key in REQUIRED)


def load_settings() -> Settings:
    values = raw_values()
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise ConfigError(
            f"Missing setting(s): {', '.join(missing)}. "
            "On GitHub add them under Settings > Secrets and variables > Actions. "
            f"On your computer set them as environment variables or in {config_dir() / 'config.env'}."
        )
    tz = values.get("GHTI_TIMEZONE") or None
    if tz:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(
                f"GHTI_TIMEZONE={tz!r} is not a valid time zone. Use a name like 'Europe/Rome'."
            ) from None
    token_file = Path(values.get("GHTI_TOKEN_FILE") or config_dir() / "token.enc")
    athlete = values.get("INTERVALS_ATHLETE_ID") or "0"  # "0" means "the owner of the API key"
    return Settings(
        google_client_id=values["GOOGLE_CLIENT_ID"],
        google_client_secret=values["GOOGLE_CLIENT_SECRET"],
        intervals_api_key=values["INTERVALS_API_KEY"],
        intervals_athlete_id=athlete,
        token_file=token_file,
        timezone=tz,
    )


def mask(value: str) -> str:
    """Show only the first characters of a secret, e.g. 'abcd...'."""
    if not value:
        return ""
    return value[:4] + "..." if len(value) > 8 else "****"


def redact(text: str, secrets: tuple[str, ...]) -> str:
    """Replace every secret found in `text` with its masked form."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, mask(secret))
    return text
