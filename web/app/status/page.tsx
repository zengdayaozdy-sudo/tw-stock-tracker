import { Panel } from "@/components/ui";
import { timeTaipei, todayTaipei } from "@/lib/format";
import { apiUsage, jobsRecent, lastHeartbeat, latestJobAny, runtimeConfig } from "@/lib/queries";
import { requireSession } from "@/lib/session";

const JOB_LABELS: Record<string, string> = {
  fetch: "資料抓取", liquidity: "流動性", news: "消息面", score: "評分", filters: "濾網", signals: "買進訊號",
  holdings: "持股檢查", notify: "LINE 報告", alert: "系統警告", backfill: "歷史回補", intraday_alert: "盤中停損提醒",
};
const STATUS_TAG: Record<string, string> = { success: "ok", failed: "block", skipped: "", running: "warn" };
const STATUS_LABELS: Record<string, string> = { success: "成功", failed: "失敗", skipped: "略過", running: "執行中" };

function summary(job: string, d: Record<string, any>): string { // eslint-disable-line @typescript-eslint/no-explicit-any
  if (d.error) return String(d.error).slice(0, 120);
  switch (job) {
    case "fetch":
      return d.counts ? `股價 ${d.counts.prices ?? 0}、財報 ${d.counts.financials ?? 0}、FinMind ${d.finmind_calls ?? 0} 次` : "";
    case "score":
      return `評分 ${d.scored ?? 0} 檔，資料不足 ${d.insufficient ?? 0}`;
    case "signals":
      return d.status === "halted" || d.status === "closed" ? String(d.reason) : `候選 ${d.candidates ?? 0}，訊號 ${d.signals ?? 0}`;
    case "filters":
      return d.light_label ? `燈號 ${d.light_label}，弱勢族群 ${(d.weak_groups ?? []).join("、") || "無"}` : "";
    case "news":
      return `收集 ${d.collected ?? 0}、判讀 ${d.judged ?? 0}、排除 ${d.excluded ?? 0}`;
    case "notify":
      return d.report_sent ? "報告已送出" : d.report_skipped ?? (d.line_configured === false ? "未設定 LINE" : "");
    default:
      return d.reason ? String(d.reason) : "";
  }
}

export default async function StatusPage() {
  await requireSession();
  const today = todayTaipei();
  const [jobs, usage, heartbeat, fetchToday, { config }] = await Promise.all([
    jobsRecent(14),
    apiUsage("finmind", 48),
    lastHeartbeat("monitor_heartbeat"),
    latestJobAny("fetch"),
    runtimeConfig(),
  ]);
  const quality = fetchToday?.detail?.quality;
  const budget = config.finmind_hourly_budget;

  return (
    <>
      <h1>系統狀態</h1>
      <p className="sub">最近 14 天排程紀錄、資料完整性、FinMind 額度用量</p>

      <div className="grid two">
        <Panel icon="checkCircle" title="最近一次資料完整性" meta={fetchToday?.run_date ?? today}>
          <div className="panel-body">
            {!fetchToday ? (
              <span className="tag warn">尚未抓取</span>
            ) : fetchToday.status === "skipped" ? (
              <span className="tag">休市</span>
            ) : quality ? (
              <>
                <span className={`tag ${quality.ok ? "ok" : "block"}`}>{quality.ok ? "通過" : "未通過，已暫停訊號"}</span>
                <ul className="small">
                  {(quality.errors ?? []).map((e: string) => <li key={e} className="up">{e}</li>)}
                  {(quality.warnings ?? []).slice(0, 15).map((w: string) => <li key={w} className="muted">{w}</li>)}
                </ul>
              </>
            ) : (
              <span className="tag">{STATUS_LABELS[fetchToday.status] ?? fetchToday.status}</span>
            )}
          </div>
        </Panel>

        <Panel icon="radar" title="盤中停損監控">
          <div className="panel-body">
            {heartbeat ? <>最後心跳 <span className="num">{timeTaipei(heartbeat)}</span></> : "尚未啟動（在 Mac 上執行 deploy/launchd/install.sh）"}
          </div>
        </Panel>
      </div>

      <div style={{ height: 14 }} />
      <Panel icon="database" title="FinMind 額度" meta={`每小時上限 ${budget} 次（已保留安全餘裕）`}>
        {usage.length ? (
          <table>
            <tbody>
              {usage.slice(0, 12).map((u) => (
                <tr key={String(u.hour)}>
                  <td className="num">{timeTaipei(u.hour)}</td>
                  <td>
                    <span className="bar" style={{ width: 160 }}>
                      <span style={{ width: `${Math.min(100, (u.calls / budget) * 100)}%`, background: u.calls >= budget ? "var(--up)" : undefined }} />
                    </span>
                  </td>
                  <td className="n">{u.calls} 次</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">最近 48 小時沒有使用</p>
        )}
      </Panel>

      <div style={{ height: 14 }} />
      <Panel icon="list" title="排程紀錄" meta={`${jobs.length} 筆`}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>開始</th>
                <th>步驟</th>
                <th>資料日</th>
                <th>結果</th>
                <th className="n">耗時</th>
                <th>摘要</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => {
                const secs = j.finished_at ? (new Date(j.finished_at).getTime() - new Date(j.started_at).getTime()) / 1000 : null;
                return (
                  <tr key={j.log_id}>
                    <td className="num small">{timeTaipei(j.started_at)}</td>
                    <td>{JOB_LABELS[j.job] ?? j.job}</td>
                    <td className="num small">{j.run_date ?? "—"}</td>
                    <td><span className={`tag ${STATUS_TAG[j.status] ?? ""}`}>{STATUS_LABELS[j.status] ?? j.status}</span></td>
                    <td className="n">{secs === null ? "—" : secs < 60 ? `${secs.toFixed(0)} 秒` : `${(secs / 60).toFixed(1)} 分`}</td>
                    <td className="wrap small muted">{summary(j.job, j.detail ?? {})}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
