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

/** 2026-11-09 → 11/09。 */
export function shortDate(d: string | null | undefined): string {
  return d ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : "—";
}

/** 距今多久：剛剛、12 分鐘前、3 小時前、2 天前。 */
export function ago(d: Date | string | null | undefined, now: Date = new Date()): string {
  if (!d) return "";
  const mins = Math.floor((now.getTime() - new Date(d).getTime()) / 60000);
  if (mins < 1) return "剛剛";
  if (mins < 60) return `${mins} 分鐘前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小時前`;
  return `${Math.floor(hours / 24)} 天前`;
}

/** 反彈偵測證據轉成短句（與 engine/filters/rebound.py evidence_text 一致） */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function reboundEvidence(evidence: Record<string, Record<string, any>> | undefined): string[] {
  const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
  return Object.entries(evidence ?? {}).map(([k, e]) => {
    if (k === "ftd") return `追蹤日 ${e.date}（反彈第 ${e.day} 天，漲 ${pct(e.gain)}）`;
    if (k === "power_rally") return `急漲反彈（${e.days} 天漲 ${pct(e.gain)}）`;
    if (k === "breadth_thrust") return `寬度衝刺 ${e.date}（上漲比率 EMA ${Number(e.ema).toFixed(2)}）`;
    if (k === "breadth_recovery")
      return `寬度回升（站上 20 日線 ${Math.round(e.low * 100)}% → ${Math.round(e.now * 100)}%）`;
    if (k === "short_covering") return `外資期貨空單回補 ${Math.round(e.covered).toLocaleString("zh-TW")} 口`;
    return e.label ?? k;
  });
}
