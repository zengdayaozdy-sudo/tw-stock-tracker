-- 初始資料表。日期欄位一律為台灣時區的交易日（date），時間戳記為 timestamptz。

CREATE TABLE IF NOT EXISTS stocks (
    stock_id      text PRIMARY KEY,
    name          text NOT NULL,
    market        text NOT NULL CHECK (market IN ('twse', 'tpex')),
    industry      text,
    market_cap    numeric,
    status        text[] NOT NULL DEFAULT '{}',   -- disposition / attention / full_delivery
    listed_date   date,
    delisted_date date,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watchlist (
    stock_id   text PRIMARY KEY REFERENCES stocks(stock_id),
    note       text,
    added_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS groups (
    group_id   serial PRIMARY KEY,
    name       text NOT NULL UNIQUE,
    kind       text NOT NULL CHECK (kind IN ('industry', 'custom')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id int  NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    stock_id text NOT NULL REFERENCES stocks(stock_id),
    PRIMARY KEY (group_id, stock_id)
);

CREATE TABLE IF NOT EXISTS daily_prices (
    stock_id text    NOT NULL REFERENCES stocks(stock_id),
    date     date    NOT NULL,
    open     numeric,
    high     numeric,
    low      numeric,
    close    numeric,
    volume   bigint,        -- 股數
    turnover numeric,       -- 成交金額
    PRIMARY KEY (stock_id, date)
);

-- 月營收與季報。published_date 用來避免偷看未來。
CREATE TABLE IF NOT EXISTS financials (
    stock_id       text NOT NULL REFERENCES stocks(stock_id),
    period_type    text NOT NULL CHECK (period_type IN ('month', 'quarter')),
    period         text NOT NULL,          -- 例：2026-09、2026Q3
    published_date date NOT NULL,
    metrics        jsonb NOT NULL,          -- 營收、EPS、毛利率、ROE 等
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_id, period_type, period)
);
CREATE INDEX IF NOT EXISTS financials_published_idx ON financials (stock_id, published_date);

CREATE TABLE IF NOT EXISTS valuations (
    stock_id   text NOT NULL REFERENCES stocks(stock_id),
    date       date NOT NULL,
    pe         numeric,
    pb         numeric,
    dividend_yield numeric,
    PRIMARY KEY (stock_id, date)
);

CREATE TABLE IF NOT EXISTS chips (
    stock_id        text NOT NULL REFERENCES stocks(stock_id),
    date            date NOT NULL,
    foreign_net     bigint,
    trust_net       bigint,
    dealer_net      bigint,
    foreign_holding_pct numeric,
    margin_balance  bigint,
    short_balance   bigint,
    major_holder_pct numeric,     -- 股權分散表（每週）
    branches        jsonb,        -- 券商分點（feature flag 控制）
    PRIMARY KEY (stock_id, date)
);

CREATE TABLE IF NOT EXISTS news (
    news_id           bigserial PRIMARY KEY,
    stock_id          text REFERENCES stocks(stock_id),
    published_at      timestamptz NOT NULL,
    source            text NOT NULL,
    title             text NOT NULL,
    url               text,
    is_major_negative boolean,
    event_type        text,
    confidence        numeric,
    summary           text,
    judged_by         text,         -- ollama / claude
    UNIQUE (stock_id, url)
);

CREATE TABLE IF NOT EXISTS market (
    date        date PRIMARY KEY,
    light       text CHECK (light IN ('bullish', 'neutral', 'bearish')),
    inputs      jsonb NOT NULL DEFAULT '{}',
    group_strength jsonb NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS events (
    event_id   bigserial PRIMARY KEY,
    stock_id   text REFERENCES stocks(stock_id),
    date       date NOT NULL,
    event_type text NOT NULL,      -- 財報、月營收、除權息、法說會、股東會
    note       text,
    UNIQUE (stock_id, date, event_type)
);

CREATE TABLE IF NOT EXISTS scores (
    stock_id   text NOT NULL REFERENCES stocks(stock_id),
    date       date NOT NULL,
    horizon    text NOT NULL CHECK (horizon IN ('short', 'long')),
    total      numeric,
    fundamental numeric,
    technical  numeric,
    chips      numeric,
    breakdown  jsonb NOT NULL,       -- 每個指標的原始值、分數、權重、貢獻度
    status     text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'insufficient_data', 'excluded')),
    PRIMARY KEY (stock_id, date, horizon)
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id   bigserial PRIMARY KEY,
    stock_id    text NOT NULL REFERENCES stocks(stock_id),
    date        date NOT NULL,
    horizon     text NOT NULL CHECK (horizon IN ('short', 'long')),
    side        text NOT NULL CHECK (side IN ('buy', 'sell')),
    triggers    text[] NOT NULL,
    entry_low   numeric,
    entry_high  numeric,
    stop_loss   numeric,
    take_profit numeric,
    suggested_lots int,
    reason      text,
    UNIQUE (stock_id, date, horizon, side)
);

CREATE TABLE IF NOT EXISTS holdings (
    stock_id      text PRIMARY KEY REFERENCES stocks(stock_id),
    shares        int NOT NULL,
    avg_cost      numeric NOT NULL,
    opened_date   date NOT NULL,
    stop_loss     numeric,
    take_profit   numeric,
    highest_close numeric,          -- 移動停利用
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id   bigserial PRIMARY KEY,
    stock_id   text NOT NULL REFERENCES stocks(stock_id),
    date       date NOT NULL,
    side       text NOT NULL CHECK (side IN ('buy', 'sell')),
    price      numeric NOT NULL,
    shares     int NOT NULL,
    fee        numeric NOT NULL DEFAULT 0,
    tax        numeric NOT NULL DEFAULT 0,
    score_snapshot  jsonb,          -- 當時的分數
    signal_snapshot jsonb,          -- 當時的訊號
    note       text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS settings (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO settings (key, value) VALUES
    ('total_capital', '1000000'),
    ('notify', '{"daily_report": true, "intraday_stop_loss": true, "system_alerts": true}')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS job_logs (
    log_id     bigserial PRIMARY KEY,
    job        text NOT NULL,       -- daily / monitor_heartbeat / backtest …
    run_date   date,
    status     text NOT NULL CHECK (status IN ('running', 'success', 'failed', 'skipped', 'heartbeat')),
    detail     jsonb NOT NULL DEFAULT '{}',
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS job_logs_job_idx ON job_logs (job, started_at DESC);

CREATE TABLE IF NOT EXISTS api_usage (
    provider   text NOT NULL,       -- finmind / claude
    hour       timestamptz NOT NULL,
    calls      int NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, hour)
);
