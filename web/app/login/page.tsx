import { authConfigured } from "@/lib/auth";

const ERRORS: Record<string, string> = {
  wrong: "密碼錯誤",
  config: "尚未設定 SITE_PASSWORD 或 SESSION_SECRET（至少 16 字元）",
};

export default async function LoginPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const { error, next } = await searchParams;
  const message = error ? ERRORS[error] : !authConfigured() ? ERRORS.config : null;
  return (
    <main className="login">
      <h1>台股追蹤</h1>
      {message && <p className="notice error">{message}</p>}
      <form method="post" action="/api/login">
        <input type="password" name="password" placeholder="密碼" autoFocus required autoComplete="current-password" />
        <input type="hidden" name="next" value={next ?? "/"} />
        <button type="submit">登入</button>
      </form>
    </main>
  );
}
