// 與 Python 引擎相同的交易規則（engine/portfolio、engine/signals/levels），
// 網站登錄的交易與指令列登錄的結果必須一致。規則數字來自 settings.runtime_config。

export type Costs = {
  commission_rate: number;
  commission_discount?: number;
  min_commission: number;
  tax_rate: number;
};

export type StopCfg = { method: string; percent: number; bearish_market_percent: number; ma60_buffer?: number; max_percent?: number };

// 證交所升降單位（交易所規則）
const TICKS: [number, number][] = [
  [10, 0.01],
  [50, 0.05],
  [100, 0.1],
  [500, 0.5],
  [1000, 1],
  [Infinity, 5],
];

export function tickSize(price: number): number {
  for (const [upper, tick] of TICKS) if (price < upper) return tick;
  return 5;
}

export function roundTick(price: number, direction: "down" | "up" | "nearest" = "nearest"): number {
  const tick = tickSize(price);
  const n = price / tick;
  const k = direction === "down" ? Math.floor(n + 1e-9) : direction === "up" ? Math.ceil(n - 1e-9) : Math.round(n);
  return Math.round(k * tick * 100) / 100;
}

export function commission(price: number, shares: number, c: Costs): number {
  const fee = Math.floor(price * shares * c.commission_rate * (c.commission_discount ?? 1));
  return Math.max(c.min_commission, fee);
}

export function sellTax(price: number, shares: number, c: Costs): number {
  return Math.floor(price * shares * c.tax_rate);
}

export function stopPrice(close: number, ma60: number | null, cfg: StopCfg, bearish = false): number {
  const pct = bearish ? cfg.bearish_market_percent : cfg.percent;
  if (cfg.method === "ma60" && ma60 && ma60 < close) {
    const stop = ma60 * (1 - (cfg.ma60_buffer ?? 0));
    if (1 - stop / close <= (cfg.max_percent ?? 1)) return roundTick(stop, "down");
  }
  return roundTick(close * (1 - pct), "down");
}

// ---------- 交易統計（平均成本法，與 engine/portfolio/stats.py 相同） ----------

export type Trade = {
  trade_id: number;
  stock_id: string;
  date: string; // YYYY-MM-DD
  side: "buy" | "sell";
  price: number;
  shares: number;
  fee: number;
  tax: number;
};

export type ClosedTrade = {
  stock_id: string;
  date: string;
  opened: string | null;
  shares: number;
  pnl: number;
  pnl_pct: number;
  days: number | null;
};

const dayDiff = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);

export function closedTrades(trades: Trade[]) {
  const pos = new Map<string, { shares: number; cost: number; opened: string | null }>();
  const closed: ClosedTrade[] = [];
  const sorted = [...trades].sort((a, b) => (a.date === b.date ? a.trade_id - b.trade_id : a.date < b.date ? -1 : 1));
  for (const t of sorted) {
    const p = pos.get(t.stock_id) ?? { shares: 0, cost: 0, opened: null };
    pos.set(t.stock_id, p);
    if (t.side === "buy") {
      if (p.shares === 0) p.opened = t.date;
      p.shares += t.shares;
      p.cost += t.price * t.shares + t.fee;
      continue;
    }
    if (p.shares <= 0) continue;
    const shares = Math.min(t.shares, p.shares);
    const basis = (p.cost / p.shares) * shares;
    const pnl = t.price * shares - t.fee - t.tax - basis;
    closed.push({
      stock_id: t.stock_id,
      date: t.date,
      opened: p.opened,
      shares,
      pnl: Math.round(pnl * 100) / 100,
      pnl_pct: basis ? pnl / basis : 0,
      days: p.opened ? dayDiff(p.opened, t.date) : null,
    });
    p.cost -= basis;
    p.shares -= shares;
  }
  const open = new Map([...pos].filter(([, p]) => p.shares > 0));
  return { closed, open };
}

export type Stats = {
  closed: number;
  wins: number;
  losses: number;
  winRate: number | null;
  avgWinPct: number | null;
  avgLossPct: number | null;
  expectancyPct: number | null;
  realized: number;
  unrealized: number;
  avgHoldingDays: number | null;
  start: string | null;
  portfolioReturn: number | null;
  benchmarks: Record<string, number | null>;
  recent: ClosedTrade[];
};

function seriesReturn(points: [string, number][], start: string, end: string): number | null {
  const upto = points.filter(([d]) => d <= end);
  if (!upto.length) return null;
  const before = points.filter(([d]) => d <= start);
  const base = before.length ? before[before.length - 1][1] : points.find(([d]) => d >= start)?.[1];
  return base ? upto[upto.length - 1][1] / base - 1 : null;
}

export function tradeStats(
  trades: Trade[],
  lastClose: Record<string, number>,
  capital: number,
  asOf: string,
  benchmarks: Record<string, [string, number][]>,
): Stats {
  const { closed, open } = closedTrades(trades.filter((t) => t.date <= asOf));
  const wins = closed.filter((c) => c.pnl > 0);
  const losses = closed.filter((c) => c.pnl <= 0);
  const n = closed.length;
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  const winRate = n ? wins.length / n : null;
  const avgWin = avg(wins.map((c) => c.pnl_pct));
  const avgLoss = avg(losses.map((c) => c.pnl_pct));
  const realized = closed.reduce((a, c) => a + c.pnl, 0);
  let unrealized = 0;
  for (const [sid, p] of open) if (lastClose[sid] !== undefined) unrealized += lastClose[sid] * p.shares - p.cost;
  const start = trades.length ? trades.map((t) => t.date).sort()[0] : null;
  const bench: Record<string, number | null> = {};
  if (start) for (const [k, pts] of Object.entries(benchmarks)) bench[k] = seriesReturn(pts, start, asOf);
  return {
    closed: n,
    wins: wins.length,
    losses: losses.length,
    winRate,
    avgWinPct: avgWin,
    avgLossPct: avgLoss,
    expectancyPct: n ? (winRate ?? 0) * (avgWin ?? 0) + (1 - (winRate ?? 0)) * (avgLoss ?? 0) : null,
    realized: Math.round(realized),
    unrealized: Math.round(unrealized),
    avgHoldingDays: n ? Math.round((closed.reduce((a, c) => a + (c.days ?? 0), 0) / n) * 10) / 10 : null,
    start,
    portfolioReturn: capital && start ? (realized + unrealized) / capital : null,
    benchmarks: bench,
    recent: closed.slice(-10),
  };
}

/** 0050 等序列還原除息與分割：調整日以前的價格乘上係數（與 engine/adjust.py 相同）。 */
export function adjustPoints(points: [string, number][], adjustments: [string, number][]): [string, number][] {
  if (!adjustments.length) return points;
  return points.map(([d, v]) => {
    let f = 1;
    for (const [ad, factor] of adjustments) if (d < ad) f *= factor;
    return [d, v * f];
  });
}
