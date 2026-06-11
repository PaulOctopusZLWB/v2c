from __future__ import annotations

from datetime import datetime, timezone

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


def set_daily_report_status(*, config: AppConfig, day: str, status: str, error: str | None = None) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into daily_reports (day, status, updated_at, error)
            values (?, ?, ?, ?)
            on conflict(day) do update set
              status = excluded.status,
              updated_at = excluded.updated_at,
              error = excluded.error
            """,
            (day, status, datetime.now(timezone.utc).isoformat(), error),
        )
        conn.commit()
    finally:
        conn.close()


def get_daily_report_status(*, config: AppConfig, day: str) -> str:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute("select status from daily_reports where day = ?", (day,)).fetchone()
        if row is None:
            return "not_started"
        return str(row["status"])
    finally:
        conn.close()
