from src import diff


def r(danchi_id, room_id):
    return {"key": f"{danchi_id}:{room_id}", "danchi_id": danchi_id, "room_name": room_id}


def keys(rooms):
    return sorted(x["key"] for x in rooms)


def test_new_and_gone():
    prev = {x["key"]: x for x in [r("A", "1"), r("A", "2")]}
    cur = diff.merge_current(prev, {"A": [r("A", "2"), r("A", "3")]})
    d = diff.compute(prev, cur)
    assert keys(d.new) == ["A:3"]
    assert keys(d.gone) == ["A:1"]


def test_no_change():
    prev = {x["key"]: x for x in [r("A", "1")]}
    cur = diff.merge_current(prev, {"A": [r("A", "1")]})
    d = diff.compute(prev, cur)
    assert d.new == [] and d.gone == []


def test_failed_danchi_keeps_previous_rooms():
    """取得に失敗した団地を「全部埋まった」と判定しない。"""
    prev = {x["key"]: x for x in [r("A", "1"), r("B", "9")]}
    cur = diff.merge_current(prev, {"A": None, "B": [r("B", "9"), r("B", "10")]})
    assert sorted(cur) == ["A:1", "B:10", "B:9"]
    d = diff.compute(prev, cur)
    assert keys(d.new) == ["B:10"]
    assert d.gone == []


def test_same_room_number_in_different_danchi_is_distinct():
    prev = {x["key"]: x for x in [r("A", "1")]}
    cur = diff.merge_current(prev, {"A": [r("A", "1")], "B": [r("B", "1")]})
    assert keys(diff.compute(prev, cur).new) == ["B:1"]


def test_removed_danchi_is_not_reported_as_gone():
    prev = {x["key"]: x for x in [r("A", "1"), r("OLD", "1")]}
    cur = diff.merge_current(prev, {"A": [r("A", "1")]})
    d = diff.compute(prev, cur, watched_danchi={"A"})
    assert d.gone == []
    assert "OLD:1" not in cur
