import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, SESSION_DAYS, authConfigured, createSession, safeEqual } from "@/lib/auth";

export async function POST(req: NextRequest) {
  const form = await req.formData();
  const password = String(form.get("password") ?? "");
  const nextPath = String(form.get("next") ?? "/");
  const back = (error: string) => {
    const url = new URL("/login", req.url);
    url.searchParams.set("error", error);
    if (nextPath !== "/") url.searchParams.set("next", nextPath);
    return NextResponse.redirect(url, 303);
  };
  if (!authConfigured()) return back("config");
  // 密碼錯誤時延遲回應，降低暴力嘗試速度
  if (!safeEqual(password, process.env.SITE_PASSWORD!)) {
    await new Promise((r) => setTimeout(r, 800));
    return back("wrong");
  }
  const safeNext = nextPath.startsWith("/") && !nextPath.startsWith("//") ? nextPath : "/";
  const res = NextResponse.redirect(new URL(safeNext, req.url), 303);
  res.cookies.set(SESSION_COOKIE, await createSession(process.env.SESSION_SECRET!), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_DAYS * 24 * 3600,
  });
  return res;
}
