import { notFound } from "next/navigation";
import { Change, FlagTags, Panel, StockLink } from "@/components/ui";
import { Updated } from "@/components/Updated";
import { dateLabel, num } from "@/lib/format";
import { dataUpdates, flagsOn, groupByName, groupMembers, lastCloses, latestDate, market, scoresOn } from "@/lib/queries";
import { requireSession } from "@/lib/session";

export default async function GroupPage({ params }: { params: Promise<{ name: string }> }) {
  await requireSession();
  const name = decodeURIComponent((await params).name);
  const [group, date] = await Promise.all([groupByName(name), latestDate()]);
  if (!group) notFound();
  const members = await groupMembers(group.group_id);
  const ids = members.map((m) => m.stock_id as string);
  const [m, scores, flags, closes, upd] = await Promise.all([
    date ? market(date) : null,
    date ? scoresOn(date, ids) : [],
    date ? flagsOn(date, ids) : ({} as Awaited<ReturnType<typeof flagsOn>>),
    date ? lastCloses(ids, date) : ({} as Record<string, number>),
    dataUpdates(),
  ]);
  const strength = m?.groups.find((g) => g.name === name);
  const by = (sid: string, h: string) => scores.find((s) => s.stock_id === sid && s.horizon === h);
  const rows = members
    .map((mem) => ({
      stock_id: mem.stock_id as string,
      name: mem.name as string,
      short: by(mem.stock_id, "short"),
      long: by(mem.stock_id, "long"),
    }))
    .sort((a, b) => (b.short?.total ?? -1) - (a.short?.total ?? -1));

  return (
    <>
      <h1>{name}</h1>
      <p className="sub">
        {group.kind === "custom" ? "自訂族群" : "產業族群"}｜{members.length} 檔
        {strength?.rank ? `｜排名第 ${strength.rank}` : ""}
        {strength?.weak ? "｜弱勢族群" : ""}
        {date ? `｜${dateLabel(date)}` : ""}
        <span className="sep">｜</span>
        <Updated at={upd.filters} />
      </p>
      {strength && (
        <section className="panel" style={{ marginBottom: 14 }}>
          <div className="stats">
            <div className="stat"><div className="stat-label">平均分數</div><div className="stat-value">{num(strength.avg_score, 1)}</div></div>
            <div className="stat"><div className="stat-label">近 1 月</div><div className="stat-value"><Change value={strength.return_20} /></div></div>
            <div className="stat"><div className="stat-label">相對大盤</div><div className="stat-value"><Change value={strength.rel_return_20} /></div></div>
            <div className="stat"><div className="stat-label">近 3 月</div><div className="stat-value"><Change value={strength.return_60} /></div></div>
            <div className="stat"><div className="stat-label">相對大盤</div><div className="stat-value"><Change value={strength.rel_return_60} /></div></div>
          </div>
        </section>
      )}
      <Panel icon="bars" title="成分股排行" meta="依短線分數">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>股票</th>
                <th className="n">收盤</th>
                <th className="n">短線</th>
                <th className="n">中長線</th>
                <th className="n">基／技／籌（短線）</th>
                <th>今日濾網</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.stock_id}>
                  <td><StockLink id={r.stock_id} name={r.name} /></td>
                  <td className="n">{num(closes[r.stock_id])}</td>
                  <td className="n">{r.short?.status === "insufficient_data" ? <span className="faint">資料不足</span> : num(r.short?.total, 1)}</td>
                  <td className="n">{num(r.long?.total, 1)}</td>
                  <td className="n">
                    {r.short?.total !== undefined && r.short?.total !== null
                      ? `${num(r.short.fundamental, 0)}／${num(r.short.technical, 0)}／${num(r.short.chips, 0)}`
                      : "—"}
                  </td>
                  <td className="wrap"><FlagTags flags={flags[r.stock_id]} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
