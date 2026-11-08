"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  ["/", "總覽"],
  ["/groups", "族群"],
  ["/watchlist", "自選股"],
  ["/portfolio", "持股與交易"],
  ["/settings", "設定"],
  ["/status", "系統狀態"],
] as const;

export function Nav() {
  const path = usePathname();
  if (path === "/login") return null;
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link href="/" className="brand">台股追蹤</Link>
        <nav className="nav">
          {LINKS.map(([href, label]) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} className={active ? "active" : ""}>
                {label}
              </Link>
            );
          })}
        </nav>
        <form method="post" action="/api/logout">
          <button className="logout" type="submit">登出</button>
        </form>
      </div>
    </header>
  );
}
