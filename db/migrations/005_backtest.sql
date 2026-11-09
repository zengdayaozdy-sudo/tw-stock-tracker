-- 第 10 階段：回測紀錄（保留驗證區間只允許跑一次，以這張表判斷）
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id      bigserial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    period      text NOT NULL CHECK (period IN ('tune', 'holdout', 'full')),
    start_date  date NOT NULL,
    end_date    date NOT NULL,
    plan        text NOT NULL,
    params      jsonb NOT NULL,      -- 參數覆寫與設定雜湊
    fingerprint text NOT NULL,       -- 設定＋資料指紋，相同代表可重現
    metrics     jsonb NOT NULL,
    trades      jsonb NOT NULL,
    equity      jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS backtest_runs_period_idx ON backtest_runs (period, created_at DESC);
