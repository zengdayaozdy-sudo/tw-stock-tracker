import { ago, timeTaipei } from "@/lib/format";

/** 資料更新時間：顯示「11/09 20:21 更新」，滑過顯示距今多久。 */
export function Updated({
  at,
  label = "更新",
  empty = "尚未更新",
}: {
  at: Date | string | null | undefined;
  label?: string;
  empty?: string;
}) {
  if (!at) return <span className="updated none">{empty}</span>;
  const d = new Date(at);
  return (
    <time className="updated" dateTime={d.toISOString()} title={ago(d)}>
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6.2" />
        <path d="M8 4.6V8l2.3 1.4" />
      </svg>
      {timeTaipei(d)} {label}
    </time>
  );
}
