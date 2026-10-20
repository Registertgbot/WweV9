"""Configuration loaded from environment variables (never hardcode secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:  # optional convenience for local development
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
VALID_POLICIES = ("pause", "cancel", "forfeit")


def normalize_database_url(url: str) -> tuple[str, bool]:
    """Convert Railway/Heroku style URLs to a SQLAlchemy asyncpg URL.

    Returns ``(url, ssl_required)``. asyncpg does not understand the libpq
    ``sslmode`` query parameter, so it is stripped and reported separately.
    """
    url = (url or "").strip()
    if not url:
        return "", False
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
            break
    ssl_required = False
    if url.startswith("postgresql+asyncpg://"):
        parts = urlsplit(url)
        kept = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key == "sslmode":
                ssl_required = value in ("require", "verify-ca", "verify-full")
                continue
            kept.append((key, value))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    return url, ssl_required


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _channel(name: str) -> str:
    """Results channel: a numeric id (-1001234567890) or an @username; empty = channel posting disabled."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return ""
    if raw.lstrip("-").isdigit() or raw.startswith("@"):
        return raw
    return "@" + raw


def _id_list(name: str) -> tuple[int, ...]:
    out = []
    for part in os.getenv(name, "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return tuple(out)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    database_url: str = ""
    database_ssl: bool = False
    owner_id: int = 0
    challenge_timeout: int = 60
    turn_timeout: int = 60
    reversal_timeout: int = 15
    timeout_policy: str = "pause"
    dm_setup_timeout: int = 300
    pause_expiry: int = 86400
    restart_grace: int = 30
    reveal_incoming_move: bool = False
    rumble_lobby_timeout: int = 60       # Royal Rumble: how long the join lobby stays open
    rumble_entry_interval: int = 45      # seconds between new entrants
    rumble_turn_timeout: int = 45        # an idle fighter's turn is skipped after this
    rumble_min_players: int = 3
    rumble_max_players: int = 12
    default_groups: tuple[int, ...] = ()   # groups that always work; every other group needs the owner's /allow
    results_channel: str = ""            # tournament results are mirrored here (bot must be a channel admin)
    log_level: str = "INFO"
    assets_dir: Path = BASE_DIR / "assets" / "gifs"


def load_settings(require_token: bool = False) -> Settings:
    if load_dotenv is not None:
        load_dotenv(BASE_DIR / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if require_token and not token:
        raise RuntimeError("BOT_TOKEN is not set")
    url, ssl_required = normalize_database_url(os.getenv("DATABASE_URL", ""))
    policy = os.getenv("TIMEOUT_POLICY", "pause").strip().lower()
    if policy not in VALID_POLICIES:
        raise ValueError(f"TIMEOUT_POLICY must be one of {VALID_POLICIES}")
    return Settings(
        bot_token=token,
        database_url=url,
        database_ssl=ssl_required,
        owner_id=_int("OWNER_ID", 0),
        challenge_timeout=_int("CHALLENGE_TIMEOUT_SECONDS", 60, 10),
        turn_timeout=_int("TURN_TIMEOUT_SECONDS", 60, 15),
        reversal_timeout=_int("REVERSAL_TIMEOUT_SECONDS", 15, 5),
        timeout_policy=policy,
        dm_setup_timeout=_int("DM_SETUP_TIMEOUT_SECONDS", 300, 30),
        pause_expiry=_int("PAUSE_EXPIRY_SECONDS", 86400, 60),
        restart_grace=_int("RESTART_GRACE_SECONDS", 30, 0),
        reveal_incoming_move=_bool("REVEAL_INCOMING_MOVE", False),
        rumble_lobby_timeout=_int("RUMBLE_LOBBY_SECONDS", 60, 10),
        rumble_entry_interval=_int("RUMBLE_ENTRY_SECONDS", 45, 5),
        rumble_turn_timeout=_int("RUMBLE_TURN_SECONDS", 45, 15),
        rumble_min_players=_int("RUMBLE_MIN_PLAYERS", 3, 2),
        rumble_max_players=_int("RUMBLE_MAX_PLAYERS", 12, 3),
        default_groups=_id_list("DEFAULT_GROUP_ID"),
        results_channel=_channel("RESULTS_CHANNEL_ID"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def set_settings(settings: Settings | None) -> None:
    """Replace the cached settings (used by tests and by bot.py)."""
    global _settings
    _settings = settings


__all__ = ["Settings", "load_settings", "get_settings", "set_settings",
           "normalize_database_url", "replace", "VALID_POLICIES"]
