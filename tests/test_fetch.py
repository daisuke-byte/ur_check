import pytest
import requests

from src import fetch
from tests.helpers import FakeResponse, FakeSession, api_room

DANCHI = {"id": "30_512_0", "name": "望海の街", "access": ["新浦安 徒歩5分"]}


def no_sleep(_):
    pass


def test_null_means_no_vacancy():
    session = FakeSession({("512", 0): FakeResponse("null")})
    assert fetch.fetch_rooms(session, DANCHI, sleep=no_sleep) == []


def test_normalizes_room():
    session = FakeSession({("512", 0): FakeResponse([api_room("000112608", name="11号棟2608号室")])})
    [room] = fetch.fetch_rooms(session, DANCHI, sleep=no_sleep)
    assert room["key"] == "30_512_0:000112608"
    assert room["room_name"] == "11号棟2608号室"
    assert room["total"] == 155000
    assert room["area"] == 50.0
    assert room["floor"] == 3
    assert room["url"] == "https://www.ur-net.go.jp/chintai/kanto/chiba/30_5120_room.html?JKSS=000112608"
    assert room["access"] == ["新浦安 徒歩5分"]


def test_pages_until_all_count():
    page0 = [api_room(f"00000000{i}", all_count=6) for i in range(5)]
    page1 = [api_room("000000099", all_count=6)]
    session = FakeSession({("512", 0): FakeResponse(page0), ("512", 1): FakeResponse(page1)})
    rooms = fetch.fetch_rooms(session, DANCHI, sleep=no_sleep)
    assert len(rooms) == 6
    assert session.calls == [("512", 0), ("512", 1)]


def test_retries_then_succeeds():
    sleeps = []
    session = FakeSession(
        {("512", 0): [requests.ConnectionError("boom"), FakeResponse("", status=503), FakeResponse("null")]}
    )
    assert fetch.fetch_rooms(session, DANCHI, sleep=sleeps.append) == []
    assert sleeps == [2, 4]  # 指数バックオフ


def test_gives_up_after_max_retries():
    session = FakeSession({("512", 0): FakeResponse("", status=500)})
    with pytest.raises(fetch.FetchError):
        fetch.fetch_rooms(session, DANCHI, max_retries=3, sleep=no_sleep)
    assert len(session.calls) == 4  # 初回 + リトライ3回


def test_html_response_is_failure_not_empty():
    """メンテナンス画面などのHTMLが返ってきても「空室0件」にしない。"""
    session = FakeSession({("512", 0): FakeResponse("<html>メンテナンス中</html>")})
    with pytest.raises(fetch.FetchError):
        fetch.fetch_rooms(session, DANCHI, max_retries=0, sleep=no_sleep)


def test_unexpected_shape_is_failure():
    session = FakeSession({("512", 0): FakeResponse({"error": "x"})})
    with pytest.raises(fetch.FetchError):
        fetch.fetch_rooms(session, DANCHI, max_retries=0, sleep=no_sleep)


def test_danchi_page_url():
    assert fetch.danchi_page_url("30_512_0", "kanto/chiba") == "https://www.ur-net.go.jp/chintai/kanto/chiba/30_5120.html"


def test_parse_traffic():
    assert fetch.parse_traffic("<li>都営新宿線｢小川町｣駅 徒歩2分</li><li>JR &amp; 私鉄 徒歩5分</li>") == [
        "都営新宿線「小川町」駅 徒歩2分",
        "JR & 私鉄 徒歩5分",
    ]
    assert fetch.parse_traffic(None) == []


def test_search_area_lists_danchi():
    from tests.helpers import api_bukken

    session = FakeSession({("search", "101", 0): FakeResponse([api_bukken("382", "神田小川町ハイツ", room_count=2)])})
    [d] = fetch.search_area(session, block="kanto", tdfk="13", skcs="101", sleep=no_sleep)
    assert d["id"] == "20_382_0" and d["name"] == "神田小川町ハイツ" and d["room_count"] == 2
    assert d["access"][0] == "都営新宿線「小川町」駅 徒歩2分"


def test_search_area_bad_record_is_failure():
    session = FakeSession({("search", "101", 0): FakeResponse([{"danchiNm": "x"}])})
    with pytest.raises(fetch.FetchError):
        fetch.search_area(session, block="kanto", tdfk="13", skcs="101", max_retries=0, sleep=no_sleep)
