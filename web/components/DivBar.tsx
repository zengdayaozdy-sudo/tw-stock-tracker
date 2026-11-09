// 以 0 為中心的漲跌長條：正值往右（紅）、負值往左（綠）
export function DivBar({ value, max = 0.1 }: { value: number | null | undefined; max?: number }) {
  if (value === null || value === undefined) return <span className="divbar" />;
  const w = Math.min(Math.abs(value) / max, 1) * 50;
  return (
    <span className="divbar" aria-hidden="true">
      <span className={value >= 0 ? "pos" : "neg"} style={{ width: `${w}%` }} />
    </span>
  );
}
