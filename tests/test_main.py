"""1回分の監視（取得 → 絞り込み → 差分 → 通知内容）をURにアクセスせずに確かめる。"""

import json

from src import main
from tests.helpers import FakeResponse, FakeSession, api_room

CONFIG = {
    "area_name": "新浦安エリア",
    "area_path": "kanto/chiba",
    "filters": {"max_total_rent": 200000, "min_area": None, "min_layout": None, "min_floor": None},
    "request": {"interval_seconds": 0, "max_retries": 0},
    "error_alert_after": 3,
    "danchi": [
        {"id": "30_512_0", "name": "望海の街", "access": ["新浦安 徒歩22分"]},
        {"id": "30_312_0", "name": "美浜西", "access": ["新浦安 徒歩6分"]},
    ],
}


def no_sleep(_):
    pass


def session_with(rooms_512, rooms_312):
    def resp(rooms):
        if isinstance(rooms, FakeResponse):
            return rooms
        return FakeResponse(rooms if rooms else "null")

    return FakeSession({("512", 0): resp(rooms_512), ("312", 0): resp(rooms_312)})


def roundtrip(state):
    return json.loads(main.dump_state(state))


def message_text(msg):
    return json.dumps(msg, ensure_ascii=False)


def test_first_run_sends_full_list_once_then_only_diffs():
    s1 = session_with([api_room("A1", name="1号棟101号室")], [])
    state, msgs = main.run(CONFIG, {}, session=s1, sleep=no_sleep)
    assert len(msgs) == 1
    assert "現在の空室 1件" in message_text(msgs[0])
    assert state["initialized"] is True

    # 2回目：変化なし → 通知なし
    state = roundtrip(state)
    s2 = session_with([api_room("A1", name="1号棟101号室")], [])
    state2, msgs = main.run(CONFIG, state, session=s2, sleep=no_sleep)
    assert msgs == []
    assert main.dump_state(state2) == main.dump_state(state)


def test_new_vacancy_is_notified_with_gone_list():
    state = roundtrip({"initialized": True, "vacancies": {}, "failures": {}})
    state, _ = main.run(CONFIG, state, session=session_with([api_room("A1", name="1号棟101号室")], []), sleep=no_sleep)
    state = roundtrip(state)

    s = session_with([], [api_room("B1", name="2号棟202号室", danchi="312")])
    _, msgs = main.run(CONFIG, state, session=s, sleep=no_sleep)
    assert len(msgs) == 1
    text = message_text(msgs[0])
    assert "新着の空室 1件" in text
    assert "美浜西　2号棟202号室" in text
    assert "¥150,000 + ¥5,000 = *¥155,000*" in text
    assert "埋まった空室（1件）: 望海の街　1号棟101号室" in text


def test_only_gone_does_not_notify():
    state = roundtrip(
        main.run(CONFIG, {"initialized": True}, session=session_with([api_room("A1")], []), sleep=no_sleep)[0]
    )
    _, msgs = main.run(CONFIG, state, session=session_with([], []), sleep=no_sleep)
    assert msgs == []


def test_rooms_over_budget_are_ignored():
    s = session_with([api_room("A1", rent="196,000円", commonfee="5,000円")], [])
    state, msgs = main.run(CONFIG, {"initialized": True}, session=s, sleep=no_sleep)
    assert msgs == []
    assert state["vacancies"] == {}


def test_failure_keeps_previous_and_alerts_once_after_three():
    state = roundtrip(
        main.run(CONFIG, {"initialized": True}, session=session_with([api_room("A1")], []), sleep=no_sleep)[0]
    )
    alerts = []
    for _ in range(5):
        s = session_with(FakeResponse("", status=500), [])
        state, msgs = main.run(CONFIG, state, session=s, sleep=no_sleep)
        state = roundtrip(state)
        assert "30_512_0:A1" in state["vacancies"]  # 埋まったと誤判定しない
        alerts.append([m for m in msgs if "⚠️" in m["text"]])
    assert [len(a) for a in alerts] == [0, 0, 1, 0, 0]
    assert "望海の街" in message_text(alerts[2][0])

    # 復旧したら失敗記録が消え、次にまた3回失敗すれば再び通知される
    state, msgs = main.run(CONFIG, state, session=session_with([api_room("A1")], []), sleep=no_sleep)
    assert msgs == [] and state["failures"] == {}


def test_first_run_with_everything_failing_does_not_claim_zero():
    s = session_with(FakeResponse("", status=500), FakeResponse("", status=500))
    state, msgs = main.run(CONFIG, {}, session=s, sleep=no_sleep)
    assert msgs == []
    assert state["initialized"] is False


def test_many_rooms_fit_in_one_message():
    rooms = [api_room(f"R{i:03d}", name=f"{i}号室", all_count=30) for i in range(30)]
    pages = {("512", p): FakeResponse(rooms[p * 5:(p + 1) * 5]) for p in range(6)}
    pages[("312", 0)] = FakeResponse("null")
    _, msgs = main.run(CONFIG, {"initialized": True}, session=FakeSession(pages), sleep=no_sleep)
    assert len(msgs) == 1
    assert "新着の空室 30件" in msgs[0]["blocks"][0]["text"]["text"]
    assert len(msgs[0]["blocks"]) <= 50
    assert "ほか 10件" in message_text(msgs[0])


def test_load_config_has_six_danchi_and_tokyo23():
    config = main.load_config(main.DEFAULT_CONFIG)
    assert len(config["danchi"]) == 6
    [area] = config["search_areas"]
    assert area["name"] == "東京23区" and area["tdfk"] == "13"
    assert area["skcs"] == [str(c) for c in range(101, 124)]
    assert config["filters"]["max_total_rent"] == 200000
    assert all(config["filters"][k] is None for k in ("min_area", "min_layout", "min_floor"))


# ---- search_areas（東京23区など） ----

AREA_CONFIG = {
    **CONFIG,
    "area_name": "新浦安・東京23区",
    "danchi": [{"id": "30_512_0", "name": "望海の街", "access": ["新浦安 徒歩22分"]}],
    "search_areas": [{"name": "東京23区", "block": "kanto", "tdfk": "13", "skcs": ["101", "102"]}],
}


def area_session(rooms_512=(), ward101=None, ward102=None, rooms_382=(), rooms_383=()):
    from tests.helpers import api_bukken

    def resp(x):
        if isinstance(x, FakeResponse):
            return x
        return FakeResponse(list(x) if x else "null")

    ward101 = ward101 if ward101 is not None else [
        api_bukken("382", "神田小川町ハイツ", room_count=len(rooms_382), bukken_count=2),
        api_bukken("383", "空き無し団地", room_count=len(rooms_383), bukken_count=2),
    ]
    ward102 = ward102 if ward102 is not None else []
    return FakeSession({
        ("512", 0): resp(rooms_512),
        ("search", "101", 0): resp(ward101),
        ("search", "102", 0): resp(ward102),
        ("382", 0): resp(rooms_382),
        ("383", 0): resp(rooms_383),
    })


def tokyo_room(room_id, danchi="382", **kw):
    return api_room(room_id, shisya="20", danchi=danchi, **kw)


def test_adding_area_sends_its_current_list_once_then_only_new():
    # 新浦安だけ監視していた状態から東京23区を追加
    state = roundtrip({"initialized": True, "vacancies": {}, "failures": {}})
    s = area_session(rooms_382=[tokyo_room("T1", name="1号棟101号室")])
    state, msgs = main.run(AREA_CONFIG, state, session=s, sleep=no_sleep)
    assert len(msgs) == 1
    text = message_text(msgs[0])
    assert "東京23区の現在の空室 1件" in text
    assert "都営新宿線「小川町」駅 徒歩2分" in text
    assert state["initialized_areas"] == ["東京23区"]
    # 空室0件の団地には詳細を取りに行かない
    assert ("383", 0) not in s.calls

    # 次の回：東京で新着が出たら通常の新着として届く
    state = roundtrip(state)
    s = area_session(rooms_382=[tokyo_room("T1", name="1号棟101号室"), tokyo_room("T2", name="2号棟202号室", all_count=2)])
    state, msgs = main.run(AREA_CONFIG, state, session=s, sleep=no_sleep)
    assert len(msgs) == 1
    text = message_text(msgs[0])
    assert "新浦安・東京23区に新着の空室 1件" in text and "2号棟202号室" in text


def test_area_rooms_gone_when_danchi_becomes_empty():
    state = roundtrip(main.run(AREA_CONFIG, {}, session=area_session(rooms_382=[tokyo_room("T1")]), sleep=no_sleep)[0])
    assert "20_382_0:T1" in state["vacancies"]
    state, msgs = main.run(AREA_CONFIG, state, session=area_session(), sleep=no_sleep)
    assert msgs == [] and state["vacancies"] == {}


def test_area_search_failure_keeps_previous_rooms():
    state = roundtrip(main.run(AREA_CONFIG, {}, session=area_session(rooms_382=[tokyo_room("T1")]), sleep=no_sleep)[0])
    alerts = []
    for _ in range(3):
        s = area_session(ward101=FakeResponse("", status=500))
        state, msgs = main.run(AREA_CONFIG, state, session=s, sleep=no_sleep)
        state = roundtrip(state)
        assert "20_382_0:T1" in state["vacancies"]
        alerts += [m for m in msgs if "⚠️" in m["text"]]
    assert len(alerts) == 1 and "市区町村コード101" in message_text(alerts[0])


def test_first_run_with_area_sends_one_combined_list():
    s = area_session(rooms_512=[api_room("A1")], rooms_382=[tokyo_room("T1")])
    state, msgs = main.run(AREA_CONFIG, {}, session=s, sleep=no_sleep)
    assert len(msgs) == 1
    assert "新浦安・東京23区の現在の空室 2件" in message_text(msgs[0])
    assert state["initialized_areas"] == ["東京23区"]
