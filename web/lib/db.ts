import "server-only";
import postgres from "postgres";

// Neon 建議使用 pooled 連線字串；pgbouncer 不支援 prepared statements，所以關閉 prepare。
const globalForDb = globalThis as unknown as { sql?: postgres.Sql };

export function db(): postgres.Sql {
  if (!globalForDb.sql) {
    const url = process.env.DATABASE_URL;
    if (!url) throw new Error("未設定 DATABASE_URL");
    globalForDb.sql = postgres(url, {
      max: 10,
      idle_timeout: 20,
      connect_timeout: 15,
      prepare: false,
      types: {
        // numeric 轉成 JS number（股價、分數都在 double 精度內）
        numeric: { to: 1700, from: [1700], serialize: (x: number) => String(x), parse: (x: string) => Number(x) },
        // date 保持 YYYY-MM-DD 字串，避免時區轉換
        date: { to: 1082, from: [1082], serialize: (x: string) => x, parse: (x: string) => x },
      },
    });
  }
  return globalForDb.sql;
}
