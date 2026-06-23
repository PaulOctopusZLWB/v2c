from __future__ import annotations

from datetime import date
from pathlib import Path

from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


DEFAULT_SUPCON_VAULT = Path("/Users/paul/Documents/Obsidian/Supcon")
INDEX_DIR_NAME = "Codex 工作会话"


def publish_supcon_agent_session_indexes(
    *,
    config: AppConfig,
    days: list[str],
    supcon_vault: Path = DEFAULT_SUPCON_VAULT,
) -> list[Path]:
    _assert_supcon_vault(supcon_vault)
    paths: list[Path] = []
    for day in sorted(set(days)):
        note_path = _index_note_path(supcon_vault=supcon_vault, day=day)
        write_text_atomic(note_path, render_supcon_agent_session_index(config=config, day=day))
        paths.append(note_path)
    return paths


def render_supcon_agent_session_index(*, config: AppConfig, day: str) -> str:
    date.fromisoformat(day)
    rows = _session_rows(config=config, day=day)
    lines = [
        "---",
        "note_type: codex_agent_session_index",
        f"date: {day}",
        "pcn_managed: true",
        "---",
        "",
        f"# Codex 工作会话 {day}",
        "",
        '<!-- pcn:block start id="codex_agent_sessions" kind="managed" version="1" -->',
        "",
        "## Sessions",
        "",
    ]
    if not rows:
        lines.append("- No Codex sessions imported for this date.")
    for session in rows:
        lines.extend(_session_lines(config=config, session=session))
    lines.extend(["", '<!-- pcn:block end id="codex_agent_sessions" -->', ""])
    return "\n".join(lines)


def _session_rows(*, config: AppConfig, day: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select agent_session_id, source_type, source_path, source_sha256,
                   cwd, model, started_at, ended_at, title,
                   message_count, tool_event_count
            from agent_sessions
            where substr(started_at, 1, 10) = ?
            order by started_at, agent_session_id
            """,
            (day,),
        )
    finally:
        conn.close()


def _session_lines(*, config: AppConfig, session: dict[str, object]) -> list[str]:
    session_id = str(session["agent_session_id"])
    lines = [
        f"### {session_id}",
        "",
        f"- agent_session_id: {session_id}",
        f"- started_at: {_one_line(session['started_at'])}",
        f"- source_type: {_one_line(session['source_type'])}",
        f"- source_path: {_one_line(session['source_path'], limit=500)}",
        f"- source_sha256: {_one_line(session['source_sha256'], limit=128)}",
        f"- cwd: {_one_line(session.get('cwd') or '', limit=500)}",
        f"- model: {_one_line(session.get('model') or '')}",
        f"- message_count: {int(session['message_count'])}",
        f"- tool_event_count: {int(session['tool_event_count'])}",
    ]
    if session.get("title"):
        lines.append(f"- title: {_one_line(session['title'], limit=240)}")
    lines.extend(["", "#### Key Turns", ""])
    for turn in _turn_rows(config=config, session_id=session_id):
        turn_id = f"{session_id}:turn:{turn['turn_index']}"
        lines.append(
            f"- {turn_id} | {turn['role']} | {_one_line(turn['occurred_at'])} | "
            f"{_one_line(turn['text'], limit=240)}"
        )
    lines.append("")
    return lines


def _turn_rows(*, config: AppConfig, session_id: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select turn_index, role, occurred_at, text
            from agent_turns
            where agent_session_id = ?
            order by turn_index
            limit 5
            """,
            (session_id,),
        )
    finally:
        conn.close()


def _assert_supcon_vault(supcon_vault: Path) -> None:
    vault = supcon_vault.expanduser()
    if not (vault / ".obsidian").is_dir():
        raise ValueError(f"Supcon Obsidian vault not found: {vault}")


def _index_note_path(*, supcon_vault: Path, day: str) -> Path:
    date.fromisoformat(day)
    output_dir = supcon_vault.expanduser() / INDEX_DIR_NAME
    note_path = output_dir / f"{day}.md"
    resolved_output = output_dir.resolve(strict=False)
    resolved_note = note_path.resolve(strict=False)
    try:
        resolved_note.relative_to(resolved_output)
    except ValueError as exc:
        raise ValueError(f"Supcon index note path escapes output directory: {day}") from exc
    return note_path


def _one_line(value: object, *, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
