-- 第 4 階段：濾網

-- 大盤燈號用的時間序列（加權指數、費半、台積電 ADR、匯率、外資台指期未平倉）
CREATE TABLE IF NOT EXISTS market_series (
    series text    NOT NULL,      -- taiex / sox / tsm_adr / usd_twd / foreign_tx_oi
    date   date    NOT NULL,
    value  numeric NOT NULL,
    PRIMARY KEY (series, date)
);

-- 每日濾網結果。每個濾網步驟只覆寫自己的資料，可單獨重跑。
--   kind = exclude：當日排除（流動性、消息面）
--   kind = block：不發買進訊號（大盤偏空、弱勢族群）
--   kind = warn：只提示（事件行事曆）
CREATE TABLE IF NOT EXISTS screen_flags (
    stock_id text NOT NULL REFERENCES stocks(stock_id),
    date     date NOT NULL,
    filter   text NOT NULL CHECK (filter IN ('liquidity', 'news', 'market', 'group', 'event')),
    kind     text NOT NULL CHECK (kind IN ('exclude', 'block', 'warn')),
    reason   text NOT NULL,
    detail   jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (stock_id, date, filter, reason)
);
CREATE INDEX IF NOT EXISTS screen_flags_date_idx ON screen_flags (date, filter);

-- 重大訊息沒有個別網址，改用去重鍵；內文供網站顯示與 AI 判讀
ALTER TABLE news ADD COLUMN IF NOT EXISTS dedup_key text;
ALTER TABLE news ADD COLUMN IF NOT EXISTS body text;
ALTER TABLE news ADD COLUMN IF NOT EXISTS judged_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS news_dedup_idx ON news (dedup_key);
CREATE INDEX IF NOT EXISTS news_stock_time_idx ON news (stock_id, published_at DESC);

-- 事件來源：auto（公開資料）／manual（網站手動登錄，例如法說會）
ALTER TABLE events ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'auto';
CREATE INDEX IF NOT EXISTS events_date_idx ON events (date);
