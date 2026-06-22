from __future__ import annotations

from pathlib import Path

from personal_context_node.agent_session_types import AgentSessionDocument, AgentToolEvent, AgentTurn
from personal_context_node.agent_sessions import import_agent_session, render_agent_session_markdown
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all


def _document(source_path: str = "/tmp/session.jsonl", source_sha256: str = "abc123") -> AgentSessionDocument:
    return AgentSessionDocument(
        session_id="thread_1",
        source_type="codex_jsonl",
        source_path=source_path,
        source_sha256=source_sha256,
        originator="Codex Desktop",
        cli_version="0.142.0-alpha.6",
        cwd="/repo",
        model="gpt-5.5",
        started_at="2026-06-22T02:11:53.245Z",
        ended_at="2026-06-22T02:13:01.000Z",
        title="用户问题",
        turns=[
            AgentTurn(
                turn_index=1,
                role="user",
                occurred_at="2026-06-22T02:12:21.053Z",
                text="用户问题",
            ),
            AgentTurn(
                turn_index=2,
                role="assistant",
                occurred_at="2026-06-22T02:13:01.000Z",
                text="助手回答",
            ),
        ],
        tool_events=[
            AgentToolEvent(
                event_index=1,
                occurred_at="2026-06-22T02:12:25.176Z",
                tool_name="exec_command",
                call_id="call_pwd",
                arguments={"cmd": "pwd"},
                output_text=None,
                status="called",
            )
        ],
    )


def test_import_agent_session_persists_rows_and_evidence_refs(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    result = import_agent_session(config=config, document=_document())

    assert result.agent_session_id == "thread_1"
    assert result.sessions_imported == 1
    assert result.turns_imported == 2
    assert result.tool_events_imported == 1
    assert result.evidence_refs_created == 2
    conn = connect(config.database_path)
    try:
        sessions = fetch_all(conn, "select agent_session_id, message_count, tool_event_count from agent_sessions")
        turns = fetch_all(conn, "select agent_turn_id, role, text from agent_turns order by turn_index")
        refs = fetch_all(conn, "select source_type, source_ref, source_id, quote from evidence_refs order by source_id")
    finally:
        conn.close()

    assert sessions == [{"agent_session_id": "thread_1", "message_count": 2, "tool_event_count": 1}]
    assert turns == [
        {"agent_turn_id": "thread_1:turn:1", "role": "user", "text": "用户问题"},
        {"agent_turn_id": "thread_1:turn:2", "role": "assistant", "text": "助手回答"},
    ]
    assert refs == [
        {
            "source_type": "agent_session_turn",
            "source_ref": "codex_jsonl:thread_1:turn:1",
            "source_id": "thread_1:turn:1",
            "quote": "用户问题",
        },
        {
            "source_type": "agent_session_turn",
            "source_ref": "codex_jsonl:thread_1:turn:2",
            "source_id": "thread_1:turn:2",
            "quote": "助手回答",
        },
    ]


def test_import_agent_session_is_idempotent_for_same_thread(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    first = import_agent_session(config=config, document=_document())
    second = import_agent_session(config=config, document=_document(source_sha256="def456"))

    assert first.sessions_imported == 1
    assert second.sessions_imported == 0
    conn = connect(config.database_path)
    try:
        session_count = fetch_all(conn, "select count(*) as count from agent_sessions")
        turn_count = fetch_all(conn, "select count(*) as count from agent_turns")
        ref_count = fetch_all(conn, "select count(*) as count from evidence_refs where source_type = 'agent_session_turn'")
    finally:
        conn.close()

    assert session_count == [{"count": 1}]
    assert turn_count == [{"count": 2}]
    assert ref_count == [{"count": 2}]


def test_render_agent_session_markdown_reads_storage(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    import_agent_session(config=config, document=_document())

    markdown = render_agent_session_markdown(config=config, agent_session_id="thread_1")

    assert "# Codex Session thread_1" in markdown
    assert "## Turns" in markdown
    assert "**user**: 用户问题" in markdown
    assert "**assistant**: 助手回答" in markdown
    assert "## Tool Events" in markdown
    assert "`exec_command`" in markdown
