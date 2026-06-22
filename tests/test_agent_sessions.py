from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from personal_context_node.agent_session_types import AgentSessionDocument, AgentToolEvent, AgentTurn
from personal_context_node.agent_sessions import import_agent_session, render_agent_session_markdown
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


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


def _counts(config: AppConfig) -> dict[str, int]:
    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            """
            select
              (select count(*) from agent_sessions) as agent_sessions,
              (select count(*) from agent_turns) as agent_turns,
              (select count(*) from agent_tool_events) as agent_tool_events,
              (select count(*) from evidence_refs where source_type = 'agent_session_turn') as evidence_refs
            """,
        )
    finally:
        conn.close()
    return rows[0]


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


def test_import_agent_session_is_idempotent_for_same_source_identity(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    first = import_agent_session(config=config, document=_document())
    second = import_agent_session(config=config, document=_document())

    assert first.sessions_imported == 1
    assert second.sessions_imported == 0
    assert _counts(config) == {
        "agent_sessions": 1,
        "agent_turns": 2,
        "agent_tool_events": 1,
        "evidence_refs": 2,
    }


@pytest.mark.parametrize(
    "changed_document",
    [
        _document(source_path="/tmp/session-copy.jsonl"),
        _document(source_sha256="def456"),
    ],
    ids=["changed-path", "changed-hash"],
)
def test_import_agent_session_rejects_changed_source_identity(
    tmp_path: Path, changed_document: AgentSessionDocument
) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    import_agent_session(config=config, document=_document())

    with pytest.raises(ValueError, match="source identity differs"):
        import_agent_session(config=config, document=changed_document)

    conn = connect(config.database_path)
    try:
        sessions = fetch_all(conn, "select agent_session_id, source_path, source_sha256 from agent_sessions")
    finally:
        conn.close()

    assert sessions == [
        {"agent_session_id": "thread_1", "source_path": "/tmp/session.jsonl", "source_sha256": "abc123"}
    ]
    assert _counts(config) == {
        "agent_sessions": 1,
        "agent_turns": 2,
        "agent_tool_events": 1,
        "evidence_refs": 2,
    }


def test_import_agent_session_counts_only_created_evidence_refs(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into evidence_refs (
              evidence_id, source_type, source_ref, source_id, owner_id, quote, summary, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ev_thread_1:turn:1",
                "agent_session_turn",
                "codex_jsonl:thread_1:turn:1",
                "thread_1:turn:1",
                None,
                "用户问题",
                None,
                "2026-06-22T02:12:21.053Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = import_agent_session(config=config, document=_document())

    assert result.evidence_refs_created == 1
    assert _counts(config) == {
        "agent_sessions": 1,
        "agent_turns": 2,
        "agent_tool_events": 1,
        "evidence_refs": 2,
    }


@pytest.mark.parametrize(
    ("evidence_id", "source_type", "source_ref", "source_id", "match"),
    [
        ("ev_thread_1:turn:1", "manual_note", "manual:1", "manual:1", "evidence_id"),
        ("ev_other", "agent_session_turn", "codex_jsonl:thread_1:turn:1", "other:turn:1", "source_ref"),
    ],
    ids=["evidence-id-conflict", "source-ref-conflict"],
)
def test_import_agent_session_rejects_conflicting_evidence_ref_and_rolls_back(
    tmp_path: Path,
    evidence_id: str,
    source_type: str,
    source_ref: str,
    source_id: str,
    match: str,
) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into evidence_refs (
              evidence_id, source_type, source_ref, source_id, owner_id, quote, summary, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (evidence_id, source_type, source_ref, source_id, None, "preexisting", None, "2026-06-22T02:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match=match):
        import_agent_session(config=config, document=_document())

    conn = connect(config.database_path)
    try:
        all_counts = fetch_all(
            conn,
            """
            select
              (select count(*) from agent_sessions) as agent_sessions,
              (select count(*) from agent_turns) as agent_turns,
              (select count(*) from agent_tool_events) as agent_tool_events,
              (select count(*) from evidence_refs) as evidence_refs
            """,
        )
        refs = fetch_all(conn, "select evidence_id, source_type, source_ref, source_id, quote from evidence_refs")
    finally:
        conn.close()

    assert all_counts == [{"agent_sessions": 0, "agent_turns": 0, "agent_tool_events": 0, "evidence_refs": 1}]
    assert refs == [
        {
            "evidence_id": evidence_id,
            "source_type": source_type,
            "source_ref": source_ref,
            "source_id": source_id,
            "quote": "preexisting",
        }
    ]


def test_render_agent_session_markdown_reads_storage(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    import_agent_session(config=config, document=_document())

    markdown = render_agent_session_markdown(config=config, agent_session_id="thread_1")

    assert "# Codex Session thread_1" in markdown
    assert "**Title**: 用户问题" in markdown
    assert "## Turns" in markdown
    assert "**user**: 用户问题" in markdown
    assert "**assistant**: 助手回答" in markdown
    assert "## Tool Events" in markdown
    assert "`exec_command`" in markdown


def test_render_agent_session_markdown_uses_safe_blocks_for_complex_text(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    document = AgentSessionDocument(
        session_id="thread_1",
        source_type="codex_jsonl",
        source_path="/tmp/session.jsonl",
        source_sha256="abc123",
        originator="Codex Desktop",
        cli_version="0.142.0-alpha.6",
        cwd="/repo",
        model="gpt-5.5",
        started_at="2026-06-22T02:11:53.245Z",
        ended_at="2026-06-22T02:13:01.000Z",
        title='title: "用户"\n---\n`bad`',
        turns=[
            AgentTurn(
                turn_index=1,
                role="user",
                occurred_at="2026-06-22T02:12:21.053Z",
                text="line 1\nline with `tick`\nline with ``` fence",
            ),
        ],
        tool_events=[
            AgentToolEvent(
                event_index=1,
                occurred_at="2026-06-22T02:12:25.176Z",
                tool_name="exec_command",
                call_id="call_pwd",
                arguments={"cmd": "printf '`'"},
                output_text="output line\n```json\n{}\n```",
                status="completed",
            )
        ],
    )
    import_agent_session(config=config, document=document)

    markdown = render_agent_session_markdown(config=config, agent_session_id="thread_1")

    assert '## Title\n\n```\ntitle: "用户"\n---\n`bad`\n```' in markdown
    assert "**user**:\n  ````\n  line 1\n  line with `tick`\n  line with ``` fence\n  ````" in markdown
    assert "arguments:\n    ```\n    {\"cmd\": \"printf '`'\"}\n    ```" in markdown
    assert "output:\n    ````\n    output line\n    ```json\n    {}\n    ```\n    ````" in markdown


def test_render_agent_session_markdown_quotes_frontmatter_scalars(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    document = replace(
        _document(),
        cwd="/repo\n---\ninjected: true",
        model="gpt-5.5\nmodel_injected: true",
    )
    import_agent_session(config=config, document=document)

    markdown = render_agent_session_markdown(config=config, agent_session_id="thread_1")

    assert markdown.startswith("---\n")
    assert "\n---\n\n# Codex Session thread_1" in markdown
    assert "note_type: agent_session" in markdown
    assert "agent_session_id: thread_1" in markdown
    assert "source_type: codex_jsonl" in markdown
    assert "started_at: 2026-06-22T02:11:53.245Z" in markdown
    assert 'cwd: "/repo\\n---\\ninjected: true"' in markdown
    assert 'model: "gpt-5.5\\nmodel_injected: true"' in markdown
    assert "\ninjected: true\n" not in markdown
    assert "\nmodel_injected: true\n" not in markdown


def test_render_agent_session_markdown_fences_malformed_arguments_json(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    import_agent_session(config=config, document=_document())
    conn = connect(config.database_path)
    try:
        conn.execute(
            "update agent_tool_events set arguments_json = ? where agent_tool_event_id = ?",
            ("not json\n`raw`", "thread_1:tool:1"),
        )
        conn.commit()
    finally:
        conn.close()

    markdown = render_agent_session_markdown(config=config, agent_session_id="thread_1")

    assert "arguments:\n    ```\n    not json\n    `raw`\n    ```" in markdown


def test_render_agent_session_markdown_preserves_fenced_tool_output(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    document = replace(
        _document(),
        tool_events=[
            AgentToolEvent(
                event_index=1,
                occurred_at="2026-06-22T02:12:25.176Z",
                tool_name="exec_command",
                call_id="call_pwd",
                arguments={"cmd": "printf"},
                output_text="  leading\ntrailing  \n",
                status="completed",
            )
        ],
    )
    import_agent_session(config=config, document=document)

    markdown = render_agent_session_markdown(config=config, agent_session_id="thread_1")

    assert "output:\n    ```\n      leading\n    trailing  \n    \n    ```" in markdown
