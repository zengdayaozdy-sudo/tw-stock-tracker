"use client";
// K 線圖：日 K、5／20／60 日均線、成交量（下方窗格）、買賣訊號標記。台股慣例紅漲綠跌。
import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";

export type Bar = { time: string; open: number; high: number; low: number; close: number; volume: number };
export type Marker = { time: string; side: "buy" | "sell"; text: string };

function css(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function movingAverage(bars: Bar[], n: number) {
  const out: { time: Time; value: number }[] = [];
  let sum = 0;
  bars.forEach((b, i) => {
    sum += b.close;
    if (i >= n) sum -= bars[i - n].close;
    if (i >= n - 1) out.push({ time: b.time as Time, value: Math.round((sum / n) * 100) / 100 });
  });
  return out;
}

export function PriceChart({ bars, markers, levels }: { bars: Bar[]; markers: Marker[]; levels?: { stop?: number | null; target?: number | null } }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || !bars.length) return;
    const up = css("--up", "#c8102e");
    const down = css("--down", "#0b7a3e");
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted", "#6b6e73"), fontSize: 11 },
      grid: { vertLines: { color: css("--line", "#e4e2dc") }, horzLines: { color: css("--line", "#e4e2dc") } },
      rightPriceScale: { borderColor: css("--line-strong", "#cfccc4") },
      timeScale: { borderColor: css("--line-strong", "#cfccc4") },
      localization: { locale: "zh-TW" },
      // 滾輪與上下滑動交給頁面捲動；拖曳與雙指縮放仍可操作圖表
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
    });
    candles.setData(bars.map((b) => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close })));

    const maColors: [number, string][] = [
      [5, "#d48a00"],
      [20, "#2f6fb0"],
      [60, "#8a4fbf"],
    ];
    for (const [n, color] of maColors) {
      const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(movingAverage(bars, n));
    }

    if (levels?.stop) candles.createPriceLine({ price: levels.stop, color: down, lineStyle: 2, lineWidth: 1, title: "停損" });
    if (levels?.target) candles.createPriceLine({ price: levels.target, color: up, lineStyle: 2, lineWidth: 1, title: "停利" });

    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false }, 1);
    volume.setData(
      bars.map((b, i) => ({
        time: b.time as Time,
        value: b.volume / 1000,
        color: i > 0 && b.close < bars[i - 1].close ? `${down}88` : `${up}88`,
      })),
    );
    chart.panes()[1]?.setStretchFactor(0.25);

    const seriesMarkers: SeriesMarker<Time>[] = markers
      .filter((m) => bars.some((b) => b.time === m.time))
      .sort((a, b) => (a.time < b.time ? -1 : 1))
      .map((m) => ({
        time: m.time as Time,
        position: m.side === "buy" ? "belowBar" : "aboveBar",
        shape: m.side === "buy" ? "arrowUp" : "arrowDown",
        color: m.side === "buy" ? up : down,
        text: m.text,
      }));
    createSeriesMarkers(candles, seriesMarkers);

    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 130), to: bars.length + 2 });
    return () => chart.remove();
  }, [bars, markers, levels?.stop, levels?.target]);

  if (!bars.length) return <p className="empty">沒有股價資料</p>;
  return (
    <div>
      <div ref={ref} className="chart" />
      <p className="small faint" style={{ margin: "6px 14px 10px" }}>
        <span style={{ color: "#d48a00" }}>━ 5 日</span>　<span style={{ color: "#2f6fb0" }}>━ 20 日</span>
        <span style={{ color: "#8a4fbf" }}>━ 60 日</span>　▲ 買進訊號　▼ 賣出提醒　成交量單位：張
      </p>
    </div>
  );
}
