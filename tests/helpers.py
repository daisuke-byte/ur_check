"""テスト用のダミー（URへは実際にはアクセスしない）。"""

import json


def api_room(room_id, name="1号棟101号室", rent="150,000円", commonfee="5,000円", type_="2DK",
             floorspace="50&#13217;", floor="3階", all_count=1, shisya="30", danchi="512"):
    """detail_bukken_room が返す部屋1件ぶんの形を真似る。"""
    return {
        "allCount": str(all_count),
        "rowMax": "5",
        "shisya": shisya,
        "danchi": danchi,
        "shikibetu": "0",
        "id": room_id,
        "name": name,
        "type": type_,
        "floorspace": floorspace,
        "floor": floor,
        "floorAll": "10階",
        "rent": rent,
        "commonfee": commonfee,
        "roomDetailLink": f"/chintai/kanto/chiba/{shisya}_{danchi}0_room.html?JKSS={room_id}",
    }


class FakeResponse:
    def __init__(self, body, status=200):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """(danchi, pageIndex) ごとに返すレスポンスを決めておけるセッション。

    routes の値は FakeResponse / 例外 / それらのリスト（呼ばれるたびに先頭から使う）。
    """

    def __init__(self, routes):
        self.routes = {k: (list(v) if isinstance(v, list) else v) for k, v in routes.items()}
        self.calls = []

    def post(self, url, data=None, timeout=None):
        if "skcs" in data:  # bukken_result（区の団地一覧）
            key = ("search", data["skcs"], int(data["pageIndex"]))
        else:
            key = (data["danchi"], int(data["pageIndex"]))
        self.calls.append(key)
        route = self.routes[key]
        item = route.pop(0) if isinstance(route, list) else route
        if isinstance(item, Exception):
            raise item
        return item


def api_bukken(danchi, name, room_count=0, bukken_count=1, shisya="20",
               traffic="<li>都営新宿線｢小川町｣駅 徒歩2分</li><li>東京メトロ千代田線｢新御茶ノ水｣駅 徒歩2分</li>"):
    """bukken_result が返す団地1件ぶんの形を真似る。"""
    return {
        "bukkenCount": str(bukken_count),
        "roomCount": str(room_count),
        "shisya": shisya,
        "danchi": danchi,
        "shikibetu": "0",
        "danchiNm": name,
        "traffic": traffic,
    }
