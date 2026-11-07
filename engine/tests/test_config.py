import shutil
from pathlib import Path

import pytest
import yaml

from engine.config import CONFIG_DIR, ConfigError, load_settings
from engine.db import migration_files


def test_free_plan_locks_paid_features():
    s = load_settings("free")
    assert s.plan.universe == "watchlist"
    assert not s.enabled("full_market_scan")
    assert not s.enabled("broker_branches")
    assert set(s.locked_features()) == set(s.all_features)


def test_upgrade_unlocks_without_code_change():
    free = load_settings("free")
    sponsor = load_settings("sponsor")
    pro = load_settings("pro")
    assert sponsor.enabled("full_market_scan")
    assert pro.enabled("broker_branches")
    assert len(pro.locked_features()) < len(sponsor.locked_features()) < len(free.locked_features())


def test_plan_from_env(monkeypatch):
    monkeypatch.setenv("PLAN", "PRO")
    assert load_settings().plan.name == "pro"


def test_invalid_plan():
    with pytest.raises(ConfigError):
        load_settings("gold")


def test_unknown_feature_raises():
    with pytest.raises(ConfigError):
        load_settings("free").enabled("auto_trading")


def test_weights_sum_to_one():
    s = load_settings("free")
    for weights in s.scoring["weights"].values():
        assert sum(weights.values()) == pytest.approx(1)


def test_bad_weights_rejected(tmp_path: Path):
    cfg = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, cfg)
    data = yaml.safe_load((cfg / "scoring.yaml").read_text(encoding="utf-8"))
    data["weights"]["short"]["technical"] = 0.9
    (cfg / "scoring.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings("free", config_dir=cfg)


def test_migrations_cover_all_tables():
    sql = "\n".join(p.read_text(encoding="utf-8") for p in migration_files())
    for table in [
        "stocks", "watchlist", "groups", "daily_prices", "financials", "chips", "news",
        "market", "events", "scores", "signals", "holdings", "trades", "settings", "job_logs",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table} " in sql, table
    assert "published_date" in sql
