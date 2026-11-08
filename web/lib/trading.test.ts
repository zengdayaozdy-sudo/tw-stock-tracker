// 與 engine/tests/test_portfolio.py、test_signals.py 相同的案例，確保網站與引擎計算一致。
import { describe, expect, it } from "vitest";
import { closedTrades, commission, roundTick, sellTax, stopPrice, tickSize, tradeStats, type Trade } from "./trading";

const COSTS = { commission_rate: 0.001425, commission_discount: 1, min_commission: 20, tax_rate: 0.003 };
const STOP = { method: "percent", percent: 0.08, bearish_market_percent: 0.05, ma60_buffer: 0.01, max_percent: 0.15 };

describe("費用", () => {
  it("手續費無條件捨去、最低 20 元", () => {
    expect(commission(100, 1000, COSTS)).toBe(142);
    expect(commission(10, 100, COSTS)).toBe(20);
    expect(commission(100, 1000, { ...COSTS, commission_discount: 0.28 })).toBe(39);
    expect(commission(170, 1000, COSTS)).toBe(242);
  });
  it("證交稅", () => expect(sellTax(100, 1000, COSTS)).toBe(300));
});

describe("升降單位", () => {
  it.each([
    [9.99, 0.01],
    [10, 0.05],
    [55, 0.1],
    [100, 0.5],
    [999, 1],
    [1000, 5],
  ])("%s 元的升降單位是 %s", (p, t) => expect(tickSize(p)).toBe(t));
  it("取整", () => {
    expect(roundTick(123.3, "down")).toBe(123);
    expect(roundTick(123.3, "up")).toBe(123.5);
    expect(roundTick(1003, "down")).toBe(1000);
    expect(roundTick(47.33, "up")).toBe(47.35);
  });
});

describe("停損", () => {
  it("百分比與偏空收緊", () => {
    expect(stopPrice(100, 95, STOP)).toBe(92);
    expect(stopPrice(100, 95, STOP, true)).toBe(95);
    expect(stopPrice(200, null, STOP)).toBe(184);
    expect(stopPrice(170, null, STOP)).toBe(156);
  });
  it("季線法，季線太遠時改百分比", () => {
    const ma = { ...STOP, method: "ma60" };
    expect(stopPrice(100, 95, ma)).toBe(94);
    expect(stopPrice(100, 80, ma)).toBe(92);
  });
});

const t = (id: number, date: string, side: "buy" | "sell", price: number, shares: number, fee = 0, tax = 0, sid = "2330"): Trade => ({
  trade_id: id,
  stock_id: sid,
  date,
  side,
  price,
  shares,
  fee,
  tax,
});

describe("交易統計", () => {
  it("平均成本法", () => {
    const { closed, open } = closedTrades([
      t(1, "2026-01-05", "buy", 100, 1000, 142),
      t(2, "2026-01-06", "buy", 110, 1000, 156),
      t(3, "2026-02-05", "sell", 120, 1000, 171, 360),
      t(4, "2026-03-05", "sell", 90, 1000, 128, 270),
    ]);
    const avg = (100_000 + 142 + 110_000 + 156) / 2000;
    expect(closed[0].pnl).toBeCloseTo(120_000 - 171 - 360 - avg * 1000, 2);
    expect(closed[1].pnl).toBeCloseTo(90_000 - 128 - 270 - avg * 1000, 2);
    expect(closed[0].days).toBe(31);
    expect(open.size).toBe(0);
  });

  it("勝率、期望值、與大盤比較", () => {
    const st = tradeStats(
      [
        t(1, "2026-01-05", "buy", 100, 1000),
        t(2, "2026-02-05", "sell", 110, 1000),
        t(3, "2026-03-05", "buy", 100, 1000),
        t(4, "2026-04-05", "sell", 95, 1000),
        t(5, "2026-05-05", "buy", 50, 2000, 0, 0, "2317"),
      ],
      { "2317": 55 },
      1_000_000,
      "2026-11-06",
      { taiex: [["2026-01-02", 20000], ["2026-11-06", 22000]], etf_0050: [["2026-01-05", 80], ["2026-11-06", 92]] },
    );
    expect(st.closed).toBe(2);
    expect(st.winRate).toBe(0.5);
    expect(st.avgWinPct).toBeCloseTo(0.1);
    expect(st.avgLossPct).toBeCloseTo(-0.05);
    expect(st.expectancyPct).toBeCloseTo(0.025);
    expect(st.realized).toBe(5000);
    expect(st.unrealized).toBe(10000);
    expect(st.portfolioReturn).toBeCloseTo(0.015);
    expect(st.benchmarks.taiex).toBeCloseTo(0.1);
    expect(st.benchmarks.etf_0050).toBeCloseTo(0.15);
  });

  it("沒有交易", () => {
    const st = tradeStats([], {}, 1_000_000, "2026-11-06", { taiex: [] });
    expect(st.closed).toBe(0);
    expect(st.winRate).toBeNull();
    expect(st.start).toBeNull();
  });
});
