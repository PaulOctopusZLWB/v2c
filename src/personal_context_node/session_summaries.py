from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import LLMPort, SessionSummary
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


PROMPT_VERSION = "llm_port.session_summary.v1"


@dataclass(frozen=True)
class SessionSummaryResult:
    summaries_created: int


def summarize_session(*, config: AppConfig, session_id: str, llm: LLMPort) -> SessionSummaryResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        segments = fetch_all(
            conn,
            """
            select segment_id, speaker, start_ms, end_ms, text, evidence_id
            from transcript_segments
            where session_id = ? and is_active = 1
            order by start_ms, segment_id
            """,
            (session_id,),
        )
        if not segments:
            return SessionSummaryResult(summaries_created=0)
        summary = llm.generate_session_summary(session_id=session_id, transcript_segments=segments)
        _persist_session_summary(conn, summary)
        conn.commit()
        return SessionSummaryResult(summaries_created=1)
    finally:
        conn.close()


def _persist_session_summary(conn: sqlite3.Connection, summary: SessionSummary) -> None:
    now = datetime.now(timezone.utc).isoformat()
    content = {
        "schema_version": "session_summary.v1",
        **asdict(summary),
    }
    conn.execute(
        """
        insert into summaries (
          summary_id, summary_type, target_type, target_id, prompt_version,
          model_name, content_json, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(summary_type, target_type, target_id, prompt_version) do update set
          model_name = excluded.model_name,
          content_json = excluded.content_json,
          updated_at = excluded.updated_at
        """,
        (
            f"sum_{uuid4().hex}",
            "session",
            "session",
            summary.session_id,
            PROMPT_VERSION,
            "rule_based",
            json.dumps(content, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )
