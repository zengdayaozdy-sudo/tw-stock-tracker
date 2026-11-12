"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { BrandMark, Icon, type IconName } from "./Icon";

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

const LINKS = [
  { href: "/", label: "總覽", icon: "overview" },
  { href: "/summary", label: "盤後總結", short: "總結", icon: "summary" },
  { href: "/watchlist", label: "自選股", icon: "watch" },
  { href: "/groups", label: "族群", icon: "groups" },
  { href: "/portfolio", label: "持股與交易", short: "持股", icon: "portfolio" },
  { href: "/settings", label: "設定", icon: "settings" },
  { href: "/status", label: "系統狀態", icon: "status", desktopOnly: true },
] as const satisfies readonly { href: string; label: string; icon: IconName; short?: string; desktopOnly?: boolean }[];

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
            <BrandMark size={26} />
            <span className="brand-name">台股追蹤</span>
          </Link>
          <nav ref={navRef} className={`nav ${ind ? "has-ind" : ""}`} aria-label="主要頁面">
            {ind && <span className="nav-ind" style={{ transform: `translateX(${ind.x}px)`, width: ind.w }} />}
            {LINKS.map((l) => (
              <Link key={l.href} href={l.href} className={isActive(l.href) ? "active" : ""}>
                <Icon name={l.icon} />
                {l.label}
              </Link>
            ))}
          </nav>
          <Link href="/status" className={`topbar-status ${isActive("/status") ? "active" : ""}`} aria-label="系統狀態">
            <Icon name="status" />
          </Link>
          <form method="post" action="/api/logout">
            <button className="logout" type="submit" aria-label="登出" title="登出">
              <Icon name="logout" />
              <span>登出</span>
            </button>
          </form>
        </div>
      </header>
      <nav className="tabbar" aria-label="主要頁面">
        {tabIndex >= 0 && <span className="tab-ind" style={{ transform: `translateX(${tabIndex * 100}%)` }} />}
        {tabs.map((l) => (
          <Link key={l.href} href={l.href} className={isActive(l.href) ? "active" : ""}>
            <span className="tab-icon">
              <Icon name={l.icon} />
            </span>
            <span>{"short" in l ? l.short : l.label}</span>
          </Link>
        ))}
      </nav>
    </>
  );
}
