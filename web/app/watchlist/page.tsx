import { addGroupMembers, addWatchlist, createGroup, deleteGroup, removeGroupMember, removeWatchlist, renameGroup } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { Panel, StockLink } from "@/components/ui";
import { groupsWithMembers, names, runtimeConfig, watchlist } from "@/lib/queries";
import { requireSession } from "@/lib/session";

const STATUS_LABELS: Record<string, string> = { disposition: "處置", attention: "注意", full_delivery: "全額交割" };

export default async function WatchlistPage() {
  await requireSession();
  const [list, groups, { config }] = await Promise.all([watchlist(), groupsWithMembers(), runtimeConfig()]);
  const custom = groups.filter((g) => g.kind === "custom");
  const industry = groups.filter((g) => g.kind === "industry" && (g.members as string[]).length);
  const memberNames = await names([...new Set(custom.flatMap((g) => g.members as string[]))]);
  const limit = config.watchlist_limit;

  return (
    <>
      <h1>自選股與族群</h1>
      <p className="sub">
        {config.plan_label}
        {limit ? `｜自選股上限 ${limit} 檔，目前 ${list.length} 檔` : "｜全市場模式，自選股只作為關注清單"}
      </p>

      <Panel title="自選股" meta={`${list.length} 檔`}>
        <div className="panel-body">
          <ActionForm action={addWatchlist} submit="加入">
            <label className="field">
              股票代號（可一次輸入多檔，以空白分隔）
              <input name="stocks" placeholder="2330 2317 2454" style={{ width: 260 }} required />
            </label>
          </ActionForm>
        </div>
        {list.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>股票</th>
                  <th>產業</th>
                  <th>市場</th>
                  <th className="n">歷史資料</th>
                  <th>狀態</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {list.map((w) => (
                  <tr key={w.stock_id}>
                    <td><StockLink id={w.stock_id} name={w.name} /></td>
                    <td className="muted">{w.industry ?? "—"}</td>
                    <td className="muted">{w.market === "twse" ? "上市" : "上櫃"}</td>
                    <td className="n">
                      {w.price_days >= config.min_history_days ? (
                        `${w.price_days} 天`
                      ) : (
                        <span className="tag warn">待回補（{w.price_days} 天）</span>
                      )}
                    </td>
                    <td>
                      {(w.status as string[]).map((s) => (
                        <span key={s} className="tag block">{STATUS_LABELS[s] ?? s}</span>
                      ))}
                    </td>
                    <td>
                      <form action={removeWatchlist}>
                        <input type="hidden" name="stock_id" value={w.stock_id} />
                        <button className="link" type="submit">移除</button>
                      </form>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">尚未加入自選股</p>
        )}
      </Panel>

      <div style={{ height: 14 }} />
      <Panel title="自訂族群" meta="一檔股票可屬於多個族群">
        <div className="panel-body">
          <ActionForm action={createGroup} submit="建立族群">
            <label className="field">
              新族群名稱
              <input name="name" placeholder="例如 AI 伺服器" className="w-md" required maxLength={30} />
            </label>
          </ActionForm>
        </div>
        {custom.map((g) => (
          <div key={g.group_id} className="panel-body" style={{ borderTop: "1px solid var(--line)" }}>
            <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
              <strong>{g.name}</strong>
              <span className="faint small">{(g.members as string[]).length} 檔</span>
              <form action={deleteGroup}>
                <input type="hidden" name="group_id" value={g.group_id} />
                <button className="link" type="submit">刪除族群</button>
              </form>
            </div>
            <div className="tags" style={{ margin: "8px 0" }}>
              {(g.members as string[]).map((sid) => (
                <span key={sid} className="tag" style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
                  <StockLink id={sid} name={memberNames[sid]} />
                  <form action={removeGroupMember} style={{ display: "inline" }}>
                    <input type="hidden" name="group_id" value={g.group_id} />
                    <input type="hidden" name="stock_id" value={sid} />
                    <button className="link" type="submit" aria-label={`從${g.name}移除${sid}`}>×</button>
                  </form>
                </span>
              ))}
            </div>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <ActionForm action={addGroupMembers} submit="加入成分股">
                <input type="hidden" name="group_id" value={g.group_id} />
                <input name="stocks" placeholder="代號" className="w-md" required aria-label="成分股代號" />
              </ActionForm>
              <ActionForm action={renameGroup} submit="更名">
                <input type="hidden" name="group_id" value={g.group_id} />
                <input name="name" defaultValue={g.name} className="w-md" required maxLength={30} aria-label="族群名稱" />
              </ActionForm>
            </div>
          </div>
        ))}
      </Panel>

      <div style={{ height: 14 }} />
      <Panel title="產業族群" meta="證交所產業分類，自動帶入">
        <p className="panel-body small muted">
          {industry.map((g) => `${g.name}（${(g.members as string[]).length}）`).join("、") || "尚未建立（執行 seed-groups）"}
        </p>
      </Panel>
    </>
  );
}
