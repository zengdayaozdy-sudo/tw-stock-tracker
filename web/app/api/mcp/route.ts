// MCP 端點（Streamable HTTP）：Authorization: Bearer <MCP_TOKEN>
// 適用 Claude Code、Claude Desktop 等可以自訂標頭的客戶端。
import { bearerToken } from "@/lib/auth";
import { serveMcp } from "@/lib/mcp/handler";

export const dynamic = "force-dynamic";

async function handler(req: Request) {
  return serveMcp(req, bearerToken(req.headers.get("authorization")), "header");
}

export { handler as DELETE, handler as GET, handler as POST };
