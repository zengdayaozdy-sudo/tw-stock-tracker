-- 每日盤勢總結與隔日操作計畫（2026-11-12）：每個交易日一筆，收盤後與晚間補抓後各重算一次
--   facts：盤型、均線、寬度、成交值、法人估算、權值股貢獻、族群漲跌、隔夜美股與匯率、消息
--   reasons：規則歸納的漲跌原因；plan：隔日盤勢方針、關鍵價位、情境、持股處理、進場名單
--   narrative：一段文字總結（AI 改寫，或規則組合）；generated_by：ollama／claude／rules
CREATE TABLE IF NOT EXISTS daily_summaries (
    date         date PRIMARY KEY,
    facts        jsonb NOT NULL DEFAULT '{}',
    reasons      jsonb NOT NULL DEFAULT '[]',
    plan         jsonb NOT NULL DEFAULT '{}',
    narrative    text,
    generated_by text,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
