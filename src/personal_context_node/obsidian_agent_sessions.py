from __future__ import annotations

from datetime import date
from pathlib import Path

from personal_context_node.agent_sessions import render_agent_session_markdown
from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.obsidian_safety import assert_personal_context_vault
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


_BLOCK_START = '<!-- pcn:block start id="agent_session" kind="managed" version="1" -->'
_BLOCK_END = '<!-- pcn:block end id="agent_session" -->'


def publish_agent_session_note(*, config: AppConfig, agent_session_id: str) -> Path:
    assert_personal_context_vault(config)
    day = _agent_session_day(config=config, agent_session_id=agent_session_id)
    markdown = render_agent_session_markdown(config=config, agent_session_id=agent_session_id)
    note_path = config.obsidian_vault / "40_Agent_Sessions" / day / f"{agent_session_id}.md"
    write_text_atomic(note_path, _managed_note_text(markdown))
    return note_path


def _agent_session_day(*, config: AppConfig, agent_session_id: str) -> str:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            "select started_at from agent_sessions where agent_session_id = ?",
            (agent_session_id,),
        )
    finally:
        conn.close()
    if not rows:
        raise ValueError(f"unknown agent session: {agent_session_id}")
    started_at = str(rows[0]["started_at"])
    day = started_at[:10]
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise ValueError(f"invalid started_at for agent session {agent_session_id}: {started_at}") from exc
    return day


def _managed_note_text(markdown: str) -> str:
    body = markdown.rstrip("\n")
    return f"{_BLOCK_START}\n{body}\n{_BLOCK_END}\n"
