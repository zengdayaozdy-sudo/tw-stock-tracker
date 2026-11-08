import { describe, expect, it } from "vitest";
import { createSession, safeEqual, verifySession } from "./auth";

const SECRET = "test-secret-at-least-16";

describe("session", () => {
  it("簽發後可驗證", async () => {
    expect(await verifySession(await createSession(SECRET), SECRET)).toBe(true);
  });
  it("密鑰不同、竄改、過期都無效", async () => {
    const token = await createSession(SECRET, 0);
    expect(await verifySession(token, SECRET)).toBe(false); // 1970 年發的早已過期
    const fresh = await createSession(SECRET);
    expect(await verifySession(fresh, "another-secret-value")).toBe(false);
    const [v, exp, sig] = fresh.split(".");
    expect(await verifySession(`${v}.${Number(exp) + 1}.${sig}`, SECRET)).toBe(false);
    expect(await verifySession(undefined, SECRET)).toBe(false);
    expect(await verifySession(fresh, undefined)).toBe(false);
    expect(await verifySession("garbage", SECRET)).toBe(false);
  });
  it("safeEqual", () => {
    expect(safeEqual("abc", "abc")).toBe(true);
    expect(safeEqual("abc", "abd")).toBe(false);
    expect(safeEqual("abc", "abcd")).toBe(false);
  });
});
