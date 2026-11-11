// 定時觸發端點：不需要登入 cookie，但必須帶正確的 CRON_SECRET 才會啟動 GitHub 工作流程
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/api/cron/daily/route";
import { proxy } from "../proxy";

const call = (auth?: string) =>
  GET(new NextRequest("http://localhost/api/cron/daily", { headers: auth ? { authorization: auth } : {} }));

describe("定時觸發 /api/cron/daily", () => {
  beforeEach(() => {
    process.env.CRON_SECRET = "cron-test-secret-0123456789";
    process.env.GITHUB_DISPATCH_TOKEN = "test-token";
  });
  afterEach(() => vi.unstubAllGlobals());

  it("proxy 放行給路由自行驗證，其他 /api/cron 子路徑仍要登入", async () => {
    expect((await proxy(new NextRequest("http://localhost/api/cron/daily"))).headers.get("x-middleware-next")).toBe("1");
    expect((await proxy(new NextRequest("http://localhost/api/cron/a/b"))).status).toBe(401);
  });

  it("沒有或錯誤的 CRON_SECRET 回 401，不呼叫 GitHub", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect((await call()).status).toBe(401);
    expect((await call("Bearer wrong")).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("正確的 CRON_SECRET 會以 vercel-cron 來源啟動 daily.yml", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await call("Bearer cron-test-secret-0123456789");
    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/actions/workflows/daily.yml/dispatches");
    expect(JSON.parse(init.body)).toEqual({ ref: "main", inputs: { source: "vercel-cron" } });
  });
});
