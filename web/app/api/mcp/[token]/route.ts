// MCP 端點（Streamable HTTP）：權杖放在網址路徑 /api/mcp/<MCP_TOKEN>
// 適用 claude.ai「自訂連接器」等無法自訂標頭的客戶端。網址等同密碼，不要外流。
import { serveMcp } from "@/lib/mcp/handler";

export const dynamic = "force-dynamic";

async function handler(req: Request, ctx: { params: Promise<{ token: string }> }) {
  const { token } = await ctx.params;
  return serveMcp(req, token, "path");
}

export { handler as DELETE, handler as GET, handler as POST };
