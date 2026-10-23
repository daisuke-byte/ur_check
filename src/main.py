"""UR賃貸 空室ウォッチャー

    python -m src.main            # 取得して、新着があれば Slack に通知し、state を更新する
    python -m src.main --dry-run  # Slack に送らず通知内容を表示する（state も更新しない）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

import yaml

from . import diff, fetch, filter, notify

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
DEFAULT_STATE = ROOT / "state" / "vacancies.json"


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    config["danchi"] = config.get("danchi") or []
    if not config["danchi"] and not config.get("search_areas"):
        raise ValueError(f"{path}: danchi も search_areas も空です")
    for d in config["danchi"]:
        fetch.split_danchi_id(str(d.get("id", "")))
        d["id"] = str(d["id"])
        d.setdefault("name", d["id"])
    areas = config.setdefault("search_areas", []) or []
    config["search_areas"] = areas
    for a in areas:
        if not a.get("name") or not a.get("skcs") or not a.get("tdfk"):
            raise ValueError(f"{path}: search_areas には name / tdfk / skcs が必要です: {a!r}")
        a["tdfk"] = str(a["tdfk"])
        a["skcs"] = [str(c) for c in a["skcs"]]
        a.setdefault("block", "kanto")
    filters = config.setdefault("filters", {})
    if filters.get("max_total_rent") is None:
        raise ValueError(f"{path}: filters.max_total_rent は必須です")
    if filters.get("min_layout") is not None and filter.layout_rank(str(filters["min_layout"])) is None:
        raise ValueError(f"{path}: filters.min_layout が読めません: {filters['min_layout']!r}（例: \"1DK\"）")
    config.setdefault("area_name", "UR")
    config.setdefault("area_path", "kanto/chiba")
    config.setdefault("request", {})
    config.setdefault("error_alert_after", 3)
    return config


def load_state(path: Path) -> dict:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_state(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _record_failure(failures: dict, key: str, error: Exception) -> None:
    prev = failures.get(key, {})
    failures[key] = {
        "count": prev.get("count", 0) + 1,
        "notified": prev.get("notified", False),
        "last_error": str(error)[:300],
    }


def run(
    config: dict,
    state: dict,
    *,
    session=None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict, list[dict]]:
    """1回分の監視。新しい state と、送るメッセージのリストを返す（送信はしない）。"""
    req = config["request"]
    interval = float(req.get("interval_seconds", 1.5))
    opts = dict(
        interval=interval,
        max_retries=int(req.get("max_retries", 3)),
        timeout=float(req.get("timeout_seconds", 20)),
        sleep=sleep,
    )
    session = session or fetch.make_session()

    previous: dict[str, dict] = state.get("vacancies", {})
    failures: dict[str, dict] = dict(state.get("failures", {}))
    fetched: dict[str, list[dict] | None] = {}
    names: dict[str, str] = {}
    danchi_order: list[str] = []
    first_request = True

    def pause():
        nonlocal first_request
        if not first_request:
            sleep(interval)
        first_request = False

    def fetch_danchi(danchi: dict) -> None:
        names[danchi["id"]] = danchi["name"]
        danchi_order.append(danchi["id"])
        pause()
        try:
            rooms = fetch.fetch_rooms(session, danchi, **opts)
        except fetch.FetchError as e:
            print(f"[warn] {danchi['name']}: {e}", file=sys.stderr)
            fetched[danchi["id"]] = None
            _record_failure(failures, danchi["id"], e)
            return
        failures.pop(danchi["id"], None)
        fetched[danchi["id"]] = filter.apply(rooms, config["filters"])
        print(f"[info] {danchi['name']}: 空室 {len(rooms)}件（条件に合うもの {len(fetched[danchi['id']])}件）", file=sys.stderr)

    # 1) config に書いた団地（新浦安エリアなど）
    for danchi in config["danchi"]:
        fetch_danchi(danchi)

    # 2) search_areas（東京23区など）：区ごとに団地一覧を取り、空室がある団地だけ詳しく取得する
    area_groups: dict[str, list[str]] = {}  # エリア名 → その区の group キー
    area_failed: dict[str, int] = {}
    for area in config.get("search_areas") or []:
        groups = area_groups.setdefault(area["name"], [])
        for skcs in area["skcs"]:
            group = f"{area['name']}:{skcs}"
            groups.append(group)
            search_key = f"search:{group}"
            names[search_key] = f"{area['name']}（市区町村コード{skcs}）の団地一覧"
            pause()
            try:
                found = fetch.search_area(session, block=area["block"], tdfk=area["tdfk"], skcs=skcs, **opts)
            except fetch.FetchError as e:
                print(f"[warn] {names[search_key]}: {e}", file=sys.stderr)
                _record_failure(failures, search_key, e)
                area_failed[area["name"]] = area_failed.get(area["name"], 0) + 1
                # この区で前回空室があった団地は、前回の状態を残す
                for room in previous.values():
                    if room.get("group") == group:
                        fetched[room["danchi_id"]] = None
                        names.setdefault(room["danchi_id"], room["danchi_name"])
                continue
            failures.pop(search_key, None)
            with_rooms = 0
            for d in found:
                if d["id"] in names:  # 既に config 側や別の区で取得済み
                    continue
                danchi = {"id": d["id"], "name": d["name"], "access": d["access"], "group": group}
                if d["room_count"] > 0:
                    with_rooms += 1
                    fetch_danchi(danchi)
                else:
                    names[d["id"]] = d["name"]
                    danchi_order.append(d["id"])
                    fetched[d["id"]] = []
                    failures.pop(d["id"], None)
            print(f"[info] {names[search_key]}: {len(found)}団地（空室あり {with_rooms}団地）", file=sys.stderr)

    current = diff.merge_current(previous, fetched)
    messages: list[dict] = []
    common = dict(filters=config["filters"], danchi_order=danchi_order)

    def area_of(room: dict) -> str | None:
        group = room.get("group")
        return group.split(":", 1)[0] if group else None

    initialized = bool(state.get("initialized"))
    initialized_areas = set(state.get("initialized_areas", []))
    # 今回はじめて監視するエリア（全区の検索に失敗したら次回に持ち越す）
    starting_areas = [
        name
        for name, groups in area_groups.items()
        if name not in initialized_areas and area_failed.get(name, 0) < len(groups)
    ]

    if not initialized:
        # 初回なのに1件も取れなかったときは「現在0件」と誤って伝えないよう、初回扱いを次回に持ち越す
        if not all(rooms is None for rooms in fetched.values()) and fetched:
            messages.append(
                notify.build_vacancy_message(list(current.values()), initial=True, area_name=config["area_name"], **common)
            )
            initialized = True
            initialized_areas.update(starting_areas)
    else:
        d = diff.compute(previous, current, watched_danchi=set(fetched))
        new = [r for r in d.new if area_of(r) not in starting_areas]
        gone = [r for r in d.gone if area_of(r) not in starting_areas]
        # 埋まった空室だけのときは通知しない
        if new:
            messages.append(notify.build_vacancy_message(new, gone=gone, area_name=config["area_name"], **common))
        for name in starting_areas:
            rooms = [r for r in current.values() if area_of(r) == name]
            messages.append(notify.build_vacancy_message(rooms, initial=True, area_name=name, **common))
            initialized_areas.add(name)
    # 設定から外したエリアは忘れる（また追加したら現在の一覧から送り直す）
    initialized_areas &= set(area_groups)

    # 連続失敗のアラート（復旧するまで1回だけ）
    alert_after = int(config["error_alert_after"])
    to_alert = [
        (key, f) for key, f in failures.items() if f["count"] >= alert_after and not f["notified"] and key in names
    ]
    if to_alert:
        messages.append(notify.build_error_message([(names[k], f["count"], f["last_error"]) for k, f in to_alert]))
        for key, _ in to_alert:
            failures[key] = {**failures[key], "notified": True}
    # 監視対象から外れた団地・区の失敗記録は消す
    failures = {k: v for k, v in failures.items() if k in names}

    new_state = {"initialized": initialized, "vacancies": current, "failures": failures}
    if area_groups:
        new_state["initialized_areas"] = sorted(initialized_areas)
    return new_state, messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UR賃貸の空室を監視して Slack に通知する")
    parser.add_argument("--dry-run", action="store_true", help="Slackに送らず内容を表示する（stateも保存しない）")
    parser.add_argument("--json", action="store_true", help="dry-run時に Block Kit の JSON も表示する")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    state = load_state(args.state)

    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not args.dry_run and not webhook:
        print("SLACK_WEBHOOK_URL が設定されていません（試すだけなら --dry-run を付けてください）", file=sys.stderr)
        return 2

    new_state, messages = run(config, state)

    if args.dry_run:
        if not messages:
            print("（通知なし：新着の空室はありません）")
        for i, payload in enumerate(messages, 1):
            print(f"\n===== Slack メッセージ {i}/{len(messages)} =====")
            print(notify.render_text(payload))
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    try:
        for payload in messages:
            notify.post(webhook, payload)
    except notify.NotifyError as e:
        # 送れなかった新着は次回もう一度通知したいので、state は保存しない
        print(f"[error] {e}", file=sys.stderr)
        return 1

    text = dump_state(new_state)
    if not args.state.exists() or args.state.read_text(encoding="utf-8") != text:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(text, encoding="utf-8")
        print(f"[info] {args.state} を更新しました", file=sys.stderr)
    else:
        print("[info] 変化なし", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
