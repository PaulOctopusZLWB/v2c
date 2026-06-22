from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from personal_context_node.agent_sessions import render_agent_session_markdown
from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.obsidian_safety import assert_personal_context_vault
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


_BLOCK_START = '<!-- pcn:block start id="agent_session" kind="managed" version="1" -->'
_BLOCK_END = '<!-- pcn:block end id="agent_session" -->'
_SAFE_AGENT_SESSION_ID = re.compile(r"[A-Za-z0-9._-]+")


def publish_agent_session_note(*, config: AppConfig, agent_session_id: str) -> Path:
    assert_personal_context_vault(config)
    if not config.database_path.exists():
        raise ValueError(f"agent session store not found: {config.database_path}")
    day = _agent_session_day(config=config, agent_session_id=agent_session_id)
    markdown = render_agent_session_markdown(config=config, agent_session_id=agent_session_id)
    note_path = _note_path(config=config, day=day, agent_session_id=agent_session_id)
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
    frontmatter, body = _split_frontmatter(markdown)
    return f"{frontmatter}{_BLOCK_START}\n{body}\n{_BLOCK_END}\n"


def _note_path(*, config: AppConfig, day: str, agent_session_id: str) -> Path:
    output_dir = config.obsidian_vault / "40_Agent_Sessions" / day
    note_path = output_dir / f"{_safe_agent_session_filename(agent_session_id)}.md"
    resolved_output_dir = output_dir.resolve(strict=False)
    resolved_note_path = note_path.resolve(strict=False)
    try:
        resolved_note_path.relative_to(resolved_output_dir)
    except ValueError as exc:
        raise ValueError(f"agent session note path escapes output directory: {agent_session_id}") from exc
    return note_path


def _safe_agent_session_filename(agent_session_id: str) -> str:
    if not _SAFE_AGENT_SESSION_ID.fullmatch(agent_session_id) or agent_session_id in {".", ".."}:
        raise ValueError(f"unsafe agent session id for note filename: {agent_session_id}")
    return agent_session_id


def _split_frontmatter(markdown: str) -> tuple[str, str]:
    if not markdown.startswith("---\n"):
        return "", markdown.rstrip("\n")
    end = markdown.find("\n---\n", 4)
    if end == -1:
        return "", markdown.rstrip("\n")
    frontmatter = markdown[: end + len("\n---\n")]
    body = markdown[end + len("\n---\n") :].lstrip("\n").rstrip("\n")
    return frontmatter, body
