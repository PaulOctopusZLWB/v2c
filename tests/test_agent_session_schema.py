from __future__ import annotations

from pathlib import Path

from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def _index_shapes(conn, table: str) -> list[tuple[str, bool, tuple[str, ...]]]:
    indexes = []
    for index in fetch_all(conn, f"pragma index_list({table})"):
        index_name = index["name"]
        columns = tuple(row["name"] for row in fetch_all(conn, f"pragma index_info({index_name})"))
        indexes.append((index_name, bool(index["unique"]), columns))
    return indexes


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
        agent_session_indexes = _index_shapes(conn, "agent_sessions")
        agent_turn_indexes = _index_shapes(conn, "agent_turns")
        agent_tool_indexes = _index_shapes(conn, "agent_tool_events")
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
    assert (
        "idx_agent_turns_session_index",
        True,
        ("agent_session_id", "turn_index"),
    ) in agent_turn_indexes
    assert (
        "idx_agent_tool_events_session_index",
        True,
        ("agent_session_id", "event_index"),
    ) in agent_tool_indexes
    assert not any(
        columns == ("source_type", "agent_session_id")
        for _index_name, _is_unique, columns in agent_session_indexes
    )
