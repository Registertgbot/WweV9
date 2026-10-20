"""Pure tournament logic: brackets, byes, schedules, placements, IST time parsing (no database, no Telegram)."""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

SIZES = (8, 16, 32)               # knockout: max players the owner can pick
LEAGUE_SIZES = (4, 6, 8, 10, 12, 16)   # league: everybody plays everybody, so keep the group small
LEAGUE, KNOCKOUT = "league", "knockout"
WIN_POINTS = 3
WALKOVER_MARGIN = 100             # HP margin credited for a walkover / owner-decided win
MIN_PLAYERS = 2                   # the owner may start with any 2..max players
DEFAULT_ROUND_GAP_MIN = 30        # minutes between the start of one round and the next
DEFAULT_MATCH_GAP_MIN = 0         # minutes between fixtures of the same round (0 = all at once)
DEFAULT_NOSHOW_SEC = 300          # how long the first mover has to act before losing by timeout
REST_SECONDS = 120                # minimum rest before a fixture starts once both players are known
REMINDER_MINUTES = 5              # "your match starts soon" notice
MAX_NAME_LEN = 40
RESCHEDULE_TTL_MIN = 20           # how long the opponent has to answer a reschedule request
RESCHEDULE_MIN_LEAD_MIN = 10      # no requests / acceptances once the match is closer than this
RESCHEDULE_MAX_SHIFT_H = 24       # a match can be moved at most this far past its current time
RESCHEDULE_MAX_ACCEPTED = 2       # accepted moves allowed per fixture
RESCHEDULE_CLASH_MIN = 10         # either fighter's other fixtures must be at least this far from the new time
ROUND_GAP_CHOICES = (15, 30, 45, 60, 90, 120)
MATCH_GAP_CHOICES = (0, 5, 10, 15)
NOSHOW_CHOICES = (180, 300, 600)

# fixture states
PENDING, SCHEDULED, LIVE, DONE = "pending", "scheduled", "live", "done"
# tournament states
REGISTRATION, ACTIVE, COMPLETED, CANCELLED = "registration", "active", "completed", "cancelled"
OPEN_STATES = (REGISTRATION, ACTIVE)
# reschedule request states
RQ_PENDING, RQ_ACCEPTED, RQ_DECLINED, RQ_EXPIRED, RQ_WITHDRAWN = "pending", "accepted", "declined", "expired", "withdrawn"

# player states
P_REGISTERED, P_WAITLIST, P_OUT, P_CHAMPION, P_DQ = "registered", "waitlist", "eliminated", "champion", "dq"


def sizes_for(fmt: str) -> tuple[int, ...]:
    return LEAGUE_SIZES if fmt == LEAGUE else SIZES


# ------------------------------------------------------------------------------ league

def round_robin(ids: list[int]) -> list[list[tuple[int, int]]]:
    """Circle method: matchdays in which everybody meets everybody exactly once (odd counts rest one player)."""
    players: list[int | None] = list(ids)
    if len(players) % 2:
        players.append(None)
    n = len(players)
    rounds = []
    for _ in range(n - 1):
        pairs = [(players[i], players[n - 1 - i]) for i in range(n // 2)
                 if players[i] is not None and players[n - 1 - i] is not None]
        rounds.append(pairs)
        players = [players[0], players[-1]] + players[1:-1]
    return rounds


def league_rounds_count(n: int) -> int:
    """Number of matchdays for ``n`` players."""
    return 0 if n < 2 else (n - 1 if n % 2 == 0 else n)


def playoff_size(n: int) -> int:
    """How many players reach the playoffs: top 4 (semis + final), top 2 (final) or none."""
    return 4 if n >= 4 else 2 if n == 3 else 0


def playoff_rounds(n: int) -> int:
    return {4: 2, 2: 1, 0: 0}[playoff_size(n)]


def round_label(round_no: int, league_rounds: int, total: int) -> str:
    if round_no <= league_rounds:
        return f"Matchday {round_no}"
    return "Final" if round_no == total else "Semifinal"


def round_short(round_no: int, league_rounds: int, total: int) -> str:
    if round_no <= league_rounds:
        return f"MD{round_no}"
    return "F" if round_no == total else "SF"


@dataclass
class Row:
    user_id: int
    seed: int
    played: int = 0
    wins: int = 0
    losses: int = 0
    points: int = 0
    margin: int = 0


def standings(entries: list[tuple[int, int]], results: list[tuple[int, int, int]]) -> list[Row]:
    """Points table. ``entries`` = (user_id, seed); ``results`` = (winner, loser, margin).

    Order: points, then HP margin (sum of winner HP - loser HP over the matches), then the draw seed.
    """
    rows = {uid: Row(uid, seed) for uid, seed in entries}
    for winner, loser, margin in results:
        w, l = rows.get(winner), rows.get(loser)
        if w:
            w.played += 1
            w.wins += 1
            w.points += WIN_POINTS
            w.margin += margin
        if l:
            l.played += 1
            l.losses += 1
            l.margin -= margin
    return sorted(rows.values(), key=lambda r: (-r.points, -r.margin, r.seed, r.user_id))


# ----------------------------------------------------------------------------- bracket

def bracket_size(n: int) -> int:
    """Smallest power of two that holds ``n`` players (at least 2)."""
    return 1 << max(1, math.ceil(math.log2(max(n, 2))))


def total_rounds(size: int) -> int:
    return int(math.log2(size))


def round_name(round_no: int, total: int) -> str:
    left = total - round_no           # 0 = final
    if left == 0:
        return "Final"
    if left == 1:
        return "Semifinal"
    if left == 2:
        return "Quarterfinal"
    return f"Round of {2 ** (left + 1)}"


def seed_order(size: int) -> list[int]:
    """Seeds in bracket order: slot i plays seeds[2i] vs seeds[2i+1]; top seeds meet as late as possible."""
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        order = [x for s in order for x in (s, n + 1 - s)]
    return order


def first_round_pairs(players_by_seed: list[int], size: int) -> list[tuple[int | None, int | None]]:
    """Round-1 pairings. ``players_by_seed[0]`` is seed 1. Seeds beyond the player count become byes (None)."""
    order = seed_order(size)
    pick = lambda seed: players_by_seed[seed - 1] if seed <= len(players_by_seed) else None
    return [(pick(order[2 * i]), pick(order[2 * i + 1])) for i in range(size // 2)]


def draw(player_ids: list[int], rng: random.Random | None = None) -> list[int]:
    """Random draw: returns the players in seed order."""
    ids = list(player_ids)
    (rng or random).shuffle(ids)
    return ids


def next_slot(round_no: int, slot: int) -> tuple[int, int, int]:
    """Where the winner of (round, slot) goes: (next round, next slot, position 0 = p1 / 1 = p2)."""
    return round_no + 1, slot // 2, slot % 2


def fixture_count(size: int, round_no: int) -> int:
    return size // (2 ** round_no)


def placement_label(out_round: int | None, total: int, champion: bool = False) -> str:
    if champion:
        return "Champion"
    if out_round is None:
        return "—"
    if out_round == total:
        return "Runner-up"
    return f"Top {2 ** (total - out_round + 1)}"


def resolve_unavailable(p1_ok: bool, p2_ok: bool) -> int | None:
    """Which side wins by walkover: 1, 2, or None when both are available / both are gone (caller decides)."""
    if p1_ok and p2_ok:
        return None
    if p1_ok:
        return 1
    if p2_ok:
        return 2
    return None


# --------------------------------------------------------------------------- scheduling

def fixture_time(base: datetime, round_no: int, slot: int, round_gap_min: int, match_gap_min: int) -> datetime:
    return base + timedelta(minutes=(round_no - 1) * round_gap_min + slot * match_gap_min)


# ------------------------------------------------------------------------ reschedule rules

def check_reschedule_time(now: datetime, old: datetime, new: datetime, accepted_so_far: int) -> str | None:
    """Why a requested move is not allowed (None = fine). Pure: the engine adds the per-player clash check."""
    lead = timedelta(minutes=RESCHEDULE_MIN_LEAD_MIN)
    if accepted_so_far >= RESCHEDULE_MAX_ACCEPTED:
        return f"🚫 This match was already moved {RESCHEDULE_MAX_ACCEPTED} times — ask the owner."
    if old - now < lead:
        return f"⏰ Too late — requests close {RESCHEDULE_MIN_LEAD_MIN} min before the match."
    if new < now + lead:
        return f"⏰ Pick a time at least {RESCHEDULE_MIN_LEAD_MIN} min from now (IST)."
    if abs((new - old).total_seconds()) < 60:
        return "That is the current match time already."
    if new > old + timedelta(hours=RESCHEDULE_MAX_SHIFT_H):
        return f"📅 You can move a match by at most {RESCHEDULE_MAX_SHIFT_H} hours."
    return None


def clashes(new: datetime, others: list[datetime | None]) -> bool:
    """True when ``new`` is within the clash window of any of a player's other fixture times."""
    window = timedelta(minutes=RESCHEDULE_CLASH_MIN)
    return any(o is not None and abs(o - new) < window for o in others)


# ----------------------------------------------------------------------- IST parsing

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_REL = re.compile(r"^(?:\+|in\s+)\s*(\d{1,4})\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)$")
_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$")


def _clock(token: str) -> tuple[int, int] | None:
    m = _CLOCK.match(token)
    if not m:
        return None
    hour, minute, ap = int(m[1]), int(m[2] or 0), m[3]
    if m[2] is None and ap is None:
        return None                       # a bare "9" is ambiguous
    if ap:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ap == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _date(tokens: list[str], today: datetime) -> tuple[int, int, int] | None:
    """(year, month, day) from 'dd-mm', 'dd/mm/yyyy', 'yyyy-mm-dd', '20 oct' or 'oct 20'."""
    year = today.year
    if len(tokens) == 1:
        parts = re.split(r"[-/.]", tokens[0])
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            return year, nums[1], nums[0]
        if len(nums) == 3:
            return (nums[0], nums[1], nums[2]) if nums[0] > 31 else (nums[2] if nums[2] > 99 else 2000 + nums[2], nums[1], nums[0])
        return None
    if len(tokens) == 2:
        a, b = tokens
        a, b = re.sub(r"(?<=\d)(st|nd|rd|th)$", "", a), re.sub(r"(?<=\d)(st|nd|rd|th)$", "", b)
        if a.isdigit() and b[:3] in _MONTHS:
            return year, _MONTHS[b[:3]], int(a)
        if b.isdigit() and a[:3] in _MONTHS:
            return year, _MONTHS[a[:3]], int(b)
    return None


def parse_ist(text: str, now: datetime) -> datetime | None:
    """Parse owner input as an India Standard Time moment and return it in UTC (None if not understood).

    Accepted: ``21:30`` / ``9:30pm`` (today, or tomorrow if already past), ``today 21:30``, ``tomorrow 9pm``,
    ``20-10 21:30``, ``20/10/2026 9:30 pm``, ``2026-10-20 21:30``, ``20 oct 9pm``, ``+30m``, ``in 2 hours``.
    """
    raw = (text or "").strip().lower().replace(",", " ")
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return None
    now_ist = now.astimezone(IST)
    rel = _REL.match(raw)
    if rel:
        amount = int(rel[1])
        delta = timedelta(hours=amount) if rel[2].startswith("h") else timedelta(minutes=amount)
        return (now + delta).replace(second=0, microsecond=0)
    tokens = raw.split(" ")
    if len(tokens) >= 2 and tokens[-1] in ("am", "pm"):
        clock_token, rest = tokens[-2] + tokens[-1], tokens[:-2]
    else:
        clock_token, rest = tokens[-1], tokens[:-1]
    clock = _clock(clock_token)
    if clock is None:
        return None
    hour, minute = clock
    try:
        if not rest or rest == ["today"]:
            day = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if not rest and day <= now_ist:
                day += timedelta(days=1)
        elif rest in (["tomorrow"], ["tmrw"], ["tom"]):
            day = (now_ist + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            ymd = _date(rest, now_ist)
            if ymd is None:
                return None
            day = datetime(ymd[0], ymd[1], ymd[2], hour, minute, tzinfo=IST)
    except ValueError:
        return None
    return day.astimezone(timezone.utc)


def fmt_ist(dt: datetime | None) -> str:
    """'Sun 18 Oct, 9:30 PM IST'."""
    if dt is None:
        return "TBD"
    t = dt.astimezone(IST)
    hour12 = t.hour % 12 or 12
    return f"{t:%a %d %b}, {hour12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'} IST"


def fmt_ist_short(dt: datetime | None) -> str:
    """'9:30 PM' (used in the bracket where the day is clear from context)."""
    if dt is None:
        return "TBD"
    t = dt.astimezone(IST)
    return f"{t:%d %b} {t.hour % 12 or 12}:{t.minute:02d}{'a' if t.hour < 12 else 'p'}"


def fmt_ist_compact(dt: datetime | None, now: datetime | None = None) -> str:
    """'9:30 PM' when the day is today (IST), otherwise '19 Oct 9:30 PM' (short enough for fixture lines)."""
    if dt is None:
        return "TBD"
    t = dt.astimezone(IST)
    today = (now or datetime.now(timezone.utc)).astimezone(IST).date()
    clock = f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"
    return clock if t.date() == today else f"{t:%d %b} {clock}"


def clean_name(raw: str | None) -> str | None:
    """Tournament name from owner input: single line, 2..MAX_NAME_LEN chars."""
    name = re.sub(r"\s+", " ", (raw or "").strip())
    if len(name) < 2:
        return None
    return name[:MAX_NAME_LEN]
