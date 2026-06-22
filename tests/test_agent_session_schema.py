from __future__ import annotations

from pathlib import Path

from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_agent_session_tables_exist_after_initialize(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data" / "db" / "personal_context.sqlite")
    try:
        initialize(conn)
        tables = {
            row["name"]
            for row in fetch_all(
                conn,
                "select name from sqlite_master where type = 'table' and name like 'agent_%'",
            )
        }
        agent_session_columns = {
            row["name"]
            for row in fetch_all(conn, "pragma table_info(agent_sessions)")
        }
        agent_turn_columns = {
            row["name"]
            for row in fetch_all(conn, "pragma table_info(agent_turns)")
        }
        agent_tool_columns = {
            row["name"]
            for row in fetch_all(conn, "pragma table_info(agent_tool_events)")
        }
    finally:
        conn.close()

    assert tables == {"agent_sessions", "agent_turns", "agent_tool_events"}
    assert {
        "agent_session_id",
        "source_type",
        "source_path",
        "source_sha256",
        "cwd",
        "model",
        "started_at",
        "ended_at",
        "message_count",
        "tool_event_count",
    }.issubset(agent_session_columns)
    assert {"agent_turn_id", "agent_session_id", "turn_index", "role", "text"}.issubset(agent_turn_columns)
    assert {
        "agent_tool_event_id",
        "agent_session_id",
        "event_index",
        "tool_name",
        "arguments_json",
        "output_text",
    }.issubset(agent_tool_columns)
