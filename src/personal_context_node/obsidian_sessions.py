from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.obsidian_safety import assert_personal_context_vault
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class PublishSessionNotesResult:
    notes_written: int


def publish_session_notes(*, config: AppConfig, day: str, source_run_id: str | None = None) -> PublishSessionNotesResult:
    assert_personal_context_vault(config)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        sessions = fetch_all(
            conn,
            """
            select
              s.session_id, s.date_key, s.started_at, s.ended_at, s.segment_count, s.active_speech_ms,
              sm.content_json as summary_json
            from sessions s
            left join summaries sm
              on sm.summary_type = 'session'
             and sm.target_type = 'session'
             and sm.target_id = s.session_id
             and sm.prompt_version = 'llm_port.session_summary.v1'
            where s.date_key = ?
            order by s.started_at
            """,
            (day,),
        )
    finally:
        conn.close()

    output_dir = config.obsidian_vault / "20_Conversations" / day
    output_dir.mkdir(parents=True, exist_ok=True)
    for session in sessions:
        note_path = output_dir / f"{session['session_id']}.md"
        existing_text = note_path.read_text(encoding="utf-8") if note_path.exists() else None
        write_text_atomic(
            note_path,
            _session_note_text(
                session,
                existing_text=existing_text,
                source_run_id=source_run_id,
            ),
        )
    return PublishSessionNotesResult(notes_written=len(sessions))


def _session_note_text(
    session: dict[str, object],
    *,
    existing_text: str | None = None,
    source_run_id: str | None = None,
) -> str:
    # Per §29.7 the note carries only the session_summary managed block and a user
    # block. The full transcript is intentionally NOT embedded; it stays queryable on
    # demand via `pcn session-transcript`.
    session_id = str(session["session_id"])
    summary_json = session.get("summary_json")
    summary = json.loads(str(summary_json)) if summary_json else None
    title = summary["headline"] if summary else f"Session {session_id}"
    managed_lines = _summary_lines(session, summary)
    user_notes = _existing_user_notes(existing_text)
    return "\n".join(
        [
            "---",
            "pcn_schema: markdown_note.v1",
            "note_type: session",
            f"date_key: {session['date_key']}",
            f"session_id: {session_id}",
            "generated_by: personal-context-node",
            f"generated_at: {datetime.now(timezone.utc).isoformat()}",
            *([f"source_run_id: {source_run_id}"] if source_run_id else []),
            "pcn_managed: true",
            "---",
            "",
            f"# {title}",
            "",
            _block_start("session_summary", "managed"),
            *managed_lines,
            _block_end("session_summary"),
            "",
            "## User Notes",
            "",
            _block_start("user_notes", "user"),
            user_notes,
            _block_end("user_notes"),
        ]
    )


def session_transcript_lines(*, config: AppConfig, session_id: str) -> list[str]:
    """Render a session's full transcript on demand (§29.7: notes never embed it)."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        segments = fetch_all(
            conn,
            """
            select ts.start_ms, ts.end_ms, ts.text, ts.speaker, ts.asr_tags_json
            from transcript_segments ts
            where ts.session_id = ? and ts.is_active = 1
            order by coalesce(ts.absolute_start_at, ''), ts.start_ms, ts.segment_id
            """,
            (session_id,),
        )
    finally:
        conn.close()
    return _transcript_lines(segments)


def _block_start(block_id: str, kind: str) -> str:
    return f'<!-- pcn:block start id="{block_id}" kind="{kind}" version="1" -->'


def _block_end(block_id: str) -> str:
    return f'<!-- pcn:block end id="{block_id}" -->'


def _existing_user_notes(existing_text: str | None) -> str:
    if not existing_text:
        return ""
    patterns = [
        r'<!-- pcn:block start id="user_notes" kind="user" version="1" -->\n?(.*?)\n?<!-- pcn:block end id="user_notes" -->',
        r'<!-- pcn:user start type="user_notes" -->\n?(.*?)\n?<!-- pcn:user end type="user_notes" -->',
    ]
    for pattern in patterns:
        match = re.search(pattern, existing_text, flags=re.DOTALL)
        if match:
            return match.group(1).rstrip("\n")
    return ""


def _summary_lines(session: dict[str, object], summary: dict[str, object] | None) -> list[str]:
    metadata = [
        f"started_at: {session['started_at']}",
        f"ended_at: {session['ended_at']}",
        f"segment_count: {session['segment_count']}",
        f"active_speech_ms: {session['active_speech_ms']}",
        "",
    ]
    if summary is None:
        return metadata
    lines = [
        *metadata,
        f"## {summary['headline']}",
        "",
        str(summary["summary"]),
        "",
    ]
    lines.extend(_item_lines("Decision", summary.get("decisions", [])))
    lines.extend(_todo_lines(summary.get("todos", [])))
    lines.extend(_plain_lines("Open Question", summary.get("open_questions", [])))
    lines.extend(_core_conclusion_lines(summary.get("core_conclusions", [])))
    lines.extend(_per_speaker_lines(summary.get("per_speaker", [])))
    return lines


def _core_conclusion_lines(items: object) -> list[str]:
    conclusions = [str(item) for item in items if str(item).strip()] if isinstance(items, list) else []
    if not conclusions:
        return []
    lines = ["## 核心结论", ""]
    lines.extend(f"- {text}" for text in conclusions)
    lines.append("")
    return lines


def _per_speaker_lines(items: object) -> list[str]:
    speakers = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    if not speakers:
        return []
    lines = ["## 发言人分析", ""]
    for speaker in speakers:
        label = str(speaker.get("speaker_cluster_id") or "unknown")
        lines.append(f"### 发言人 {label}")
        lines.append("")
        lines.extend(_speaker_viewpoint_lines(speaker.get("viewpoints", [])))
        sentiment = str(speaker.get("sentiment") or "").strip()
        if sentiment:
            lines.append(f"- 情绪: {sentiment}")
        stance = str(speaker.get("stance") or "").strip()
        if stance:
            lines.append(f"- 倾向: {stance}")
        needs = [str(need) for need in speaker.get("latent_needs", []) if str(need).strip()] if isinstance(
            speaker.get("latent_needs"), list
        ) else []
        if needs:
            lines.append(f"- 潜在需求: {'、'.join(needs)}")
        lines.append("")
    return lines


def _speaker_viewpoint_lines(items: object) -> list[str]:
    lines: list[str] = []
    viewpoints = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    for viewpoint in viewpoints:
        text = str(viewpoint.get("text") or "").strip()
        if not text:
            continue
        refs = [str(ref) for ref in viewpoint.get("evidence_refs", []) if str(ref).strip()] if isinstance(
            viewpoint.get("evidence_refs"), list
        ) else []
        suffix = f" (refs: {', '.join(refs)})" if refs else ""
        lines.append(f"- 观点: {text}{suffix}")
    return lines


def _transcript_lines(segments: list[dict[str, object]]) -> list[str]:
    lines = ["## Transcript", ""]
    if not segments:
        return [*lines, "暂无转写片段。"]
    for segment in segments:
        start_ms = _int_ms(segment.get("start_ms"))
        end_ms = _int_ms(segment.get("end_ms"))
        speaker = _single_line(segment.get("speaker") or "unknown")
        text = _single_line(segment.get("text") or "")
        tags = _tags_from_json(segment.get("asr_tags_json"))
        tag_suffix = f" _(tags: {', '.join(tags)})_" if tags else ""
        lines.append(f"- `{_format_ms(start_ms)}-{_format_ms(end_ms)}` **{speaker}**: {text}{tag_suffix}")
    return lines


def _int_ms(value: object) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _format_ms(value: int) -> str:
    hours, remainder = divmod(max(0, value), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _tags_from_json(value: object) -> list[str]:
    if value is None:
        return []
    raw = str(value).strip()
    if not raw or raw == "[]":
        return []
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return [_single_line(raw)]
    if not isinstance(decoded, list):
        return [_single_line(decoded)]
    return [_single_line(tag) for tag in decoded if str(tag).strip()]


def _single_line(value: object) -> str:
    return " ".join(str(value).split())


def _item_lines(label: str, items: object) -> list[str]:
    lines: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            lines.append(f"- {label}: {item['text']}")
    if lines:
        lines.append("")
    return lines


def _todo_lines(items: object) -> list[str]:
    lines: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            lines.append(f"- Todo: {item['text']} (owner: {item['owner']})")
    if lines:
        lines.append("")
    return lines


def _plain_lines(label: str, items: object) -> list[str]:
    lines = [f"- {label}: {item}" for item in items if isinstance(item, str)] if isinstance(items, list) else []
    if lines:
        lines.append("")
    return lines
