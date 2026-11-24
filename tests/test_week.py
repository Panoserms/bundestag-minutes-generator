"""One file for a whole sitting week: every planned item once, in the printed order, with the content of whichever
sitting handled it. Runs on the development week of sittings 98 to 100 (structure only, no language model)."""
import pytest

from niederschrift import week

SIDS = ["21098", "21099", "21100"]


@pytest.fixture(scope="module")
def plan():
    return week.build_plan(SIDS)


def entry(plan, printed):
    return next(e for e in plan["entries"] if e["printed"] and e["printed"]["id"] == printed)


def notes(e):
    return " ".join(e["notes"])


def test_planned_items_run_from_first_to_last_in_printed_order(plan):
    assert [e["printed"]["id"] for e in plan["entries"] if e["printed"]] == [f"TOP {n}" for n in range(1, 39)]


def test_every_item_with_content_appears_exactly_once(plan):
    expected = {(s, i) for s in SIDS for i, it in plan["structures"][s]["items"].items() if week.has_content(it)}
    shown = [e["key"] for e in plan["entries"] if e["key"] and not e["merged_into"]]
    assert len(shown) == len(set(shown))
    assert expected <= set(shown)


def test_item_moved_to_another_day_keeps_its_place_and_says_so(plan):
    e = entry(plan, "TOP 3")                       # planned for Wednesday, handled on Thursday as TOP 16
    assert e["key"] == ("21099", "TOP 16")
    assert "99. Sitzung" in notes(e) and "als TOP 16" in notes(e)
    assert entry(plan, "TOP 36")["key"] == ("21098", "TOP 5")      # planned for Friday, handled on Wednesday


def test_renumbered_item_names_the_number_it_was_called_under(plan):
    e = entry(plan, "TOP 13")
    assert e["key"] == ("21099", "TOP 14")
    assert "als TOP 14 aufgerufen" in notes(e)


def test_two_planned_items_handled_as_one_are_reported_once(plan):
    a, b = entry(plan, "TOP 23"), entry(plan, "TOP 20")            # 99th sitting, TOP 27: a) is planned item 23
    assert a["key"] == b["key"] == ("21099", "TOP 27")
    assert a["merged_into"] is None and b["merged_into"] == "TOP 23"
    assert "TOP 23" in notes(b)


def test_withdrawn_and_never_called_items_stay_in_the_list(plan):
    for p in ("TOP 27", "TOP 32", "TOP 35"):
        assert "abgesetzt" in notes(entry(plan, p))
    e = entry(plan, "TOP 19")
    assert e["key"] is None and "kein Aufruf" in notes(e)


def test_unplanned_joint_partner_follows_its_planned_anchor(plan):
    ids = [(e["printed"]["id"] if e["printed"] else e["key"]) for e in plan["entries"]]
    assert ids[ids.index("TOP 7") + 1] == ("21099", "ZP 2")        # called together with TOP 7
    assert ids[ids.index("TOP 37") + 1] == ("21099", "ZP 4")


def test_other_unplanned_items_close_the_file_in_the_order_they_were_called(plan):
    extra = [e["key"] for e in plan["entries"] if e["section"] == "rest"]
    assert extra[0] == ("21098", "ZP 1") and ("21100", "ZP 12") == extra[-1]
    first = next(i for i, e in enumerate(plan["entries"]) if e["section"] == "rest")
    assert all(e["section"] == "rest" for e in plan["entries"][first:])


def test_an_incomplete_week_is_refused():
    with pytest.raises(ValueError, match="99"):
        week.build_plan(["21098", "21100"])


def test_notes_use_the_planned_numbers(plan):
    e = entry(plan, "TOP 16")                      # called as TOP 17 right after TOP 16 of that day = planned TOP 3
    assert "nach TOP 3 behandelt" in notes(e)


def test_an_unplanned_item_is_not_confused_with_the_planned_item_of_the_same_number(plan):
    assert week.own_number(("21098", "TOP 3")) == "TOP 3 (98. Sitzung)"
    assert week.own_number(("21099", "ZP 6")) == "ZP 6"


def test_every_speech_of_the_week_stands_in_the_file_once(plan):
    """Which paragraphs belong to a shown item; jointly called items report their debate once, under the owner."""
    shown = {e["key"] for e in plan["entries"] if e["key"] and not e["merged_into"]}
    for s in SIDS:
        for i, it in plan["structures"][s]["items"].items():
            if it["units"] and any(plan["sittings"][s]["units"][u]["rede"] for u in it["units"]):
                assert (s, it["debate_owner"]) in shown, (s, i)
