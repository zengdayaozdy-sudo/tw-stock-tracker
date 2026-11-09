import Link from "next/link";
import { HORIZON_LABELS, num, pct, tone } from "@/lib/format";
import type { ScoreRow, Signal } from "@/lib/queries";

export function Panel({ title, meta, children }: { title: string; meta?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{title}</h2>
        {meta && <span className="meta">{meta}</span>}
      </div>
      {children}
    </section>
  );
}

export function StockLink({ id, name }: { id: string; name?: string | null }) {
  return (
    <Link href={`/stocks/${id}`}>
      <span className="num">{id}</span> {name ?? ""}
    </Link>
  );
}

type Flag = { filter: string; kind: string; reason: string };
const KIND_CLASS: Record<string, string> = { exclude: "block", block: "block", warn: "warn" };

export function FlagTags({ flags }: { flags?: Flag[] }) {
  if (!flags?.length) return null;
  return (
    <span className="tags">
      {flags.map((f) => (
        <span key={`${f.filter}-${f.reason}`} className={`tag ${KIND_CLASS[f.kind] ?? ""}`} title={f.reason}>
          {f.reason.length > 28 ? `${f.reason.slice(0, 28)}…` : f.reason}
        </span>
      ))}
    </span>
  );
}

export function Change({ value, digits = 1 }: { value: number | null | undefined; digits?: number }) {
  return <span className={tone(value)}>{pct(value, digits)}</span>;
}

function lotsText(s: Signal): string {
  if (s.suggested_lots) return `${s.suggested_lots} 張`;
  const odd = s.detail?.sizing?.odd_shares;
  return odd ? `零股 ${odd}` : "0";
}

export function BuySignalTable({ signals, showDate = false }: { signals: Signal[]; showDate?: boolean }) {
  if (!signals.length) return <p className="empty">沒有買進訊號</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {showDate && <th>日期</th>}
            <th>股票</th>
            <th>週期</th>
            <th className="n">總分</th>
            <th className="n">基／技／籌</th>
            <th className="n">收盤</th>
            <th className="n">買進區間</th>
            <th className="n">停損</th>
            <th className="n">停利</th>
            <th className="n">建議</th>
            <th>觸發與提示</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => {
            const sc = s.detail?.score ?? {};
            return (
              <tr key={`${s.date}-${s.stock_id}-${s.horizon}`}>
                {showDate && <td className="num">{s.date}</td>}
                <td>
                  <StockLink id={s.stock_id} name={s.detail?.name} />
                </td>
                <td>{HORIZON_LABELS[s.horizon]}</td>
                <td className="n">{num(sc.total, 1)}</td>
                <td className="n">
                  {num(sc.fundamental, 0)}／{num(sc.technical, 0)}／{num(sc.chips, 0)}
                </td>
                <td className="n">{num(s.detail?.close)}</td>
                <td className="n">
                  {num(s.entry_low)}–{num(s.entry_high)}
                </td>
                <td className="n down">{num(s.stop_loss)}</td>
                <td className="n up">{num(s.take_profit)}</td>
                <td className="n">{lotsText(s)}</td>
                <td className="wrap">
                  <div>{(s.detail?.trigger_text ?? []).join("、")}</div>
                  {s.detail?.high_score_reasons?.length ? (
                    <div className="small muted">高分理由：{s.detail.high_score_reasons.join("、")}</div>
                  ) : null}
                  <span className="tags">
                    {[...(s.detail?.warnings ?? []), ...(s.detail?.sizing?.notes ?? [])].map((w: string) => (
                      <span key={w} className="tag warn">
                        {w}
                      </span>
                    ))}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const DIM_LABELS: Record<string, string> = { fundamental: "基本面", technical: "技術面", chips: "籌碼面" };

type Indicator = {
  key: string;
  label: string;
  dimension: string;
  raw: number | null;
  score: number | null;
  locked?: boolean;
  weight: number;
  dimension_weight: number;
  contribution: number;
  self_percentile: number | null;
  peer_percentile: number | null;
  note: string | null;
};

export function ScoreBreakdown({ score }: { score: ScoreRow & { date?: string } }) {
  if (score.status === "insufficient_data")
    return <p className="empty">資料不足未評分：{score.breakdown?.reason ?? ""}</p>;
  const items: Indicator[] = score.breakdown?.indicators ?? [];
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>指標</th>
            <th className="n">原始值</th>
            <th className="n">自身</th>
            <th className="n">同族群</th>
            <th>分數</th>
            <th className="n">權重</th>
            <th className="n">貢獻</th>
            <th>備註</th>
          </tr>
        </thead>
        <tbody>
          {(["fundamental", "technical", "chips"] as const).map((dim) => [
            <tr key={dim}>
              <td colSpan={8}>
                <strong>{DIM_LABELS[dim]}</strong>{" "}
                <span className="num">{num(score[dim], 1)}</span>
                <span className="faint small"> × {pct(score.breakdown?.dimensions?.[dim]?.weight, 0, false)}</span>
              </td>
            </tr>,
            ...items
              .filter((i) => i.dimension === dim)
              .map((i) =>
                i.locked ? (
                  <tr key={i.key} className="locked-row">
                    <td>
                      <span className="lock">{i.label}</span>
                    </td>
                    <td colSpan={6} className="small faint">
                      未解鎖，不計分（權重已分給同面向其他指標）
                    </td>
                    <td className="small muted">{(i.note ?? "").replace(/^未解鎖：/, "")}</td>
                  </tr>
                ) : (
                <tr key={i.key}>
                  <td>{i.label}</td>
                  <td className="n">{i.raw === null ? "—" : num(i.raw, Math.abs(i.raw) < 10 ? 3 : 1)}</td>
                  <td className="n">{num(i.self_percentile, 0)}</td>
                  <td className="n">{num(i.peer_percentile, 0)}</td>
                  <td>
                    <span className="bar">
                      <span style={{ width: `${Math.max(0, Math.min(100, i.score ?? 0))}%` }} />
                    </span>{" "}
                    <span className="num small">{num(i.score, 0)}</span>
                  </td>
                  <td className="n">{pct(i.weight, 0, false)}</td>
                  <td className="n">{num(i.contribution, 1)}</td>
                  <td className="small muted">{i.note ?? ""}</td>
                </tr>
                ),
              ),
          ])}
        </tbody>
      </table>
    </div>
  );
}
