import { saveCapital, saveNotify } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { Panel } from "@/components/ui";
import { money, pct, timeTaipei } from "@/lib/format";
import { runtimeConfig, setting, settingsUpdated } from "@/lib/queries";
import { requireSession } from "@/lib/session";

export default async function SettingsPage() {
  await requireSession();
  const [{ config, synced }, capital, notify, setUpd] = await Promise.all([
    runtimeConfig(),
    setting<number>("total_capital", 1_000_000),
    setting<Record<string, boolean>>("notify", {}),
    settingsUpdated(["total_capital", "notify"]),
  ]);
  const modified = (k: string) => (setUpd[k] ? `${timeTaipei(setUpd[k])} 修改` : "尚未修改");
  const prefs = { daily_report: true, intraday_stop_loss: true, system_alerts: true, ...notify };
  const pos = config.risk.position;

  return (
    <>
      <h1>設定</h1>
      <p className="sub">總資金影響建議張數；方案模式由環境變數 PLAN 決定</p>

      <div className="grid two">
        <Panel title="總資金" meta={modified("total_capital")}>
          <div className="panel-body">
            <ActionForm action={saveCapital} submit="儲存">
              <label className="field">
                新台幣（元）
                <input name="capital" type="number" min="1" step="1" defaultValue={Number(capital)} style={{ width: 180 }} required />
              </label>
            </ActionForm>
            <p className="small muted">
              目前 {money(Number(capital))} 元。單筆最大虧損 {pct(pos.risk_per_trade, 1, false)}、單檔上限 {pct(pos.max_position_pct, 0, false)}、
              單一族群上限 {pct(pos.max_group_pct, 0, false)}、最多 {pos.max_holdings} 檔（config/risk.yaml）
            </p>
          </div>
        </Panel>

        <Panel title="LINE 通知" meta={modified("notify")}>
          <div className="panel-body">
            <ActionForm action={saveNotify} submit="儲存" className="">
              {(
                [
                  ["daily_report", "每日報告（收盤後）"],
                  ["intraday_stop_loss", "盤中停損提醒"],
                  ["system_alerts", "系統警告"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                  <input type="checkbox" name={key} defaultChecked={prefs[key]} />
                  {label}
                </label>
              ))}
            </ActionForm>
          </div>
        </Panel>
      </div>

      <div style={{ height: 14 }} />
      <Panel
        title={`方案：${config.plan_label}`}
        meta={synced ? `設定同步於 ${timeTaipei(config.updated_at)}` : "尚未同步（每日排程執行後更新）"}
      >
        {Object.keys(config.features).length ? (
          <table>
            <tbody>
              {Object.entries(config.features).map(([key, f]) => (
                <tr key={key}>
                  <td>{f.enabled ? f.label : <span className="lock">{f.label}</span>}</td>
                  <td>{f.enabled ? <span className="tag ok">已啟用</span> : <span className="faint small">升級 FinMind 方案後可用</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">每日排程同步設定後顯示各功能狀態</p>
        )}
        <p className="panel-body small muted">
          升級方式：在 GitHub 專案 Settings → Secrets and variables → Actions → Variables 把 PLAN 改成 sponsor 或 pro，不需要改程式
        </p>
      </Panel>
    </>
  );
}
