"use server";
// 所有寫入操作。每個動作先驗證登入，再寫資料庫，最後重新整理相關頁面。

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { db } from "@/lib/db";
import { runtimeConfig } from "@/lib/queries";
import { requireSession } from "@/lib/session";
import { commission, sellTax, stopPrice } from "@/lib/trading";

export type ActionState = { ok: boolean; message: string } | null;

const STOCK_ID = /^[0-9A-Z]{4,6}$/;

function ok(message: string): ActionState {
  return { ok: true, message };
}
function fail(message: string): ActionState {
  return { ok: false, message };
}

function parseIds(text: string): string[] {
  return [...new Set(text.toUpperCase().split(/[\s,，、]+/).filter(Boolean))];
}

// ---------- 自選股 ----------

export async function addWatchlist(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const ids = parseIds(String(form.get("stocks") ?? ""));
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
  revalidatePath("/watchlist");
  return ok(`已加入 ${ids.join("、")}，下一次每日排程會自動回補歷史資料`);
}

export async function removeWatchlist(form: FormData): Promise<void> {
  await requireSession();
  const id = String(form.get("stock_id") ?? "");
  await db()`DELETE FROM watchlist WHERE stock_id = ${id}`;
  revalidatePath("/watchlist");
}

// ---------- 自訂族群 ----------

export async function createGroup(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const name = String(form.get("name") ?? "").trim();
  if (!name || name.length > 30) return fail("族群名稱 1–30 字");
  const sql = db();
  const exists = await sql`SELECT kind FROM groups WHERE name = ${name}`;
  if (exists.length) return fail(`已有同名族群（${exists[0].kind === "industry" ? "產業" : "自訂"}）`);
  await sql`INSERT INTO groups (name, kind) VALUES (${name}, 'custom')`;
  revalidatePath("/watchlist");
  return ok(`已建立族群「${name}」`);
}

export async function renameGroup(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const id = Number(form.get("group_id"));
  const name = String(form.get("name") ?? "").trim();
  if (!name || name.length > 30) return fail("族群名稱 1–30 字");
  const sql = db();
  const dup = await sql`SELECT 1 FROM groups WHERE name = ${name} AND group_id <> ${id}`;
  if (dup.length) return fail("已有同名族群");
  const res = await sql`UPDATE groups SET name = ${name} WHERE group_id = ${id} AND kind = 'custom'`;
  if (!res.count) return fail("只能修改自訂族群");
  revalidatePath("/watchlist");
  revalidatePath("/groups");
  return ok("已更名");
}

export async function deleteGroup(form: FormData): Promise<void> {
  await requireSession();
  const id = Number(form.get("group_id"));
  await db()`DELETE FROM groups WHERE group_id = ${id} AND kind = 'custom'`;
  revalidatePath("/watchlist");
  revalidatePath("/groups");
}

export async function addGroupMembers(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const id = Number(form.get("group_id"));
  const ids = parseIds(String(form.get("stocks") ?? ""));
  if (!ids.length) return fail("請輸入股票代號");
  const sql = db();
  const group = await sql`SELECT kind FROM groups WHERE group_id = ${id}`;
  if (group[0]?.kind !== "custom") return fail("只能修改自訂族群");
  const known = new Set((await sql`SELECT stock_id FROM stocks WHERE stock_id = ANY(${ids})`).map((r) => r.stock_id as string));
  const unknown = ids.filter((s) => !known.has(s));
  if (unknown.length) return fail(`找不到：${unknown.join("、")}`);
  await sql`INSERT INTO group_members ${sql(ids.map((stock_id) => ({ group_id: id, stock_id })))} ON CONFLICT DO NOTHING`;
  revalidatePath("/watchlist");
  revalidatePath("/groups");
  return ok(`已加入 ${ids.join("、")}`);
}

export async function removeGroupMember(form: FormData): Promise<void> {
  await requireSession();
  const id = Number(form.get("group_id"));
  const stock = String(form.get("stock_id") ?? "");
  await db()`
    DELETE FROM group_members WHERE group_id = ${id} AND stock_id = ${stock}
      AND EXISTS (SELECT 1 FROM groups WHERE group_id = ${id} AND kind = 'custom')`;
  revalidatePath("/watchlist");
  revalidatePath("/groups");
}

// ---------- 交易日誌（與 engine/portfolio/trades.py 相同規則） ----------

function addDays(d: string, n: number): string {
  const t = new Date(`${d}T00:00:00Z`);
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
}

export async function recordTrade(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const stockId = String(form.get("stock_id") ?? "").trim().toUpperCase();
  const side = String(form.get("side") ?? "");
  const date = String(form.get("date") ?? "");
  const price = Number(form.get("price"));
  const unit = String(form.get("unit") ?? "lots");
  const qty = Number(form.get("quantity"));
  const feeText = String(form.get("fee") ?? "").trim();
  const taxText = String(form.get("tax") ?? "").trim();
  const note = String(form.get("note") ?? "").trim() || null;

  if (!STOCK_ID.test(stockId)) return fail("股票代號格式不正確");
  if (side !== "buy" && side !== "sell") return fail("請選擇買進或賣出");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return fail("日期格式不正確");
  if (!(price > 0) || !(qty > 0) || !Number.isInteger(qty)) return fail("價格與數量必須大於 0，數量為整數");

  const { config } = await runtimeConfig();
  const risk = config.risk;
  const shares = unit === "lots" ? qty * risk.position.lot_size : qty;
  const fee = feeText === "" ? commission(price, shares, risk.costs) : Number(feeText);
  const tax = taxText === "" ? (side === "sell" ? sellTax(price, shares, risk.costs) : 0) : Number(taxText);
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
  revalidatePath("/portfolio");
  revalidatePath("/");
  const word = side === "buy" ? "買進" : "賣出";
  return ok(`已登錄 ${date} ${word} ${stockId} ${shares.toLocaleString()} 股 @ ${price}（手續費 ${fee}、證交稅 ${tax}）`);
}

export async function updateHoldingLevels(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const id = String(form.get("stock_id") ?? "");
  const stop = String(form.get("stop_loss") ?? "").trim();
  const target = String(form.get("take_profit") ?? "").trim();
  const stopN = stop === "" ? null : Number(stop);
  const targetN = target === "" ? null : Number(target);
  if ((stopN !== null && !(stopN > 0)) || (targetN !== null && !(targetN > 0))) return fail("價格必須大於 0");
  await db()`UPDATE holdings SET stop_loss = ${stopN}, take_profit = ${targetN}, updated_at = now() WHERE stock_id = ${id}`;
  revalidatePath("/portfolio");
  return ok(`${id} 停損停利已更新`);
}

// ---------- 設定 ----------

export async function saveCapital(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const amount = Number(String(form.get("capital") ?? "").replaceAll(",", ""));
  if (!(amount > 0) || amount > 1e11) return fail("請輸入合理的總資金");
  await db()`
    INSERT INTO settings (key, value, updated_at) VALUES ('total_capital', ${db().json(amount)}, now())
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`;
  revalidatePath("/settings");
  return ok(`總資金已設為 ${amount.toLocaleString("zh-TW")} 元，下一次訊號會依此計算建議張數`);
}

export async function saveNotify(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const value = {
    daily_report: form.get("daily_report") === "on",
    intraday_stop_loss: form.get("intraday_stop_loss") === "on",
    system_alerts: form.get("system_alerts") === "on",
  };
  await db()`
    INSERT INTO settings (key, value, updated_at) VALUES ('notify', ${db().json(value)}, now())
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()`;
  revalidatePath("/settings");
  return ok("通知設定已儲存");
}

// ---------- 事件（法說會等手動登錄） ----------

export async function addEvent(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const id = String(form.get("stock_id") ?? "");
  const date = String(form.get("date") ?? "");
  const type = String(form.get("event_type") ?? "");
  const note = String(form.get("note") ?? "").trim() || null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return fail("日期格式不正確");
  if (!["法說會", "股東會", "除權息", "財報", "月營收", "其他"].includes(type)) return fail("事件類型不正確");
  await db()`
    INSERT INTO events (stock_id, date, event_type, note, source) VALUES (${id}, ${date}, ${type}, ${note}, 'manual')
    ON CONFLICT (stock_id, date, event_type) DO UPDATE SET note = EXCLUDED.note, source = 'manual'`;
  revalidatePath(`/stocks/${id}`);
  return ok(`已登錄 ${date} ${type}`);
}

export async function deleteEvent(form: FormData): Promise<void> {
  await requireSession();
  const eventId = Number(form.get("event_id"));
  const id = String(form.get("stock_id") ?? "");
  await db()`DELETE FROM events WHERE event_id = ${eventId} AND source = 'manual'`;
  revalidatePath(`/stocks/${id}`);
}

export async function goToStock(form: FormData): Promise<void> {
  await requireSession();
  const id = String(form.get("q") ?? "").trim().toUpperCase();
  if (STOCK_ID.test(id)) redirect(`/stocks/${id}`);
  redirect("/");
}
