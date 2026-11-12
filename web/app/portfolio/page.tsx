import { recordTrade, updateHoldingLevels } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { Change, Panel, StockLink } from "@/components/ui";
import { Updated } from "@/components/Updated";
import { HORIZON_LABELS, money, num, pct, shortDate, timeTaipei, todayTaipei, tone } from "@/lib/format";
import { dataUpdates, holdingsDetail, lastCloses, latestDate, names, priceAdjustments, seriesBetween, signalsOn, totalCapital, trades as loadTrades } from "@/lib/queries";
import { requireSession } from "@/lib/session";
import { adjustPoints, tradeStats, type Trade } from "@/lib/trading";

const BENCH: Record<string, string> = { taiex: "加權指數", etf_0050: "0050（含息）" };

export default async function PortfolioPage() {
  await requireSession();
  const today = todayTaipei();
  const date = (await latestDate()) ?? today;
  const [holdings, rows, capital, sells, upd] = await Promise.all([
    holdingsDetail(date),
    loadTrades(),
    totalCapital(),
    signalsOn(date, "sell"),
    dataUpdates(),
  ]);
  const trades: Trade[] = rows.map((r) => ({
    trade_id: Number(r.trade_id),
    stock_id: r.stock_id,
    date: r.date,
    side: r.side,
    price: r.price,
    shares: r.shares,
    fee: r.fee,
    tax: r.tax,
  }));
  const ids = [...new Set(trades.map((t) => t.stock_id))];
  const start = trades.length ? trades.map((t) => t.date).sort()[0] : today;
  const [closes, tradeNames, taiex, etf, etfAdj] = await Promise.all([
    lastCloses(ids, today),
    names(ids),
    seriesBetween("taiex", start, today),
    seriesBetween("etf_0050", start, today),
    priceAdjustments("0050"),
  ]);
  const st = tradeStats(trades, closes, Number(capital), today, { taiex, etf_0050: adjustPoints(etf, etfAdj) });
  const value = holdings.reduce((a, h) => a + (h.close ?? h.avg_cost) * h.shares, 0);

  return (
    <>
      <h1>持股與交易日誌</h1>
      <p className="sub">
        總資金 {money(Number(capital))} 元<span className="faint">（持股成本合計）</span>
        ｜持股市值 {money(value)} 元（{pct(capital ? value / Number(capital) : null, 1, false)}）
        <span className="sep">｜</span>
        現價為 {shortDate(date)} 收盤
      </p>

      <Panel icon="pen" title="登錄交易" meta="手續費、證交稅留空會自動計算；會自動記錄當時的分數與訊號">
        <div className="panel-body">
          <ActionForm action={recordTrade} submit="登錄">
            <label className="field">
              日期
              <input type="date" name="date" defaultValue={today} required />
            </label>
            <label className="field">
              買賣
              <select name="side" defaultValue="buy">
                <option value="buy">買進</option>
                <option value="sell">賣出</option>
              </select>
            </label>
            <label className="field">
              股票代號
              <input name="stock_id" className="w-sm" required />
            </label>
            <label className="field">
              成交價
              <input name="price" type="number" step="0.01" min="0" className="w-sm" required />
            </label>
            <label className="field">
              數量
              <input name="quantity" type="number" step="1" min="1" className="w-sm" required />
            </label>
            <label className="field">
              單位
              <select name="unit" defaultValue="lots">
                <option value="lots">張</option>
                <option value="shares">股</option>
              </select>
            </label>
            <label className="field">
              手續費
              <input name="fee" type="number" step="1" min="0" className="w-sm" placeholder="自動" />
            </label>
            <label className="field">
              證交稅
              <input name="tax" type="number" step="1" min="0" className="w-sm" placeholder="自動" />
            </label>
            <label className="field">
              備註
              <input name="note" className="w-md" />
            </label>
          </ActionForm>
        </div>
      </Panel>

      <div style={{ height: 14 }} />
      <Panel icon="portfolio" title="目前持股" meta={<>{holdings.length} 檔<span className="sep">｜</span><Updated at={upd.holdings} label="檢查" /></>}>
        {holdings.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>股票</th>
                  <th className="n">股數</th>
                  <th className="n">平均成本</th>
                  <th className="n">現價</th>
                  <th className="n">損益</th>
                  <th className="n">報酬</th>
                  <th className="n">最高收盤</th>
                  <th>開倉日</th>
                  <th>停損／停利</th>
                  <th>最後更新</th>
                  <th>提醒</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h) => {
                  const pnl = h.close ? (h.close - h.avg_cost) * h.shares : null;
                  const sell = sells.find((s) => s.stock_id === h.stock_id);
                  return (
                    <tr key={h.stock_id}>
                      <td><StockLink id={h.stock_id} name={h.name} /></td>
                      <td className="n">{num(h.shares, 0)}</td>
                      <td className="n">{num(h.avg_cost, 2)}</td>
                      <td className="n">{num(h.close)}</td>
                      <td className={`n ${tone(pnl)}`}>{money(pnl)}</td>
                      <td className="n"><Change value={h.close ? h.close / h.avg_cost - 1 : null} /></td>
                      <td className="n">{num(h.highest_close)}</td>
                      <td className="num small">{h.opened_date}</td>
                      <td>
                        <ActionForm action={updateHoldingLevels} submit="更新" className="inline nowrap">
                          <input type="hidden" name="stock_id" value={h.stock_id} />
                          <input name="stop_loss" type="number" step="0.01" defaultValue={h.stop_loss ?? ""} className="w-xs" aria-label="停損" placeholder="停損" />
                          <input name="take_profit" type="number" step="0.01" defaultValue={h.take_profit ?? ""} className="w-xs" aria-label="停利" placeholder="停利" />
                        </ActionForm>
                      </td>
                      <td className="num small faint">{timeTaipei(h.updated_at)}</td>
                      <td className="wrap small">{sell ? <span className="up">{sell.reason}</span> : null}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">沒有持股</p>
        )}
      </Panel>

      <div style={{ height: 14 }} />
      <Panel icon="pie" title="績效統計" meta={st.start ? `${st.start} 起` : undefined}>
        <div className="stats">
          <div className="stat"><div className="stat-label">已實現交易（{st.wins} 勝 {st.losses} 敗）</div><div className="stat-value">{st.closed} 筆</div></div>
          <div className="stat"><div className="stat-label">勝率</div><div className="stat-value">{pct(st.winRate, 1, false)}</div></div>
          <div className="stat"><div className="stat-label">平均獲利</div><div className="stat-value"><Change value={st.avgWinPct} digits={2} /></div></div>
          <div className="stat"><div className="stat-label">平均虧損</div><div className="stat-value"><Change value={st.avgLossPct} digits={2} /></div></div>
          <div className="stat"><div className="stat-label">期望值（每筆）</div><div className="stat-value"><Change value={st.expectancyPct} digits={2} /></div></div>
          <div className="stat"><div className="stat-label">已實現損益</div><div className={`stat-value ${tone(st.realized)}`}>{money(st.realized)}</div></div>
          <div className="stat"><div className="stat-label">未實現損益</div><div className={`stat-value ${tone(st.unrealized)}`}>{money(st.unrealized)}</div></div>
          <div className="stat"><div className="stat-label">平均持有天數</div><div className="stat-value">{num(st.avgHoldingDays, 1)}</div></div>
          <div className="stat"><div className="stat-label">整體績效（佔總資金）</div><div className="stat-value"><Change value={st.portfolioReturn} digits={2} /></div></div>
          {Object.entries(st.benchmarks).map(([k, v]) => (
            <div className="stat" key={k}><div className="stat-label">同期 {BENCH[k]}</div><div className="stat-value"><Change value={v} digits={2} /></div></div>
          ))}
        </div>
        <p className="small faint panel-body">平均成本法（含買進手續費）；每筆賣出算一筆交易。加權指數為價格指數；0050 已還原除息與分割</p>
      </Panel>

      <div style={{ height: 14 }} />
      <Panel icon="list" title="交易紀錄" meta={`${rows.length} 筆`}>
        {rows.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>日期</th>
                  <th>買賣</th>
                  <th>股票</th>
                  <th className="n">價格</th>
                  <th className="n">股數</th>
                  <th className="n">手續費</th>
                  <th className="n">證交稅</th>
                  <th>當時分數</th>
                  <th>對應訊號</th>
                  <th>備註</th>
                  <th>登錄時間</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const snap = r.score_snapshot ?? {};
                  const sig = r.signal_snapshot;
                  return (
                    <tr key={r.trade_id}>
                      <td className="num">{r.date}</td>
                      <td className={r.side === "buy" ? "up" : "down"}>{r.side === "buy" ? "買進" : "賣出"}</td>
                      <td><StockLink id={r.stock_id} name={tradeNames[r.stock_id]} /></td>
                      <td className="n">{num(r.price)}</td>
                      <td className="n">{num(r.shares, 0)}</td>
                      <td className="n">{money(r.fee)}</td>
                      <td className="n">{money(r.tax)}</td>
                      <td className="small">
                        {Object.entries(snap)
                          .map(([h, v]) => `${HORIZON_LABELS[h] ?? h} ${num((v as { total: number | null }).total, 0)}`)
                          .join("／") || "—"}
                      </td>
                      <td className="small wrap">{sig ? `${sig.date} ${sig.reason}` : "—"}</td>
                      <td className="small muted">{r.note}</td>
                      <td className="num small faint">{timeTaipei(r.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">尚無交易紀錄</p>
        )}
      </Panel>
    </>
  );
}
