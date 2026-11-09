"use server";
// 所有寫入操作。每個動作先驗證登入，再寫資料庫，最後重新整理相關頁面。

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { db } from "@/lib/db";
import * as m from "@/lib/mutations";
import { STOCK_ID } from "@/lib/mutations";
import { requireSession } from "@/lib/session";

export type ActionState = { ok: boolean; message: string } | null;

function ok(message: string): ActionState {
  return { ok: true, message };
}
function fail(message: string): ActionState {
  return { ok: false, message };
}

// ---------- 自選股 ----------

export async function addWatchlist(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const res = await m.addToWatchlist(m.parseIds(String(form.get("stocks") ?? "")));
  if (res.ok) revalidatePath("/watchlist");
  return res;
}

export async function removeWatchlist(form: FormData): Promise<void> {
  await requireSession();
  await m.removeFromWatchlist([String(form.get("stock_id") ?? "")]);
  revalidatePath("/watchlist");
}

// ---------- 自訂族群 ----------

function groupPaths() {
  revalidatePath("/watchlist");
  revalidatePath("/groups");
}

export async function createGroup(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const res = await m.createCustomGroup(String(form.get("name") ?? ""));
  if (res.ok) revalidatePath("/watchlist");
  return res;
}

export async function renameGroup(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const res = await m.renameCustomGroup(Number(form.get("group_id")), String(form.get("name") ?? ""));
  if (res.ok) groupPaths();
  return res;
}

export async function deleteGroup(form: FormData): Promise<void> {
  await requireSession();
  await m.deleteCustomGroup(Number(form.get("group_id")));
  groupPaths();
}

export async function addGroupMembers(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const res = await m.addGroupMembers(Number(form.get("group_id")), m.parseIds(String(form.get("stocks") ?? "")));
  if (res.ok) groupPaths();
  return res;
}

export async function removeGroupMember(form: FormData): Promise<void> {
  await requireSession();
  await m.removeGroupMember(Number(form.get("group_id")), String(form.get("stock_id") ?? ""));
  groupPaths();
}

// ---------- 交易日誌 ----------

export async function recordTrade(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const feeText = String(form.get("fee") ?? "").trim();
  const taxText = String(form.get("tax") ?? "").trim();
  const res = await m.recordTrade({
    stockId: String(form.get("stock_id") ?? ""),
    side: String(form.get("side") ?? ""),
    date: String(form.get("date") ?? ""),
    price: Number(form.get("price")),
    unit: String(form.get("unit") ?? "lots"),
    quantity: Number(form.get("quantity")),
    fee: feeText === "" ? null : Number(feeText),
    tax: taxText === "" ? null : Number(taxText),
    note: String(form.get("note") ?? "").trim() || null,
  });
  if (res.ok) {
    revalidatePath("/portfolio");
    revalidatePath("/");
  }
  return res;
}

export async function updateHoldingLevels(_prev: ActionState, form: FormData): Promise<ActionState> {
  await requireSession();
  const stop = String(form.get("stop_loss") ?? "").trim();
  const target = String(form.get("take_profit") ?? "").trim();
  const res = await m.updateHoldingLevels(
    String(form.get("stock_id") ?? ""),
    stop === "" ? null : Number(stop),
    target === "" ? null : Number(target),
  );
  if (res.ok) revalidatePath("/portfolio");
  return res;
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
