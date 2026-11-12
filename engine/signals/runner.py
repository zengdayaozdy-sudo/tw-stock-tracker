"""買進訊號步驟（第 4 節步驟 7）：名單 → 濾網 → 進場條件 → 參考價位 → 風控張數。

資料完整性檢查未通過時暫停當日訊號（第 4 節步驟 2）。重跑同一天會覆寫當日買進訊號。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from engine.config import Settings
from engine.filters.runner import screen_summary
from engine.pipeline import universe
from engine.repository import Repository
from engine.scoring.loader import load_frames, load_groups
from engine.scoring.model import reasons
from engine.signals.entry import entry_triggers, trigger_text
from engine.signals.levels import compute_levels
from engine.signals.regime import market_regime, scaled_position_cfg
from engine.signals.selection import select
from engine.signals.setup import setup_levels, setup_signs, setup_text
from engine.signals.sizing import position_size

HORIZON_LABELS = {"short": "短線", "long": "中長線"}


def quality_gate(repo: Repository, as_of: date) -> tuple[bool, str]:
    """回傳 (可以發訊號, 原因)。"""
    job = repo.latest_job("fetch", as_of)
    if job is None:
        return False, "當日尚未執行資料抓取"
    status, detail = job
    if status == "skipped":
        return False, "休市或官方資料尚未更新"
    if status != "success":
        return False, f"資料抓取{('失敗' if status == 'failed' else '未完成')}"
    quality = detail.get("quality") or {}
    if quality.get("ok") is False:
        errors = quality.get("errors") or []
        return False, "資料完整性檢查未通過：" + "；".join(errors[:3])
    return True, "資料完整"


def _stock_groups(groups: dict[str, tuple[str, set[str]]]) -> dict[str, list[str]]:
    """每檔股票用來計算族群曝險的族群：有自訂族群用自訂，否則用產業。"""
    custom: dict[str, list[str]] = {}
    industry: dict[str, list[str]] = {}
    for name, (kind, members) in groups.items():
        target = custom if kind == "custom" else industry
        for sid in members:
            target.setdefault(sid, []).append(name)
    return {sid: custom.get(sid) or industry.get(sid) or [] for sid in set(custom) | set(industry)}


def group_exposures(holdings: dict[str, float], stock_groups: dict[str, list[str]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for sid, value in holdings.items():
        for g in stock_groups.get(sid, []):
            out[g] = out.get(g, 0.0) + value
    return out


def run_signals(settings: Settings, repo: Repository, as_of: date, force: bool = False) -> dict[str, Any]:
    ok, gate_reason = quality_gate(repo, as_of)
    if not ok and not force:
        repo.replace_signals(as_of, "buy", [])
        repo.conn.commit()
        closed = (repo.latest_job("fetch", as_of) or ("", {}))[0] == "skipped"
        return {"status": "closed" if closed else "halted", "reason": gate_reason, "signals": [], "blocked": []}

    ids = universe(settings, repo)
    risk, scoring = settings.risk, settings.scoring
    flags = screen_summary(repo, as_of)
    light = repo.market_light(as_of)
    rebound = repo.market_rebound(as_of) or {}
    regime = market_regime(light, rebound.get("state"), risk)
    groups = load_groups(repo)
    scores = repo.score_rows(as_of, ids)
    top_percent = settings.enabled("top_percent_list")

    selected: dict[str, dict[str, list[str]]] = {}
    for h in HORIZON_LABELS:
        totals = {sid: r["total"] for (sid, hh), r in scores.items()
                  if hh == h and r["status"] == "ok" and r["total"] is not None}
        selected[h] = select(totals, groups, top_percent, scoring)
    candidates = sorted(set().union(*(s.keys() for s in selected.values())))

    capital = repo.total_capital()
    holdings = repo.holdings_value(as_of)
    stock_groups = _stock_groups(groups)
    exposures = group_exposures(holdings, stock_groups)
    setup_cfg = risk.get("setup_watch") or {}
    setup_ids = sorted(set(ids)) if setup_cfg.get("enabled") else []
    load_ids = sorted(set(candidates) | set(setup_ids))
    names = repo.stock_names(load_ids)
    frames = load_frames(repo, load_ids, as_of, risk["pe_river"]["lookback_years"]) if load_ids else {}
    position_cfg = scaled_position_cfg(risk["position"], regime["size_factor"])
    # 觀察名單與醞釀名單的張數：以「成立後」會用的部位計算（偏空時＝反彈單）
    watch_factor = (float((risk.get("rebound_mode") or {}).get("confirmed", {}).get("size_factor", 0.5))
                    if light == "bearish" else 1.0)
    watch_position_cfg = scaled_position_cfg(risk["position"], watch_factor)

    def _watch_size(sid: str, entry: float, stop: float) -> dict:
        exposure = max((exposures.get(g, 0.0) for g in stock_groups.get(sid, [])), default=0.0)
        return position_size(capital, entry, stop, watch_position_cfg, holdings_count=len(holdings),
                             group_exposure=exposure, already_held=sid in holdings)

    signals: list[dict] = []
    blocked: list[dict] = []
    for sid in candidates:
        f = frames[sid]
        triggers = entry_triggers(f.prices, f.chips, as_of, risk, f.benchmark, regime["entry_conditions"])
        if not triggers:
            continue
        fl = flags.get(sid, {"exclude": [], "block": [], "warn": []})
        if fl["exclude"] or fl["block"]:
            item = {"stock_id": sid, "name": names.get(sid), "triggers": trigger_text(triggers),
                    "reasons": fl["exclude"] + fl["block"]}
            if not fl["exclude"]:  # 只被大盤或族群擋下：附上參考價位，條件成立時可直接使用
                lv = compute_levels(f.prices, f.valuations, as_of, risk, bearish=regime["tighten_stop"])
                if lv is not None:
                    sz = _watch_size(sid, lv.entry_high, lv.stop_loss)
                    item.update({"close": lv.close, "entry_low": lv.entry_low, "entry_high": lv.entry_high,
                                 "stop_loss": lv.stop_loss, "take_profit": lv.take_profit,
                                 "suggested_lots": sz["lots"], "odd_shares": sz["odd_shares"],
                                 "size_factor": watch_factor})
            blocked.append(item)
            continue
        levels = compute_levels(f.prices, f.valuations, as_of, risk, bearish=regime["tighten_stop"])
        if levels is None:
            continue
        exposure = max((exposures.get(g, 0.0) for g in stock_groups.get(sid, [])), default=0.0)
        size = position_size(capital, levels.entry_high, levels.stop_loss, position_cfg,
                             holdings_count=len(holdings), group_exposure=exposure, already_held=sid in holdings)
        for h, label in HORIZON_LABELS.items():
            if sid not in selected[h]:
                continue
            score = scores[(sid, h)]
            why = reasons(score["breakdown"])
            text = f"{label} {score['total']:.1f} 分；{'、'.join(trigger_text(triggers))}"
            if regime["mode"] != "normal":
                text = f"{regime['label']}（部位 ×{regime['size_factor']:g}）｜" + text
            if why:
                text += f"；高分理由：{'、'.join(why)}"
            signals.append({
                "stock_id": sid,
                "date": as_of,
                "horizon": h,
                "side": "buy",
                "triggers": list(triggers),
                "entry_low": levels.entry_low,
                "entry_high": levels.entry_high,
                "stop_loss": levels.stop_loss,
                "take_profit": levels.take_profit,
                "suggested_lots": size["lots"],
                "reason": text,
                "detail": {
                    "name": names.get(sid),
                    "close": levels.close,
                    "score": {k: score[k] for k in ("total", "fundamental", "technical", "chips")},
                    "high_score_reasons": why,
                    "selected_by": selected[h][sid],
                    "triggers": triggers,
                    "trigger_text": trigger_text(triggers),
                    "levels": levels.detail,
                    "sizing": size,
                    "capital": capital,
                    "market_light": light,
                    "regime": regime["mode"],
                    "regime_label": regime["label"],
                    "size_factor": regime["size_factor"],
                    "rebound": rebound.get("state"),
                    # 大盤反彈模式的提示已由 regime 欄位表示，不在每檔重複
                    "warnings": [w for w in fl["warn"] if not w.startswith("大盤偏空")],
                    "forced": not ok,
                },
            })

    setups = _setups(setup_ids, frames, flags, scores, names, as_of, setup_cfg, risk, _watch_size,
                     watch_factor, {s["stock_id"] for s in signals})

    repo.replace_signals(as_of, "buy", signals)
    repo.conn.commit()
    return {
        "setups": setups,
        "status": "ok" if ok else "forced",
        "reason": gate_reason,
        "market_light": light,
        "regime": regime["mode"],
        "rebound": rebound.get("state"),
        "candidates": len(candidates),
        "signals": signals,
        "blocked": blocked,
    }


def _setups(ids, frames, flags, scores, names, as_of, cfg, risk, size_fn, factor, signaled) -> list[dict]:
    """醞釀名單：跌深＋強勢跡象，只差一根轉強 K 棒（engine/signals/setup.py）。"""
    out = []
    for sid in ids:
        f = frames.get(sid)
        if f is None or sid in signaled:
            continue
        fl = flags.get(sid, {"exclude": [], "block": [], "warn": []})
        if fl["exclude"]:
            continue
        signs = setup_signs(f.prices, f.chips, f.benchmark, as_of, cfg)
        if not signs or len(signs) < cfg["min_signs"]:
            continue
        lv = setup_levels(f.prices, as_of, cfg, risk)
        size = size_fn(sid, lv["entry_high"], lv["stop_loss"])
        notes = [r for r in fl["block"] if not r.startswith("大盤燈號偏空")]
        if lv["reward_risk"] is not None and lv["reward_risk"] < cfg.get("min_reward_risk", 1.5):
            notes.append(f"報酬風險比 {lv['reward_risk']:g} 偏低")
        score = scores.get((sid, "short")) or {}
        out.append({
            "stock_id": sid, "name": names.get(sid), "score": score.get("total"),
            "signs": sorted(signs), "sign_text": setup_text(signs), **lv,
            "suggested_lots": size["lots"], "odd_shares": size["odd_shares"], "size_factor": factor,
            "notes": notes,
        })
    out.sort(key=lambda x: (-len(x["signs"]), x["close"] / x["ma20"]))
    return out[: cfg["max_items"]]
