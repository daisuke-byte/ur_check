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


def test_load_config_has_six_danchi():
    config = main.load_config(main.DEFAULT_CONFIG)
    assert len(config["danchi"]) == 6
    assert config["filters"]["max_total_rent"] == 200000
    assert all(config["filters"][k] is None for k in ("min_area", "min_layout", "min_floor"))
