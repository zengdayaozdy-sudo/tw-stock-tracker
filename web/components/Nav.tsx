"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

// 圖示：20×20，線條風格
const ICONS: Record<string, React.ReactNode> = {
  home: <path d="M3 9.5 10 4l7 5.5V16a1 1 0 0 1-1 1h-3.5v-4.5h-5V17H4a1 1 0 0 1-1-1Z" />,
  layers: <path d="m10 3 7 3.5-7 3.5-7-3.5Zm-7 7 7 3.5 7-3.5M3 13.5 10 17l7-3.5" />,
  star: <path d="m10 3 2.1 4.3 4.7.7-3.4 3.3.8 4.7L10 13.8 5.8 16l.8-4.7L3.2 8l4.7-.7Z" />,
  wallet: <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h11A1.5 1.5 0 0 1 17 6.5v8a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5Zm10 4.5h4" />,
  gear: (
    <>
      <circle cx="10" cy="10" r="2.5" />
      <path d="M10 2.5v2m0 11v2M2.5 10h2m11 0h2M4.7 4.7l1.4 1.4m7.8 7.8 1.4 1.4m0-10.6-1.4 1.4m-7.8 7.8-1.4 1.4" />
    </>
  ),
  pulse: <path d="M2.5 10h3l2-5 3 10 2-5h5" />,
};

const LINKS = [
  { href: "/", label: "總覽", icon: "home" },
  { href: "/watchlist", label: "自選股", icon: "star" },
  { href: "/groups", label: "族群", icon: "layers" },
  { href: "/portfolio", label: "持股與交易", short: "持股", icon: "wallet" },
  { href: "/settings", label: "設定", icon: "gear" },
  { href: "/status", label: "系統狀態", icon: "pulse", desktopOnly: true },
] as const;

function Icon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="nav-icon">
      {ICONS[name]}
    </svg>
  );
}

export function Nav() {
  const path = usePathname();
  const navRef = useRef<HTMLElement>(null);
  const [ind, setInd] = useState<{ x: number; w: number } | null>(null);
  const isActive = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  // 桌機：量測目前頁面連結位置，讓底色滑過去
  useIsoLayoutEffect(() => {
    const measure = () => {
      const a = navRef.current?.querySelector<HTMLElement>("a.active");
      setInd(a ? { x: a.offsetLeft, w: a.offsetWidth } : null);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [path]);

  if (path === "/login") return null;
  const tabs = LINKS.filter((l) => !("desktopOnly" in l));
  const tabIndex = tabs.findIndex((l) => isActive(l.href));
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <Link href="/" className="brand">
            <span className="brand-mark" aria-hidden="true" />
            台股追蹤
          </Link>
          <nav ref={navRef} className={`nav ${ind ? "has-ind" : ""}`} aria-label="主要頁面">
            {ind && <span className="nav-ind" style={{ transform: `translateX(${ind.x}px)`, width: ind.w }} />}
            {LINKS.map((l) => (
              <Link key={l.href} href={l.href} className={isActive(l.href) ? "active" : ""}>
                {l.label}
              </Link>
            ))}
          </nav>
          <Link href="/status" className={`topbar-status ${isActive("/status") ? "active" : ""}`} aria-label="系統狀態">
            <Icon name="pulse" />
          </Link>
          <form method="post" action="/api/logout">
            <button className="logout" type="submit">登出</button>
          </form>
        </div>
      </header>
      <nav className="tabbar" aria-label="主要頁面">
        {tabIndex >= 0 && <span className="tab-ind" style={{ transform: `translateX(${tabIndex * 100}%)` }} />}
        {tabs.map((l) => (
          <Link key={l.href} href={l.href} className={isActive(l.href) ? "active" : ""}>
            <Icon name={l.icon} />
            <span>{"short" in l ? l.short : l.label}</span>
          </Link>
        ))}
      </nav>
    </>
  );
}
