from __future__ import annotations

from datetime import datetime, timezone

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


def set_daily_report_status(*, config: AppConfig, day: str, status: str, error: str | None = None) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            insert into daily_reports (date_key, status, error, created_at, updated_at)
            values (?, ?, ?, ?, ?)
            on conflict(date_key) do update set
              status = excluded.status,
              error = excluded.error,
              updated_at = excluded.updated_at
            """,
            (day, status, error, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_daily_report_status(*, config: AppConfig, day: str) -> str:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute("select status from daily_reports where date_key = ?", (day,)).fetchone()
        if row is None:
            return "not_started"
        return str(row["status"])
    finally:
        conn.close()
