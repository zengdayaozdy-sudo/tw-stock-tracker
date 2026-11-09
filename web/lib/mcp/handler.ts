import "server-only";
import { createMcpHandler } from "mcp-handler";
import { verifyMcpToken } from "../auth";
import { registerTools } from "./tools";

const INSTRUCTIONS = `台股自選股評分追蹤程式（擁有者自用）。資料由每個交易日收盤後的排程更新，日期一律為台灣交易日。
- 先用 get_overview 看當日大盤燈號、族群強弱、買進訊號與持股提醒；個股細節用 get_stock（含評分拆解）。
- 分數 0–100，短線偏重技術與籌碼、中長線偏重基本面；status 為「資料不足」者不評分。
- 訊號與參考價位只供參考，本服務不會下單。record_trade 只登錄使用者已在券商成交的交易，登錄前先向使用者確認內容。
- 回覆使用者時使用繁體中文（台灣用語），台股慣例紅漲綠跌。`;

const mcp = createMcpHandler(registerTools, {
  serverInfo: { name: "tw-stock-tracker", version: "1.0.0" },
  instructions: INSTRUCTIONS,
});

/**
 * 驗證權杖後交給 MCP 處理。權杖錯誤時：標頭驗證回 401，路徑驗證回 404（不透露端點存在）。
 */
export async function serveMcp(req: Request, token: string | null, via: "header" | "path"): Promise<Response> {
  if (!verifyMcpToken(token, process.env.MCP_TOKEN)) {
    if (via === "path") return new Response("Not Found", { status: 404 });
    return Response.json(
      { jsonrpc: "2.0", error: { code: -32001, message: "未授權：請以 Authorization: Bearer <MCP_TOKEN> 呼叫" }, id: null },
      { status: 401 },
    );
  }
  return mcp(req);
}
