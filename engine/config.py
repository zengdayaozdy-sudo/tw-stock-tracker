"""設定檔載入與方案功能開關。

程式各處只透過 `Settings.enabled(feature)` 判斷功能是否可用，
不直接比對方案名稱，升級方案時只需修改環境變數 PLAN。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
TZ = ZoneInfo("Asia/Taipei")

VALID_PLANS = ("free", "sponsor", "pro")


class ConfigError(ValueError):
    """設定錯誤。"""


def _load_yaml(name: str, config_dir: Path) -> dict[str, Any]:
    path = config_dir / name
    if not path.exists():
        raise ConfigError(f"找不到設定檔：{path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class Plan:
    name: str
    label: str
    universe: str
    watchlist_limit: int | None
    features: frozenset[str]


@dataclass(frozen=True)
class Settings:
    plan: Plan
    scoring: dict[str, Any]
    risk: dict[str, Any]
    filters: dict[str, Any]
    groups: dict[str, Any]
    sources: dict[str, Any] = field(default_factory=dict)
    notify: dict[str, Any] = field(default_factory=dict)
    backtest: dict[str, Any] = field(default_factory=dict)
    all_features: dict[str, str] = field(default_factory=dict)

    def enabled(self, feature: str) -> bool:
        if feature not in self.all_features:
            raise ConfigError(f"未定義的功能開關：{feature}")
        return feature in self.plan.features

    def locked_features(self) -> dict[str, str]:
        """目前方案無法使用的功能（網站以鎖頭顯示）。"""
        return {k: v for k, v in self.all_features.items() if k not in self.plan.features}


def _validate_scoring(scoring: dict[str, Any]) -> None:
    for horizon, weights in scoring["weights"].items():
        total = round(sum(weights.values()), 6)
        if total != 1:
            raise ConfigError(f"{horizon} 面向權重加總為 {total}，應為 1")
    for dimension, weights in scoring["indicators"].items():
        total = round(sum(weights.values()), 6)
        if total != 1:
            raise ConfigError(f"{dimension} 指標權重加總為 {total}，應為 1")


def load_settings(plan_name: str | None = None, config_dir: Path = CONFIG_DIR) -> Settings:
    plan_name = (plan_name or os.environ.get("PLAN") or "free").strip().lower()
    if plan_name not in VALID_PLANS:
        raise ConfigError(f"PLAN 必須是 {'、'.join(VALID_PLANS)} 之一，目前為 {plan_name}")

    plans_cfg = _load_yaml("plans.yaml", config_dir)
    all_features: dict[str, str] = plans_cfg["features"]
    raw = plans_cfg["plans"][plan_name]
    unknown = set(raw.get("features") or []) - set(all_features)
    if unknown:
        raise ConfigError(f"方案 {plan_name} 含未定義功能：{sorted(unknown)}")

    plan = Plan(
        name=plan_name,
        label=raw["label"],
        universe=raw["universe"],
        watchlist_limit=raw.get("watchlist_limit"),
        features=frozenset(raw.get("features") or []),
    )
    scoring = _load_yaml("scoring.yaml", config_dir)
    _validate_scoring(scoring)

    return Settings(
        plan=plan,
        scoring=scoring,
        risk=_load_yaml("risk.yaml", config_dir),
        filters=_load_yaml("filters.yaml", config_dir),
        groups=_load_yaml("groups.yaml", config_dir),
        sources=_load_yaml("sources.yaml", config_dir),
        notify=_load_yaml("notify.yaml", config_dir),
        backtest=_load_yaml("backtest.yaml", config_dir),
        all_features=all_features,
    )


def load_dotenv(path: Path = ROOT / ".env") -> int:
    """讀取專案根目錄的 .env（本機常駐程式用）；已存在的環境變數不覆寫。回傳設定的變數數。"""
    if not path.exists():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            n += 1
    return n


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
