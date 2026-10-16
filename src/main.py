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
    if not config.get("danchi"):
        raise ValueError(f"{path}: danchi が空です")
    for d in config["danchi"]:
        fetch.split_danchi_id(str(d.get("id", "")))
        d["id"] = str(d["id"])
        d.setdefault("name", d["id"])
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
    session = session or fetch.make_session()

    previous: dict[str, dict] = state.get("vacancies", {})
    failures: dict[str, dict] = dict(state.get("failures", {}))
    fetched: dict[str, list[dict] | None] = {}

    for i, danchi in enumerate(config["danchi"]):
        if i:
            sleep(interval)
        try:
            rooms = fetch.fetch_rooms(
                session,
                danchi,
                interval=interval,
                max_retries=int(req.get("max_retries", 3)),
                timeout=float(req.get("timeout_seconds", 20)),
                sleep=sleep,
            )
        except fetch.FetchError as e:
            print(f"[warn] {danchi['name']}: {e}", file=sys.stderr)
            fetched[danchi["id"]] = None
            prev = failures.get(danchi["id"], {})
            failures[danchi["id"]] = {
                "count": prev.get("count", 0) + 1,
                "notified": prev.get("notified", False),
                "last_error": str(e)[:300],
            }
            continue
        failures.pop(danchi["id"], None)
        fetched[danchi["id"]] = filter.apply(rooms, config["filters"])
        print(f"[info] {danchi['name']}: 空室 {len(rooms)}件（条件に合うもの {len(fetched[danchi['id']])}件）", file=sys.stderr)

    current = diff.merge_current(previous, fetched)
    messages: list[dict] = []
    common = dict(
        area_name=config["area_name"],
        filters=config["filters"],
        danchi_order=[d["id"] for d in config["danchi"]],
    )

    initialized = bool(state.get("initialized"))
    all_failed = all(rooms is None for rooms in fetched.values())
    if not initialized:
        # 初回なのに1件も取れなかったときは「現在0件」と誤って伝えないよう、初回扱いを次回に持ち越す
        if not all_failed:
            messages.append(notify.build_vacancy_message(list(current.values()), initial=True, **common))
            initialized = True
    else:
        d = diff.compute(previous, current, watched_danchi=set(fetched))
        # 埋まった空室だけのときは通知しない
        if d.new:
            messages.append(notify.build_vacancy_message(d.new, gone=d.gone, **common))

    # 連続失敗のアラート（復旧するまで1回だけ）
    alert_after = int(config["error_alert_after"])
    names = {d["id"]: d["name"] for d in config["danchi"]}
    to_alert = [
        (danchi_id, f) for danchi_id, f in failures.items() if f["count"] >= alert_after and not f["notified"]
    ]
    if to_alert:
        messages.append(
            notify.build_error_message([(names.get(i, i), f["count"], f["last_error"]) for i, f in to_alert])
        )
        for danchi_id, _ in to_alert:
            failures[danchi_id] = {**failures[danchi_id], "notified": True}
    # 監視対象から外した団地の失敗記録は消す
    failures = {k: v for k, v in failures.items() if k in names}

    new_state = {"initialized": initialized, "vacancies": current, "failures": failures}
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
