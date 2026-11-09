import Link from "next/link";
import { addGroupMembers, addWatchlist, createGroup, deleteGroup, removeGroupMember, removeWatchlist, renameGroup } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { StockSearch } from "@/components/StockSearch";
import { Updated } from "@/components/Updated";
import { num, pct, shortDate, tone } from "@/lib/format";
import { type WatchItem, dataUpdates, groupsWithMembers, latestDate, names, runtimeConfig, watchlistDetail } from "@/lib/queries";
import { requireSession } from "@/lib/session";

const STATUS_LABELS: Record<string, string> = { disposition: "處置", attention: "注意", full_delivery: "全額交割" };

const SORTS = {
  code: { label: "代號", by: (a: WatchItem, b: WatchItem) => a.stock_id.localeCompare(b.stock_id) },
  short: { label: "短線分數", by: (a: WatchItem, b: WatchItem) => (b.short ?? -1) - (a.short ?? -1) },
  long: { label: "中長線分數", by: (a: WatchItem, b: WatchItem) => (b.long ?? -1) - (a.long ?? -1) },
  change: { label: "漲跌", by: (a: WatchItem, b: WatchItem) => (change(b) ?? -9) - (change(a) ?? -9) },
} as const;
type SortKey = keyof typeof SORTS;

function change(w: WatchItem): number | null {
  return w.close && w.prev_close ? w.close / w.prev_close - 1 : null;
}

function scoreClass(x: number | null): string {
  if (x === null) return "none";
  return x >= 70 ? "hi" : x >= 50 ? "mid" : "lo";
}

function href(params: { g?: string; sort?: string }) {
  const q = new URLSearchParams();
  if (params.g) q.set("g", params.g);
  if (params.sort && params.sort !== "code") q.set("sort", params.sort);
  const s = q.toString();
  return s ? `/watchlist?${s}` : "/watchlist";
}

export default async function WatchlistPage({ searchParams }: { searchParams: Promise<{ g?: string; sort?: string }> }) {
  await requireSession();
  const sp = await searchParams;
  const [list, groups, { config }, upd, date] = await Promise.all([
    watchlistDetail(),
    groupsWithMembers(),
    runtimeConfig(),
    dataUpdates(),
    latestDate(),
  ]);
  const custom = groups.filter((g) => g.kind === "custom");
  const industry = groups.filter((g) => g.kind === "industry" && (g.members as string[]).length);
  const memberNames = await names([...new Set(custom.flatMap((g) => g.members as string[]))]);
  const watched = new Set(list.map((w) => w.stock_id));
  const limit = config.watchlist_limit;
  // 免費模式的同族群比較只算自選股裡的成分股，不足門檻時改用產業、再不足只與自身歷史比較
  const minPeers = config.min_peers ?? 3;

  const sort: SortKey = sp.sort && sp.sort in SORTS ? (sp.sort as SortKey) : "code";
  const group = sp.g && custom.some((g) => g.name === sp.g) ? sp.g : undefined;
  const shown = list.filter((w) => !group || w.groups.includes(group)).sort(SORTS[sort].by);
  const ungrouped = list.filter((w) => !w.groups.length).length;
  const needBackfill = list.filter((w) => w.price_days < config.min_history_days).length;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>自選股與族群</h1>
          <p className="sub">
            {config.plan_label}・{limit ? `已使用 ${list.length} / ${limit} 檔` : `全市場模式，自選 ${list.length} 檔`}
            <span className="sep">｜</span>
            {date ? `${shortDate(date)} 收盤評分　` : ""}
            <Updated at={upd.score} />
          </p>
        </div>
        {limit ? (
          <div className="meter" aria-label={`自選股已使用 ${list.length} / ${limit}`}>
            <span style={{ width: `${Math.min(100, (list.length / limit) * 100)}%` }} />
          </div>
        ) : null}
      </header>

      <section className="card">
        <div className="card-head">
          <h2>加入自選股</h2>
        </div>
        <div className="card-body">
          <StockSearch action={addWatchlist} />
        </div>
      </section>

      {needBackfill > 0 && (
        <p className="notice">
          {needBackfill} 檔歷史資料不足 {config.min_history_days} 天，暫時不會評分；到 GitHub「手動操作」執行 backfill 即可補齊
        </p>
      )}

      <section className="card">
        <div className="card-head">
          <h2>我的自選股</h2>
          <span className="meta">
            {shown.length === list.length ? `${list.length} 檔` : `${shown.length} / ${list.length} 檔`}
          </span>
        </div>

        <div className="toolbar">
          <nav className="chips" aria-label="依族群篩選">
            <Link href={href({ sort })} className={`chip ${group ? "" : "on"}`}>全部</Link>
            {custom
              .filter((g) => list.some((w) => w.groups.includes(g.name)))
              .map((g) => (
                <Link key={g.group_id} href={href({ g: g.name, sort })} className={`chip ${group === g.name ? "on" : ""}`}>
                  {g.name}
                </Link>
              ))}
          </nav>
          <nav className="segmented" aria-label="排序">
            {(Object.keys(SORTS) as SortKey[]).map((k) => (
              <Link key={k} href={href({ g: group, sort: k })} className={sort === k ? "on" : ""}>
                {SORTS[k].label}
              </Link>
            ))}
          </nav>
        </div>

        {shown.length ? (
          <ul className="watch-list">
            <li className="watch-row watch-head" aria-hidden="true">
              <span>股票</span>
              <span className="r">收盤</span>
              <span className="r">漲跌</span>
              <span className="r">短線</span>
              <span className="r">中長線</span>
              <span />
            </li>
            {shown.map((w) => {
              const c = change(w);
              const thin = w.price_days < config.min_history_days;
              return (
                <li key={w.stock_id} className="watch-row">
                  <Link href={`/stocks/${w.stock_id}`} className="watch-stock">
                    <span className="watch-title">
                      <span className="num">{w.stock_id}</span> {w.name}
                    </span>
                    <span className="watch-sub">
                      {w.industry ?? "—"}・{w.market === "twse" ? "上市" : "上櫃"}
                      {w.groups.length ? `・${w.groups.join("、")}` : ""}
                    </span>
                    {(w.status.length > 0 || thin) && (
                      <span className="tags">
                        {w.status.map((s) => (
                          <span key={s} className="tag block">{STATUS_LABELS[s] ?? s}</span>
                        ))}
                        {thin && <span className="tag warn">待回補 {w.price_days} 天</span>}
                      </span>
                    )}
                  </Link>
                  <span className="watch-price num r">{num(w.close)}</span>
                  <span className={`watch-change num r ${tone(c)}`}>{pct(c, 2)}</span>
                  <span className="r cell-short">
                    <span className={`score ${scoreClass(w.short)}`} title="短線分數">
                      <small>短</small>
                      {w.short === null ? "—" : num(w.short, 0)}
                    </span>
                  </span>
                  <span className="r cell-long">
                    <span className={`score ${scoreClass(w.long)}`} title="中長線分數">
                      <small>長</small>
                      {w.long === null ? "—" : num(w.long, 0)}
                    </span>
                  </span>
                  <details className="menu">
                    <summary aria-label={`${w.stock_id} 更多操作`}>⋯</summary>
                    <div className="menu-pop">
                      <Link href={`/stocks/${w.stock_id}`}>查看個股</Link>
                      <form action={removeWatchlist}>
                        <input type="hidden" name="stock_id" value={w.stock_id} />
                        <button type="submit" className="danger">從自選股移除</button>
                      </form>
                    </div>
                  </details>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="empty">{list.length ? "這個族群沒有自選股" : "還沒有自選股，從上方搜尋加入"}</p>
        )}
        {ungrouped > 0 && !group && (
          <p className="card-foot faint small">{ungrouped} 檔尚未歸入任何自訂族群</p>
        )}
      </section>

      <section className="section-head">
        <h2>自訂族群</h2>
        <details className="reveal">
          <summary className="btn ghost">＋ 新增族群</summary>
          <div className="reveal-pop">
            <ActionForm action={createGroup} submit="建立">
              <input name="name" placeholder="族群名稱，例如 AI 伺服器" required maxLength={30} aria-label="族群名稱" />
            </ActionForm>
          </div>
        </details>
      </section>

      {custom.length ? (
        <div className="group-grid">
          {custom.map((g) => {
            const members = g.members as string[];
            const inList = members.filter((sid) => watched.has(sid)).length;
            return (
              <section key={g.group_id} className="card group-card">
                <div className="card-head">
                  <h3>
                    <Link href={`/groups/${encodeURIComponent(g.name)}`}>{g.name}</Link>
                  </h3>
                  <span className="meta">{members.length} 檔</span>
                  {limit && inList < minPeers ? (
                    <span className="tag warn" title="少於門檻時改用產業族群比較，仍不足則只與自身歷史比較">
                      自選股中 {inList} 檔，需 {minPeers} 檔才做同族群比較
                    </span>
                  ) : null}
                  <details className="menu">
                    <summary aria-label={`${g.name} 族群設定`}>⋯</summary>
                    <div className="menu-pop wide">
                      <ActionForm action={renameGroup} submit="更名">
                        <input type="hidden" name="group_id" value={g.group_id} />
                        <input name="name" defaultValue={g.name} required maxLength={30} aria-label="族群名稱" />
                      </ActionForm>
                      <form action={deleteGroup}>
                        <input type="hidden" name="group_id" value={g.group_id} />
                        <button type="submit" className="danger">刪除這個族群</button>
                      </form>
                    </div>
                  </details>
                </div>
                <div className="card-body">
                  {members.length ? (
                    <div className="member-chips">
                      {members.map((sid) => (
                        <span key={sid} className={`member ${watched.has(sid) ? "" : "unwatched"}`}>
                          <Link href={`/stocks/${sid}`}>
                            <span className="num">{sid}</span> {memberNames[sid] ?? ""}
                          </Link>
                          <form action={removeGroupMember}>
                            <input type="hidden" name="group_id" value={g.group_id} />
                            <input type="hidden" name="stock_id" value={sid} />
                            <button type="submit" aria-label={`從${g.name}移除${sid}`} title="從族群移除">×</button>
                          </form>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="faint small">還沒有成分股</p>
                  )}
                  <details className="reveal inline-reveal">
                    <summary>＋ 加入成分股</summary>
                    <StockSearch
                      action={addGroupMembers}
                      hidden={{ group_id: String(g.group_id) }}
                      exclude={members}
                      placeholder="代號或名稱"
                      compact
                    />
                  </details>
                </div>
              </section>
            );
          })}
        </div>
      ) : (
        <p className="empty card">還沒有自訂族群</p>
      )}
      <p className="faint small legend">虛線外框的成分股不在自選股內，不會出現在每日報告</p>

      <section className="section-head">
        <h2>產業族群</h2>
        <span className="meta">證交所產業分類，自動帶入</span>
      </section>
      <div className="chips wrap">
        {industry.map((g) => (
          <Link key={g.group_id} href={`/groups/${encodeURIComponent(g.name)}`} className="chip">
            {g.name}
            <span className="chip-count">{(g.members as string[]).length}</span>
          </Link>
        ))}
        {!industry.length && <span className="faint">尚未建立（執行 seed-groups）</span>}
      </div>
    </>
  );
}
