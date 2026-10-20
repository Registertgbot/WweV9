import random
from datetime import datetime, timezone

from services import tour_rules as tr

NOW = datetime(2026, 10, 18, 7, 30, tzinfo=timezone.utc)      # = 13:00 IST on Sun 18 Oct 2026


def ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=tr.IST).astimezone(timezone.utc)


def test_bracket_size_and_round_names():
    assert [tr.bracket_size(n) for n in (2, 3, 4, 5, 8, 9, 16, 17, 32)] == [2, 4, 4, 8, 8, 16, 16, 32, 32]
    assert tr.total_rounds(32) == 5
    assert [tr.round_name(r, 5) for r in range(1, 6)] == ["Round of 32", "Round of 16", "Quarterfinal", "Semifinal", "Final"]
    assert [tr.round_name(r, 3) for r in range(1, 4)] == ["Quarterfinal", "Semifinal", "Final"]
    assert tr.round_name(1, 1) == "Final"


def test_seed_order_is_standard():
    assert tr.seed_order(2) == [1, 2]
    assert tr.seed_order(4) == [1, 4, 2, 3]
    assert tr.seed_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]


def test_first_round_pairs_give_byes_to_top_seeds_and_never_double_bye():
    for n in range(2, 33):
        size = tr.bracket_size(n)
        ids = list(range(100, 100 + n))
        pairs = tr.first_round_pairs(ids, size)
        assert len(pairs) == size // 2
        flat = [p for pair in pairs for p in pair if p is not None]
        assert sorted(flat) == sorted(ids)                      # everybody appears exactly once
        assert all(not (a is None and b is None) for a, b in pairs)
        byes = [pair for pair in pairs if None in pair]
        assert len(byes) == size - n
        if byes:                                                 # byes go to the top seeds
            assert {p for pair in byes for p in pair if p is not None} == set(ids[:size - n])


def test_draw_is_a_permutation_and_deterministic_with_rng():
    ids = [1, 2, 3, 4, 5, 6]
    assert sorted(tr.draw(ids, random.Random(1))) == ids
    assert tr.draw(ids, random.Random(1)) == tr.draw(ids, random.Random(1))


def test_next_slot_and_counts():
    assert tr.next_slot(1, 0) == (2, 0, 0) and tr.next_slot(1, 1) == (2, 0, 1)
    assert tr.next_slot(2, 3) == (3, 1, 1)
    assert [tr.fixture_count(16, r) for r in (1, 2, 3, 4)] == [8, 4, 2, 1]


def test_placement_labels():
    assert tr.placement_label(None, 4, champion=True) == "Champion"
    assert tr.placement_label(4, 4) == "Runner-up"
    assert tr.placement_label(3, 4) == "Top 4"
    assert tr.placement_label(2, 4) == "Top 8"
    assert tr.placement_label(1, 4) == "Top 16"


def test_resolve_unavailable():
    assert tr.resolve_unavailable(True, True) is None
    assert tr.resolve_unavailable(True, False) == 1
    assert tr.resolve_unavailable(False, True) == 2
    assert tr.resolve_unavailable(False, False) is None


def test_fixture_time_schedule():
    base = ist(2026, 10, 18, 21, 0)
    assert tr.fixture_time(base, 1, 0, 30, 0) == base
    assert tr.fixture_time(base, 2, 0, 30, 0) == ist(2026, 10, 18, 21, 30)
    assert tr.fixture_time(base, 2, 3, 30, 5) == ist(2026, 10, 18, 21, 45)


def test_parse_ist_time_only_rolls_to_tomorrow_when_past():
    assert tr.parse_ist("21:30", NOW) == ist(2026, 10, 18, 21, 30)
    assert tr.parse_ist("9:30pm", NOW) == ist(2026, 10, 18, 21, 30)
    assert tr.parse_ist("9:30 PM", NOW) == ist(2026, 10, 18, 21, 30)
    assert tr.parse_ist("09:00", NOW) == ist(2026, 10, 19, 9, 0)        # 09:00 IST already passed today
    assert tr.parse_ist("9pm", NOW) == ist(2026, 10, 18, 21, 0)
    assert tr.parse_ist("12am", NOW) == ist(2026, 10, 19, 0, 0)
    assert tr.parse_ist("12:30 pm", NOW) == ist(2026, 10, 19, 12, 30)   # 12:30 IST passed (now 13:00)


def test_parse_ist_dates_and_keywords():
    assert tr.parse_ist("today 21:30", NOW) == ist(2026, 10, 18, 21, 30)
    assert tr.parse_ist("tomorrow 9pm", NOW) == ist(2026, 10, 19, 21, 0)
    assert tr.parse_ist("20-10 21:30", NOW) == ist(2026, 10, 20, 21, 30)
    assert tr.parse_ist("20/10/2026 9:30 pm", NOW) == ist(2026, 10, 20, 21, 30)
    assert tr.parse_ist("2026-10-20 21:30", NOW) == ist(2026, 10, 20, 21, 30)
    assert tr.parse_ist("20 oct 9pm", NOW) == ist(2026, 10, 20, 21, 0)
    assert tr.parse_ist("oct 20 9pm", NOW) == ist(2026, 10, 20, 21, 0)
    assert tr.parse_ist("5-11-26 7:15pm", NOW) == ist(2026, 11, 5, 19, 15)


def test_parse_ist_relative():
    assert tr.parse_ist("+30m", NOW) == datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)
    assert tr.parse_ist("in 2 hours", NOW) == datetime(2026, 10, 18, 9, 30, tzinfo=timezone.utc)
    assert tr.parse_ist("+1h", NOW) == datetime(2026, 10, 18, 8, 30, tzinfo=timezone.utc)


def test_parse_ist_rejects_garbage():
    for bad in ("", "soon", "9", "25:00", "13pm", "32-13 10:00", "tomorrow", "10:75", "yesterday 9pm", "31-02 10:00"):
        assert tr.parse_ist(bad, NOW) is None, bad


def test_fmt_ist():
    assert tr.fmt_ist(ist(2026, 10, 18, 21, 30)) == "Sun 18 Oct, 9:30 PM IST"
    assert tr.fmt_ist(ist(2026, 10, 19, 0, 5)) == "Mon 19 Oct, 12:05 AM IST"
    assert tr.fmt_ist(None) == "TBD"
    assert tr.fmt_ist_short(ist(2026, 10, 18, 21, 30)) == "18 Oct 9:30p"


def test_clean_name():
    assert tr.clean_name("  Summer   Slam  ") == "Summer Slam"
    assert tr.clean_name("x") is None and tr.clean_name(None) is None
    assert len(tr.clean_name("a" * 100)) == tr.MAX_NAME_LEN


def test_round_robin_everyone_meets_everyone_once():
    from itertools import combinations
    for n in range(2, 17):
        ids = list(range(10, 10 + n))
        rounds = tr.round_robin(ids)
        assert len(rounds) == tr.league_rounds_count(n)
        seen = set()
        for pairs in rounds:
            in_round = [p for pair in pairs for p in pair]
            assert len(in_round) == len(set(in_round))              # nobody plays twice on a matchday
            for a, b in pairs:
                key = frozenset((a, b))
                assert key not in seen
                seen.add(key)
        assert seen == {frozenset(c) for c in combinations(ids, 2)}
        assert sum(len(p) for p in rounds) == n * (n - 1) // 2
    assert tr.league_rounds_count(10) == 9 and tr.league_rounds_count(5) == 5 and tr.league_rounds_count(1) == 0


def test_playoff_sizes_and_labels():
    assert [tr.playoff_size(n) for n in (2, 3, 4, 10, 16)] == [0, 2, 4, 4, 4]
    assert [tr.playoff_rounds(n) for n in (2, 3, 4, 10)] == [0, 1, 2, 2]
    # 10 players: 9 matchdays, semifinal = round 10, final = round 11
    assert tr.round_label(9, 9, 11) == "Matchday 9" and tr.round_label(10, 9, 11) == "Semifinal"
    assert tr.round_label(11, 9, 11) == "Final" and tr.round_short(3, 9, 11) == "MD3" and tr.round_short(10, 9, 11) == "SF"
    assert tr.sizes_for("league") == tr.LEAGUE_SIZES and tr.sizes_for("knockout") == tr.SIZES


def test_standings_points_then_margin_then_seed():
    entries = [(1, 1), (2, 2), (3, 3), (4, 4)]
    results = [(1, 2, 60), (3, 4, 20), (1, 3, 10), (2, 4, 90), (4, 1, 30), (2, 3, 40)]
    table = tr.standings(entries, results)
    # wins: 1->2, 2->2, 3->1, 4->1 ; margins: 1: 60+10-30=40, 2: -60+90+40=70, 3: 20-10-40=-30, 4: -20-90+30=-80
    assert [r.user_id for r in table] == [2, 1, 3, 4]
    assert table[0].points == 6 and table[0].wins == 2 and table[0].losses == 1 and table[0].played == 3
    assert table[0].margin == 70 and table[3].margin == -80
    # equal points and margin -> the earlier draw seed ranks higher
    tie = tr.standings([(7, 2), (8, 1)], [(7, 8, 50), (8, 7, 50)])
    assert [r.user_id for r in tie] == [8, 7]
    assert tr.standings([(1, 1)], [])[0].points == 0
