// Vercel 定時觸發：晚間補抓（當日融資融券、借券、晚間重大訊息），只推播有變動的訊號
import type { NextRequest } from "next/server";
import { dispatchDaily } from "@/lib/dispatch";

export function GET(req: NextRequest) {
  return dispatchDaily(req, "evening");
}
