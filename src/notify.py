"""Slack通知（Incoming Webhook + Block Kit）。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests

JST = timezone(timedelta(hours=9))

# Slack の1メッセージは50ブロックまで。1部屋あたり2ブロック（本文＋区切り線）使うので、
# 詳しく載せるのはこの件数までにして、残りは「ほか N件」としてリンクだけ並べる。
MAX_DETAILED_ROOMS = 20
MAX_TEXT = 2900  # section/context の text は3000文字まで


class NotifyError(Exception):
    pass


def yen(value: int | None) -> str:
    return "不明" if value is None else f"¥{value:,}"


def _fmt_area(area: float | None) -> str:
    if area is None:
        return "面積不明"
    return f"{area:g}㎡"


def _fmt_floor(room: dict) -> str:
    if room.get("floor") is None:
        return "階数不明"
    if room.get("floor_all"):
        return f"{room['floor']}階（{room['floor_all']}階建）"
    return f"{room['floor']}階"


def _truncate(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _esc(text: str) -> str:
    """Slack mrkdwn で特別な意味を持つ文字をエスケープする。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _room_title(room: dict) -> str:
    return _esc(f"{room['danchi_name']}　{room['room_name']}")


def room_block(room: dict, icon: str) -> dict:
    title = _room_title(room)
    if room.get("url"):
        title = f"<{room['url']}|{title}>"
    lines = [
        f"{icon} *{title}*",
        f"{room.get('layout') or '間取り不明'} ／ {_fmt_area(room.get('area'))} ／ {_fmt_floor(room)}",
        f"{yen(room.get('rent'))} + {yen(room.get('commonfee'))} = *{yen(room.get('total'))}*",
    ]
    lines += [f"🚉 {a}" for a in room.get("access") or []]
    block: dict = {"type": "section", "text": {"type": "mrkdwn", "text": _truncate("\n".join(lines))}}
    if room.get("url"):
        block["accessory"] = {
            "type": "button",
            "text": {"type": "plain_text", "text": "部屋を見る", "emoji": True},
            "url": room["url"],
        }
    return block


def _sort(rooms: list[dict], danchi_order: list[str]) -> list[dict]:
    order = {d: i for i, d in enumerate(danchi_order)}
    return sorted(
        rooms,
        key=lambda r: (order.get(r["danchi_id"], len(order)), r.get("total") or 0, r["room_name"]),
    )


def _filters_summary(filters: dict) -> str:
    parts = []
    if filters.get("max_total_rent") is not None:
        parts.append(f"家賃+共益費 ≤ {yen(filters['max_total_rent'])}")
    if filters.get("min_area") is not None:
        parts.append(f"{filters['min_area']}㎡以上")
    if filters.get("min_layout") is not None:
        parts.append(f"{filters['min_layout']}以上")
    if filters.get("min_floor") is not None:
        parts.append(f"{filters['min_floor']}階以上")
    return "、".join(parts) or "条件なし"


def build_vacancy_message(
    rooms: list[dict],
    *,
    gone: list[dict] | None = None,
    initial: bool = False,
    area_name: str,
    filters: dict,
    danchi_order: list[str],
    now: datetime | None = None,
) -> dict:
    """新着（initial=True なら現在の全空室）の通知メッセージを作る。"""
    now = now or datetime.now(JST)
    rooms = _sort(rooms, danchi_order)
    gone = _sort(gone or [], danchi_order)
    icon = "🏠" if initial else "🆕"
    if initial:
        heading = f"🏠 {area_name}の現在の空室 {len(rooms)}件"
        fallback = f"{area_name}のUR空室の監視を始めました（現在 {len(rooms)}件）"
    else:
        heading = f"🆕 {area_name}に新着の空室 {len(rooms)}件"
        fallback = f"{area_name}にURの新着空室が {len(rooms)}件 出ました"

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": heading, "emoji": True}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"条件: {_filters_summary(filters)}　|　{now.astimezone(JST):%Y-%m-%d %H:%M} 時点",
                }
            ],
        },
    ]
    if initial:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "監視を始めました。次回からは *新しく出た空室だけ* をお知らせします。"
                    + ("\n今のところ条件に合う空室はありません。" if not rooms else ""),
                },
            }
        )

    for room in rooms[:MAX_DETAILED_ROOMS]:
        blocks.append({"type": "divider"})
        blocks.append(room_block(room, icon))

    rest = rooms[MAX_DETAILED_ROOMS:]
    if rest:
        lines = [
            f"• <{r['url']}|{_room_title(r)}> {r.get('layout') or ''} {yen(r.get('total'))}" for r in rest
        ]
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _truncate(f"*ほか {len(rest)}件*\n" + "\n".join(lines))},
            }
        )

    if gone:
        names = "、".join(_room_title(r) for r in gone)
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": _truncate(f"埋まった空室（{len(gone)}件）: {names}")}],
            }
        )

    return {"text": fallback, "blocks": blocks}


def build_error_message(failures: list[tuple[str, int, str]], *, now: datetime | None = None) -> dict:
    """failures: (団地名, 連続失敗回数, 最後のエラー) のリスト"""
    now = now or datetime.now(JST)
    lines = [f"• *{name}*（{count}回連続）: {error}" for name, count, error in failures]
    text = (
        "⚠️ *UR空室ウォッチャー: 空室を取得できていません*\n"
        + "\n".join(lines)
        + "\n\nこの団地は前回の空室のまま扱っています。URのサイトの仕様変更やメンテナンスの可能性があります。"
        "\n（復旧するまで、この通知は繰り返し送りません）"
    )
    return {
        "text": "⚠️ UR空室ウォッチャー: 空室の取得に失敗しています",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(text)}},
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"{now.astimezone(JST):%Y-%m-%d %H:%M} 時点"}],
            },
        ],
    }


def render_text(payload: dict) -> str:
    """dry-run用に、メッセージをターミナルで読める形にする。"""
    out: list[str] = []
    for block in payload["blocks"]:
        kind = block["type"]
        if kind == "header":
            out.append(f"■ {block['text']['text']}")
        elif kind == "divider":
            out.append("─" * 40)
        elif kind == "section":
            out.append(block["text"]["text"])
            if "accessory" in block:
                out.append(f"  [{block['accessory']['text']['text']}] {block['accessory']['url']}")
        elif kind == "context":
            out.extend(f"  ({e['text']})" for e in block["elements"])
    return "\n".join(out)


def post(
    webhook_url: str,
    payload: dict,
    *,
    max_retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        if attempt:
            sleep(2 ** attempt)
        try:
            resp = requests.post(webhook_url, json=payload, timeout=20)
            if resp.status_code == 200:
                return
            last_error = NotifyError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                break  # URL違いやメッセージ形式の誤りはリトライしても直らない
        except requests.RequestException as e:
            last_error = e
    raise NotifyError(f"Slackへの送信に失敗しました: {last_error}")
