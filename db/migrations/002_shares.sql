-- 發行股數，用來計算市值（股數 × 收盤價）
ALTER TABLE stocks ADD COLUMN IF NOT EXISTS shares bigint;
