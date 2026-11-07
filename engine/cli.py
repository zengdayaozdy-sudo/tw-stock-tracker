"""命令列入口。每個步驟都能單獨執行，方便除錯與補跑指定日期。

用法：python -m engine.cli <指令> [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from engine.config import TZ, load_settings

PIPELINE_STEPS = [
    ("fetch", "抓取資料"),
    ("check", "資料完整性檢查"),
    ("liquidity", "流動性過濾"),
    ("news", "消息面排雷"),
    ("score", "三面向評分"),
    ("filters", "大盤燈號與族群強弱濾網"),
    ("signals", "買進訊號與參考價位"),
    ("holdings", "持股檢查"),
    ("notify", "LINE 每日報告"),
]


def _parse_date(value: str | None) -> date:
    if not value:
        return datetime.now(TZ).date()
    return date.fromisoformat(value)


def cmd_plan(_args) -> None:
    s = load_settings()
    print(f"目前方案：{s.plan.label}（{s.plan.name}）")
    print(f"選股範圍：{'自選股' if s.plan.universe == 'watchlist' else '全市場'}")
    print("已啟用：" + ("、".join(s.all_features[f] for f in sorted(s.plan.features)) or "（無）"))
    locked = s.locked_features()
    if locked:
        print("鎖定中：" + "、".join(locked.values()))


def cmd_migrate(_args) -> None:
    from engine.db import migrate

    applied = migrate()
    print("已套用：" + ("、".join(applied) if applied else "沒有新的 migration"))


def cmd_step(args) -> None:
    label = dict(PIPELINE_STEPS)[args.command]
    raise SystemExit(f"「{label}」尚未實作（{_parse_date(args.date)}）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engine", description="台股自選股評分追蹤程式")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="顯示目前方案與功能開關").set_defaults(func=cmd_plan)
    sub.add_parser("migrate", help="套用資料庫 migration").set_defaults(func=cmd_migrate)
    for name, label in PIPELINE_STEPS:
        p = sub.add_parser(name, help=label)
        p.add_argument("--date", help="指定日期 YYYY-MM-DD，預設今天（台灣時間）")
        p.set_defaults(func=cmd_step)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
