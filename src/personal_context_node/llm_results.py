from __future__ import annotations

import json

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def _latest_summary(config: AppConfig, *, summary_type: str, target_id: str) -> dict[str, object] | None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select content_json, model_name, updated_at
            from summaries
            where summary_type = ? and target_id = ?
            order by updated_at desc
            limit 1
            """,
            (summary_type, target_id),
        )
    finally:
        conn.close()
    if not rows:
        return None
    return {
        "content": json.loads(str(rows[0]["content_json"])),
        "model_name": rows[0]["model_name"],
        "updated_at": rows[0]["updated_at"],
    }


def session_summary(*, config: AppConfig, session_id: str) -> dict[str, object] | None:
    result = _latest_summary(config, summary_type="session", target_id=session_id)
    return None if result is None else {"session_id": session_id, **result}


def daily_context(*, config: AppConfig, day: str) -> dict[str, object] | None:
    result = _latest_summary(config, summary_type="daily", target_id=day)
    return None if result is None else {"day": day, **result}


def day_memory_candidates(*, config: AppConfig, day: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select candidate_id, candidate_claim, edited_claim, claim_type, confidence, status
            from memory_candidates
            where date_key = ?
            order by created_at
            """,
            (day,),
        )
    finally:
        conn.close()
