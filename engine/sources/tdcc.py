"""集保結算所股權分散表（每週，公開資料只提供最新一週）。

欄位：資料日期, 證券代號, 持股分級, 人數, 股數, 占集保庫存數比例%
持股分級 1–15 依持股股數由小到大，16 為差異數調整，17 為合計；
大戶持股比例＝分級 major_holder_min_level（含）到 15 的比例加總（預設 12：40 萬股＝400 張以上）。
"""

from __future__ import annotations

import csv
import io
from datetime import date

from engine.sources.common import parse_tw_date, to_number


def parse_distribution(text: str, min_level: int) -> list[dict]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    next(reader, None)  # 表頭
    acc: dict[tuple[str, date], float] = {}
    for row in reader:
        if len(row) < 6:
            continue
        d = parse_tw_date(row[0])
        sid = row[1].strip()
        level = to_number(row[2])
        pct = to_number(row[5])
        if not d or not (len(sid) == 4 and sid.isdigit() and not sid.startswith("0")) or level is None:
            continue
        if min_level <= level <= 15:
            acc[(sid, d)] = acc.get((sid, d), 0.0) + (pct or 0.0)
        else:
            acc.setdefault((sid, d), 0.0)
    return [{"stock_id": s, "date": d, "major_holder_pct": round(v, 2)} for (s, d), v in sorted(acc.items())]
