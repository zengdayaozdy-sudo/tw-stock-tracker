-- FinMind 在無成交日回傳價格 0 的資料列：視為沒有成交，刪除
DELETE FROM daily_prices WHERE close IS NOT NULL AND close <= 0;
UPDATE daily_prices SET open = NULL WHERE open <= 0;
UPDATE daily_prices SET high = NULL WHERE high <= 0;
UPDATE daily_prices SET low = NULL WHERE low <= 0;

-- 除權息、分割、減資的參考價，用來還原股價（不含外鍵：0050 等 ETF 也會用到）
CREATE TABLE IF NOT EXISTS price_adjustments (
    stock_id     text NOT NULL,
    date         date NOT NULL,          -- 除權息／恢復交易日
    kind         text NOT NULL CHECK (kind IN ('dividend', 'split', 'capital_reduction')),
    before_price numeric NOT NULL,       -- 前一日收盤
    after_price  numeric NOT NULL,       -- 參考價
    PRIMARY KEY (stock_id, date, kind)
);
