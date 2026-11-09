"use client";
import { useEffect, useRef, useState } from "react";
import { money, num, pct } from "@/lib/format";

type Kind = "money" | "num" | "pct";

function render(v: number, kind: Kind, digits: number, sign: boolean): string {
  if (kind === "money") return (sign && v > 0 ? "+" : "") + money(v);
  if (kind === "pct") return pct(v, digits, sign);
  return num(v, digits);
}

/** 數字由 0 滾動到目標值；伺服器端先輸出最終值，系統設定減少動態時不播放。 */
export function CountUp({
  value,
  kind = "num",
  digits = 0,
  sign = false,
  duration = 800,
}: {
  value: number | null | undefined;
  kind?: Kind;
  digits?: number;
  sign?: boolean;
  duration?: number;
}) {
  const [shown, setShown] = useState(value ?? 0);
  const raf = useRef(0);
  useEffect(() => {
    if (value === null || value === undefined) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(value);
      return;
    }
    const start = performance.now();
    const step = (t: number) => {
      const p = Math.min(1, (t - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setShown(value * eased);
      if (p < 1) raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf.current);
  }, [value, duration]);
  if (value === null || value === undefined) return <>—</>;
  return <>{render(shown, kind, digits, sign)}</>;
}
