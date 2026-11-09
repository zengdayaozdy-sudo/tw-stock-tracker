-- 新增指標所需欄位（2026-11-09）
-- 借券賣出餘額（股）：每日，上市取證交所 TWT93U、上櫃取櫃買 tpex_margin_sbl，歷史由 FinMind 回補
ALTER TABLE chips ADD COLUMN IF NOT EXISTS sbl_balance bigint;
-- 董監質押比（%）：證交所／櫃買 t187ap09 彙總表，只有最新一期
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS pledge_ratio numeric;
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS pledge_date date;
