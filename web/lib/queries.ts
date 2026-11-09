import "server-only";
import { db } from "./db";

// 資料表結構見 db/migrations。所有日期為台灣交易日（YYYY-MM-DD 字串）。

export type RuntimeConfig = {
  plan: string;
  plan_label: string;
  universe: string;
  watchlist_limit: number | null;
  features: Record<string, { label: string; enabled: boolean }>;
  risk: {
    costs: { commission_rate: number; commission_discount?: number; min_commission: number; tax_rate: number };
    stop_loss: { method: string; percent: number; bearish_market_percent: number; ma60_buffer?: number; max_percent?: number };
    exit: { signal_lookback_days: number; [k: string]: number };
    position: { lot_size: number; [k: string]: number };
    take_profit: Record<string, number>;
  };
  finmind_hourly_budget: number;
  min_history_days: number;
  min_peers?: number;
  updated_at: string;
};

// 每日排程尚未同步設定前的預設值（與 config/*.yaml 初始值相同）
export const DEFAULT_CONFIG: RuntimeConfig = {
  plan: "free",
  plan_label: "免費模式",
  universe: "watchlist",
  watchlist_limit: 100,
  features: {},
  risk: {
    costs: { commission_rate: 0.001425, commission_discount: 1, min_commission: 20, tax_rate: 0.003 },
    stop_loss: { method: "percent", percent: 0.08, bearish_market_percent: 0.05, ma60_buffer: 0.01, max_percent: 0.15 },
    exit: { signal_lookback_days: 5 },
    position: { lot_size: 1000 },
    take_profit: {},
  },
  finmind_hourly_budget: 540,
  min_history_days: 240,
  min_peers: 3,
  updated_at: "",
};

export async function setting<T>(key: string, fallback: T): Promise<T> {
  const rows = await db()`SELECT value FROM settings WHERE key = ${key}`;
  return rows.length ? (rows[0].value as T) : fallback;
}

export async function runtimeConfig(): Promise<{ config: RuntimeConfig; synced: boolean }> {
  const cfg = await setting<RuntimeConfig | null>("runtime_config", null);
  return { config: cfg ?? DEFAULT_CONFIG, synced: cfg !== null };
}

/** 最近一個有評分或大盤燈號的交易日。 */
export async function latestDate(): Promise<string | null> {
  const rows = await db()`
    SELECT max(d) AS d FROM (SELECT max(date) AS d FROM market UNION ALL SELECT max(date) FROM scores) t`;
  return rows[0]?.d ?? null;
}

export type GroupStrength = {
  name: string;
  kind: string;
  members: string[];
  size: number;
  return_20: number | null;
  return_60: number | null;
  rel_return_20: number | null;
  rel_return_60: number | null;
  avg_score: number | null;
  composite: number | null;
  rank: number | null;
  weak: boolean;
};

export type LightInput = { label: string; vote: number; reason: string; missing?: boolean };

export async function market(date: string) {
  const rows = await db()`SELECT light, inputs, group_strength FROM market WHERE date = ${date}`;
  if (!rows.length) return null;
  const inputs = rows[0].inputs as Record<string, LightInput | number>;
  return {
    light: rows[0].light as string,
    total: (inputs.total as number) ?? null,
    inputs: Object.entries(inputs)
      .filter(([k, v]) => k !== "total" && typeof v === "object")
      .map(([k, v]) => ({ key: k, ...(v as LightInput) })),
    groups: (rows[0].group_strength as GroupStrength[]) ?? [],
  };
}

export type Signal = {
  stock_id: string;
  date: string;
  horizon: string;
  side: string;
  triggers: string[];
  entry_low: number | null;
  entry_high: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  suggested_lots: number | null;
  reason: string;
  detail: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
};

export async function signalsOn(date: string, side: "buy" | "sell"): Promise<Signal[]> {
  return (await db()`
    SELECT stock_id, date, horizon, side, triggers, entry_low, entry_high, stop_loss, take_profit, suggested_lots,
           reason, detail
    FROM signals WHERE date = ${date} AND side = ${side}
    ORDER BY horizon DESC, (detail->'score'->>'total')::numeric DESC NULLS LAST, stock_id`) as unknown as Signal[];
}

export async function latestJob(job: string, date: string) {
  const rows = await db()`
    SELECT status, detail, started_at, finished_at FROM job_logs WHERE job = ${job} AND run_date = ${date}
    ORDER BY started_at DESC, log_id DESC LIMIT 1`;
  return rows[0] as { status: string; detail: Record<string, any>; started_at: Date; finished_at: Date | null } | undefined; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export async function latestJobAny(job: string) {
  const rows = await db()`
    SELECT run_date, status, detail, started_at FROM job_logs WHERE job = ${job}
    ORDER BY started_at DESC, log_id DESC LIMIT 1`;
  return rows[0] as { run_date: string; status: string; detail: Record<string, any>; started_at: Date } | undefined; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export async function flagsOn(date: string, ids?: string[]) {
  const sql = db();
  const rows = ids
    ? await sql`SELECT stock_id, filter, kind, reason, detail FROM screen_flags WHERE date = ${date} AND stock_id = ANY(${ids})`
    : await sql`SELECT stock_id, filter, kind, reason, detail FROM screen_flags WHERE date = ${date}`;
  const out: Record<string, { filter: string; kind: string; reason: string; detail: any }[]> = {}; // eslint-disable-line @typescript-eslint/no-explicit-any
  for (const r of rows) (out[r.stock_id] ??= []).push({ filter: r.filter, kind: r.kind, reason: r.reason, detail: r.detail });
  return out;
}

export async function names(ids: string[]): Promise<Record<string, string>> {
  if (!ids.length) return {};
  const rows = await db()`SELECT stock_id, name FROM stocks WHERE stock_id = ANY(${ids})`;
  return Object.fromEntries(rows.map((r) => [r.stock_id, r.name]));
}

export type ScoreRow = {
  stock_id: string;
  horizon: string;
  total: number | null;
  fundamental: number | null;
  technical: number | null;
  chips: number | null;
  status: string;
  breakdown: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
};

export async function scoresOn(date: string, ids?: string[]): Promise<ScoreRow[]> {
  const sql = db();
  return (ids
    ? await sql`SELECT stock_id, horizon, total, fundamental, technical, chips, status, breakdown FROM scores
                WHERE date = ${date} AND stock_id = ANY(${ids})`
    : await sql`SELECT stock_id, horizon, total, fundamental, technical, chips, status, breakdown FROM scores
                WHERE date = ${date}`) as unknown as ScoreRow[];
}

export async function holdingsDetail(date: string) {
  return await db()`
    SELECT h.stock_id, s.name, h.shares, h.avg_cost, h.opened_date, h.stop_loss, h.take_profit, h.highest_close,
      (SELECT close FROM daily_prices p WHERE p.stock_id = h.stock_id AND p.date <= ${date} AND p.close IS NOT NULL
       ORDER BY p.date DESC LIMIT 1) AS close
    FROM holdings h JOIN stocks s USING (stock_id) ORDER BY h.stock_id`;
}

export async function stock(id: string) {
  const rows = await db()`
    SELECT stock_id, name, market, industry, market_cap, status, listed_date FROM stocks WHERE stock_id = ${id}`;
  return rows[0];
}

export async function priceHistory(id: string, days = 400) {
  return await db()`
    SELECT date, open, high, low, close, volume FROM daily_prices
    WHERE stock_id = ${id} AND close IS NOT NULL ORDER BY date DESC LIMIT ${days}`;
}

export async function stockSignals(id: string, limit = 60) {
  return (await db()`
    SELECT stock_id, date, horizon, side, triggers, entry_low, entry_high, stop_loss, take_profit, suggested_lots,
           reason, detail
    FROM signals WHERE stock_id = ${id} ORDER BY date DESC LIMIT ${limit}`) as unknown as Signal[];
}

export async function latestScores(id: string) {
  return (await db()`
    SELECT DISTINCT ON (horizon) date, stock_id, horizon, total, fundamental, technical, chips, status, breakdown
    FROM scores WHERE stock_id = ${id} ORDER BY horizon, date DESC`) as unknown as (ScoreRow & { date: string })[];
}

export async function stockNews(id: string, limit = 30) {
  return await db()`
    SELECT news_id, published_at, source, title, url, is_major_negative, event_type, confidence, summary, judged_by
    FROM news WHERE stock_id = ${id} ORDER BY published_at DESC LIMIT ${limit}`;
}

export async function stockEvents(id: string, from: string) {
  return await db()`
    SELECT event_id, date, event_type, note, source FROM events WHERE stock_id = ${id} AND date >= ${from}
    ORDER BY date LIMIT 30`;
}

export async function stockGroups(id: string) {
  return await db()`
    SELECT g.group_id, g.name, g.kind FROM groups g JOIN group_members m USING (group_id)
    WHERE m.stock_id = ${id} ORDER BY g.kind DESC, g.name`;
}

export async function watchlist() {
  return await db()`
    SELECT w.stock_id, s.name, s.industry, s.market, s.status, w.added_at,
      (SELECT count(*) FROM daily_prices p WHERE p.stock_id = w.stock_id)::int AS price_days
    FROM watchlist w JOIN stocks s USING (stock_id) ORDER BY w.stock_id`;
}

export type WatchItem = {
  stock_id: string;
  name: string;
  industry: string | null;
  market: string;
  status: string[];
  price_date: string | null;
  close: number | null;
  prev_close: number | null;
  short: number | null;
  long: number | null;
  score_status: string | null;
  price_days: number;
  groups: string[];
};

/** 自選股清單＋最新收盤、前一日收盤、最新短線／中長線分數與所屬自訂族群。 */
export async function watchlistDetail(): Promise<WatchItem[]> {
  return (await db()`
    WITH w AS (SELECT stock_id FROM watchlist),
    last AS (
      SELECT DISTINCT ON (p.stock_id) p.stock_id, p.date, p.close FROM daily_prices p JOIN w USING (stock_id)
      WHERE p.close IS NOT NULL ORDER BY p.stock_id, p.date DESC),
    prev AS (
      SELECT DISTINCT ON (p.stock_id) p.stock_id, p.close FROM daily_prices p JOIN last l USING (stock_id)
      WHERE p.date < l.date AND p.close IS NOT NULL ORDER BY p.stock_id, p.date DESC),
    sc AS (
      SELECT stock_id,
        max(total) FILTER (WHERE horizon = 'short') AS short,
        max(total) FILTER (WHERE horizon = 'long') AS long,
        min(status) AS score_status
      FROM scores WHERE date = (SELECT max(date) FROM scores) AND stock_id IN (SELECT stock_id FROM w)
      GROUP BY stock_id)
    SELECT w.stock_id, s.name, s.industry, s.market, s.status,
      last.date AS price_date, last.close, prev.close AS prev_close,
      sc.short, sc.long, sc.score_status,
      (SELECT count(*) FROM daily_prices p WHERE p.stock_id = w.stock_id)::int AS price_days,
      coalesce((SELECT array_agg(g.name ORDER BY g.name) FROM group_members m JOIN groups g USING (group_id)
                WHERE m.stock_id = w.stock_id AND g.kind = 'custom'), '{}') AS groups
    FROM w JOIN stocks s USING (stock_id)
    LEFT JOIN last USING (stock_id) LEFT JOIN prev USING (stock_id) LEFT JOIN sc USING (stock_id)
    ORDER BY w.stock_id`) as unknown as WatchItem[];
}

export async function groupsWithMembers() {
  return await db()`
    SELECT g.group_id, g.name, g.kind,
      coalesce(array_agg(m.stock_id ORDER BY m.stock_id) FILTER (WHERE m.stock_id IS NOT NULL), '{}') AS members
    FROM groups g LEFT JOIN group_members m USING (group_id)
    GROUP BY g.group_id ORDER BY g.kind DESC, g.name`;
}

export async function groupByName(name: string) {
  const rows = await db()`SELECT group_id, name, kind FROM groups WHERE name = ${name}`;
  return rows[0];
}

export async function groupMembers(groupId: number) {
  return await db()`
    SELECT m.stock_id, s.name, s.status FROM group_members m JOIN stocks s USING (stock_id)
    WHERE m.group_id = ${groupId} ORDER BY m.stock_id`;
}

export async function trades() {
  return await db()`
    SELECT trade_id, stock_id, date, side, price, shares, fee, tax, score_snapshot, signal_snapshot, note
    FROM trades ORDER BY date DESC, trade_id DESC`;
}

export async function seriesBetween(series: string, start: string, end: string) {
  const rows = await db()`
    SELECT date, value FROM market_series WHERE series = ${series} AND date BETWEEN ${start} AND ${end} ORDER BY date`;
  return rows.map((r) => [r.date, r.value] as [string, number]);
}

export async function priceAdjustments(id: string): Promise<[string, number][]> {
  const rows = await db()`
    SELECT date, before_price, after_price FROM price_adjustments WHERE stock_id = ${id} ORDER BY date`;
  return rows.map((r) => [r.date, r.after_price / r.before_price] as [string, number]);
}

export async function lastCloses(ids: string[], date: string): Promise<Record<string, number>> {
  if (!ids.length) return {};
  const rows = await db()`
    SELECT DISTINCT ON (stock_id) stock_id, close FROM daily_prices
    WHERE stock_id = ANY(${ids}) AND date <= ${date} AND close IS NOT NULL ORDER BY stock_id, date DESC`;
  return Object.fromEntries(rows.map((r) => [r.stock_id, r.close]));
}

export async function jobsRecent(days = 14) {
  return await db()`
    SELECT log_id, job, run_date, status, detail, started_at, finished_at FROM job_logs
    WHERE started_at > now() - make_interval(days => ${days}) AND status <> 'heartbeat'
    ORDER BY started_at DESC LIMIT 200`;
}

export async function apiUsage(provider: string, hours = 48) {
  return await db()`
    SELECT hour, calls FROM api_usage WHERE provider = ${provider} AND hour > now() - make_interval(hours => ${hours})
    ORDER BY hour DESC`;
}

export async function lastHeartbeat(job: string) {
  const rows = await db()`SELECT max(started_at) AS t FROM job_logs WHERE job = ${job} AND status = 'heartbeat'`;
  return rows[0]?.t as Date | null;
}

// ---------- MCP 用查詢 ----------

/** 以代號前綴或名稱關鍵字搜尋股票清單。 */
export async function searchStocks(q: string, limit = 20) {
  const text = q.trim();
  const like = `%${text.replace(/[\\%_]/g, (c) => `\\${c}`)}%`;
  return await db()`
    SELECT s.stock_id, s.name, s.market, s.industry, s.status,
      EXISTS (SELECT 1 FROM watchlist w WHERE w.stock_id = s.stock_id) AS in_watchlist
    FROM stocks s
    WHERE s.stock_id = ${text.toUpperCase()} OR s.stock_id LIKE ${`${text.toUpperCase()}%`} OR s.name LIKE ${like}
    ORDER BY (s.stock_id = ${text.toUpperCase()}) DESC, (s.name = ${text}) DESC, s.stock_id
    LIMIT ${limit}`;
}

export async function tradesRecent(limit = 30) {
  return await db()`
    SELECT trade_id, stock_id, date, side, price, shares, fee, tax, note
    FROM trades ORDER BY date DESC, trade_id DESC LIMIT ${limit}`;
}
