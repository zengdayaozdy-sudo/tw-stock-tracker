# 台股自選股評分追蹤程式

個人自用的台股選股與追蹤程式。完整規格見 [CLAUDE.md](CLAUDE.md)。

## 快速開始

```bash
cp .env.example .env          # 填入各項金鑰
pip install -e ".[dev]"
python -m engine.cli plan     # 檢視目前方案與鎖定功能
python -m engine.cli migrate  # 建立資料表（需 DATABASE_URL）
pytest -q
```

## 開發進度

- [x] 1. 專案骨架、設定檔、方案開關、資料庫結構
- [x] 2. 資料來源、額度管理、完整性檢查
- [ ] 3. 評分引擎
- [ ] 4. 濾網
- [ ] 5. 訊號與風控
- [ ] 6. LINE 報告
- [ ] 7. 持股追蹤與交易日誌
- [ ] 8. 盤中停損監控
- [ ] 9. 個人網站
- [ ] 10. 回測
- [ ] 11. 排程與維運文件
