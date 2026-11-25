// 未登入時：頁面導向 /login，API 回 401。只讀 cookie 做驗證（不查資料庫）。
// 例外：MCP 端點改用 MCP_TOKEN 驗證，由路由處理。
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, verifySession } from "@/lib/auth";

const PUBLIC = new Set(["/login", "/api/login"]);
// MCP 端點不用 cookie，由路由自行驗證 MCP_TOKEN（app/api/mcp）
const MCP = /^\/api\/mcp(\/[^/]+)?$/;

export async function proxy(req: NextRequest) {
  const path = req.nextUrl.pathname;
  if (PUBLIC.has(path) || MCP.test(path)) return NextResponse.next();
  const ok = await verifySession(req.cookies.get(SESSION_COOKIE)?.value, process.env.SESSION_SECRET);
  if (ok) return NextResponse.next();
  if (path.startsWith("/api/")) return NextResponse.json({ error: "未登入" }, { status: 401 });
  const url = new URL("/login", req.url);
  if (path !== "/") url.searchParams.set("next", path);
  return NextResponse.redirect(url);
}

export const config = {
  // 靜態資源與網站圖示不檢查，其餘（含所有 API）都要登入
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|apple-icon.png|icon-192.png|icon-512.png|manifest.webmanifest).*)"],
};
