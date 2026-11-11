import Link from "next/link";
import { CountUp } from "@/components/CountUp";
import { DivBar } from "@/components/DivBar";
import { Updated } from "@/components/Updated";
import { BuySignalTable, Change, Panel, StockLink } from "@/components/ui";
import { LIGHT_LABELS, dateLabel, money, num, reboundEvidence, timeTaipei, tone } from "@/lib/format";
import { dataUpdates, holdingsDetail, lastHeartbeat, latestDate, latestJob, market, signalsOn } from "@/lib/queries";
import { requireSession } from "@/lib/session";
import { goToStock } from "./actions";

function latest(...dates: (Date | undefined)[]): Date | undefined {
  return dates.filter((d): d is Date => !!d).sort((a, b) => +new Date(b) - +new Date(a))[0];
}

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

  const [m, buys, sells, holdings, fetchJob, signalJob, notifyJob, heartbeat, upd] = await Promise.all([
    market(date),
    signalsOn(date, "buy"),
    signalsOn(date, "sell"),
    holdingsDetail(date),
    latestJob("fetch", date),
    latestJob("signals", date),
    latestJob("notify", date),
    lastHeartbeat("monitor_heartbeat"),
    dataUpdates(),
  ]);
  const ranked = (m?.groups ?? []).filter((g) => g.rank);
  const blocked: { stock_id: string; name?: string; reasons: string[]; triggers: string[] }[] = signalJob?.detail?.blocked ?? [];
  const value = holdings.reduce((a, h) => a + (h.close ?? h.avg_cost) * h.shares, 0);
  const cost = holdings.reduce((a, h) => a + h.avg_cost * h.shares, 0);
  const quality = fetchJob?.detail?.quality;
  const rb = m?.rebound;
  const rebounding = rb?.state === "attempt" || rb?.state === "confirmed";
  const regime: string | undefined = signalJob?.detail?.regime;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{dateLabel(date)}</h1>
          <p className="sub">
            收盤後評分與訊號<span className="sep">｜</span>
            <Updated at={latest(upd.signals, upd.score, upd["notify-evening"])} />
          </p>
        </div>
        <form action={goToStock} className="quick-search">
          <svg viewBox="0 0 20 20" className="search-icon" aria-hidden="true">
            <path d="M8.5 3a5.5 5.5 0 0 1 4.38 8.83l3.65 3.64-1.06 1.06-3.64-3.65A5.5 5.5 0 1 1 8.5 3Zm0 1.5a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z" />
          </svg>
          <input name="q" placeholder="代號或名稱" aria-label="查詢股票" />
        </form>
      </header>

      <section className="kpis" aria-label="今日摘要">
        <div className={`kpi kpi-light light-${m?.light ?? "none"}`}>
          <div className="kpi-label">大盤燈號</div>
          <div className="kpi-value light">
            <span className="light-dot" />
            {m ? LIGHT_LABELS[m.light] : "—"}
          </div>
          <div className="kpi-sub">{m ? `合計 ${m.total! > 0 ? "+" : ""}${m.total}` : "尚未計算"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">今日買進訊號</div>
          <div className="kpi-value num">
            <CountUp value={buys.length} />
            <small> 筆</small>
          </div>
          <div className="kpi-sub">
            {signalJob?.status === "skipped" ? `暫停：${signalJob.detail?.reason ?? ""}` : `${blocked.length} 檔觸發但被濾網擋下`}
          </div>
        </div>
        <div className="kpi">
          <div className="kpi-label">持股市值</div>
          <div className="kpi-value num">
            <CountUp value={holdings.length ? value : null} kind="money" />
          </div>
          <div className="kpi-sub">{holdings.length} 檔持股</div>
        </div>
        <div className={`kpi tone-${tone(value - cost) || "flat"}`}>
          <div className="kpi-label">未實現損益</div>
          <div className={`kpi-value num ${tone(value - cost)}`}>
            <CountUp value={holdings.length ? value - cost : null} kind="money" sign />
          </div>
          <div className="kpi-sub">
            {holdings.length ? <Change value={cost ? value / cost - 1 : null} digits={2} /> : "沒有持股"}
          </div>
        </div>
      </section>

      <div className="grid wide-left">
        <Panel title="大盤燈號依據" meta={<Updated at={upd.filters} />}>
          {m ? (
            <ul className="votes">
              {m.inputs.map((i) => (
                <li key={i.key}>
                  <span className={`vote-chip ${i.vote > 0 ? "up" : i.vote < 0 ? "down" : ""}`}>
                    {i.vote > 0 ? `+${i.vote}` : i.vote}
                  </span>
                  <span className="vote-label">{i.label}</span>
                  <span className="vote-reason">{i.reason}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty">今日大盤燈號尚未計算</p>
          )}
          {rb && (
            <ul className="votes rebound">
              <li>
                <span className={`vote-chip ${rebounding ? "up" : ""}`}>{rebounding ? `+${rb.points}` : "0"}</span>
                <span className="vote-label">反彈偵測</span>
                <span className="vote-reason">
                  <span className={`tag ${rb.state === "confirmed" ? "block" : rb.state === "attempt" ? "warn" : ""}`}>
                    {rb.label}
                  </span>{" "}
                  {rb.rally?.reason}
                  {reboundEvidence(rb.evidence).map((t) => (
                    <span key={t} className="rebound-evidence">
                      {t}
                    </span>
                  ))}
                </span>
              </li>
            </ul>
          )}
          {m?.light === "bearish" &&
            (regime === "rebound_confirmed" || regime === "rebound_attempt" ? (
              <p className="panel-note">
                偏空但{rb?.label}：買進訊號以{regime === "rebound_confirmed" ? "反彈單" : "反彈試單"}處理，部位縮小、停損收緊
              </p>
            ) : (
              <p className="panel-note">偏空：不發新買進訊號、停損收緊、建議降低部位</p>
            ))}
        </Panel>

        <Panel title="系統狀態" meta={<Link href="/status">排程紀錄</Link>}>
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
              <Updated at={upd.fetch} empty="" />
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
              <Updated at={upd.signals} empty="" />
            </dd>
            <dt>消息面排雷</dt>
            <dd className="flush">
              <Updated at={upd.news} empty="尚未執行" label="判讀" />
            </dd>
            <dt>晚間補抓</dt>
            <dd className="flush">
              <Updated at={upd["notify-evening"]} empty="今晚 21 點後" label="完成" />
            </dd>
            <dt>LINE 報告</dt>
            <dd>
              {notifyJob?.detail?.report_sent ? "已送出" : notifyJob ? "未送出" : "尚未執行"}
              <Updated at={notifyJob?.finished_at} empty="" label="" />
            </dd>
            <dt>盤中監控</dt>
            <dd>{heartbeat ? `最後心跳 ${timeTaipei(heartbeat)}` : "尚未啟動"}</dd>
          </dl>
        </Panel>
      </div>

      <div className="gap" />
      <Panel title="今日買進訊號" meta={<>{buys.length} 筆<span className="sep">｜</span><Updated at={upd.signals} /></>}>
        {buys.length === 0 && m?.light === "bearish" && !rebounding ? (
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

      <div className="gap" />
      <div className="grid two">
        <Panel title="族群強弱" meta={<><Updated at={upd.filters} /><span className="sep">｜</span><Link href="/groups">全部族群</Link></>}>
          {ranked.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="n">#</th>
                    <th>族群</th>
                    <th className="n">平均分</th>
                    <th>1 個月相對大盤</th>
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
                      <td>
                        <span className="rel">
                          <DivBar value={g.rel_return_20 ?? g.return_20} />
                          <span className="num small">
                            <Change value={g.rel_return_20 ?? g.return_20} />
                          </span>
                        </span>
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
                <Updated at={upd.holdings} label="檢查" /><span className="sep">｜</span>未實現{" "}
                <span className={tone(value - cost)}>{money(value - cost)}</span>
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
