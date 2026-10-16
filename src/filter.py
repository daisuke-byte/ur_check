"""config.yaml の filters による絞り込み。"""

from __future__ import annotations

import re

# 1R < 1K < 1DK < 1LDK < 2K < 2DK < 2LDK < ...（S付きは同じ部屋数の中で少し上）
_LAYOUT_TYPES = {"R": 0, "K": 1, "DK": 2, "LDK": 3}
_LAYOUT_RE = re.compile(r"(\d+)\s*(S)?\s*(LDK|DK|K|R)", re.IGNORECASE)


def layout_rank(layout: str | None) -> float | None:
    """間取り文字列を比較用の数値にする。読めなければ None。"""
    if not layout:
        return None
    text = layout.upper().replace("Ｌ", "L").replace("Ｄ", "D").replace("Ｋ", "K").replace("Ｓ", "S")
    m = _LAYOUT_RE.search(text)
    if not m:
        return None
    rooms, service, kind = int(m.group(1)), m.group(2), m.group(3)
    return rooms * 10 + _LAYOUT_TYPES[kind] + (0.5 if service else 0)


def matches(room: dict, filters: dict) -> bool:
    max_total = filters.get("max_total_rent")
    if max_total is not None:
        if room.get("total") is None or room["total"] > max_total:
            return False

    min_area = filters.get("min_area")
    if min_area is not None:
        if room.get("area") is None or room["area"] < min_area:
            return False

    min_floor = filters.get("min_floor")
    if min_floor is not None:
        if room.get("floor") is None or room["floor"] < min_floor:
            return False

    min_layout = filters.get("min_layout")
    if min_layout is not None:
        need = layout_rank(str(min_layout))
        if need is None:
            raise ValueError(f"filters.min_layout が読めません: {min_layout!r}（例: \"1DK\"）")
        have = layout_rank(room.get("layout"))
        if have is None or have < need:
            return False

    return True


def apply(rooms: list[dict], filters: dict) -> list[dict]:
    return [r for r in rooms if matches(r, filters)]
