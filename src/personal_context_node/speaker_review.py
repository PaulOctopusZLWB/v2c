from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class SpeakerReviewSyncResult:
    mappings_upserted: int
    segment_overrides_upserted: int


def publish_speaker_review(*, config: AppConfig, day: str) -> Path:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        speakers = fetch_all(
            conn,
            """
            select distinct ts.speaker
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            where substr(af.recorded_at, 1, 10) = ?
            order by ts.speaker
            """,
            (day,),
        )
        segments = fetch_all(
            conn,
            """
            select ts.segment_id, ts.speaker, ts.text
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            where substr(af.recorded_at, 1, 10) = ?
            order by ts.start_ms, ts.segment_id
            """,
            (day,),
        )
    finally:
        conn.close()

    review_dir = config.obsidian_vault / "90_System" / "Speaker_Review"
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = review_dir / f"{day}.md"
    lines = [
        f"# {day} Speaker Review",
        "",
        f'<!-- pcn:speaker_mapping start date_key="{day}" version="1" -->',
        "## Speaker Mapping",
        "",
    ]
    for row in speakers:
        default_person = "self" if row["speaker"] in {"self", "spk_self"} else "unknown"
        lines.append(f"- {row['speaker']}: {default_person}")
    lines.extend(["", "## Segment Overrides", ""])
    for row in segments:
        default_person = "self" if row["speaker"] in {"self", "spk_self"} else "unknown"
        lines.append(f"<!-- segment_id: {row['segment_id']} -->")
        lines.append(f"{row['speaker']} -> {default_person}: {row['text']}")
    lines.extend(["", f'<!-- pcn:speaker_mapping end date_key="{day}" -->'])
    review_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return review_path


def sync_speaker_review(*, config: AppConfig, day: str) -> SpeakerReviewSyncResult:
    review_path = config.obsidian_vault / "90_System" / "Speaker_Review" / f"{day}.md"
    if not review_path.exists():
        return SpeakerReviewSyncResult(mappings_upserted=0, segment_overrides_upserted=0)

    text = review_path.read_text(encoding="utf-8")
    mappings = _parse_mappings(text)
    raw_overrides = _parse_overrides(text)
    overrides = {
        segment_id: person
        for segment_id, (speaker, person) in raw_overrides.items()
        if person not in {"self", "unknown"} and mappings.get(speaker, speaker) != person
    }
    now = datetime.now(timezone.utc).isoformat()

    conn = connect(config.database_path)
    try:
        initialize(conn)
        for speaker, person in mappings.items():
            conn.execute(
                """
                insert into speaker_mappings (speaker, person_label, updated_at)
                values (?, ?, ?)
                on conflict(speaker) do update set
                  person_label = excluded.person_label,
                  updated_at = excluded.updated_at
                """,
                (speaker, person, now),
            )
        for segment_id, person in overrides.items():
            conn.execute(
                """
                insert into segment_person_overrides (segment_id, person_label, updated_at)
                values (?, ?, ?)
                on conflict(segment_id) do update set
                  person_label = excluded.person_label,
                  updated_at = excluded.updated_at
                """,
                (segment_id, person, now),
            )
        conn.commit()
    finally:
        conn.close()
    return SpeakerReviewSyncResult(
        mappings_upserted=len(mappings),
        segment_overrides_upserted=len(overrides),
    )


def materialized_transcript_segments(*, config: AppConfig, day: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select
              ts.segment_id,
              ts.speaker,
              coalesce(override.person_label, mapping.person_label, ts.speaker) as effective_person,
              ts.text,
              ts.start_ms,
              ts.end_ms
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            left join speaker_mappings mapping on mapping.speaker = ts.speaker
            left join segment_person_overrides override on override.segment_id = ts.segment_id
            where substr(af.recorded_at, 1, 10) = ?
            order by ts.start_ms, ts.segment_id
            """,
            (day,),
        )
    finally:
        conn.close()


def _parse_mappings(text: str) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"-\s+([^:]+):\s*(.+)\s*$", line)
        if match:
            mappings[match.group(1).strip()] = match.group(2).strip()
    return mappings


def _parse_overrides(text: str) -> dict[str, tuple[str, str]]:
    overrides: dict[str, tuple[str, str]] = {}
    current_segment_id: str | None = None
    for line in text.splitlines():
        marker = re.match(r"<!--\s*segment_id:\s*([^ ]+)\s*-->", line)
        if marker:
            current_segment_id = marker.group(1)
            continue
        override = re.match(r"([^ ]+)\s*->\s*([^:]+):", line)
        if current_segment_id and override:
            overrides[current_segment_id] = (override.group(1).strip(), override.group(2).strip())
            current_segment_id = None
    return overrides
