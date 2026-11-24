"""Rules S2b (cataphoric colon) and S6b (withdrawn items are not called), found on sitting 21030."""
from niederschrift import segment


def test_colon_ending_is_a_call_whatever_the_verb():
    t = "Wir sind damit am Schluss dieses Tagesordnungspunkts und steigen jetzt ein in Tagesordnungspunkt 2:"
    assert segment.is_call(t)
    assert segment.call_targets(t) == ["TOP 2"]


def test_colon_inside_a_turn_is_not_a_call():
    assert not segment.is_call("Zu Tagesordnungspunkt 2: Die Fragen werden schriftlich beantwortet.")


def test_withdrawn_items_are_not_called():
    t = "Tagesordnungspunkt 8, Tagesordnungspunkt 30b und Tagesordnungspunkt 39x werden abgesetzt."
    assert segment.call_targets(t) == []


def test_ordinary_call_still_works():
    assert segment.call_targets("Ich rufe auf den Tagesordnungspunkt 5:") == ["TOP 5"]
