import "server-only";
// 寫入資料庫的核心規則，網站 Server Action 與 MCP 工具共用，兩邊結果必須一致。
// 呼叫端負責驗證登入（requireSession 或 MCP token）與重新整理頁面快取。

import { db } from "./db";
import { runtimeConfig } from "./queries";
import { commission, sellTax, stopPrice } from "./trading";

export type Result = { ok: boolean; message: string };

export const STOCK_ID = /^[0-9A-Z]{4,6}$/;

const ok = (message: string): Result => ({ ok: true, message });
const fail = (message: string): Result => ({ ok: false, message });

export function parseIds(text: string): string[] {
  return [...new Set(text.toUpperCase().split(/[\s,，、]+/).filter(Boolean))];
}

// ---------- 自選股 ----------

export async function addToWatchlist(ids: string[]): Promise<Result> {
  if (!ids.length) return fail("請輸入股票代號");
  if (ids.some((id) => !STOCK_ID.test(id))) return fail("股票代號格式不正確");
  const sql = db();
  const known = await sql`SELECT stock_id FROM stocks WHERE stock_id = ANY(${ids})`;
  const knownSet = new Set(known.map((r) => r.stock_id as string));
  const unknown = ids.filter((id) => !knownSet.has(id));
  if (unknown.length) return fail(`股票清單中找不到：${unknown.join("、")}（只支援上市櫃普通股）`);
  const { config } = await runtimeConfig();
  if (config.watchlist_limit) {
    const current = await sql`SELECT stock_id FROM watchlist`;
    const total = new Set([...current.map((r) => r.stock_id as string), ...ids]).size;
    if (total > config.watchlist_limit) return fail(`${config.plan_label}自選股上限 ${config.watchlist_limit} 檔`);
  }
  await sql`INSERT INTO watchlist ${sql(ids.map((stock_id) => ({ stock_id })))} ON CONFLICT DO NOTHING`;
  return ok(`已加入 ${ids.join("、")}，下一次每日排程會自動回補歷史資料`);
}

export async function removeFromWatchlist(ids: string[]): Promise<Result> {
  if (!ids.length) return fail("請輸入股票代號");
  const res = await db()`DELETE FROM watchlist WHERE stock_id = ANY(${ids}) RETURNING stock_id`;
  if (!res.length) return fail(`自選股中沒有 ${ids.join("、")}`);
  return ok(`已移除 ${res.map((r) => r.stock_id).join("、")}`);
}

// ---------- 自訂族群 ----------

export async function createCustomGroup(rawName: string): Promise<Result> {
  const name = rawName.trim();
  if (!name || name.length > 30) return fail("族群名稱 1–30 字");
  const sql = db();
  const exists = await sql`SELECT kind FROM groups WHERE name = ${name}`;
  if (exists.length) return fail(`已有同名族群（${exists[0].kind === "industry" ? "產業" : "自訂"}）`);
  await sql`INSERT INTO groups (name, kind) VALUES (${name}, 'custom')`;
  return ok(`已建立族群「${name}」`);
}

export async function renameCustomGroup(id: number, rawName: string): Promise<Result> {
  const name = rawName.trim();
  if (!name || name.length > 30) return fail("族群名稱 1–30 字");
  const sql = db();
  const dup = await sql`SELECT 1 FROM groups WHERE name = ${name} AND group_id <> ${id}`;
  if (dup.length) return fail("已有同名族群");
  const res = await sql`UPDATE groups SET name = ${name} WHERE group_id = ${id} AND kind = 'custom'`;
  if (!res.count) return fail("只能修改自訂族群");
  return ok("已更名");
}

export async function deleteCustomGroup(id: number): Promise<Result> {
  const res = await db()`DELETE FROM groups WHERE group_id = ${id} AND kind = 'custom' RETURNING name`;
  if (!res.length) return fail("只能刪除自訂族群");
  return ok(`已刪除族群「${res[0].name}」`);
}

export async function addGroupMembers(id: number, ids: string[]): Promise<Result> {
  if (!ids.length) return fail("請輸入股票代號");
  const sql = db();
  const group = await sql`SELECT kind FROM groups WHERE group_id = ${id}`;
  if (group[0]?.kind !== "custom") return fail("只能修改自訂族群");
  const known = new Set((await sql`SELECT stock_id FROM stocks WHERE stock_id = ANY(${ids})`).map((r) => r.stock_id as string));
  const unknown = ids.filter((s) => !known.has(s));
  if (unknown.length) return fail(`找不到：${unknown.join("、")}`);
  await sql`INSERT INTO group_members ${sql(ids.map((stock_id) => ({ group_id: id, stock_id })))} ON CONFLICT DO NOTHING`;
  return ok(`已加入 ${ids.join("、")}`);
}

export async function removeGroupMember(id: number, stockId: string): Promise<Result> {
  const res = await db()`
    DELETE FROM group_members WHERE group_id = ${id} AND stock_id = ${stockId}
      AND EXISTS (SELECT 1 FROM groups WHERE group_id = ${id} AND kind = 'custom')`;
  if (!res.count) return fail(`自訂族群中沒有 ${stockId}`);
  return ok(`已移除 ${stockId}`);
}

// ---------- 交易日誌（與 engine/portfolio/trades.py 相同規則） ----------

function addDays(d: string, n: number): string {
  const t = new Date(`${d}T00:00:00Z`);
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
}

export type TradeInput = {
  stockId: string;
  side: string;
  date: string;
  price: number;
  /** lots＝張、shares＝股 */
  unit: string;
  quantity: number;
  /** 留空（null）時自動計算 */
  fee: number | null;
  tax: number | null;
  note: string | null;
};

export async function recordTrade(input: TradeInput): Promise<Result> {
  const { side, date, price, unit, quantity: qty, note } = input;
  const stockId = input.stockId.trim().toUpperCase();
  if (!STOCK_ID.test(stockId)) return fail("股票代號格式不正確");
  if (side !== "buy" && side !== "sell") return fail("請選擇買進或賣出");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return fail("日期格式不正確");
  if (!(price > 0) || !(qty > 0) || !Number.isInteger(qty)) return fail("價格與數量必須大於 0，數量為整數");

  const { config } = await runtimeConfig();
  const risk = config.risk;
  const shares = unit === "lots" ? qty * risk.position.lot_size : qty;
  const fee = input.fee ?? commission(price, shares, risk.costs);
  const tax = input.tax ?? (side === "sell" ? sellTax(price, shares, risk.costs) : 0);
  if (!(fee >= 0) || !(tax >= 0)) return fail("手續費與證交稅不可為負");

  try {
    await db().begin(async (sql) => {
      const stock = await sql`SELECT name FROM stocks WHERE stock_id = ${stockId}`;
      if (!stock.length) throw new Error(`股票清單中找不到 ${stockId}`);
      const lookback = addDays(date, -(risk.exit.signal_lookback_days ?? 5));
      const signals = await sql`
        SELECT date, horizon, triggers, entry_low, entry_high, stop_loss, take_profit, suggested_lots, reason
        FROM signals WHERE stock_id = ${stockId} AND side = ${side} AND date BETWEEN ${lookback} AND ${date}
        ORDER BY date DESC, horizon DESC LIMIT 1`;
      const signal = signals[0] ?? null;
      const scoreRows = await sql`
        SELECT DISTINCT ON (horizon) horizon, date, total, fundamental, technical, chips, status FROM scores
        WHERE stock_id = ${stockId} AND date <= ${date} ORDER BY horizon, date DESC`;
      const scores = Object.fromEntries(scoreRows.map((r) => [r.horizon, { ...r, horizon: undefined }]));
      const held = (await sql`SELECT * FROM holdings WHERE stock_id = ${stockId} FOR UPDATE`)[0];

      if (side === "sell") {
        if (!held) throw new Error(`目前沒有持有 ${stockId}，無法登錄賣出`);
        if (shares > held.shares) throw new Error(`賣出 ${shares.toLocaleString()} 股超過持有 ${held.shares.toLocaleString()} 股`);
      }

      await sql`
        INSERT INTO trades (stock_id, date, side, price, shares, fee, tax, score_snapshot, signal_snapshot, note)
        VALUES (${stockId}, ${date}, ${side}, ${price}, ${shares}, ${fee}, ${tax}, ${sql.json(scores)},
                ${signal ? sql.json(signal) : null}, ${note})`;

      if (side === "sell") {
        const remaining = held.shares - shares;
        if (remaining === 0) await sql`DELETE FROM holdings WHERE stock_id = ${stockId}`;
        else await sql`UPDATE holdings SET shares = ${remaining}, updated_at = now() WHERE stock_id = ${stockId}`;
        return;
      }

      const cost = price * shares + fee;
      let stop = held?.stop_loss ?? null;
      let target = held?.take_profit ?? null;
      if (signal?.stop_loss) {
        stop = signal.stop_loss;
        target = signal.take_profit;
      } else if (stop === null) {
        const closes = await sql`
          SELECT close FROM daily_prices WHERE stock_id = ${stockId} AND date <= ${date} AND close IS NOT NULL
          ORDER BY date DESC LIMIT 60`;
        const ma60 = closes.length >= 60 ? closes.reduce((a, r) => a + r.close, 0) / 60 : null;
        stop = stopPrice(price, ma60, risk.stop_loss);
      }
      if (held) {
        const total = held.shares + shares;
        const avg = (held.avg_cost * held.shares + cost) / total;
        await sql`
          UPDATE holdings SET shares = ${total}, avg_cost = ${Math.round(avg * 10000) / 10000}, stop_loss = ${stop},
            take_profit = ${target}, highest_close = greatest(coalesce(highest_close, 0), ${price}), updated_at = now()
          WHERE stock_id = ${stockId}`;
      } else {
        await sql`
          INSERT INTO holdings (stock_id, shares, avg_cost, opened_date, stop_loss, take_profit, highest_close)
          VALUES (${stockId}, ${shares}, ${Math.round((cost / shares) * 10000) / 10000}, ${date}, ${stop}, ${target}, ${price})`;
      }
    });
  } catch (e) {
    return fail(`無法登錄：${(e as Error).message}`);
  }
  const word = side === "buy" ? "買進" : "賣出";
  return ok(`已登錄 ${date} ${word} ${stockId} ${shares.toLocaleString()} 股 @ ${price}（手續費 ${fee}、證交稅 ${tax}）`);
}

export async function updateHoldingLevels(id: string, stop: number | null, target: number | null): Promise<Result> {
  if ((stop !== null && !(stop > 0)) || (target !== null && !(target > 0))) return fail("價格必須大於 0");
  const res = await db()`
    UPDATE holdings SET stop_loss = ${stop}, take_profit = ${target}, updated_at = now() WHERE stock_id = ${id}`;
  if (!res.count) return fail(`目前沒有持有 ${id}`);
  return ok(`${id} 停損停利已更新`);
}
