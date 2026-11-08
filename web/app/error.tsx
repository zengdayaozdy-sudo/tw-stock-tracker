"use client";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <>
      <h1>讀取資料時發生錯誤</h1>
      <p className="notice error">{error.message.includes("DATABASE_URL") ? "未設定 DATABASE_URL" : "資料庫連線或查詢失敗，請稍後再試"}</p>
      <button onClick={reset}>重試</button>
    </>
  );
}
