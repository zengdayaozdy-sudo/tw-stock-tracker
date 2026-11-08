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
- [x] 3. 評分引擎
- [ ] 4. 濾網
- [ ] 5. 訊號與風控
- [ ] 6. LINE 報告
- [ ] 7. 持股追蹤與交易日誌
- [ ] 8. 盤中停損監控
- [ ] 9. 個人網站
- [ ] 10. 回測
- [ ] 11. 排程與維運文件

## 已完成的前置設定

| 項目 | 狀態 |
|---|---|
| GitHub 私人 repo、CI | 完成 |
| Neon 資料庫（專案 tw-stock-tracker，round-violet-78597036） | 完成，migration 001、002 已套用 |
| GitHub Secrets：DATABASE_URL、FINMIND_TOKEN | 完成 |
| 證交所／櫃買資料欄位 probe | 完成，2026-11-06 全市場實際抓取通過（約 1,980 檔，26 秒） |
| 股票清單、產業族群、自訂族群 | 完成：1,990 家公司、35 個產業族群＋5 個自訂族群 |
| LINE Messaging API（LINE_CHANNEL_ACCESS_TOKEN、LINE_OWNER_USER_ID） | 第 6 階段前由擁有者設定 |
| Vercel 專案（root 設為 web/，環境變數 DATABASE_URL、SITE_PASSWORD、SESSION_SECRET） | 第 9 階段建立 |
| 自選股 | 擁有者在程式完成後自行加入 |

## 手動操作（GitHub → Actions → 手動操作 → Run workflow）

- `watch-add` 參數填股票代號（空白分隔）加入自選股；`backfill` 回補自選股 3 年歷史
- `companies` 更新公司資料與股數；`seed-groups` 依 config/groups.yaml 建立族群
- 每日收盤後批次可指定日期補跑

## 交給 Claude Code 接手

1. 用 Claude Code 開啟本 repo
2. 指示：「讀 CLAUDE.md、README.md、docs/open-questions.md，從第 14 節第 4 階段開始開發」
3. 第 6 階段（LINE）前，擁有者需在 GitHub Secrets 加上 `LINE_CHANNEL_ACCESS_TOKEN`、`LINE_OWNER_USER_ID`
4. 第 9 階段（網站）前，在 Vercel 匯入本 repo，Root Directory 設為 `web`，並設定 `DATABASE_URL`、`SITE_PASSWORD`、`SESSION_SECRET`
