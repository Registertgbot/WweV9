from datetime import timedelta

from models import utcnow
from services import turn_ping as tp


def test_mention_links_the_user_and_escapes_the_name():
    text = tp.mention(42, "<b>Raj</b>")
    assert 'tg://user?id=42' in text and "<b>Raj" not in text


def test_move_ping_text_has_turn_and_timer():
    text = tp.move_ping_text(7, "Sam", 45)
    assert "YOUR TURN" in text and "45s" in text and "tg://user?id=7" in text


def test_defend_ping_text():
    assert "ATTACKED" in tp.defend_ping_text(7, "Sam", None)
    assert "left" not in tp.defend_ping_text(7, "Sam", None)


def test_seconds_left():
    now = utcnow()
    assert tp.seconds_left(now + timedelta(seconds=30), now) == 30
    assert tp.seconds_left(now - timedelta(seconds=5), now) == 0
    assert tp.seconds_left(None) is None
