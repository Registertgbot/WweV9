from services import fancy, tag_rules as tr


def test_fonts_keep_length_of_letters_and_digits():
    assert fancy.sans_bold("TAG 2v2") == "𝗧𝗔𝗚 𝟮𝘃𝟮"
    assert fancy.script("Lions") == "𝓛𝓲𝓸𝓷𝓼"
    assert fancy.double("RC") == "ℝℂ"           # letters that live in the Letterlike block
    assert fancy.small_caps("hp") == "ʜᴘ"
    assert fancy.sans_bold("!? ") == "!? "        # punctuation untouched


def test_tag_rules():
    assert tr.other_team(1) == 2 and tr.other_team(2) == 1
    assert tr.rest(98, 100) == 100 and tr.rest(50, 100) == 50 + tr.APRON_HEAL
    assert tr.TEAM_EMOJI == {1: "🦁", 2: "🐯"}


def test_valid_teams_and_compensation():
    assert tr.valid_teams(2, 2) and tr.valid_teams(3, 3) and tr.valid_teams(3, 2) and tr.valid_teams(4, 3)
    assert not tr.valid_teams(2, 4) and not tr.valid_teams(1, 2) and not tr.valid_teams(1, 1)
    assert tr.compensation(2, 2) == 0 and tr.compensation(3, 2) == 1
