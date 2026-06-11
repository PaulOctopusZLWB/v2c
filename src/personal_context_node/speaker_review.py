from __future__ import annotations

import re
import hashlib
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
    mapping_block = _speaker_mapping_block(text)
    if mapping_block is None:
        return SpeakerReviewSyncResult(mappings_upserted=0, segment_overrides_upserted=0)
    mappings = _parse_mappings(mapping_block)
    raw_overrides = _parse_overrides(mapping_block)
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
            person_id = _person_id_for_label(person)
            _upsert_person(conn, person_id=person_id, display_name=person, now=now)
            _upsert_speaker_cluster(conn, speaker=speaker, now=now)
            conn.execute(
                """
                insert into speaker_mappings (
                  speaker, speaker_mapping_id, person_label, speaker_cluster_id, person_id,
                  confidence, source, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(speaker) do update set
                  speaker_mapping_id = excluded.speaker_mapping_id,
                  person_label = excluded.person_label,
                  speaker_cluster_id = excluded.speaker_cluster_id,
                  person_id = excluded.person_id,
                  confidence = excluded.confidence,
                  source = excluded.source,
                  updated_at = excluded.updated_at
                """,
                (speaker, f"spmap_{speaker}", person, speaker, person_id, 1.0, "speaker_review", now, now),
            )
        for segment_id, person in overrides.items():
            person_id = _person_id_for_label(person)
            _upsert_person(conn, person_id=person_id, display_name=person, now=now)
            conn.execute(
                """
                insert into segment_person_overrides (segment_id, person_label, updated_at, person_id)
                values (?, ?, ?, ?)
                on conflict(segment_id) do update set
                  person_label = excluded.person_label,
                  updated_at = excluded.updated_at,
                  person_id = excluded.person_id
                """,
                (segment_id, person, now, person_id),
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


def _speaker_mapping_block(text: str) -> str | None:
    match = re.search(
        r'<!--\s*pcn:speaker_mapping start\b[^>]*-->(?P<body>.*?)<!--\s*pcn:speaker_mapping end\b[^>]*-->',
        text,
        flags=re.DOTALL,
    )
    if not match:
        return None
    return match.group("body")


def _person_id_for_label(label: str) -> str:
    if label == "self":
        return "per_self"
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:16]
    return f"per_{digest}"


def _upsert_person(conn, *, person_id: str, display_name: str, now: str) -> None:
    conn.execute(
        """
        insert into persons (person_id, display_name, person_type, is_self, public_identity_id, created_at, updated_at)
        values (?, ?, ?, ?, ?, ?, ?)
        on conflict(person_id) do update set
          display_name = excluded.display_name,
          updated_at = excluded.updated_at
        """,
        (person_id, display_name, "self" if person_id == "per_self" else "local", 1 if person_id == "per_self" else 0, None, now, now),
    )


def _upsert_speaker_cluster(conn, *, speaker: str, now: str) -> None:
    conn.execute(
        """
        insert into speaker_clusters (speaker_cluster_id, label, source_type, source_ref, created_at)
        values (?, ?, ?, ?, ?)
        on conflict(speaker_cluster_id) do nothing
        """,
        (speaker, speaker, "transcript_speaker", speaker, now),
    )


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
