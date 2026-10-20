"""Pure Royal Rumble rules (no database, no Telegram) so they are easy to test."""
from __future__ import annotations

import random

RUMBLE_HP = 150          # more HP than a 1v1 (100) so the Rumble lasts longer
ADRENALINE_HEAL = 25     # an eliminator recovers this much HP
START_RING = 2           # wrestlers in the ring at the opening bell
AFK_LIMIT = 2            # this many skipped turns in a row = eliminated for inactivity


def heal(hp: int) -> int:
    """HP after the adrenaline rush of an elimination (never above the cap)."""
    return min(RUMBLE_HP, hp + ADRENALINE_HEAL)


def shuffle_order(user_ids, rng: random.Random) -> list[int]:
    """Random entry order (#1 enters first)."""
    order = list(user_ids)
    rng.shuffle(order)
    return order


def next_actor(ring: list[tuple[int, int]], last_order: int) -> int | None:
    """``ring`` = [(entry_order, user_id)] of everyone currently in the ring.

    Turns go round the ring in entry order: the first wrestler whose number is above the last
    actor's, wrapping to the lowest number. Works even if the last actor was just eliminated.
    """
    if not ring:
        return None
    ordered = sorted(ring)
    for order, user_id in ordered:
        if order > last_order:
            return user_id
    return ordered[0][1]


def hp_bar(hp: int, width: int = 10) -> str:
    filled = round(width * max(hp, 0) / RUMBLE_HP)
    return "▰" * filled + "▱" * (width - filled)
