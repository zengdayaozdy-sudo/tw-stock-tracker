"use client";
// 輸入代號或名稱即時搜尋，點選或按 Enter 直接加入。
// 也可一次貼上多個代號（以空白或逗號分隔）後按 Enter。
import { useEffect, useId, useRef, useState, useTransition } from "react";
import type { ActionState } from "@/app/actions";

type Hit = { stock_id: string; name: string; industry: string | null; market: string; in_watchlist: boolean };

export function StockSearch({
  action,
  hidden = {},
  placeholder = "代號或名稱，如 2330、台積電",
  submitLabel = "加入",
  exclude = [],
  autoFocus = false,
  compact = false,
}: {
  action: (prev: ActionState, form: FormData) => Promise<ActionState>;
  hidden?: Record<string, string>;
  placeholder?: string;
  submitLabel?: string;
  exclude?: string[];
  autoFocus?: boolean;
  compact?: boolean;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [state, setState] = useState<ActionState>(null);
  const [pending, start] = useTransition();
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  const excludeKey = exclude.join(",");

  useEffect(() => {
    const text = q.trim();
    if (!text || /[\s,，、]/.test(text)) {
      setHits([]);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`/api/stocks/search?q=${encodeURIComponent(text)}`, { signal: ctrl.signal });
        if (res.ok) {
          const data = (await res.json()) as { results: Hit[] };
          const skip = new Set(excludeKey.split(","));
          setHits(data.results.filter((h) => !skip.has(h.stock_id)));
          setActive(0);
        }
      } catch {
        /* 中斷或網路錯誤時保留原清單 */
      }
    }, 180);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [q, excludeKey]);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  function submit(value: string) {
    const text = value.trim();
    if (!text) return;
    const form = new FormData();
    form.set("stocks", text);
    for (const [k, v] of Object.entries(hidden)) form.set(k, v);
    start(async () => {
      const res = await action(null, form);
      setState(res);
      if (res?.ok) {
        setQ("");
        setHits([]);
        setOpen(false);
      }
    });
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = open && active >= 0 ? hits[active] : undefined;
      submit(pick ? pick.stock_id : q);
    }
  }

  const showList = open && hits.length > 0;
  return (
    <div className={`search ${compact ? "compact" : ""}`} ref={box}>
      <div className="search-row">
        <div className="search-field">
          <svg className="search-icon" viewBox="0 0 20 20" aria-hidden="true">
            <path d="M8.5 3a5.5 5.5 0 1 0 3.4 9.8l3.7 3.7 1.1-1.1-3.7-3.7A5.5 5.5 0 0 0 8.5 3Zm0 1.6a3.9 3.9 0 1 1 0 7.8 3.9 3.9 0 0 1 0-7.8Z" />
          </svg>
          <input
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOpen(true);
              setState(null);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKey}
            placeholder={placeholder}
            autoFocus={autoFocus}
            autoComplete="off"
            role="combobox"
            aria-expanded={showList}
            aria-controls={listId}
            aria-label={placeholder}
          />
        </div>
        <button type="button" onClick={() => submit(q)} disabled={pending || !q.trim()}>
          {pending ? "處理中" : submitLabel}
        </button>
      </div>
      {showList && (
        <ul className="search-list" id={listId} role="listbox">
          {hits.map((h, i) => (
            <li
              key={h.stock_id}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : ""}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                submit(h.stock_id);
              }}
            >
              <span className="num">{h.stock_id}</span>
              <span className="search-name">{h.name}</span>
              <span className="search-meta">
                {h.industry ?? ""}・{h.market === "twse" ? "上市" : "上櫃"}
              </span>
              {h.in_watchlist && <span className="tag">已在自選</span>}
            </li>
          ))}
        </ul>
      )}
      {state && <p className={`notice ${state.ok ? "ok" : "error"}`}>{state.message}</p>}
    </div>
  );
}
