"""資料完整性檢查。任何一項「錯誤」都會讓當日訊號暫停並推播警告。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

ONE_LOT = 1000  # 一張 = 1,000 股


@dataclass
class QualityReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def check_prices(rows: list[dict], trade_date: date, expected_ids: list[str], min_market_rows: int = 0) -> QualityReport:
    report = QualityReport()
    if not rows:
        report.errors.append(f"{trade_date} 沒有任何股價資料")
        return report

    dates = {r["date"] for r in rows}
    if dates != {trade_date}:
        report.errors.append(f"股價日期不符：預期 {trade_date}，實際 {sorted(dates)}")

    if min_market_rows and len(rows) < min_market_rows:
        report.errors.append(f"全市場股價只有 {len(rows)} 筆，低於下限 {min_market_rows}")

    by_id = {r["stock_id"]: r for r in rows}
    missing = [s for s in expected_ids if s not in by_id]
    if missing:
        report.errors.append(f"缺少 {len(missing)} 檔股價：{', '.join(missing[:20])}")

    for r in rows:
        o, h, lo, c = r.get("open"), r.get("high"), r.get("low"), r.get("close")
        sid = r["stock_id"]
        if c is None:
            # 只有零股成交（不到一張）時沒有收盤價，屬正常
            if (r.get("volume") or 0) >= ONE_LOT:
                report.errors.append(f"{sid} 有成交量但沒有收盤價")
            continue  # 當日無成交
        if c <= 0:
            report.errors.append(f"{sid} 收盤價異常：{c}")
        if None not in (h, lo) and h < lo:
            report.errors.append(f"{sid} 最高價低於最低價")
        if None not in (h, lo, o) and not (lo <= o <= h and lo <= c <= h):
            report.warnings.append(f"{sid} 開收盤價超出高低區間")
    return report


def check_price_jumps(today: dict[str, float], yesterday: dict[str, float], limit: float = 0.105) -> list[str]:
    """漲跌幅超過 10%（含容差）的股票，可能是除權息、新上市或資料錯誤。"""
    out = []
    for sid, c in today.items():
        p = yesterday.get(sid)
        if p and c and abs(c / p - 1) > limit:
            out.append(f"{sid} 單日變動 {c / p - 1:+.1%}，請確認是否除權息")
    return out


def check_chips(rows: list[dict], expected_ids: list[str], label: str) -> QualityReport:
    report = QualityReport()
    ids = {r["stock_id"] for r in rows}
    missing = [s for s in expected_ids if s not in ids]
    # 籌碼資料個股當天沒有交易可能本來就沒有，缺太多才視為錯誤
    if expected_ids and len(missing) > max(3, len(expected_ids) * 0.2):
        report.errors.append(f"{label}缺少 {len(missing)} 檔：{', '.join(missing[:20])}")
    elif missing:
        report.warnings.append(f"{label}缺少 {len(missing)} 檔：{', '.join(missing[:20])}")
    return report
