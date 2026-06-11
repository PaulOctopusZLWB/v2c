from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.obsidian_safety import assert_personal_context_vault


def record_sync_log(
    *,
    config: AppConfig,
    conn,
    day: str,
    source: str,
    target_id: str,
    status: str,
    message: str,
) -> None:
    assert_personal_context_vault(config)
    created_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        insert into sync_logs (sync_log_id, source, target_id, status, message, created_at)
        values (?, ?, ?, ?, ?, ?)
        """,
        (f"sync_{uuid4().hex}", source, target_id, status, message, created_at),
    )
    _append_sync_log_note(
        path=config.obsidian_vault / "90_System" / "Sync_Log" / f"{day}.md",
        day=day,
        source=source,
        target_id=target_id,
        status=status,
        message=message,
        created_at=created_at,
    )


def _append_sync_log_note(
    *,
    path: Path,
    day: str,
    source: str,
    target_id: str,
    status: str,
    message: str,
    created_at: str,
) -> None:
    if path.exists():
        text = path.read_text(encoding="utf-8").rstrip() + "\n"
    else:
        text = "\n".join(
            [
                "---",
                "pcn_schema: markdown_note.v1",
                "note_type: sync_log",
                f"date_key: {day}",
                "generated_by: personal-context-node",
                f"generated_at: {created_at}",
                "pcn_managed: true",
                "---",
                "",
                f"# {day} Sync Log",
                "",
            ]
        )
    entry = "\n".join(
        [
            _block_start(f"sync_log_entry:{target_id}:{created_at}", "managed"),
            f"- created_at: {created_at}",
            f"- source: {source}",
            f"- target_id: {target_id}",
            f"- status: {status}",
            f"- message: {message}",
            _block_end(f"sync_log_entry:{target_id}:{created_at}"),
            "",
        ]
    )
    write_text_atomic(path, text + entry)


def _block_start(block_id: str, kind: str) -> str:
    return f'<!-- pcn:block start id="{block_id}" kind="{kind}" version="1" -->'


def _block_end(block_id: str) -> str:
    return f'<!-- pcn:block end id="{block_id}" -->'
