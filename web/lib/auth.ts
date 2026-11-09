// 單一密碼登入：登入成功後發 HMAC 簽章的 session token（HTTP-only cookie）。
// 只用 Web Crypto，proxy（Edge）與 Node 環境都能驗證。

export const SESSION_COOKIE = "session";
export const SESSION_DAYS = 30;

const encoder = new TextEncoder();

function toHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmac(secret: string, data: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
  ]);
  return toHex(await crypto.subtle.sign("HMAC", key, encoder.encode(data)));
}

/** 長度不同也不提早結束的比較，避免以回應時間猜測內容。 */
export function safeEqual(a: string, b: string): boolean {
  const len = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < len; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

export async function createSession(secret: string, now = Date.now()): Promise<string> {
  const expires = now + SESSION_DAYS * 24 * 3600 * 1000;
  const payload = `v1.${expires}`;
  return `${payload}.${await hmac(secret, payload)}`;
}

export async function verifySession(token: string | undefined, secret: string | undefined, now = Date.now()) {
  if (!token || !secret) return false;
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return false;
  const expires = Number(parts[1]);
  if (!Number.isFinite(expires) || expires < now) return false;
  return safeEqual(parts[2], await hmac(secret, `${parts[0]}.${parts[1]}`));
}

export function authConfigured(): boolean {
  return Boolean(process.env.SITE_PASSWORD && process.env.SESSION_SECRET && process.env.SESSION_SECRET.length >= 16);
}

// ---------- MCP 存取權杖 ----------

export const MCP_TOKEN_MIN_LENGTH = 32;

/** MCP 端點只接受 MCP_TOKEN；未設定或長度不足時一律拒絕（端點等於關閉）。 */
export function verifyMcpToken(token: string | undefined | null, expected: string | undefined): boolean {
  if (!token || !expected || expected.length < MCP_TOKEN_MIN_LENGTH) return false;
  return safeEqual(token, expected);
}

/** 從 Authorization: Bearer 標頭取出權杖。 */
export function bearerToken(header: string | null): string | null {
  const m = header?.match(/^Bearer\s+(\S+)\s*$/i);
  return m ? m[1] : null;
}
