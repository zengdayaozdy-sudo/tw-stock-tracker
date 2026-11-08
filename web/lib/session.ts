import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { SESSION_COOKIE, verifySession } from "./auth";

/** 每個頁面與 Server Action 再驗證一次（proxy 只是第一道檢查）。 */
export async function requireSession(): Promise<void> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!(await verifySession(token, process.env.SESSION_SECRET))) redirect("/login");
}
