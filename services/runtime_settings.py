"""Owner-adjustable settings stored in PostgreSQL, falling back to environment defaults."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from config import VALID_POLICIES, get_settings
from models import BotSetting

# key -> (type, min, max) / (choice, options) / (bool,)
SPECS: dict[str, tuple] = {
    "challenge_timeout": ("int", 10, 3600),
    "turn_timeout": ("int", 15, 3600),
    "reversal_timeout": ("int", 5, 120),
    "timeout_policy": ("choice", VALID_POLICIES),
    "maintenance_mode": ("bool",),
    "matches_enabled": ("bool",),
}


def default_value(key: str):
    s = get_settings()
    return {
        "challenge_timeout": s.challenge_timeout,
        "turn_timeout": s.turn_timeout,
        "reversal_timeout": s.reversal_timeout,
        "timeout_policy": s.timeout_policy,
        "maintenance_mode": False,
        "matches_enabled": True,
    }[key]


def parse_value(key: str, raw: str):
    """Validate and convert owner input. Raises ValueError with a friendly message."""
    spec = SPECS.get(key)
    if spec is None:
        raise ValueError(f"Unknown setting. Valid keys: {', '.join(SPECS)}")
    raw = raw.strip().lower()
    kind = spec[0]
    if kind == "int":
        try:
            value = int(raw)
        except ValueError:
            raise ValueError("Value must be a whole number of seconds.") from None
        if not spec[1] <= value <= spec[2]:
            raise ValueError(f"Value must be between {spec[1]} and {spec[2]}.")
        return value
    if kind == "choice":
        if raw not in spec[1]:
            raise ValueError(f"Value must be one of: {', '.join(spec[1])}")
        return raw
    if raw in ("on", "true", "1", "yes"):
        return True
    if raw in ("off", "false", "0", "no"):
        return False
    raise ValueError("Value must be on or off.")


async def get_value(session: AsyncSession, key: str):
    row = await session.get(BotSetting, key)
    if row is None:
        return default_value(key)
    try:
        return parse_value(key, row.value)
    except ValueError:
        return default_value(key)


async def get_all(session: AsyncSession) -> dict[str, object]:
    return {key: await get_value(session, key) for key in SPECS}


async def set_value(session: AsyncSession, key: str, raw: str):
    value = parse_value(key, raw)
    stored = ("on" if value else "off") if isinstance(value, bool) else str(value)
    row = await session.get(BotSetting, key)
    if row is None:
        session.add(BotSetting(key=key, value=stored))
    else:
        row.value = stored
    await session.flush()
    return value
