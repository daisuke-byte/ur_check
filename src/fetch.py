"""UR賃貸の内部JSON APIから団地ごとの空室を取得する。

取得元の詳細は docs/data-source.md を参照。
"""

from __future__ import annotations

import html
import re
import time
from typing import Any, Callable

import requests

API_URL = "https://chintai.r6.ur-net.go.jp/chintai/api/bukken/detail/detail_bukken_room/"
SEARCH_URL = "https://chintai.r6.ur-net.go.jp/chintai/api/bukken/result/bukken_result/"
SITE_BASE = "https://www.ur-net.go.jp"
USER_AGENT = "ur-vacancy-watcher/1.0 (personal vacancy notifier; +https://github.com/daisuke-byte/ur_check)"

# 1団地あたりのページ送りの上限（1ページ5件なので500部屋ぶん）。無限ループ防止用。
MAX_PAGES = 100


class FetchError(Exception):
    """取得に失敗した。この団地の結果は「不明」として扱い、前回の状態を残す。"""


def split_danchi_id(danchi_id: str) -> tuple[str, str, str]:
    parts = danchi_id.split("_")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"団地IDは <shisya>_<danchi>_<shikibetu> の形式で指定してください: {danchi_id!r}")
    return parts[0], parts[1], parts[2]


def danchi_page_url(danchi_id: str, area_path: str) -> str:
    shisya, danchi, shikibetu = split_danchi_id(danchi_id)
    return f"{SITE_BASE}/chintai/{area_path}/{shisya}_{danchi}{shikibetu}.html"


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"{SITE_BASE}/chintai/",
        }
    )
    return session


def parse_yen(value: Any) -> int | None:
    """'164,300円' → 164300。'125,000～150,000円' のような範囲は上限を返す。"""
    if value is None:
        return None
    numbers = re.findall(r"\d[\d,]*", str(value))
    if not numbers:
        return None
    return max(int(n.replace(",", "")) for n in numbers)


def parse_area(value: Any) -> float | None:
    """'54&#13217;' → 54.0"""
    if value is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", html.unescape(str(value)))
    return float(m.group()) if m else None


def parse_floor(value: Any) -> int | None:
    """'26階' → 26"""
    if value is None:
        return None
    m = re.search(r"(\d+)\s*階", str(value))
    return int(m.group(1)) if m else None


def parse_traffic(value: Any) -> list[str]:
    """'<li>都営新宿線｢小川町｣駅 徒歩2分</li><li>…</li>' → ['都営新宿線「小川町」駅 徒歩2分', …]"""
    if not value:
        return []
    items = re.split(r"</li\s*>", str(value), flags=re.IGNORECASE)
    out = []
    for item in items:
        text = html.unescape(re.sub(r"<[^>]+>", "", item)).replace("｢", "「").replace("｣", "」")
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append(text)
    return out


def search_area(
    session: requests.Session,
    *,
    block: str,
    tdfk: str,
    skcs: str,
    interval: float = 1.5,
    max_retries: int = 3,
    timeout: float = 20,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """市区町村（skcs）内の全団地を返す（空室0件の団地も含む）。

    返す団地: {"id", "name", "access", "room_count"}
    """
    found: dict[str, dict] = {}
    for page in range(MAX_PAGES):
        if page:
            sleep(interval)
        data = _post_json(
            session,
            {
                "mode": "area",
                "skcs": str(skcs),
                "block": block,
                "tdfk": str(tdfk),
                "rireki_tdfk": str(tdfk),
                "orderByField": "0",
                "pageSize": "50",
                "pageIndex": str(page),
                "pageIndexRoom": "0",
                "sp": "",
            },
            max_retries=max_retries,
            timeout=timeout,
            sleep=sleep,
            url=SEARCH_URL,
        )
        if data is None:
            if page == 0:
                return []
            raise FetchError(f"団地一覧の{page}ページ目が null でした")
        if not isinstance(data, list) or not all(isinstance(b, dict) for b in data):
            raise FetchError(f"団地一覧が想定外の形式です: {str(data)[:200]}")
        if not data:
            break
        for b in data:
            try:
                danchi_id = f"{int(b['shisya'])}_{int(b['danchi'])}_{int(b['shikibetu'])}"
                room_count = int(b.get("roomCount") or 0)
                total = int(b.get("bukkenCount"))
            except (KeyError, TypeError, ValueError):
                raise FetchError(f"団地一覧のレコードが読めません: {str(b)[:200]}")
            found[danchi_id] = {
                "id": danchi_id,
                "name": b.get("danchiNm") or danchi_id,
                "access": parse_traffic(b.get("traffic")),
                "room_count": room_count,
            }
        if len(found) >= total:
            break
    else:
        raise FetchError(f"団地一覧のページ数が上限({MAX_PAGES})を超えました")
    return list(found.values())


def normalize_room(raw: dict, danchi: dict) -> dict:
    room_id = raw.get("id")
    if not room_id:
        raise FetchError(f"部屋IDがないレコードがありました: {raw!r:.200}")
    rent = parse_yen(raw.get("rent"))
    commonfee = parse_yen(raw.get("commonfee"))
    link = raw.get("roomDetailLink") or ""
    room = {
        "key": f"{danchi['id']}:{room_id}",
        "danchi_id": danchi["id"],
        "danchi_name": danchi["name"],
        "room_id": str(room_id),
        "room_name": raw.get("name") or str(room_id),
        "layout": raw.get("type") or "",
        "area": parse_area(raw.get("floorspace")),
        "floor": parse_floor(raw.get("floor")),
        "floor_all": parse_floor(raw.get("floorAll")),
        "rent": rent,
        "commonfee": commonfee,
        "total": None if rent is None else rent + (commonfee or 0),
        "url": SITE_BASE + link if link.startswith("/") else link,
        "access": list(danchi.get("access") or []),
    }
    if danchi.get("group"):
        # search_areas で見つけた団地は「どの区の検索で見つけたか」を持つ（検索失敗時に前回を残すため）
        room["group"] = danchi["group"]
    return room


def _post_json(
    session: requests.Session,
    data: dict,
    *,
    max_retries: int,
    timeout: float,
    sleep: Callable[[float], None],
    url: str = API_URL,
) -> Any:
    """POSTしてJSONを返す。失敗したら指数バックオフでリトライし、それでもだめなら FetchError。"""
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        if attempt:
            sleep(2 ** attempt)  # 2, 4, 8 秒
        try:
            resp = session.post(url, data=data, timeout=timeout)
            resp.raise_for_status()
            # 空室なしは本文が "null" で返る。メンテナンス画面などのHTMLは JSON として読めずに例外になる
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last_error = e
    raise FetchError(f"{max_retries + 1}回試しても取得できませんでした: {last_error}")


def fetch_rooms(
    session: requests.Session,
    danchi: dict,
    *,
    interval: float = 1.5,
    max_retries: int = 3,
    timeout: float = 20,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """団地の空室をすべて取得する（ページ送り込み）。空室なしなら []。"""
    shisya, danchi_no, shikibetu = split_danchi_id(danchi["id"])
    rooms: list[dict] = []
    for page in range(MAX_PAGES):
        if page:
            sleep(interval)
        data = _post_json(
            session,
            {
                "shisya": shisya,
                "danchi": danchi_no,
                "shikibetu": shikibetu,
                "orderByField": "0",
                "orderBySort": "0",
                "pageIndex": str(page),
                "sp": "",
            },
            max_retries=max_retries,
            timeout=timeout,
            sleep=sleep,
        )
        if data is None:
            if page == 0:
                return []
            raise FetchError(f"{page}ページ目が null でした（途中で空室数が変わった可能性）")
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise FetchError(f"想定外のレスポンス形式です: {str(data)[:200]}")
        if not data:
            break
        rooms.extend(normalize_room(r, danchi) for r in data)
        try:
            total = int(data[0].get("allCount"))
        except (TypeError, ValueError):
            raise FetchError(f"allCount が読めません: {data[0].get('allCount')!r}")
        if len(rooms) >= total:
            break
    else:
        raise FetchError(f"ページ数が上限({MAX_PAGES})を超えました")

    # ページ送りの途中で順番がずれた場合に備えて重複を除く
    unique = {r["key"]: r for r in rooms}
    return list(unique.values())
