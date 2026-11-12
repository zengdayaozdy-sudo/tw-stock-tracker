// MCP 端點：權杖驗證、兩代協定（2026-07-28 無狀態與 2025 年版 initialize）、工具清單與呼叫
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("../queries", () => ({
  latestDate: vi.fn(async () => "2026-11-06"),
  watchlist: vi.fn(async () => [
    { stock_id: "2330", name: "台積電", industry: "半導體業", market: "twse", status: "normal", price_days: 750 },
  ]),
  runtimeConfig: vi.fn(async () => ({ config: { plan_label: "免費模式", watchlist_limit: 100 }, synced: true })),
  scoresOn: vi.fn(async () => [
    { stock_id: "2330", horizon: "short", total: 71.2, fundamental: 60, technical: 80, chips: 65, status: "ok", breakdown: {} },
  ]),
  lastCloses: vi.fn(async () => ({ "2330": 1085 })),
  names: vi.fn(async () => ({})),
  summaryDates: vi.fn(async () => ["2026-11-12"]),
  dailySummary: vi.fn(async (date: string) =>
    date === "2026-11-12"
      ? {
          date,
          narrative: "加權指數開低後盤中一度翻紅，終場跌 1,165 點。",
          generated_by: "rules",
          facts: { headline: "加權指數開低", index: { close: 43091.87, change: -1165.28 }, flows: { foreign: -6.8e8 } },
          reasons: [{ key: "profit_taking", tone: "neg", text: "短線漲多", weight: 9 }],
          plan: { posture: { tone: "bearish", title: "偏空", text: "不開新倉" }, holdings: [], entries: [] },
          updated_at: new Date("2026-11-12T08:00:00Z"),
        }
      : null,
  ),
}));
vi.mock("../mutations", async () => {
  const actual = await vi.importActual<typeof import("../mutations")>("../mutations");
  return { ...actual, addToWatchlist: vi.fn(async (ids: string[]) => ({ ok: true, message: `已加入 ${ids.join("、")}` })) };
});
vi.mock("../db", () => ({ db: () => { throw new Error("測試不應連線資料庫"); } }));

const TOKEN = "t".repeat(40);
let serveMcp: typeof import("./handler").serveMcp;
let mutations: typeof import("../mutations");

beforeAll(async () => {
  process.env.MCP_TOKEN = TOKEN;
  ({ serveMcp } = await import("./handler"));
  mutations = await import("../mutations");
});
beforeEach(() => vi.clearAllMocks());

const NEW = "2026-07-28";
const OLD = "2025-06-18";

function rpc(method: string, params: Record<string, unknown> = {}, version = NEW, id = 1) {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
    "mcp-protocol-version": version,
  };
  let body: Record<string, unknown> = { jsonrpc: "2.0", id, method, params };
  if (version === NEW) {
    headers["mcp-method"] = method;
    if (method === "tools/call") headers["mcp-name"] = String(params.name);
    body = {
      ...body,
      params: {
        ...params,
        _meta: {
          "io.modelcontextprotocol/protocolVersion": NEW,
          "io.modelcontextprotocol/clientInfo": { name: "test", version: "1" },
          "io.modelcontextprotocol/clientCapabilities": {},
        },
      },
    };
  }
  return new Request("http://localhost/api/mcp", { method: "POST", headers, body: JSON.stringify(body) });
}

/** 回應可能是 JSON 或 SSE，統一取出 JSON-RPC 訊息。 */
async function message(res: Response) {
  const text = await res.text();
  if ((res.headers.get("content-type") ?? "").includes("text/event-stream")) {
    const data = text.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).filter(Boolean);
    return JSON.parse(data[data.length - 1]);
  }
  return JSON.parse(text);
}

describe("權杖驗證", () => {
  it("沒有或錯誤的權杖：標頭方式回 401，路徑方式回 404", async () => {
    expect((await serveMcp(rpc("tools/list"), null, "header")).status).toBe(401);
    expect((await serveMcp(rpc("tools/list"), "wrong", "header")).status).toBe(401);
    expect((await serveMcp(rpc("tools/list"), "wrong", "path")).status).toBe(404);
  });

  it("MCP_TOKEN 未設定或太短時端點關閉", async () => {
    process.env.MCP_TOKEN = "short";
    expect((await serveMcp(rpc("tools/list"), "short", "header")).status).toBe(401);
    process.env.MCP_TOKEN = TOKEN;
  });
});

describe("2026-07-28 無狀態協定", () => {
  it("server/discover 回傳伺服器資訊", async () => {
    const res = await serveMcp(rpc("server/discover"), TOKEN, "header");
    expect(res.status).toBe(200);
    const msg = await message(res);
    expect(JSON.stringify(msg.result)).toContain("tw-stock-tracker");
  });

  it("tools/list 列出讀取與寫入工具，並標示唯讀／破壞性", async () => {
    const msg = await message(await serveMcp(rpc("tools/list"), TOKEN, "path"));
    const tools = msg.result.tools as { name: string; annotations?: Record<string, boolean> }[];
    const names = tools.map((t) => t.name);
    for (const n of ["get_overview", "get_daily_summary", "get_stock", "get_watchlist", "list_signals", "get_portfolio", "add_to_watchlist", "record_trade"])
      expect(names).toContain(n);
    expect(tools.find((t) => t.name === "get_watchlist")?.annotations?.readOnlyHint).toBe(true);
    expect(tools.find((t) => t.name === "remove_from_watchlist")?.annotations?.destructiveHint).toBe(true);
    expect(names.some((n) => /order|place/.test(n))).toBe(false); // 不提供下單
  });

  it("get_watchlist 回傳自選股、收盤價與分數", async () => {
    const msg = await message(await serveMcp(rpc("tools/call", { name: "get_watchlist", arguments: {} }), TOKEN, "header"));
    expect(msg.result.isError).toBeFalsy();
    const data = JSON.parse(msg.result.content[0].text);
    expect(data.count).toBe(1);
    expect(data.stocks[0]).toMatchObject({ stock_id: "2330", close: 1085, scores: { short: { total: 71.2 } } });
  });

  it("get_daily_summary 預設取最新一天，沒有總結的日期回報錯誤", async () => {
    const msg = await message(await serveMcp(rpc("tools/call", { name: "get_daily_summary", arguments: {} }), TOKEN, "header"));
    expect(msg.result.isError).toBeFalsy();
    const data = JSON.parse(msg.result.content[0].text);
    expect(data.date).toBe("2026-11-12");
    expect(data.reasons).toEqual([{ tone: "neg", text: "短線漲多" }]);
    expect(data.next_day_plan.posture.title).toBe("偏空");
    const none = await message(
      await serveMcp(rpc("tools/call", { name: "get_daily_summary", arguments: { date: "2026-11-11" } }), TOKEN, "header"),
    );
    expect(none.result.isError).toBe(true);
  });

  it("add_to_watchlist 代號轉大寫、去重後交給共用寫入邏輯", async () => {
    const msg = await message(
      await serveMcp(rpc("tools/call", { name: "add_to_watchlist", arguments: { stock_ids: ["2330", "00878", "2330"] } }), TOKEN, "header"),
    );
    expect(msg.result.isError).toBeFalsy();
    expect(mutations.addToWatchlist).toHaveBeenCalledWith(["2330", "00878"]);
  });

  it("參數格式錯誤時回報錯誤，不寫入", async () => {
    const msg = await message(
      await serveMcp(rpc("tools/call", { name: "add_to_watchlist", arguments: { stock_ids: ["台積電"] } }), TOKEN, "header"),
    );
    expect(msg.error ?? msg.result?.isError).toBeTruthy();
    expect(mutations.addToWatchlist).not.toHaveBeenCalled();
  });
});

describe("2025 年版協定（initialize）", () => {
  it("initialize 後可列出工具", async () => {
    const init = await message(
      await serveMcp(
        rpc("initialize", { protocolVersion: OLD, capabilities: {}, clientInfo: { name: "old", version: "1" } }, OLD),
        TOKEN,
        "header",
      ),
    );
    expect(init.result.serverInfo.name).toBe("tw-stock-tracker");
    const list = await message(await serveMcp(rpc("tools/list", {}, OLD, 2), TOKEN, "header"));
    expect(list.result.tools.length).toBeGreaterThan(10);
  });
});

describe("MCP 端點 GET", () => {
  it("有權杖的 GET 立刻回 405，不開 SSE 串流（避免掛到 300 秒逾時）", async () => {
    const res = await serveMcp(new Request("http://localhost/api/mcp", { method: "GET" }), TOKEN, "path");
    expect(res.status).toBe(405);
    expect(res.headers.get("allow")).toContain("POST");
  });

  it("權杖錯誤的 GET 仍回 404，不透露端點存在", async () => {
    const res = await serveMcp(new Request("http://localhost/api/mcp", { method: "GET" }), "wrong", "path");
    expect(res.status).toBe(404);
  });
});
