// Vercel 定時觸發：收盤後主流程（行情、法人、評分、訊號、每日報告）
import type { NextRequest } from "next/server";
import { dispatchDaily } from "@/lib/dispatch";

export function GET(req: NextRequest) {
  return dispatchDaily(req, "main");
}
