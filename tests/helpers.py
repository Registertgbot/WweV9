import random

from database import transaction
from services import match_engine as eng
from services import rules
from services.match_engine import Identity
from services.rules import Move

A = Identity(1001, "alice", "Alice")
B = Identity(1002, "bob", "Bob")
C = Identity(1003, "carol", "Carol")      # spectator
CHAT = -100500


async def register(*idents, dm=True):
    async with transaction() as s:
        for i in idents:
            await eng.upsert_user(s, i, dm_started=dm)


async def live_match(seed=7):
    await register(A, B, C)
    async with transaction() as s:
        challenge = await eng.create_challenge(s, A, CHAT, "Ring")
    async with transaction() as s:
        res = await eng.accept_challenge(s, challenge.match.id, B, CHAT, rng=random.Random(seed))
    assert res.ok and res.match.status == "active", res.error
    return res.match


def roles(snap):
    attacker = snap.active_player_id
    return attacker, snap.other(attacker).user_id


async def attack(match_id, user_id, turn, move, now=None):
    async with transaction() as s:
        return await eng.submit_attack(s, match_id, user_id, turn, move, now=now)


class StubRng:
    """Deterministic stand-in for random.Random: fixed ``random()`` roll and a forced Wild Draw pick."""

    def __init__(self, roll: float = 0.0, pick: Move | None = None):
        self.roll, self.pick = roll, pick

    def random(self) -> float:
        return self.roll

    def choices(self, population, weights=None, k=1):
        chosen = self.pick if self.pick in population else population[0]
        return [chosen] * k


WIN = StubRng(roll=0.0)      # an attempted Reversal succeeds
FAIL = StubRng(roll=1.0)     # forces the failure branch (never happens in real play: REVERSAL_CHANCE is 1.0)


async def respond(match_id, user_id, turn, reverse, now=None, rng=None):
    async with transaction() as s:
        return await eng.submit_response(s, match_id, user_id, turn, use_reversal=reverse, now=now, rng=rng)


async def wild(match_id, user_id, turn, now=None, rng=None):
    async with transaction() as s:
        return await eng.submit_wild_draw(s, match_id, user_id, turn, now=now, rng=rng)


async def snap_of(match_id):
    async with transaction() as s:
        return await eng.get_snapshot(s, match_id)


async def play_to_knockout(match_id):
    """Always pick the strongest legal attack and never reverse, until someone is knocked out."""
    order = [m.value for m in rules.ATTACKS]
    for _ in range(40):
        snap = await snap_of(match_id)
        if snap.status != "active":
            return snap
        attacker, defender = roles(snap)
        for move in order:
            res = await attack(match_id, attacker, snap.turn_number, move)
            if res.ok:
                if res.kind == "attack_declared":
                    res = await respond(match_id, defender, snap.turn_number, False)
                    assert res.ok, res.error
                break
        else:
            raise AssertionError("no legal attack found")
    raise AssertionError("match did not finish")
