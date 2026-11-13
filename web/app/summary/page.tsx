import Link from "next/link";
import { DivBar } from "@/components/DivBar";
import { Updated } from "@/components/Updated";
import { Change, Panel, StockLink } from "@/components/ui";
import { dateLabel, num, pct, shortDate, timeTaipei, tone } from "@/lib/format";
import { dailySummary, latestDate, summaryDates, type DailySummary, type SummaryFacts } from "@/lib/queries";
import { requireSession } from "@/lib/session";

const TONE_LABEL: Record<string, string> = { neg: "偏空", pos: "偏多", info: "觀察" };
const TONE_CLASS: Record<string, string> = { neg: "down", pos: "up", info: "" };
const ENTRY_LABEL: Record<string, string> = { buy: "買進", setup: "醞釀", watch: "觀察" };
const LEVEL_TAG: Record<string, { text: string; cls: string }> = {
  sell: { text: "處理", cls: "block" },
  watch: { text: "留意", cls: "warn" },
  hold: { text: "續抱", cls: "" },
};
const BY_LABEL: Record<string, string> = { claude: "Claude 改寫", ollama: "Ollama 改寫", rules: "規則產生" };

function yi(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  const v = x / 1e8;
  return `${v > 0 ? "+" : ""}${v.toLocaleString("zh-TW", { maximumFractionDigits: 1, minimumFractionDigits: 1 })} 億`;
}

/** 盤中區間：最低到最高為實心區段，標出昨收、開盤、收盤與區間附近的均線。 */
function RangeTrack({ f }: { f: SummaryFacts }) {
  const ix = f.index;
  if (!ix?.high || !ix.low || ix.open === undefined) return null;
  const near = (f.ma ?? []).filter((m) => Math.abs(m.value / ix.close - 1) <= 0.04);
  const lo = Math.min(ix.low, ix.prev_close, ...near.map((m) => m.value));
  const hi = Math.max(ix.high, ix.prev_close, ...near.map((m) => m.value));
  const pad = (hi - lo) * 0.06;
  const min = lo - pad;
  const span = hi + pad - min;
  const at = (v: number) => `${((v - min) / span) * 100}%`;
  // 標籤避讓：依位置排序，距離同一層上一個標籤太近就往下一層放（上方兩層、均線固定在下方）
  const raw = [
    { key: "close", label: "收", value: ix.close },
    { key: "prev", label: "昨收", value: ix.prev_close },
    { key: "open", label: "開", value: ix.open },
  ];
  const lastAt: number[] = [];
  const marks = raw.map((m) => {
    const x = (m.value - min) / span;
    let row = lastAt.findIndex((p) => Math.abs(x - p) > 0.09);
    if (row < 0) row = lastAt.length;
    lastAt[row] = x;
    return { ...m, row: Math.min(row, 2) };
  });
  return (
    <div className="range">
      <div className="range-track">
        <span className={`range-body ${tone(ix.change)}`} style={{ left: at(ix.low), width: `calc(${at(ix.high)} - ${at(ix.low)})` }} />
        {near.map((m) => (
          <span key={m.days} className="range-ma" style={{ left: at(m.value) }} title={`${m.days} 日線 ${num(m.value)}`}>
            <i>{m.days}</i>
          </span>
        ))}
        {marks.map((m) => (
          <span key={m.key} className={`range-mark ${m.key} row-${m.row}`} style={{ left: at(m.value) }} title={`${m.label} ${num(m.value)}`}>
            <i>{m.label}</i>
          </span>
        ))}
      </div>
      <dl className="range-legend">
        <div><dt>最高</dt><dd className="num">{num(ix.high)}<small className={tone(ix.high - ix.prev_close)}>{pct(ix.high / ix.prev_close - 1, 2)}</small></dd></div>
        <div><dt>最低</dt><dd className="num">{num(ix.low)}<small className={tone(ix.low - ix.prev_close)}>{pct(ix.low / ix.prev_close - 1, 2)}</small></dd></div>
        <div><dt>開盤</dt><dd className="num">{num(ix.open)}<small className={tone(ix.gap_pct)}>{pct(ix.gap_pct, 2)}</small></dd></div>
        <div><dt>高低差</dt><dd className="num">{num(ix.range, 0)}<small>{pct(ix.range_pct, 2, false)}</small></dd></div>
        {near.map((m) => (
          <div key={m.days}>
            <dt>{m.days} 日線</dt>
            <dd className="num">
              {num(m.value)}
              <small className={m.above ? "up" : "down"}>{m.above ? "站上" : "跌破"}</small>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function Movers({ items, unit }: { items: { stock_id: string; name: string | null; value: number }[]; unit: "points" | "yi" }) {
  const max = Math.max(...items.map((x) => Math.abs(x.value)), 1);
  return (
    <ul className="movers">
      {items.map((x) => (
        <li key={x.stock_id}>
          <StockLink id={x.stock_id} name={x.name} />
          <DivBar value={x.value / max} max={1} />
          <span className={`num ${tone(x.value)}`}>
            {unit === "points" ? `${x.value > 0 ? "+" : ""}${num(x.value, 1)} 點` : yi(x.value)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Plan({ s }: { s: DailySummary }) {
  const p = s.plan;
  const lv = p.levels;
  return (
    <section className="plan" aria-label="隔日操作計畫">
      <div className={`posture posture-${p.posture.tone}`}>
        <div className="posture-label">隔日方針</div>
        <div className="posture-title">{p.posture.title}</div>
        <p>{p.posture.text}</p>
      </div>

      <div className="grid two">
        <Panel title="關鍵價位" icon="target" meta="加權指數">
          <div className="levels">
            <div>
              <h3 className="levels-head up">壓力</h3>
              {lv.resistance.length ? (
                lv.resistance.map((x) => (
                  <div key={x.label} className="level">
                    <span>{x.label}</span>
                    <span className="num">{num(x.value)}</span>
                    <span className="num faint">+{num(x.distance, 0)}</span>
                  </div>
                ))
              ) : (
                <p className="faint small">收盤在所有均線與今日高點之上</p>
              )}
            </div>
            <div>
              <h3 className="levels-head down">支撐</h3>
              {lv.support.length ? (
                lv.support.map((x) => (
                  <div key={x.label} className="level">
                    <span>{x.label}</span>
                    <span className="num">{num(x.value)}</span>
                    <span className="num faint">{num(x.distance, 0)}</span>
                  </div>
                ))
              ) : (
                <p className="faint small">收盤在所有均線與今日低點之下</p>
              )}
            </div>
          </div>
        </Panel>
        <Panel title="三種情境" icon="layers" meta="依收盤位置">
          <ul className="scenarios">
            {p.scenarios.map((c) => (
              <li key={c.case}>
                <span className={`scenario-case case-${c.case}`}>{c.case}</span>
                <div>
                  <div className="scenario-when">{c.when}</div>
                  <div className="muted small">{c.action}</div>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="gap" />
      <Panel title="持股處理" icon="portfolio" meta={`${p.holdings.length} 檔`}>
        {p.holdings.length ? (
          <ul className="actions">
            {p.holdings.map((h) => (
              <li key={h.stock_id} className={`action-${h.level}`}>
                <div className="action-head">
                  <StockLink id={h.stock_id} name={h.name} />
                  <span className={`tag ${LEVEL_TAG[h.level].cls}`}>{LEVEL_TAG[h.level].text}</span>
                </div>
                <div className="action-nums num">
                  <span>收盤 {num(h.close)}</span>
                  <Change value={h.gain} />
                  <span>停損 {num(h.stop_loss)}</span>
                </div>
                <div className="action-text">{h.action}</div>
                {h.reason && <div className="small faint">{h.reason}</div>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">沒有持股</p>
        )}
      </Panel>

      <div className="gap" />
      <Panel title="進場名單" icon="sprout" meta={p.entries.length ? `${p.entries.length} 檔` : undefined}>
        {p.entries.length ? (
          <ul className="actions">
            {p.entries.map((e) => (
              <li key={`${e.kind}-${e.stock_id}`}>
                <div className="action-head">
                  <StockLink id={e.stock_id} name={e.name} />
                  <span className={`tag ${e.kind === "buy" ? "block" : e.kind === "setup" ? "warn" : ""}`}>{ENTRY_LABEL[e.kind]}</span>
                </div>
                <div className="action-nums num">
                  <span>
                    買進 {num(e.entry_low)}–{num(e.entry_high)}
                  </span>
                  <span className="down">停損 {num(e.stop_loss)}</span>
                  <span className="up">目標 {num(e.take_profit)}</span>
                </div>
                <div className="action-text">{e.action}</div>
                {e.blocked_by?.length ? <div className="small faint">{e.blocked_by.join("、")}</div> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">沒有買進訊號、醞釀或觀察名單</p>
        )}
      </Panel>

      {(p.events.length > 0 || p.watch.length > 0) && (
        <>
          <div className="gap" />
          <div className="grid two">
            {p.watch.length > 0 && (
              <Panel title="觀察重點" icon="search">
                <ul className="bullets">
                  {p.watch.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </Panel>
            )}
            {p.events.length > 0 && (
              <Panel title="事件提醒" icon="calendar">
                <ul className="bullets">
                  {p.events.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </Panel>
            )}
          </div>
        </>
      )}
    </section>
  );
}

export default async function SummaryPage({ searchParams }: { searchParams: Promise<{ date?: string }> }) {
  await requireSession();
  const { date: requested } = await searchParams;
  const dates = await summaryDates();
  const date = requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) ? requested : (dates[0] ?? (await latestDate()));
  const s = date ? await dailySummary(date) : null;
  const i = date ? dates.indexOf(date) : -1;
  const older = i >= 0 ? dates[i + 1] : dates.find((d) => date && d < date);
  const newer = i > 0 ? dates[i - 1] : undefined;

  const nav = (
    <nav className="day-nav" aria-label="切換日期">
      {older ? <Link href={`/summary?date=${older}`}>‹ {shortDate(older)}</Link> : <span className="faint">‹</span>}
      {newer ? <Link href={`/summary?date=${newer}`}>{shortDate(newer)} ›</Link> : <span className="faint">›</span>}
    </nav>
  );

  if (!s || !date)
    return (
      <>
        <header className="page-head">
          <div>
            <h1>盤後總結</h1>
            <p className="sub">{date ? dateLabel(date) : "尚無資料"}</p>
          </div>
          {nav}
        </header>
        <p className="notice">這一天還沒有盤勢總結，每日收盤後排程跑完「盤勢總結與隔日操作計畫」步驟後會出現</p>
      </>
    );

  const f = s.facts;
  const ix = f.index;
  const fl = f.flows;
  const b = f.breadth;
  const t = f.turnover;
  const con = f.contributors;
  const on = f.overnight ?? {};
  const down = (ix?.change ?? 0) < 0;
  const reasons = s.reasons.filter((r) => r.key !== "news");

  return (
    <>
      <header className="page-head">
        <div>
          <h1>盤後總結</h1>
          <p className="sub">
            {dateLabel(date)}
            <span className="sep">｜</span>
            {BY_LABEL[s.generated_by ?? ""] ?? s.generated_by}
            <span className="sep">｜</span>
            <Updated at={s.updated_at} />
          </p>
        </div>
        {nav}
      </header>

      {ix && (
        <section className="kpis" aria-label="大盤摘要">
          <div className={`kpi tone-${tone(ix.change) || "flat"}`}>
            <div className="kpi-label">加權指數</div>
            <div className="kpi-value num">{num(ix.close, 0)}</div>
            <div className={`kpi-sub ${tone(ix.change)}`}>
              {ix.change > 0 ? "+" : ""}
              {num(ix.change, 2)}（{pct(ix.pct, 2)}）
            </div>
          </div>
          <div className="kpi">
            <div className="kpi-label">盤型</div>
            <div className="kpi-value">{ix.pattern_label}</div>
            <div className="kpi-sub">
              {[ix.intraday_flip && `盤中${ix.intraday_flip}`, ix.close_zone === "low" ? "收最低附近" : ix.close_zone === "high" ? "收最高附近" : null]
                .filter(Boolean)
                .join("，") || "—"}
            </div>
          </div>
          <div className="kpi">
            <div className="kpi-label">漲跌家數</div>
            <div className="kpi-value num">
              {b ? (
                <>
                  <span className="up">{b.advancers}</span>
                  <small> ／ </small>
                  <span className="down">{b.decliners}</span>
                </>
              ) : (
                "—"
              )}
            </div>
            <div className="kpi-sub">{b ? `漲停 ${b.limit_up}｜跌停 ${b.limit_down}｜平盤 ${b.unchanged}` : "資料不足"}</div>
          </div>
          <div className="kpi">
            <div className="kpi-label">{t?.source === "stocks" ? "上市普通股成交值" : "上市成交值"}</div>
            <div className="kpi-value num">
              {t ? num(t.value / 1e8, 0) : "—"}
              <small> 億</small>
            </div>
            <div className="kpi-sub">{t?.ratio ? `${t.avg_days} 日均量 ${num(t.ratio, 2)} 倍` : "—"}</div>
          </div>
        </section>
      )}

      <section className={`lead ${down ? "lead-down" : (ix?.change ?? 0) > 0 ? "lead-up" : ""}`}>
        <p>{s.narrative}</p>
      </section>

      <div className="grid wide-left">
        <Panel title="原因拆解" icon="list" meta="依影響程度排序">
          {reasons.length ? (
            <ol className="reasons">
              {reasons.map((r) => (
                <li key={r.key}>
                  <span className={`reason-chip ${TONE_CLASS[r.tone]}`}>{TONE_LABEL[r.tone]}</span>
                  <span>{r.text}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="empty">沒有歸納出明顯原因</p>
          )}
        </Panel>
        <Panel title="盤中區間" icon="chart" meta={ix ? `昨收 ${num(ix.prev_close)}` : undefined}>
          <RangeTrack f={f} />
          {f.prior_run && (
            <p className="panel-foot small muted">
              今天以前 {f.prior_run.days} 個交易日累計 <Change value={f.prior_run.pct} />（{f.prior_run.points > 0 ? "+" : ""}
              {num(f.prior_run.points, 0)} 點）
            </p>
          )}
        </Panel>
      </div>

      <div className="gap" />
      <div className="grid three">
        <Panel title="法人" icon="coins" meta={fl?.source === "estimated" ? "上市，以收盤價估算" : "上市，證交所公布"}>
          {fl ? (
            <>
              <dl className="kv panel-body">
                <dt>外資</dt>
                <dd className={tone(fl.foreign)}>
                  {yi(fl.foreign)}
                  {fl.foreign_streak >= 2 && <small className="faint"> 連 {fl.foreign_streak} {fl.foreign < 0 ? "賣" : "買"}</small>}
                </dd>
                <dt>投信</dt>
                <dd className={tone(fl.trust)}>
                  {yi(fl.trust)}
                  {fl.trust_streak >= 2 && <small className="faint"> 連 {fl.trust_streak} {fl.trust < 0 ? "賣" : "買"}</small>}
                </dd>
                <dt>自營商</dt>
                <dd className={tone(fl.dealer)}>{yi(fl.dealer)}</dd>
                <dt>合計</dt>
                <dd className={tone(fl.total)}>{yi(fl.total)}</dd>
              </dl>
              {f.foreign_top && (f.foreign_top.buy.length > 0 || f.foreign_top.sell.length > 0) && (
                <div className="split">
                  <div>
                    <h3 className="split-head">外資買超（個股估算）</h3>
                    <Movers items={f.foreign_top.buy} unit="yi" />
                  </div>
                  <div>
                    <h3 className="split-head">外資賣超（個股估算）</h3>
                    <Movers items={f.foreign_top.sell} unit="yi" />
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="empty">當日法人資料尚未完整</p>
          )}
        </Panel>

        <Panel title="權值股貢獻" icon="bars" meta={con ? `估算合計 ${con.estimated_change > 0 ? "+" : ""}${num(con.estimated_change, 0)} 點` : undefined}>
          {con && (con.drags.length || con.boosts.length) ? (
            <div className="split stack">
              {con.drags.length > 0 && (
                <div>
                  <h3 className="split-head">拖累</h3>
                  <Movers items={con.drags.map((x) => ({ ...x, value: x.points }))} unit="points" />
                </div>
              )}
              {con.boosts.length > 0 && (
                <div>
                  <h3 className="split-head">撐盤</h3>
                  <Movers items={con.boosts.map((x) => ({ ...x, value: x.points }))} unit="points" />
                </div>
              )}
            </div>
          ) : (
            <p className="empty">缺少全市場股數或行情</p>
          )}
        </Panel>

        <Panel title="族群與外部變數" icon="radar">
          {f.groups && (f.groups.strong.length || f.groups.weak.length) ? (
            <ul className="movers">
              {[...f.groups.strong, ...f.groups.weak].map((g) => (
                <li key={g.name}>
                  <Link href={`/groups/${encodeURIComponent(g.name)}`}>{g.name}</Link>
                  <DivBar value={g.pct} max={0.06} />
                  <Change value={g.pct} />
                </li>
              ))}
            </ul>
          ) : null}
          <dl className="kv panel-body bordered">
            {on.sox && (
              <>
                <dt>費城半導體</dt>
                <dd>
                  <Change value={on.sox.pct} /> <small className="faint">{shortDate(on.sox.date)}</small>
                </dd>
              </>
            )}
            {on.tsm_adr && (
              <>
                <dt>台積電 ADR</dt>
                <dd>
                  <Change value={on.tsm_adr.pct} /> <small className="faint">{shortDate(on.tsm_adr.date)}</small>
                </dd>
              </>
            )}
            {on.usd_twd && (
              <>
                <dt>美元兌台幣</dt>
                <dd>
                  {num(on.usd_twd.value, 3)}{" "}
                  <small className={on.usd_twd.change > 0 ? "down" : "up"}>{on.usd_twd.change > 0 ? "貶" : "升"} {num(Math.abs(on.usd_twd.change), 3)}</small>
                </dd>
              </>
            )}
            {on.foreign_tx_oi && (
              <>
                <dt>外資台指期</dt>
                <dd>
                  淨{on.foreign_tx_oi.value < 0 ? "空" : "多"} {num(Math.abs(on.foreign_tx_oi.value), 0)} 口{" "}
                  <small className={tone(on.foreign_tx_oi.change)}>
                    {on.foreign_tx_oi.change > 0 ? "+" : ""}
                    {num(on.foreign_tx_oi.change, 0)}
                  </small>
                </dd>
              </>
            )}
          </dl>
        </Panel>
      </div>

      {((f.news?.length ?? 0) > 0 || (f.news_after_close?.length ?? 0) > 0) && (
        <>
          <div className="gap" />
          <Panel title="盤勢與產業新聞" icon="bell" meta="Yahoo股市 RSS，依原出處標示">
            {[
              { key: "today", head: "今日（盤前、盤中、收盤報導）", items: f.news ?? [] },
              { key: "evening", head: "晚間消息（收盤後發生，列入隔日觀察）", items: f.news_after_close ?? [] },
            ]
              .filter((g) => g.items.length > 0)
              .map((g) => (
                <div key={g.key}>
                  <h3 className="split-head">{g.head}</h3>
                  <ul className="news">
                    {g.items.map((n) => (
                      <li key={n.title}>
                        {n.url ? (
                          <a href={n.url} target="_blank" rel="noreferrer">
                            {n.title}
                          </a>
                        ) : (
                          n.title
                        )}
                        {n.is_major_negative && <span className="tag block">利空</span>}
                        <div className="small faint">
                          {n.source}
                          <span className="sep">｜</span>
                          {timeTaipei(n.published_at)}
                          {n.topic && (
                            <>
                              <span className="sep">｜</span>
                              {n.topic}
                            </>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
          </Panel>
        </>
      )}

      <h2 className="section-title">隔日操作計畫</h2>
      <Plan s={s} />

      <p className="faint small" style={{ marginTop: 16 }}>
        權值股貢獻點數與個股外資買賣超以收盤價估算，與交易所公布值會有些微差距｜僅供參考，買賣請自行判斷
      </p>
    </>
  );
}
