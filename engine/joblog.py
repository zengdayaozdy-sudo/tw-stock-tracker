"""排程執行紀錄（job_logs）。"""

from __future__ import annotations

import json
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

STALE_RUNNING_MINUTES = 240  # 每日排程逾時上限 180 分鐘，再留一點緩衝


class JobRun:
    def __init__(self, conn, job: str, run_date: date | None):
        self.conn = conn
        self.job = job
        self.run_date = run_date
        self.detail: dict[str, Any] = {}
        self.status = "success"
        with conn.cursor() as cur:
            # 先前被取消或逾時中斷的同名執行會一直停在 running：超過上限時間就標為失敗，系統狀態頁才不會誤判
            cur.execute(
                "UPDATE job_logs SET status = 'failed', finished_at = now(), "
                "detail = coalesce(detail, '{}'::jsonb) || '{\"error\": \"執行中斷（排程取消或逾時）\"}'::jsonb "
                "WHERE job = %s AND status = 'running' AND started_at < now() - %s * interval '1 minute'",
                (job, STALE_RUNNING_MINUTES),
            )
            cur.execute(
                "INSERT INTO job_logs (job, run_date, status) VALUES (%s, %s, 'running') RETURNING log_id",
                (job, run_date),
            )
            self.log_id = cur.fetchone()[0]
        conn.commit()

    def note(self, **kwargs: Any) -> None:
        self.detail.update(kwargs)

    def skip(self, reason: str) -> None:
        self.status = "skipped"
        self.detail["reason"] = reason

    def finish(self, status: str | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE job_logs SET status = %s, detail = %s, finished_at = now() WHERE log_id = %s",
                (status or self.status, json.dumps(self.detail, ensure_ascii=False, default=str), self.log_id),
            )
        self.conn.commit()


@contextmanager
def job_run(conn, job: str, run_date: date | None = None) -> Iterator[JobRun]:
    run = JobRun(conn, job, run_date)
    try:
        yield run
    except BaseException as e:
        run.note(error=str(e), traceback=traceback.format_exc()[-2000:])
        try:
            conn.rollback()
            run.finish("failed")
        except Exception:
            # 連線已被切斷（例如 Neon 逾時）：另開連線記成失敗，否則紀錄會一直停在 running，警告也看不到真正原因
            _finish_on_new_connection(run, "failed")
        raise
    else:
        conn.commit()
        run.finish()


def _finish_on_new_connection(run: JobRun, status: str) -> None:
    from engine.db import connect

    try:
        with connect() as fresh:
            run.conn = fresh
            run.finish(status)
    except Exception:
        pass  # 補記失敗不可蓋掉原本的錯誤；超過 STALE_RUNNING_MINUTES 後會被標為失敗


def heartbeat(conn, job: str, detail: dict | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO job_logs (job, status, detail, finished_at) VALUES (%s, 'heartbeat', %s, now())",
            (job, json.dumps(detail or {}, ensure_ascii=False)),
        )
    conn.commit()
