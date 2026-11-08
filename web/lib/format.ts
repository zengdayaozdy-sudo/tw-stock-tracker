// 數字與日期格式。台股慣例：上漲紅、下跌綠。

export function num(x: number | null | undefined, digits?: number): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  if (digits !== undefined) return x.toLocaleString("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const d = Math.abs(x) >= 1000 ? 0 : 2;
  return x.toLocaleString("zh-TW", { maximumFractionDigits: d });
}

export function pct(x: number | null | undefined, digits = 1, sign = true): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const s = (x * 100).toFixed(digits) + "%";
  return sign && x > 0 ? `+${s}` : s;
}

export function money(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  return Math.round(x).toLocaleString("zh-TW");
}

/** 漲跌顏色 class：正值紅、負值綠。 */
export function tone(x: number | null | undefined): string {
  if (!x) return "";
  return x > 0 ? "up" : "down";
}

const WEEKDAYS = "日一二三四五六";

/** 2026-11-06 → 2026/11/06（五）。台灣中午 12 點換算 UTC 仍是同一天，可直接取星期。 */
export function dateLabel(d: string): string {
  const weekday = new Date(`${d}T12:00:00+08:00`).getUTCDay();
  return `${d.replaceAll("-", "/")}（${WEEKDAYS[weekday]}）`;
}

export function todayTaipei(): string {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Taipei" }).format(new Date());
}

export function timeTaipei(d: Date | string | null | undefined): string {
  if (!d) return "—";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(d));
}

export const LIGHT_LABELS: Record<string, string> = { bullish: "偏多", neutral: "中性", bearish: "偏空" };
export const HORIZON_LABELS: Record<string, string> = { short: "短線", long: "中長線" };
