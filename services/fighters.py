"""Wrestler pictures: any image dropped into assets/wrestlers can be picked by a player for his cards."""
from __future__ import annotations

from pathlib import Path

from database import transaction
from models import UserFighter

DIR = Path(__file__).resolve().parent.parent / "assets" / "wrestlers"
EXT = (".png", ".jpg", ".jpeg", ".webp")


def stems() -> list[str]:
    return sorted(p.stem for p in DIR.glob("*") if p.suffix.lower() in EXT and len(p.stem) <= 40) if DIR.is_dir() else []


def label(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").title()


def image_bytes(stem: str) -> bytes | None:
    for p in DIR.glob(stem + ".*"):
        if p.suffix.lower() in EXT:
            return p.read_bytes()
    return None


async def get(user_id: int) -> str | None:
    async with transaction() as s:
        row = await s.get(UserFighter, user_id)
        return row.fighter if row else None


async def choose(user_id: int, stem: str | None) -> None:
    async with transaction() as s:
        row = await s.get(UserFighter, user_id)
        if stem is None:
            if row is not None:
                await s.delete(row)
        elif row is None:
            s.add(UserFighter(user_id=user_id, fighter=stem))
        else:
            row.fighter = stem
