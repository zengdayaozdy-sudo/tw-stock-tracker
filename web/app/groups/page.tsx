import Link from "next/link";
import { Change, Panel } from "@/components/ui";
import { Updated } from "@/components/Updated";
import { dateLabel, num, pct, timeTaipei } from "@/lib/format";
import { dataUpdates, groupsWithMembers, latestDate, market, scopeNews } from "@/lib/queries";
import { requireSession } from "@/lib/session";

export default async function GroupsPage() {
  await requireSession();
  const date = await latestDate();
  const [m, all, upd, news] = await Promise.all([
    date ? market(date) : null, groupsWithMembers(), dataUpdates(), scopeNews(),
  ]);
  const strength = m?.groups ?? [];
  const ranked = new Set(strength.map((g) => g.name));
  const others = all.filter((g) => !ranked.has(g.name) && (g.members as string[]).length);

  return (
    <>
      <h1>族群強弱</h1>
      <p className="sub">
        {date ? dateLabel(date) : "尚無資料"}｜綜合近 1／3 個月相對大盤報酬與族群平均分數排名，後段為弱勢族群（不發買進訊號）
      </p>
      <Panel icon="groups" title="排名" meta={<>{strength.filter((g) => g.rank).length} 個族群<span className="sep">｜</span><Updated at={upd.filters} /></>}>
        {strength.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="n">#</th>
                  <th>族群</th>
                  <th>類型</th>
                  <th className="n">檔數</th>
                  <th className="n">平均分</th>
                  <th className="n">近 1 月</th>
                  <th className="n">相對大盤</th>
                  <th className="n">近 3 月</th>
                  <th className="n">相對大盤</th>
                  <th className="n">綜合</th>
                </tr>
              </thead>
              <tbody>
                {strength.map((g) => (
                  <tr key={g.name}>
                    <td className="n">{g.rank ?? "—"}</td>
                    <td>
                      <Link href={`/groups/${encodeURIComponent(g.name)}`}>{g.name}</Link>{" "}
                      {g.weak && <span className="tag block">弱勢</span>}
                    </td>
                    <td className="muted">{g.kind === "custom" ? "自訂" : "產業"}</td>
                    <td className="n">{g.size}</td>
                    <td className="n">{num(g.avg_score, 1)}</td>
                    <td className="n"><Change value={g.return_20} /></td>
                    <td className="n"><Change value={g.rel_return_20} /></td>
                    <td className="n"><Change value={g.return_60} /></td>
                    <td className="n"><Change value={g.rel_return_60} /></td>
                    <td className="n">{num(g.composite, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">尚未計算族群強弱（每日排程的 filters 步驟）</p>
        )}
      </Panel>
      {others.length > 0 && (
        <>
          <div style={{ height: 14 }} />
          <Panel icon="groups" title="未排名的族群" meta="成分股不在追蹤範圍內">
            <p className="panel-body small">
              {others.map((g) => (
                <Link key={g.group_id} href={`/groups/${encodeURIComponent(g.name)}`} style={{ marginRight: 12 }}>
                  {g.name}
                </Link>
              ))}
            </p>
          </Panel>
        </>
      )}

      <div style={{ height: 14 }} />
      <Panel
        icon="shield"
        title="族群與盤勢消息"
        meta={<>近 3 天 {news.length} 則<span className="sep">｜</span>Yahoo股市<span className="sep">｜</span><Updated at={upd.news} label="判讀" /></>}
      >
        {news.length ? (
          <div className="table-wrap">
            <table>
              <tbody>
                {news.map((n) => (
                  <tr key={n.news_id}>
                    <td className="num small faint">{timeTaipei(n.published_at)}</td>
                    <td className="small">{n.scope === "group" ? "族群" : "盤勢"}{n.topic ? `｜${n.topic}` : ""}</td>
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
                          <span className="tag warn">
                            {n.event_type ?? "利空"} {pct(n.confidence, 0, false)}
                          </span>
                        ) : (
                          <span className="tag ok">非利空</span>
                        )}{" "}
                        {n.summary}｜{n.source}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">近 3 天沒有族群或盤勢新聞</p>
        )}
      </Panel>
    </>
  );
}
