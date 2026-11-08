import Link from "next/link";
import { BuySignalTable, Change, Panel, StockLink } from "@/components/ui";
import { LIGHT_LABELS, dateLabel, money, num, timeTaipei, tone } from "@/lib/format";
import { holdingsDetail, lastHeartbeat, latestDate, latestJob, market, signalsOn } from "@/lib/queries";
import { requireSession } from "@/lib/session";
import { goToStock } from "./actions";

export default async function Dashboard({ searchParams }: { searchParams: Promise<{ date?: string }> }) {
  await requireSession();
  const { date: requested } = await searchParams;
  const date = requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) ? requested : await latestDate();
  if (!date)
    return (
      <>
        <h1>總覽</h1>
        <p className="notice">資料庫還沒有評分資料，請先執行每日排程</p>
      </>
    );

  const [m, buys, sells, holdings, fetchJob, signalJob, notifyJob, heartbeat] = await Promise.all([
    market(date),
    signalsOn(date, "buy"),
    signalsOn(date, "sell"),
    holdingsDetail(date),
    latestJob("fetch", date),
    latestJob("signals", date),
    latestJob("notify", date),
    lastHeartbeat("monitor_heartbeat"),
  ]);
  const ranked = (m?.groups ?? []).filter((g) => g.rank);
  const blocked: { stock_id: string; name?: string; reasons: string[]; triggers: string[] }[] = signalJob?.detail?.blocked ?? [];
  const value = holdings.reduce((a, h) => a + (h.close ?? h.avg_cost) * h.shares, 0);
  const cost = holdings.reduce((a, h) => a + h.avg_cost * h.shares, 0);
  const quality = fetchJob?.detail?.quality;

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h1>{dateLabel(date)}</h1>
          <p className="sub">收盤後評分與訊號</p>
        </div>
        <form action={goToStock} className="inline" style={{ marginBottom: 16 }}>
          <input name="q" placeholder="股票代號" className="w-md" aria-label="查詢股票" />
        </form>
      </div>

      <div className="grid wide-left">
        <Panel title="大盤燈號" meta={m ? `合計 ${m.total! > 0 ? "+" : ""}${m.total}` : undefined}>
          {m ? (
            <div className="panel-body">
              <div className={`light light-${m.light}`}>
                <span className="light-dot" />
                <span className="light-word">{LIGHT_LABELS[m.light]}</span>
                {m.light === "bearish" && <span className="small muted">不發新買進訊號、停損收緊、建議降低部位</span>}
              </div>
              <table style={{ marginTop: 10 }}>
                <tbody>
                  {m.inputs.map((i) => (
                    <tr key={i.key}>
                      <td className={i.vote > 0 ? "up" : i.vote < 0 ? "down" : "faint"}>
                        <span className="vote">{i.vote > 0 ? `+${i.vote}` : i.vote}</span>
                      </td>
                      <td>{i.label}</td>
                      <td className="text muted">{i.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty">今日大盤燈號尚未計算</p>
          )}
        </Panel>

        <Panel title="系統狀態" meta={<Link href="/status">詳細</Link>}>
          <dl className="kv panel-body">
            <dt>資料抓取</dt>
            <dd>
              {!fetchJob ? (
                <span className="tag warn">尚未執行</span>
              ) : fetchJob.status === "skipped" ? (
                <span className="tag">休市</span>
              ) : quality?.ok === false ? (
                <span className="tag block">完整性未通過</span>
              ) : fetchJob.status === "success" ? (
                <span className="tag ok">完整</span>
              ) : (
                <span className="tag block">{fetchJob.status}</span>
              )}
            </dd>
            <dt>買進訊號</dt>
            <dd>
              {signalJob?.status === "skipped" ? (
                <span className="tag block">暫停：{signalJob.detail?.reason}</span>
              ) : signalJob ? (
                `${buys.length} 筆`
              ) : (
                <span className="tag warn">尚未執行</span>
              )}
            </dd>
            <dt>LINE 報告</dt>
            <dd>{notifyJob?.detail?.report_sent ? "已送出" : notifyJob ? "未送出" : "尚未執行"}</dd>
            <dt>盤中監控</dt>
            <dd>{heartbeat ? `最後心跳 ${timeTaipei(heartbeat)}` : "尚未啟動"}</dd>
          </dl>
        </Panel>
      </div>

      <div style={{ height: 14 }} />
      <Panel title="今日買進訊號" meta={`${buys.length} 筆`}>
        {buys.length === 0 && m?.light === "bearish" ? (
          <p className="empty">大盤燈號偏空，今日不發新買進訊號</p>
        ) : (
          <BuySignalTable signals={buys} />
        )}
        {blocked.length > 0 && (
          <div className="panel-body small" style={{ borderTop: "1px solid var(--line)" }}>
            <span className="faint">觸發進場條件但未發訊號：</span>
            {blocked.map((b) => (
              <span key={b.stock_id} style={{ marginRight: 12 }}>
                <StockLink id={b.stock_id} name={b.name} /> <span className="muted">{b.reasons.join("、")}</span>
              </span>
            ))}
          </div>
        )}
      </Panel>

      <div style={{ height: 14 }} />
      <div className="grid two">
        <Panel title="族群強弱" meta={<>相對大盤報酬｜<Link href="/groups">全部族群</Link></>}>
          {ranked.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="n">#</th>
                    <th>族群</th>
                    <th className="n">平均分</th>
                    <th className="n">1 個月</th>
                    <th className="n">3 個月</th>
                  </tr>
                </thead>
                <tbody>
                  {ranked.slice(0, 10).map((g) => (
                    <tr key={g.name}>
                      <td className="n">{g.rank}</td>
                      <td>
                        <Link href={`/groups/${encodeURIComponent(g.name)}`}>{g.name}</Link>{" "}
                        {g.weak && <span className="tag block">弱勢</span>}
                      </td>
                      <td className="n">{num(g.avg_score, 0)}</td>
                      <td className="n">
                        <Change value={g.rel_return_20 ?? g.return_20} />
                      </td>
                      <td className="n">
                        <Change value={g.rel_return_60 ?? g.return_60} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty">尚未計算族群強弱</p>
          )}
        </Panel>

        <Panel
          title="持股"
          meta={
            holdings.length ? (
              <>
                市值 {money(value)}｜未實現 <span className={tone(value - cost)}>{money(value - cost)}</span>
              </>
            ) : undefined
          }
        >
          {holdings.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>股票</th>
                    <th className="n">現價</th>
                    <th className="n">損益</th>
                    <th className="n">停損</th>
                    <th>提醒</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((h) => {
                    const sell = sells.find((s) => s.stock_id === h.stock_id);
                    return (
                      <tr key={h.stock_id}>
                        <td>
                          <StockLink id={h.stock_id} name={h.name} />
                        </td>
                        <td className="n">{num(h.close)}</td>
                        <td className="n">
                          <Change value={h.close ? h.close / h.avg_cost - 1 : null} />
                        </td>
                        <td className="n">{num(h.stop_loss)}</td>
                        <td>
                          {sell ? (
                            <span className="tag block" title={sell.reason}>
                              {sell.reason.split("：")[0]}
                            </span>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty">
              沒有持股。<Link href="/portfolio">登錄交易</Link>
            </p>
          )}
        </Panel>
      </div>
      <p className="faint small" style={{ marginTop: 16 }}>
        資料日期 {date}｜僅供參考，買賣請自行判斷
      </p>
    </>
  );
}
