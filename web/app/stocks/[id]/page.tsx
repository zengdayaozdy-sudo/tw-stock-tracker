import Link from "next/link";
import { notFound } from "next/navigation";
import { addEvent, deleteEvent } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { PriceChart, type Bar, type Marker } from "@/components/PriceChart";
import { FlagTags, Panel, ScoreBreakdown } from "@/components/ui";
import { HORIZON_LABELS, money, num, pct, timeTaipei, todayTaipei } from "@/lib/format";
import { flagsOn, latestDate, latestScores, priceHistory, stock, stockEvents, stockGroups, stockNews, stockSignals } from "@/lib/queries";
import { requireSession } from "@/lib/session";

const STATUS_LABELS: Record<string, string> = { disposition: "處置股", attention: "注意股", full_delivery: "全額交割" };
const MARKET_LABELS: Record<string, string> = { twse: "上市", tpex: "上櫃" };

export default async function StockPage({ params }: { params: Promise<{ id: string }> }) {
  await requireSession();
  const { id } = await params;
  // 基本資料與最新日期互不相依，一起查以減少資料庫往返
  const [s, date] = await Promise.all([stock(id.toUpperCase()), latestDate()]);
  if (!s) notFound();
  const [prices, signals, scores, news, groups, flags, events] = await Promise.all([
    priceHistory(s.stock_id),
    stockSignals(s.stock_id),
    latestScores(s.stock_id),
    stockNews(s.stock_id),
    stockGroups(s.stock_id),
    date ? flagsOn(date, [s.stock_id]) : ({} as Awaited<ReturnType<typeof flagsOn>>),
    stockEvents(s.stock_id, todayTaipei()),
  ]);
  const bars: Bar[] = [...prices].reverse().map((p) => ({
    time: p.date,
    open: p.open ?? p.close,
    high: p.high ?? p.close,
    low: p.low ?? p.close,
    close: p.close,
    volume: Number(p.volume ?? 0),
  }));
  const markers: Marker[] = signals.map((sg) => ({
    time: sg.date,
    side: sg.side as "buy" | "sell",
    text: sg.side === "buy" ? HORIZON_LABELS[sg.horizon] : "賣",
  }));
  const last = bars.at(-1);
  const prev = bars.at(-2);
  const change = last && prev ? last.close / prev.close - 1 : null;
  const latestBuy = signals.find((sg) => sg.side === "buy");
  scores.sort((a, b) => (a.horizon === "short" ? -1 : b.horizon === "short" ? 1 : 0));
  const eventWarnings = (flags[s.stock_id] ?? []).filter((f) => f.filter === "event");

  return (
    <>
      <h1>
        <span className="num">{s.stock_id}</span> {s.name}
      </h1>
      <p className="sub">
        {MARKET_LABELS[s.market] ?? s.market}｜{s.industry ?? "產業未分類"}
        {s.market_cap ? `｜市值 ${money(s.market_cap / 1e8)} 億` : ""}
        {(s.status as string[]).map((st) => (
          <span key={st} className="tag block" style={{ marginLeft: 6 }}>
            {STATUS_LABELS[st] ?? st}
          </span>
        ))}
      </p>

      <section className="panel" style={{ marginBottom: 14 }}>
        <div className="stats">
          <div className="stat">
            <div className="stat-label">收盤 {last?.time ?? ""}</div>
            <div className="stat-value">{num(last?.close)}</div>
          </div>
          <div className="stat">
            <div className="stat-label">漲跌</div>
            <div className={`stat-value ${change && change > 0 ? "up" : change && change < 0 ? "down" : ""}`}>{pct(change, 2)}</div>
          </div>
          {scores.map((sc) => (
            <div className="stat" key={sc.horizon}>
              <div className="stat-label">
                {HORIZON_LABELS[sc.horizon]}分數 {sc.date}
              </div>
              <div className="stat-value">{sc.status === "insufficient_data" ? "資料不足" : num(sc.total, 1)}</div>
            </div>
          ))}
        </div>
        {flags[s.stock_id]?.length ? (
          <div className="panel-body">
            <FlagTags flags={flags[s.stock_id]} />
          </div>
        ) : null}
      </section>

      <Panel
        title="K 線"
        meta={
          <>
            <a href={`https://statementdog.com/analysis/${s.stock_id}`} target="_blank" rel="noreferrer">財報狗</a>
            {"　"}
            <a href={`https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID=${s.stock_id}`} target="_blank" rel="noreferrer">Goodinfo</a>
          </>
        }
      >
        <PriceChart bars={bars} markers={markers} levels={{ stop: latestBuy?.stop_loss, target: latestBuy?.take_profit }} />
      </Panel>

      {latestBuy && (
        <>
          <div style={{ height: 14 }} />
          <Panel title="最近買進訊號參考價位" meta={`${latestBuy.date}｜${HORIZON_LABELS[latestBuy.horizon]}`}>
            <dl className="kv panel-body">
              <dt>買進區間</dt>
              <dd>
                {num(latestBuy.entry_low)}–{num(latestBuy.entry_high)}
                <span className="faint small">　{latestBuy.detail?.levels?.entry_basis}</span>
              </dd>
              <dt>停損</dt>
              <dd className="down">
                {num(latestBuy.stop_loss)}
                <span className="faint small">　{latestBuy.detail?.levels?.stop_basis}</span>
              </dd>
              <dt>停利</dt>
              <dd className="up">
                {num(latestBuy.take_profit)}
                <span className="faint small">　{latestBuy.detail?.levels?.target_basis}</span>
              </dd>
              <dt>建議張數</dt>
              <dd>
                {latestBuy.suggested_lots ?? 0} 張
                <span className="faint small">　受{latestBuy.detail?.sizing?.limited_by}限制</span>
              </dd>
              {latestBuy.detail?.levels?.pe_river && (
                <>
                  <dt>本益比河流圖</dt>
                  <dd>
                    偏低 {num(latestBuy.detail.levels.pe_river.low_price)}／偏高 {num(latestBuy.detail.levels.pe_river.high_price)}
                    <span className="faint small">　目前本益比 {num(latestBuy.detail.levels.pe_river.pe)}</span>
                  </dd>
                </>
              )}
              <dt>理由</dt>
              <dd style={{ fontFamily: "var(--sans)" }}>{latestBuy.reason}</dd>
            </dl>
          </Panel>
        </>
      )}

      {scores.map((sc) => (
        <div key={sc.horizon}>
          <div style={{ height: 14 }} />
          <Panel title={`${HORIZON_LABELS[sc.horizon]}評分拆解`} meta={`${sc.date}｜總分 ${num(sc.total, 1)}`}>
            <ScoreBreakdown score={sc} />
          </Panel>
        </div>
      ))}

      <div style={{ height: 14 }} />
      <div className="grid two">
        <Panel title="消息面排雷紀錄" meta={`${news.length} 則`}>
          {news.length ? (
            <div className="table-wrap">
              <table>
                <tbody>
                  {news.map((n) => (
                    <tr key={n.news_id}>
                      <td className="num small faint">{timeTaipei(n.published_at)}</td>
                      <td className="wrap">
                        {n.url ? (
                          <a href={n.url} target="_blank" rel="noreferrer">{n.title}</a>
                        ) : (
                          n.title
                        )}
                        <div className="small muted">
                          {n.is_major_negative === null ? (
                            "尚未判讀"
                          ) : n.is_major_negative ? (
                            <span className="tag block">
                              {n.event_type ?? "利空"} {pct(n.confidence, 0, false)}
                            </span>
                          ) : (
                            <span className="tag ok">非重大利空</span>
                          )}{" "}
                          {n.summary}
                          {n.judged_by ? `（${n.judged_by === "rules" ? "關鍵字" : n.judged_by}）` : ""}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty">沒有新聞或重大訊息</p>
          )}
        </Panel>

        <Panel title="事件行事曆">
          {eventWarnings.length > 0 && (
            <div className="panel-body tags" style={{ borderBottom: "1px solid var(--line)" }}>
              {eventWarnings.map((f) => (
                <span key={f.reason} className="tag warn">{f.reason}</span>
              ))}
            </div>
          )}
          {events.length ? (
            <table>
              <tbody>
                {events.map((e) => (
                  <tr key={e.event_id}>
                    <td className="num">{e.date}</td>
                    <td>{e.event_type}</td>
                    <td className="muted wrap">{e.note}</td>
                    <td>
                      {e.source === "manual" && (
                        <form action={deleteEvent}>
                          <input type="hidden" name="event_id" value={e.event_id} />
                          <input type="hidden" name="stock_id" value={s.stock_id} />
                          <button className="link" type="submit">刪除</button>
                        </form>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            !eventWarnings.length && <p className="empty">近期沒有已知事件</p>
          )}
          <div className="panel-body" style={{ borderTop: "1px solid var(--line)" }}>
            <ActionForm action={addEvent} submit="登錄事件">
              <input type="hidden" name="stock_id" value={s.stock_id} />
              <label className="field">
                日期
                <input type="date" name="date" required />
              </label>
              <label className="field">
                類型
                <select name="event_type" defaultValue="法說會">
                  {["法說會", "股東會", "除權息", "財報", "月營收", "其他"].map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                備註
                <input name="note" className="w-md" />
              </label>
            </ActionForm>
          </div>
          {groups.length > 0 && (
            <div className="panel-body small" style={{ borderTop: "1px solid var(--line)" }}>
              <span className="faint">所屬族群：</span>
              {groups.map((g) => (
                <Link key={g.group_id} href={`/groups/${encodeURIComponent(g.name)}`} style={{ marginRight: 10 }}>
                  {g.name}
                </Link>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
