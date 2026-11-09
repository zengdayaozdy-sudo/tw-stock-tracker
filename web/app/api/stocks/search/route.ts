// 股票搜尋（代號前綴或名稱關鍵字），供自選股與族群的輸入建議使用。
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, verifySession } from "@/lib/auth";
import { searchStocks } from "@/lib/queries";

export async function GET(req: NextRequest) {
  if (!(await verifySession(req.cookies.get(SESSION_COOKIE)?.value, process.env.SESSION_SECRET)))
    return NextResponse.json({ error: "未登入" }, { status: 401 });
  const q = (req.nextUrl.searchParams.get("q") ?? "").trim().slice(0, 20);
  if (!q) return NextResponse.json({ results: [] });
  const rows = await searchStocks(q, 8);
  return NextResponse.json({
    results: rows.map((r) => ({
      stock_id: r.stock_id,
      name: r.name,
      industry: r.industry,
      market: r.market,
      in_watchlist: r.in_watchlist,
    })),
  });
}
