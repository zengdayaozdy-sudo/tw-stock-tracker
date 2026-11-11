import "server-only";
// MCP 工具：讓 Claude 等 AI 助理讀取評分、訊號、持股，並維護自選股、族群與交易日誌。
// 讀取工具與網站頁面使用相同查詢；寫入工具與網站 Server Action 共用 lib/mutations.ts。
// 不提供任何下單功能（規格第 0 節）。

import type { McpServer } from "@modelcontextprotocol/server";
import { revalidatePath } from "next/cache";
import { z } from "zod";
import { reboundEvidence, todayTaipei } from "../format";
import * as m from "../mutations";
import * as q from "../queries";
import { adjustPoints, tradeStats, type Trade } from "../trading";

const DATE = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "日期格式為 YYYY-MM-DD")
  .describe("交易日 YYYY-MM-DD（台灣時間）；省略時為最新有資料的交易日");
const STOCK = z.string().trim().toUpperCase().regex(m.STOCK_ID, "股票代號為 4–6 碼英數字").describe("股票代號，例如 2330");
const STOCKS = z.array(STOCK).min(1).max(100).describe("股票代號清單，例如 [\"2330\", \"2317\"]");

const LIGHT: Record<string, string> = { bullish: "偏多", neutral: "中性", bearish: "偏空" };

const READ = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false } as const;
const WRITE = { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false } as const;
const REMOVE = { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: false } as const;

type Content = { content: { type: "text"; text: string }[]; isError?: boolean };

/** postgres.js 的結果陣列帶有 count、command 等屬性，轉成一般陣列再輸出。 */
function plain<T>(rows: readonly T[]): T[] {
  return [...rows];
}

function json(data: unknown): Content {
  return { content: [{ type: "text", text: JSON.stringify(data, null, 1) }] };
}

function errorText(message: string): Content {
  return { content: [{ type: "text", text: message }], isError: true };
}

function result(res: m.Result, paths: string[]): Content {
  if (res.ok) for (const p of paths) revalidatePath(p);
  return res.ok ? { content: [{ type: "text", text: res.message }] } : errorText(res.message);
}

async function resolveDate(date?: string): Promise<string | null> {
  return date ?? (await q.latestDate());
}

const NO_DATA = "資料庫還沒有評分資料，請先執行每日排程";

/** 評分列精簡成 { short: {...}, long: {...} }，breakdown 只在個股工具輸出。 */
function scoreMap(rows: q.ScoreRow[], withBreakdown = false) {
  const out: Record<string, Record<string, Record<string, unknown>>> = {};
  for (const r of rows) {
    (out[r.stock_id] ??= {})[r.horizon] = {
      total: r.total,
      fundamental: r.fundamental,
      technical: r.technical,
      chips: r.chips,
      status: r.status,
      ...(withBreakdown ? { breakdown: r.breakdown } : {}),
    };
  }
  return out;
}

async function withNames<T extends { stock_id: string }>(rows: T[]): Promise<(T & { name: string | null })[]> {
  const n = await q.names([...new Set(rows.map((r) => r.stock_id))]);
  return rows.map((r) => ({ ...r, name: n[r.stock_id] ?? null }));
}

export function registerTools(server: McpServer) {
  // ================= 讀取 =================

  server.registerTool(
    "get_overview",
    {
      title: "每日總覽",
      description:
        "取得指定交易日的總覽：大盤多空燈號與各輸入依據、反彈偵測、族群強弱排名、醞釀名單（轉強價、買進區間、停損、目標價）、買進訊號（含參考買進區間、停損、停利、建議張數）、持股賣出提醒、目前持股損益、資料完整性與排程狀態。想知道「今天有什麼訊號」「大盤怎麼樣」時先呼叫這個。",
      inputSchema: z.object({ date: DATE.optional() }),
      annotations: READ,
    },
    async ({ date }) => {
      const d = await resolveDate(date);
      if (!d) return errorText(NO_DATA);
      const [mk, buys, sells, holdings, fetchJob, signalJob, notifyJob, heartbeat] = await Promise.all([
        q.market(d),
        q.signalsOn(d, "buy"),
        q.signalsOn(d, "sell"),
        q.holdingsDetail(d),
        q.latestJob("fetch", d),
        q.latestJob("signals", d),
        q.latestJob("notify", d),
        q.lastHeartbeat("monitor_heartbeat"),
      ]);
      return json({
        date: d,
        market: mk
          ? {
              light: LIGHT[mk.light] ?? mk.light,
              total: mk.total,
              inputs: mk.inputs,
              rebound: mk.rebound
                ? { state: mk.rebound.label, points: mk.rebound.points, rally: mk.rebound.rally?.reason,
                    evidence: reboundEvidence(mk.rebound.evidence), regime: signalJob?.detail?.regime ?? null }
                : null,
            }
          : "當日沒有大盤燈號資料",
        groups: (mk?.groups ?? [])
          .filter((g) => g.rank)
          .map(({ members: _members, ...g }) => g),
        buy_signals: await withNames(plain(buys)),
        sell_alerts: await withNames(plain(sells)),
        blocked_by_filters: signalJob?.detail?.blocked ?? [],
        setup_watch: signalJob?.detail?.setups ?? [],
        holdings: plain(holdings).map((h) => ({
          ...h,
          pnl: h.close != null ? Math.round((h.close - h.avg_cost) * h.shares) : null,
          pnl_pct: h.close != null ? Math.round((h.close / h.avg_cost - 1) * 10000) / 100 : null,
        })),
        system: {
          fetch: fetchJob ? { status: fetchJob.status, quality: fetchJob.detail?.quality ?? null } : "尚未抓取",
          signals: signalJob?.status ?? "尚未執行",
          notify: notifyJob?.status ?? "尚未執行",
          intraday_monitor_heartbeat: heartbeat,
        },
      });
    },
  );

  server.registerTool(
    "search_stocks",
    {
      title: "搜尋股票",
      description: "以股票代號（可只打前幾碼）或公司名稱關鍵字搜尋上市櫃股票，回傳代號、名稱、市場、產業、交易狀態、是否在自選股。",
      inputSchema: z.object({
        query: z.string().trim().min(1).max(30).describe("代號或名稱，例如 2330、台積、聯發"),
        limit: z.number().int().min(1).max(50).default(20),
      }),
      annotations: READ,
    },
    async ({ query, limit }) => {
      const rows = plain(await q.searchStocks(query, limit));
      return rows.length ? json(rows) : errorText(`找不到「${query}」`);
    },
  );

  server.registerTool(
    "get_stock",
    {
      title: "個股詳情",
      description:
        "取得單一個股的完整資料：基本資料、所屬族群、最新短線與中長線評分（含每個指標的原始值、分數、權重、貢獻度拆解）、近期買賣訊號與參考價位、消息面排雷紀錄、即將到來的事件、最近 N 個交易日的價格。分析「為什麼這檔高分」時使用。",
      inputSchema: z.object({
        stock_id: STOCK,
        price_days: z.number().int().min(0).max(250).default(20).describe("附帶最近幾個交易日的開高低收量，0 表示不附"),
      }),
      annotations: READ,
    },
    async ({ stock_id, price_days }) => {
      const info = await q.stock(stock_id);
      if (!info) return errorText(`股票清單中找不到 ${stock_id}`);
      const [groups, scores, signals, news, events, prices, holdings] = await Promise.all([
        q.stockGroups(stock_id),
        q.latestScores(stock_id),
        q.stockSignals(stock_id, 10),
        q.stockNews(stock_id, 10),
        q.stockEvents(stock_id, todayTaipei()),
        price_days ? q.priceHistory(stock_id, price_days) : Promise.resolve([]),
        q.holdingsDetail(todayTaipei()),
      ]);
      return json({
        ...info,
        groups: plain(groups).map((g) => ({ name: g.name, kind: g.kind === "custom" ? "自訂" : "產業" })),
        scores: Object.fromEntries(scores.map((s) => [s.horizon, { date: s.date, ...scoreMap([s], true)[stock_id][s.horizon] }])),
        recent_signals: plain(signals),
        news: plain(news),
        upcoming_events: plain(events),
        holding: plain(holdings).find((h) => h.stock_id === stock_id) ?? null,
        prices: plain(prices).reverse(),
        links: {
          site: `/stocks/${stock_id}`,
          statementdog: `https://statementdog.com/analysis/${stock_id}`,
          goodinfo: `https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID=${stock_id}`,
        },
      });
    },
  );

  server.registerTool(
    "get_price_history",
    {
      title: "歷史股價",
      description: "取得個股最近 N 個交易日的日 K（開高低收、成交量），由舊到新排序。價格為原始收盤價，未還原除權息。",
      inputSchema: z.object({ stock_id: STOCK, days: z.number().int().min(1).max(800).default(120) }),
      annotations: READ,
    },
    async ({ stock_id, days }) => {
      const rows = plain(await q.priceHistory(stock_id, days)).reverse();
      return rows.length ? json({ stock_id, days: rows.length, prices: rows }) : errorText(`${stock_id} 沒有股價資料`);
    },
  );

  server.registerTool(
    "list_signals",
    {
      title: "訊號清單",
      description: "列出指定交易日的買進訊號、賣出提醒或兩者，每筆含觸發條件、參考買進區間、停損、停利、建議張數與理由。",
      inputSchema: z.object({
        date: DATE.optional(),
        side: z.enum(["buy", "sell", "all"]).default("all").describe("buy＝買進訊號、sell＝持股賣出提醒、all＝兩者"),
      }),
      annotations: READ,
    },
    async ({ date, side }) => {
      const d = await resolveDate(date);
      if (!d) return errorText(NO_DATA);
      const [buys, sells] = await Promise.all([
        side === "sell" ? [] : q.signalsOn(d, "buy"),
        side === "buy" ? [] : q.signalsOn(d, "sell"),
      ]);
      return json({ date: d, buy: await withNames(plain(buys)), sell: await withNames(plain(sells)) });
    },
  );

  server.registerTool(
    "get_watchlist",
    {
      title: "自選股",
      description: "列出所有自選股：名稱、產業、交易狀態、已有幾天股價、最新收盤價、最新短線與中長線分數，以及方案的自選股上限。",
      inputSchema: z.object({}),
      annotations: READ,
    },
    async () => {
      const [list, { config }, d] = await Promise.all([q.watchlist(), q.runtimeConfig(), q.latestDate()]);
      const ids = list.map((r) => r.stock_id as string);
      const [scores, closes] = await Promise.all([
        d ? q.scoresOn(d, ids) : Promise.resolve([] as q.ScoreRow[]),
        q.lastCloses(ids, d ?? todayTaipei()),
      ]);
      const sm = scoreMap(scores);
      return json({
        plan: config.plan_label,
        limit: config.watchlist_limit,
        count: list.length,
        score_date: d,
        stocks: plain(list).map((r) => ({ ...r, close: closes[r.stock_id] ?? null, scores: sm[r.stock_id] ?? null })),
      });
    },
  );

  server.registerTool(
    "list_groups",
    {
      title: "族群強弱",
      description: "列出族群強弱排名（近 1／3 個月相對大盤報酬、族群平均分數、綜合排名、是否為弱勢族群），以及所有自訂族群的成分股。",
      inputSchema: z.object({ date: DATE.optional() }),
      annotations: READ,
    },
    async ({ date }) => {
      const d = await resolveDate(date);
      const [mk, all] = await Promise.all([d ? q.market(d) : null, q.groupsWithMembers()]);
      return json({
        date: d,
        ranking: (mk?.groups ?? []).map(({ members: _members, ...g }) => g),
        custom_groups: plain(all)
          .filter((g) => g.kind === "custom")
          .map((g) => ({ name: g.name, members: g.members })),
      });
    },
  );

  server.registerTool(
    "get_group",
    {
      title: "族群成分股",
      description: "取得單一族群（產業或自訂）的強弱數據與成分股排行（最新收盤價、短線與中長線分數）。",
      inputSchema: z.object({ name: z.string().trim().min(1).describe("族群名稱，例如 半導體業 或自訂族群名稱") }),
      annotations: READ,
    },
    async ({ name }) => {
      const group = await q.groupByName(name);
      if (!group) return errorText(`找不到族群「${name}」，可先用 list_groups 查看名稱`);
      const d = await q.latestDate();
      const members = plain(await q.groupMembers(group.group_id));
      const ids = members.map((r) => r.stock_id as string);
      const [mk, scores, closes] = await Promise.all([
        d ? q.market(d) : null,
        d ? q.scoresOn(d, ids) : Promise.resolve([] as q.ScoreRow[]),
        q.lastCloses(ids, d ?? todayTaipei()),
      ]);
      const sm = scoreMap(scores);
      const strength = mk?.groups.find((g) => g.name === name);
      return json({
        name: group.name,
        kind: group.kind === "custom" ? "自訂" : "產業",
        date: d,
        strength: strength ? { ...strength, members: undefined } : null,
        members: members
          .map((r) => ({ ...r, close: closes[r.stock_id] ?? null, scores: sm[r.stock_id] ?? null }))
          .sort((a, b) => Number(b.scores?.short?.total ?? -1) - Number(a.scores?.short?.total ?? -1)),
      });
    },
  );

  server.registerTool(
    "get_portfolio",
    {
      title: "持股與績效",
      description:
        "取得目前持股（成本、張數、現價、損益、停損停利價）、交易績效統計（勝率、平均獲利、平均虧損、期望值、已實現與未實現損益、與加權指數及 0050 比較）與最近的交易紀錄。",
      inputSchema: z.object({ recent_trades: z.number().int().min(0).max(200).default(20) }),
      annotations: READ,
    },
    async ({ recent_trades }) => {
      const today = todayTaipei();
      const date = (await q.latestDate()) ?? today;
      const [holdings, rows, capital, recent] = await Promise.all([
        q.holdingsDetail(date),
        q.trades(),
        q.setting<number>("total_capital", 0),
        recent_trades ? q.tradesRecent(recent_trades) : Promise.resolve([]),
      ]);
      const trades: Trade[] = rows.map((r) => ({
        trade_id: Number(r.trade_id),
        stock_id: r.stock_id,
        date: r.date,
        side: r.side,
        price: r.price,
        shares: r.shares,
        fee: r.fee,
        tax: r.tax,
      }));
      const ids = [...new Set(trades.map((t) => t.stock_id))];
      const start = trades.length ? trades.map((t) => t.date).sort()[0] : today;
      const [closes, taiex, etf, etfAdj] = await Promise.all([
        q.lastCloses(ids, today),
        q.seriesBetween("taiex", start, today),
        q.seriesBetween("etf_0050", start, today),
        q.priceAdjustments("0050"),
      ]);
      const stats = tradeStats(trades, closes, Number(capital), today, { taiex, etf_0050: adjustPoints(etf, etfAdj) });
      const value = holdings.reduce((a, h) => a + (h.close ?? h.avg_cost) * h.shares, 0);
      return json({
        as_of: date,
        total_capital: Number(capital),
        market_value: Math.round(value),
        holdings: plain(holdings).map((h) => ({
          ...h,
          pnl: h.close != null ? Math.round((h.close - h.avg_cost) * h.shares) : null,
          pnl_pct: h.close != null ? Math.round((h.close / h.avg_cost - 1) * 10000) / 100 : null,
        })),
        stats: { ...stats, note: "百分比欄位為小數（0.05＝5%）；0050 為還原除息與分割後的含息報酬，加權指數為價格指數" },
        recent_trades: await withNames(plain(recent) as unknown as { stock_id: string }[]),
      });
    },
  );

  server.registerTool(
    "get_system_status",
    {
      title: "系統狀態",
      description: "取得方案模式與功能開關、最近排程紀錄（含失敗原因）、FinMind 每小時用量、盤中停損監控最後心跳。排查「今天怎麼沒有報告」時使用。",
      inputSchema: z.object({ days: z.number().int().min(1).max(14).default(3).describe("排程紀錄往回幾天") }),
      annotations: READ,
    },
    async ({ days }) => {
      const [jobs, usage, heartbeat, { config, synced }] = await Promise.all([
        q.jobsRecent(days),
        q.apiUsage("finmind", 24),
        q.lastHeartbeat("monitor_heartbeat"),
        q.runtimeConfig(),
      ]);
      return json({
        plan: config.plan_label,
        config_synced: synced,
        features: config.features,
        finmind_hourly_budget: config.finmind_hourly_budget,
        finmind_usage_24h: plain(usage),
        intraday_monitor_heartbeat: heartbeat,
        jobs: plain(jobs).map((j) => ({ ...j, detail: j.detail })),
      });
    },
  );

  // ================= 寫入 =================

  server.registerTool(
    "add_to_watchlist",
    {
      title: "加入自選股",
      description: "把股票加入自選股（受方案上限限制）。新加入的股票會在下一次每日排程自動回補 3 年歷史資料並開始評分。",
      inputSchema: z.object({ stock_ids: STOCKS }),
      annotations: WRITE,
    },
    async ({ stock_ids }) => result(await m.addToWatchlist([...new Set(stock_ids)]), ["/watchlist"]),
  );

  server.registerTool(
    "remove_from_watchlist",
    {
      title: "移除自選股",
      description: "從自選股移除股票。歷史資料保留；若仍有持股，該檔仍會被每日排程追蹤。",
      inputSchema: z.object({ stock_ids: STOCKS }),
      annotations: REMOVE,
    },
    async ({ stock_ids }) => result(await m.removeFromWatchlist([...new Set(stock_ids)]), ["/watchlist"]),
  );

  server.registerTool(
    "create_custom_group",
    {
      title: "建立自訂族群",
      description: "建立自訂族群（例如 AI 伺服器、CoWoS），名稱 1–30 字，不可與產業族群同名。建立後用 add_group_members 加入成分股。",
      inputSchema: z.object({ name: z.string().trim().min(1).max(30) }),
      annotations: { ...WRITE, idempotentHint: false },
    },
    async ({ name }) => result(await m.createCustomGroup(name), ["/watchlist", "/groups"]),
  );

  server.registerTool(
    "add_group_members",
    {
      title: "加入族群成分股",
      description: "把股票加入自訂族群（一檔可屬多個族群）。產業族群由系統自動維護，不能修改。",
      inputSchema: z.object({ group: z.string().trim().min(1).describe("自訂族群名稱"), stock_ids: STOCKS }),
      annotations: WRITE,
    },
    async ({ group, stock_ids }) => {
      const g = await q.groupByName(group);
      if (!g) return errorText(`找不到族群「${group}」`);
      return result(await m.addGroupMembers(g.group_id, [...new Set(stock_ids)]), ["/watchlist", "/groups"]);
    },
  );

  server.registerTool(
    "remove_group_member",
    {
      title: "移除族群成分股",
      description: "從自訂族群移除一檔股票。",
      inputSchema: z.object({ group: z.string().trim().min(1).describe("自訂族群名稱"), stock_id: STOCK }),
      annotations: REMOVE,
    },
    async ({ group, stock_id }) => {
      const g = await q.groupByName(group);
      if (!g) return errorText(`找不到族群「${group}」`);
      return result(await m.removeGroupMember(g.group_id, stock_id), ["/watchlist", "/groups"]);
    },
  );

  server.registerTool(
    "record_trade",
    {
      title: "登錄交易",
      description:
        "在交易日誌登錄一筆已經在券商成交的買進或賣出（本工具不會下單）。手續費、證交稅省略時依設定自動計算；會自動記錄當時的分數與 5 天內的訊號，並更新持股的平均成本與停損停利價。登錄前請向使用者確認日期、價格、數量。",
      inputSchema: z.object({
        stock_id: STOCK,
        side: z.enum(["buy", "sell"]),
        date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).describe("成交日 YYYY-MM-DD"),
        price: z.number().positive().describe("成交價"),
        quantity: z.number().int().positive(),
        unit: z.enum(["lots", "shares"]).default("lots").describe("lots＝張（1 張 1000 股）、shares＝股（零股）"),
        fee: z.number().min(0).optional().describe("實際手續費，省略則自動計算"),
        tax: z.number().min(0).optional().describe("實際證交稅，省略則自動計算（只有賣出收稅）"),
        note: z.string().max(200).optional(),
      }),
      annotations: { ...WRITE, idempotentHint: false },
    },
    async (a) =>
      result(
        await m.recordTrade({
          stockId: a.stock_id,
          side: a.side,
          date: a.date,
          price: a.price,
          unit: a.unit,
          quantity: a.quantity,
          fee: a.fee ?? null,
          tax: a.tax ?? null,
          note: a.note?.trim() || null,
        }),
        ["/portfolio", "/"],
      ),
  );

  server.registerTool(
    "update_holding_levels",
    {
      title: "調整停損停利",
      description: "修改目前持股的停損價與停利目標價。傳 null 表示清除該價位；盤中停損監控會用新的停損價。",
      inputSchema: z.object({
        stock_id: STOCK,
        stop_loss: z.number().positive().nullable(),
        take_profit: z.number().positive().nullable(),
      }),
      annotations: WRITE,
    },
    async ({ stock_id, stop_loss, take_profit }) =>
      result(await m.updateHoldingLevels(stock_id, stop_loss, take_profit), ["/portfolio"]),
  );
}
