// 全站圖標：24×24 網格、1.6 線寬、圓角端點，與數字字體的筆畫粗細一致
const PATHS = {
  // 導覽
  overview: (
    <>
      <path d="M7 5v3.5M7 17v2.5M17 3v2.5M17 15.5V21" />
      <rect x="4.5" y="8.5" width="5" height="8.5" rx="1.4" />
      <rect x="14.5" y="5.5" width="5" height="10" rx="1.4" />
    </>
  ),
  watch: (
    <>
      <path d="M2.8 12.3c1.9-3.9 5.3-6.3 9.2-6.3s7.3 2.4 9.2 6.3c-1.9 3.9-5.3 6.2-9.2 6.2s-7.3-2.3-9.2-6.2Z" />
      <circle cx="12" cy="12.2" r="3" />
    </>
  ),
  groups: (
    <>
      <circle cx="6.5" cy="7" r="2.5" />
      <circle cx="17.5" cy="7" r="2.5" />
      <circle cx="12" cy="17" r="2.5" />
      <path d="M9 7h6M7.7 9.2l3.1 5.6M16.3 9.2l-3.1 5.6" />
    </>
  ),
  portfolio: (
    <>
      <rect x="3.5" y="7" width="17" height="12.5" rx="2.5" />
      <path d="M9 7V5.6c0-.9.7-1.6 1.6-1.6h2.8c.9 0 1.6.7 1.6 1.6V7M3.5 12.5h17M10.5 12.5v1.5h3v-1.5" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7.5h8.5M17.5 7.5H20M4 16.5h2.5M11.5 16.5H20" />
      <circle cx="15" cy="7.5" r="2.5" />
      <circle cx="9" cy="16.5" r="2.5" />
    </>
  ),
  status: <path d="M3 12.5h3.8l2.4-6.5 4.6 12 2.4-5.5H21" />,
  summary: (
    <>
      <path d="M14 3.5H7.2a1.7 1.7 0 0 0-1.7 1.7v13.6a1.7 1.7 0 0 0 1.7 1.7h9.6a1.7 1.7 0 0 0 1.7-1.7V8Z" />
      <path d="M14 3.5V8h4.5M8.5 16l2.2-2.6 2 1.6 2.8-3.5" />
    </>
  ),

  // 面板
  gauge: (
    <>
      <path d="M4.2 16.5a8 8 0 1 1 15.6 0" />
      <path d="M12 15.5l3.6-4.6M7.2 9.4l.9.8M12 6.6v1.2M16.8 9.4l-.9.8" />
      <circle cx="12" cy="16" r="1.3" />
    </>
  ),
  server: (
    <>
      <rect x="4" y="4" width="16" height="7" rx="2" />
      <rect x="4" y="13" width="16" height="7" rx="2" />
      <path d="M7.5 7.5h.01M7.5 16.5h.01M11 7.5h5.5M11 16.5h5.5" />
    </>
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="3.6" />
      <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3" />
    </>
  ),
  sprout: (
    <>
      <path d="M12 20.5V11.5" />
      <path d="M12 11.5c0-4.1 2.8-6.6 7.5-6.6 0 4.2-2.9 6.6-7.5 6.6Z" />
      <path d="M12 14c0-3.1-2.2-5.2-6-5.2 0 3.2 2.3 5.2 6 5.2Z" />
      <path d="M8 20.5h8" />
    </>
  ),
  layers: <path d="m12 3.8 8.2 4.1-8.2 4.1-8.2-4.1Zm-8.2 8.1 8.2 4.1 8.2-4.1M3.8 15.9 12 20l8.2-4.1" />,
  bars: <path d="M4.5 6.5h9M4.5 12h15M4.5 17.5h6" />,
  shield: (
    <>
      <path d="M12 3.3 5 6v5.6c0 4.3 2.9 7.6 7 9.1 4.1-1.5 7-4.8 7-9.1V6Z" />
      <path d="m9.2 12.1 2 2 3.8-4" />
    </>
  ),
  calendar: (
    <>
      <rect x="4" y="5.5" width="16" height="14.5" rx="2.5" />
      <path d="M8 3.5v4M16 3.5v4M4 10.5h16M8.5 14.5h2M13.5 14.5h2" />
    </>
  ),
  chart: (
    <>
      <path d="M4 4.5v15h16" />
      <path d="m7.5 15 3.5-4 3 2.5 5-6" />
    </>
  ),
  pen: (
    <>
      <path d="M14.8 5.2l4 4L9.3 18.7 4.5 19.5l.8-4.8Z" />
      <path d="M12.8 7.2l4 4" />
    </>
  ),
  list: <path d="M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01" />,
  pie: (
    <>
      <path d="M11 4.1a8 8 0 1 0 8.9 8.9H11Z" />
      <path d="M14 3.6a7 7 0 0 1 6.4 6.4H14Z" />
    </>
  ),
  coins: (
    <>
      <ellipse cx="10" cy="7" rx="6" ry="2.5" />
      <path d="M4 7v4.5c0 1.4 2.7 2.5 6 2.5M4 11.5V16c0 1.4 2.7 2.5 6 2.5M16 7v2" />
      <ellipse cx="16" cy="13" rx="4.5" ry="2" />
      <path d="M11.5 13v4c0 1.1 2 2 4.5 2s4.5-.9 4.5-2v-4" />
    </>
  ),
  bell: (
    <>
      <path d="M6.2 16.5V11a5.8 5.8 0 0 1 11.6 0v5.5l1.7 1.5H4.5Z" />
      <path d="M10 20.5h4" />
    </>
  ),
  radar: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 12l5.2-5.2" />
      <circle cx="12" cy="12" r="1" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="2.8" />
      <path d="M5 6v12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8" />
    </>
  ),
  checkCircle: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m8.5 12.2 2.4 2.4 4.6-4.8" />
    </>
  ),
  layersLock: (
    <>
      <rect x="5" y="10.5" width="14" height="10" rx="2.5" />
      <path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5M12 14.5v2" />
    </>
  ),

  // 小工具
  search: (
    <>
      <circle cx="10.8" cy="10.8" r="6.3" />
      <path d="m15.5 15.5 4.5 4.5" />
    </>
  ),
  logout: (
    <>
      <path d="M14 4.5H7a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h7" />
      <path d="M10.5 12H20M16.5 8.5 20 12l-3.5 3.5" />
    </>
  ),
  external: <path d="M10 5.5H6.5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V14M13.5 4.5h6v6M19.5 4.5l-9 9" />,
  arrowRight: <path d="M5 12h14M13.5 6.5 19 12l-5.5 5.5" />,
  plus: <path d="M12 5v14M5 12h14" />,
  alert: (
    <>
      <path d="M10.3 4.6 3.2 17.3a2 2 0 0 0 1.7 3h14.2a2 2 0 0 0 1.7-3L13.7 4.6a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9.5v4.2M12 16.8h.01" />
    </>
  ),
  ban: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m6 6 12 12" />
    </>
  ),
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size, className = "" }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className={`icon ${className}`}
      style={size ? { width: size, height: size } : undefined}
    >
      {PATHS[name]}
    </svg>
  );
}

/** 品牌標誌：深色圓角方塊，一根下跌綠 K 與一根上漲紅 K，台股慣例紅漲綠跌 */
export function BrandMark({ size = 26 }: { size?: number }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true" className="brand-mark">
      <rect width="64" height="64" rx="16" fill="var(--brand-bg)" />
      <path d="M22 17v7M22 42v6" stroke="#2bb673" strokeWidth="3.4" strokeLinecap="round" />
      <rect x="16" y="24" width="12" height="18" rx="3" fill="#2bb673" />
      <path d="M42 10v6M42 46v8" stroke="#ff4d5e" strokeWidth="3.4" strokeLinecap="round" />
      <rect x="36" y="16" width="12" height="30" rx="3" fill="#ff4d5e" />
    </svg>
  );
}
