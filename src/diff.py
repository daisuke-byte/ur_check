"""前回の空室一覧との差分。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Diff:
    new: list[dict]
    gone: list[dict]


def merge_current(
    previous: dict[str, dict],
    fetched: dict[str, list[dict] | None],
) -> dict[str, dict]:
    """今回の空室一覧を作る。

    fetched は 団地ID → 部屋リスト。取得に失敗した団地は None で、
    その団地は前回の部屋をそのまま残す（「全部埋まった」と誤判定しないため）。
    config から外した団地の部屋は消える。
    """
    current: dict[str, dict] = {}
    for danchi_id, rooms in fetched.items():
        if rooms is None:
            for key, room in previous.items():
                if room["danchi_id"] == danchi_id:
                    current[key] = room
        else:
            for room in rooms:
                current[room["key"]] = room
    return current


def compute(
    previous: dict[str, dict],
    current: dict[str, dict],
    watched_danchi: set[str] | None = None,
) -> Diff:
    """新着＝前回なく今回ある部屋、埋まった＝前回あり今回ない部屋。

    watched_danchi を渡すと、監視対象から外した団地の部屋は「埋まった」に含めない。
    """
    new = [room for key, room in current.items() if key not in previous]
    gone = [
        room
        for key, room in previous.items()
        if key not in current and (watched_danchi is None or room["danchi_id"] in watched_danchi)
    ]
    return Diff(new=new, gone=gone)
