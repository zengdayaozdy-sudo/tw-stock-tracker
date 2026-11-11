// Vercel 定時觸發共用：驗證 CRON_SECRET 後呼叫 GitHub API 啟動 daily.yml。
// GitHub Actions 的排程在尖峰時段可能延遲數小時（2026-11-10 延遲約 8 小時），所以由 Vercel Cron 準時觸發；
// GitHub 自己的排程保留當備援。同一階段當天已完成時，工作流程會在「判斷是否為交易日」步驟直接略過。
import { NextResponse, type NextRequest } from "next/server";

const REPO = process.env.GITHUB_REPO ?? "zengdayaozdy-sudo/tw-stock-tracker";

export type Phase = "main" | "evening";

export async function dispatchDaily(req: NextRequest, phase: Phase) {
  // 貼上時常多帶前後空白或換行，去掉再用
  const secret = process.env.CRON_SECRET?.trim();
  if (!secret || req.headers.get("authorization") !== `Bearer ${secret}`) {
    return NextResponse.json({ error: "未授權" }, { status: 401 });
  }
  const token = process.env.GITHUB_DISPATCH_TOKEN?.trim();
  if (!token) {
    return NextResponse.json({ error: "尚未設定 GITHUB_DISPATCH_TOKEN" }, { status: 500 });
  }
  const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/daily.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "tw-stock-tracker-cron",
    },
    body: JSON.stringify({ ref: "main", inputs: { source: "vercel-cron", phase } }),
  });
  if (res.status !== 204) {
    const detail = await res.text();
    return NextResponse.json({ error: `GitHub 回應 ${res.status}`, detail: detail.slice(0, 300) }, { status: 502 });
  }
  return NextResponse.json({ ok: true, phase, triggered: new Date().toISOString() });
}
