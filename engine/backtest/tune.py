"""正式回測調參（tune 指令）：只用調參區間，以座標輪替法找出「最大回撤不超過門檻下年化報酬最高」的參數。

做法：
1. 讀一次資料，用現行設定跑基準。
2. 對 config/tune.yaml 的每個參數，固定其他參數、逐一試每個值，留下最好的值；重複 rounds 輪直到不再改善。
   這些參數只影響風控與濾網，沿用同一份評分快取。
3. 在最佳風控參數下比較 rescoring 列出的評分權重組合（每組重新評分）。
保留驗證區間不參與；結果要另外用 backtest --holdout 驗證一次。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import yaml

from engine.backtest.runner import apply_overrides, evaluate, load_context, periods
from engine.config import CONFIG_DIR, Settings, _load_yaml

log = logging.getLogger(__name__)


def load_tune_config() -> dict[str, Any]:
    return _load_yaml("tune.yaml", CONFIG_DIR)


@dataclass
class Trial:
    overrides: dict[str, Any]
    metrics: dict[str, Any]
    seconds: float
    label: str = ""

    def feasible(self, obj: dict) -> bool:
        m = self.metrics
        return (m.get("max_drawdown") is not None and m["max_drawdown"] >= obj["max_drawdown"]
                and (m.get("trades") or 0) >= obj["min_trades"])

    def key(self, obj: dict) -> tuple:
        """排序鍵（大者為佳）：先看是否符合回撤與交易數門檻，符合者比年化報酬，不符合者比回撤。"""
        m = self.metrics
        cagr = m.get("cagr") if m.get("cagr") is not None else -9.0
        mdd = m.get("max_drawdown") if m.get("max_drawdown") is not None else -9.0
        return (1, cagr, mdd) if self.feasible(obj) else (0, mdd, cagr)


@dataclass
class TuneResult:
    baseline: Trial
    best: Trial
    trials: list[Trial] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    rescoring: list[Trial] = field(default_factory=list)
    period: tuple = ()
    universe: int = 0


def current_value(settings: Settings, path: str) -> Any:
    node: Any = getattr(settings, path.split(".")[0])
    for k in path.split(".")[1:]:
        node = node[k]
    return node


def _as_overrides(values: dict[str, Any]) -> dict[str, str]:
    return {k: json.dumps(v) for k, v in values.items()}


def run_tune(settings: Settings, repo, cfg: dict[str, Any] | None = None, progress=None) -> TuneResult:
    cfg = cfg or load_tune_config()
    obj = cfg["objective"]
    latest = repo.latest_series_date("taiex")
    start, stop = periods(settings, latest)["tune"]
    ctx = load_context(settings, repo, start, stop)
    repo.conn.rollback()  # 之後只在記憶體計算，不佔用資料庫交易
    cache: dict[str, Trial] = {}
    steps: list[dict[str, Any]] = []

    def run(values: dict[str, Any], label: str = "", rescore: bool = False) -> Trial:
        sig = json.dumps(values, sort_keys=True)
        if sig in cache and not rescore:
            return cache[sig]
        t0 = time.time()
        s = apply_overrides(settings, _as_overrides(values)) if values else settings
        metrics, _result, _plans = evaluate(ctx, s, reuse_scores=not rescore and ctx.planner is not None)
        trial = Trial(dict(values), metrics, time.time() - t0, label)
        if not rescore:
            cache[sig] = trial
        log.info("%s 年化 %s 回撤 %s 交易 %s（%.0f 秒）", label or sig, metrics.get("cagr"),
                 metrics.get("max_drawdown"), metrics.get("trades"), trial.seconds)
        if progress is not None:  # 每組跑完就寫出目前結果，逾時中斷也不會全部白費
            done = list(cache.values()) or [trial]
            top = max(done, key=lambda t: t.key(obj))
            progress(TuneResult(done[0], top, done, list(steps), [], (start, stop), len(ctx.frames)))
        return trial

    baseline = run({}, "基準（現行設定）")
    best_values: dict[str, Any] = {}
    best = baseline
    params: dict[str, list] = cfg["params"]
    for rnd in range(1, int(cfg.get("rounds", 1)) + 1):
        improved = False
        for path, values in params.items():
            base_val = best_values.get(path, current_value(settings, path))
            tried = []
            for v in values:
                cand = {**best_values, path: v}
                if v == current_value(settings, path) and path not in best_values:
                    cand = dict(best_values)  # 與現行設定相同＝不覆寫
                trial = run(cand, f"第 {rnd} 輪 {path}={v}")
                tried.append((v, trial))
            v_best, t_best = max(tried, key=lambda vt: vt[1].key(obj))
            steps.append({"round": rnd, "param": path, "from": base_val, "to": v_best,
                          "results": [(v, t.metrics.get("cagr"), t.metrics.get("max_drawdown"), t.metrics.get("trades"))
                                      for v, t in tried]})
            if t_best.key(obj) > best.key(obj):
                best, improved = t_best, True
                if v_best == current_value(settings, path):
                    best_values.pop(path, None)
                else:
                    best_values[path] = v_best
        if not improved:
            break

    rescored: list[Trial] = []
    for variant in cfg.get("rescoring") or []:
        values = {**best_values, **(variant.get("set") or {})}
        rescored.append(run(values, f"評分組合：{variant['name']}", rescore=bool(variant.get("set"))))
    winner = max([best, *rescored], key=lambda t: t.key(obj))
    return TuneResult(baseline, winner, list(cache.values()), steps, rescored, (start, stop), len(ctx.frames))


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1%}"


def render_tune(res: TuneResult, cfg: dict[str, Any]) -> str:
    obj = cfg["objective"]

    def row(label: str, t: Trial) -> str:
        m = t.metrics
        wr = "—" if m.get("win_rate") is None else f"{m['win_rate']:.0%}"
        ok = "符合" if t.feasible(obj) else "不符合"
        return (f"| {label} | {_pct(m.get('cagr'))} | {_pct(m.get('max_drawdown'))} | {m.get('trades')} | {wr} | "
                f"{_pct(m.get('expectancy_pct'))} | {ok} |")

    b0 = res.baseline.metrics.get("benchmarks", {})
    lines = [
        "# 正式回測調參｜調參區間",
        "",
        f"期間 {res.period[0]} 至 {res.period[1]}｜回測股票 {res.universe} 檔｜目標：最大回撤 ≥ {obj['max_drawdown']:.0%}、"
        f"交易至少 {obj['min_trades']} 筆，年化報酬最高｜共試 {len(res.trials) + len(res.rescoring)} 組",
        "",
        "| 組合 | 年化報酬 | 最大回撤 | 交易數 | 勝率 | 期望值 | 門檻 |",
        "|---|---:|---:|---:|---:|---:|---|",
        row("基準（現行設定）", res.baseline),
        row("最佳", res.best),
    ]
    for name, b in b0.items():
        label = {"taiex": "加權指數", "etf_0050": "0050（含息）"}.get(name, name)
        lines.append(f"| {label} | {_pct(b.get('cagr'))} | {_pct(b.get('max_drawdown'))} | — | — | — | — |")
    lines += ["", "## 最佳參數（相對現行設定的變動）", ""]
    if res.best.overrides:
        lines += [f"- `{k}`：{v}" for k, v in res.best.overrides.items()]
    else:
        lines.append("- 沒有變動：現行設定已是最佳")
    lines += ["", "## 每個參數的測試結果", "", "| 輪 | 參數 | 原值 → 選用 | 各值：年化／回撤／交易數 |", "|---|---|---|---|"]
    for st in res.steps:
        detail = "；".join(f"{v}：{_pct(c)}／{_pct(d)}／{n}" for v, c, d, n in st["results"])
        lines.append(f"| {st['round']} | `{st['param']}` | {st['from']} → {st['to']} | {detail} |")
    if res.rescoring:
        lines += ["", "## 評分權重比較（最佳風控參數下）", "",
                  "| 組合 | 年化報酬 | 最大回撤 | 交易數 | 勝率 | 期望值 | 門檻 |",
                  "|---|---:|---:|---:|---:|---:|---|"]
        lines += [row(t.label.replace("評分組合：", ""), t) for t in res.rescoring]
    lines += [
        "",
        "注意：自選股是事後挑選的（選擇偏差），消息面不參與回測，結果偏樂觀；參數是在調參區間挑出來的，"
        "必須再用保留驗證區間驗證一次（backtest --holdout），驗證結果才代表實際可期待的水準。",
        "",
        "最佳參數（可直接用於 backtest --set）：",
        "",
        "```",
        " ".join(f"--set {k}={json.dumps(v)}" for k, v in res.best.overrides.items()) or "（無）",
        "```",
        "",
    ]
    return "\n".join(lines)


def best_overrides_yaml(res: TuneResult) -> str:
    return yaml.safe_dump(res.best.overrides, allow_unicode=True, sort_keys=False)
