// 驗收條件 5：未登入無法存取任何頁面與 API
import { NextRequest } from "next/server";
import { beforeAll, describe, expect, it } from "vitest";
import { proxy } from "../proxy";
import { SESSION_COOKIE, createSession } from "./auth";

const SECRET = "proxy-test-secret-0123456789";

beforeAll(() => {
  process.env.SESSION_SECRET = SECRET;
});

const req = (path: string, cookie?: string) =>
  new NextRequest(`http://localhost${path}`, { headers: cookie ? { cookie: `${SESSION_COOKIE}=${cookie}` } : {} });

describe("proxy", () => {
  it.each(["/", "/portfolio", "/stocks/2330", "/settings", "/status", "/groups/半導體業"])("未登入的頁面 %s 導向登入", async (path) => {
    const res = await proxy(req(path));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/login");
  });

  it("未登入的 API 回 401", async () => {
    expect((await proxy(req("/api/anything"))).status).toBe(401);
    expect((await proxy(req("/api/logout"))).status).toBe(401);
  });

  it("偽造或過期的 session 無效", async () => {
    expect((await proxy(req("/", "v1.9999999999999.deadbeef"))).status).toBe(307);
    expect((await proxy(req("/", await createSession(SECRET, 0)))).status).toBe(307);
    expect((await proxy(req("/", await createSession("other-secret-0123456789")))).status).toBe(307);
  });

  it("登入頁與登入 API 不需要 session；有效 session 可通過", async () => {
    expect((await proxy(req("/login"))).headers.get("x-middleware-next")).toBe("1");
    expect((await proxy(req("/api/login"))).headers.get("x-middleware-next")).toBe("1");
    expect((await proxy(req("/portfolio", await createSession(SECRET)))).headers.get("x-middleware-next")).toBe("1");
  });

  it("MCP 端點不檢查 cookie，交給路由驗證 MCP_TOKEN；其他路徑不受影響", async () => {
    expect((await proxy(req("/api/mcp"))).headers.get("x-middleware-next")).toBe("1");
    expect((await proxy(req("/api/mcp/some-token"))).headers.get("x-middleware-next")).toBe("1");
    expect((await proxy(req("/api/mcp/a/b"))).status).toBe(401);
    expect((await proxy(req("/api/mcpx"))).status).toBe(401);
  });
});
