from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from personal_context_node import llm_results
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.summary_schemas import validate_session_summary
from personal_context_node.transcript_review import reviewed_segments_for_session


# The editable persona/instruction for the session-summary LLM prompt. This is the *instruction*
# half of the GLM wrapper's session system message (scripts/glm_llm_wrapper.py
# build_session_messages); the transcript formatting + closed JSON-schema enforcement stay
# wrapper-owned. When this is sent as `prompt=`, the wrapper substitutes it for its built-in
# persona line and keeps the schema constraints intact.
DEFAULT_SESSION_PROMPT = "你是会话分析助手。只依据给定转写输出 JSON，禁止编造证据。"

# Active task statuses for a summarize_session task that is still in flight (pending → claimed →
# running). Mirrors the statuses used by tasks.py/process_runner.py; a task in any of these is
# "generating" from the viewpoint workspace's perspective.
_ACTIVE_TASK_STATUSES = ("pending", "claimed", "running")


def session_fingerprint(segments: list[dict]) -> str:
    """A stable sha256 hex over the session's segments, in order.

    Each segment contributes ``segment_id|text|speaker_component``, where the speaker
    component is ``person_label or speaker`` so a speaker correction (which surfaces as a
    resolved person_label) marks any prior generated 观点 as stale. Deterministic and
    order-sensitive: reordering, editing text, or relabeling a speaker all change the hash.
    """
    hasher = hashlib.sha256()
    for segment in segments:
        speaker = segment.get("person_label") or segment.get("speaker") or ""
        line = f"{segment.get('segment_id', '')}|{segment.get('text', '')}|{speaker}\n"
        hasher.update(line.encode("utf-8"))
    return hasher.hexdigest()


def viewpoint_state(*, config: AppConfig, session_id: str) -> dict:
    """The READ side of a session's 观点 workspace.

    Combines the live (reviewed) segments, the generated session summary (if any), and the
    edit/publish sidecar row into one payload. ``effective`` is the edited doc when present,
    else the generated doc, else None. ``stale`` is True only when there IS a generated doc
    and a stored fingerprint that no longer matches the live segments.
    """
    rows = reviewed_segments_for_session(config=config, session_id=session_id)
    segments = [
        {
            "segment_id": row["segment_id"],
            "text": row["text"],
            "speaker": row["speaker"],
            "person_label": row["person_label"],
        }
        for row in rows
    ]

    summary = llm_results.session_summary(config=config, session_id=session_id)
    generated = summary["content"] if summary is not None else None
    has_generated = generated is not None

    sidecar = _load_sidecar(config=config, session_id=session_id)
    status = str(sidecar["status"]) if sidecar is not None else "draft"
    edited_json = sidecar["edited_content_json"] if sidecar is not None else None
    edited = json.loads(str(edited_json)) if edited_json else None
    source_fingerprint = sidecar["source_fingerprint"] if sidecar is not None else None
    published_at = sidecar["published_at"] if sidecar is not None else None
    note_path = sidecar["note_path"] if sidecar is not None else None

    effective = edited if edited is not None else generated

    stale = bool(
        has_generated
        and source_fingerprint is not None
        and source_fingerprint != session_fingerprint(segments)
    )

    return {
        "session_id": session_id,
        "segments": segments,
        "has_generated": has_generated,
        "generated": generated,
        "edited": edited,
        "effective": effective,
        "status": status,
        "stale": stale,
        "published_at": published_at,
        "note_path": note_path,
        "prompt": effective_session_prompt(config=config, session_id=session_id),
        "generating": _is_generating(config=config, session_id=session_id),
    }


def _is_generating(*, config: AppConfig, session_id: str) -> bool:
    """True when a summarize_session task for this session is pending/claimed/running."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        placeholders = ", ".join("?" for _ in _ACTIVE_TASK_STATUSES)
        row = conn.execute(
            f"""
            select 1 from tasks
            where task_type = 'summarize_session'
              and target_type = 'session'
              and target_id = ?
              and status in ({placeholders})
            limit 1
            """,
            (session_id, *_ACTIVE_TASK_STATUSES),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def get_session_prompt_template(*, config: AppConfig) -> str:
    """The global session-summary prompt template (app_prompts['session_viewpoint']), else default."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute(
            "select template from app_prompts where kind = 'session_viewpoint'",
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return DEFAULT_SESSION_PROMPT
    template = str(row["template"])
    return template if template.strip() else DEFAULT_SESSION_PROMPT


def set_session_prompt_template(*, config: AppConfig, template: str | None) -> None:
    """Upsert the global template; a None/empty/blank template deletes the row (reset to default)."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        if template is None or not template.strip():
            conn.execute("delete from app_prompts where kind = 'session_viewpoint'")
        else:
            conn.execute(
                """
                insert into app_prompts (kind, template, updated_at)
                values ('session_viewpoint', ?, ?)
                on conflict(kind) do update set
                  template = excluded.template,
                  updated_at = excluded.updated_at
                """,
                (template, _now()),
            )
        conn.commit()
    finally:
        conn.close()


def effective_session_prompt(*, config: AppConfig, session_id: str) -> dict[str, object]:
    """Resolve the prompt for a session: per-session override ?? global template ?? DEFAULT.

    ``default`` is the GLOBAL template (so the UI can offer "reset to global"); ``is_override`` is
    True only when a per-session prompt_override is set.
    """
    global_template = get_session_prompt_template(config=config)
    sidecar = _load_sidecar(config=config, session_id=session_id)
    override = sidecar["prompt_override"] if sidecar is not None else None
    has_override = override is not None and str(override).strip() != ""
    effective = str(override) if has_override else global_template
    return {
        "effective": effective,
        "default": global_template,
        "is_override": has_override,
    }


def record_summary_regenerated(*, config: AppConfig, session_id: str) -> None:
    """Stamp the sidecar after a fresh summarize_session: write the live segments' fingerprint, clear
    any prior manual edit, reset status to 'draft'. The per-session prompt_override is left intact.

    Regenerate discards prior manual edits by design (the frontend warns first). source_fingerprint
    is the fingerprint of the *reviewed* segments, so the new summary reads as not-stale.
    """
    rows = reviewed_segments_for_session(config=config, session_id=session_id)
    segments = [
        {
            "segment_id": row["segment_id"],
            "text": row["text"],
            "speaker": row["speaker"],
            "person_label": row["person_label"],
        }
        for row in rows
    ]
    fingerprint = session_fingerprint(segments)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into session_viewpoint_state
              (session_id, source_fingerprint, edited_content_json, status, updated_at)
            values (?, ?, null, 'draft', ?)
            on conflict(session_id) do update set
              source_fingerprint = excluded.source_fingerprint,
              edited_content_json = null,
              status = 'draft',
              updated_at = excluded.updated_at
            """,
            (session_id, fingerprint, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def set_viewpoint_edit(*, config: AppConfig, session_id: str, content: dict) -> None:
    """Validate + store a manual edit of the session 观点 result (the single source of truth).

    ``content`` is a full session_summary.v1 doc; it is validated with the closed schema FIRST so
    an invalid doc raises (the route maps that to 400) and nothing is stored — keeping the result
    publishable. On success the normalized doc is upserted as ``edited_content_json`` and
    ``status`` flips to 'edited'. The generated baseline + source_fingerprint are left untouched.
    """
    normalized = validate_session_summary(content)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into session_viewpoint_state
              (session_id, edited_content_json, status, updated_at)
            values (?, ?, 'edited', ?)
            on conflict(session_id) do update set
              edited_content_json = excluded.edited_content_json,
              status = 'edited',
              updated_at = excluded.updated_at
            """,
            (session_id, json.dumps(normalized, ensure_ascii=False, sort_keys=True), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def clear_viewpoint_edit(*, config: AppConfig, session_id: str) -> None:
    """Discard a manual edit and revert to the generated baseline: clears ``edited_content_json``
    and resets ``status`` to 'draft'. The generated summary + prompt_override are left intact.
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into session_viewpoint_state
              (session_id, edited_content_json, status, updated_at)
            values (?, null, 'draft', ?)
            on conflict(session_id) do update set
              edited_content_json = null,
              status = 'draft',
              updated_at = excluded.updated_at
            """,
            (session_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def record_viewpoint_published(*, config: AppConfig, session_id: str, note_path: str, published_at: str) -> None:
    """Stamp the sidecar after a manual publish: record note_path + published_at, status='published'."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into session_viewpoint_state
              (session_id, note_path, published_at, status, updated_at)
            values (?, ?, ?, 'published', ?)
            on conflict(session_id) do update set
              note_path = excluded.note_path,
              published_at = excluded.published_at,
              status = 'published',
              updated_at = excluded.updated_at
            """,
            (session_id, note_path, published_at, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def set_session_prompt_override(*, config: AppConfig, session_id: str, template: str | None) -> dict[str, object]:
    """Set (or clear, on None/blank) the per-session prompt_override; returns effective_session_prompt."""
    normalized = template.strip() if isinstance(template, str) and template.strip() else None
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into session_viewpoint_state (session_id, prompt_override, updated_at)
            values (?, ?, ?)
            on conflict(session_id) do update set
              prompt_override = excluded.prompt_override,
              updated_at = excluded.updated_at
            """,
            (session_id, normalized, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return effective_session_prompt(config=config, session_id=session_id)


def set_segment_text(*, config: AppConfig, segment_id: str, text: str) -> bool:
    """Update a transcript segment's text (trimmed). Returns whether a row matched (404 semantics).

    Staleness is derived on read from the live fingerprint, so no extra writes happen here.
    """
    trimmed = text.strip()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        cursor = conn.execute(
            "update transcript_segments set text = ? where segment_id = ?",
            (trimmed, segment_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _load_sidecar(*, config: AppConfig, session_id: str) -> dict | None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute(
            """
            select edited_content_json, prompt_override, status, source_fingerprint,
                   note_path, published_at, updated_at
            from session_viewpoint_state
            where session_id = ?
            """,
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
