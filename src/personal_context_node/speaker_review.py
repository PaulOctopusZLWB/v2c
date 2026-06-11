from __future__ import annotations

import re
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.obsidian_safety import assert_personal_context_vault
from personal_context_node.obsidian_sync_log import record_sync_log
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class SpeakerReviewSyncResult:
    mappings_upserted: int
    segment_overrides_upserted: int


@dataclass(frozen=True)
class SpeakerPerson:
    person_id: str
    display_name: str
    is_self: bool = False


@dataclass(frozen=True)
class ParsedSpeakerReview:
    mappings: dict[str, str]
    persons: dict[str, SpeakerPerson]
    segment_overrides: dict[str, str]


def publish_speaker_review(*, config: AppConfig, day: str, source_run_id: str | None = None) -> Path:
    assert_personal_context_vault(config)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        speakers = fetch_all(
            conn,
            """
            select distinct ts.speaker
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            join sessions s on s.session_id = ts.session_id
            where s.date_key = ?
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
            join sessions s on s.session_id = ts.session_id
            where s.date_key = ?
            order by s.started_at, ts.start_ms, ts.segment_id
            """,
            (day,),
        )
    finally:
        conn.close()

    review_dir = config.obsidian_vault / "90_System" / "Speaker_Review"
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = review_dir / f"{day}.md"
    lines = [
        "---",
        "pcn_schema: markdown_note.v1",
        "note_type: speaker_review",
        f"date_key: {day}",
        "generated_by: personal-context-node",
        f"generated_at: {datetime.now(timezone.utc).isoformat()}",
        *([f"source_run_id: {source_run_id}"] if source_run_id else []),
        "pcn_managed: true",
        "---",
        "",
        f"# {day} Speaker Review",
        "",
        f'<!-- pcn:speaker_mapping start date_key="{day}" version="1" -->',
        "```yaml",
        "mappings:",
    ]
    for row in speakers:
        lines.append(f"  {row['speaker']}: {_default_person_id(str(row['speaker']))}")
    lines.extend(
        [
            "persons:",
            "  per_self:",
            "    display_name: self",
            "    is_self: true",
            "  per_unknown:",
            "    display_name: unknown",
            "    is_self: false",
            "segment_overrides: {}",
            "```",
            f'<!-- pcn:speaker_mapping end date_key="{day}" -->',
            "",
            "## Segments",
            "",
        ]
    )
    for row in segments:
        lines.append(f"- {row['segment_id']} | {row['speaker']} | {row['text']}")
    write_text_atomic(review_path, "\n".join(lines) + "\n")
    return review_path


def sync_speaker_review(*, config: AppConfig, day: str) -> SpeakerReviewSyncResult:
    assert_personal_context_vault(config)
    review_path = config.obsidian_vault / "90_System" / "Speaker_Review" / f"{day}.md"
    if not review_path.exists():
        return SpeakerReviewSyncResult(mappings_upserted=0, segment_overrides_upserted=0)
    if _within_edit_grace(review_path, edit_grace_seconds=config.edit_grace_seconds):
        conn = connect(config.database_path)
        try:
            initialize(conn)
            record_sync_log(
                config=config,
                conn=conn,
                day=day,
                source="speaker_mapping_review",
                target_id=day,
                status="skipped",
                message=f"review file modified within edit grace: {day}",
            )
            conn.commit()
        finally:
            conn.close()
        return SpeakerReviewSyncResult(mappings_upserted=0, segment_overrides_upserted=0)

    text = review_path.read_text(encoding="utf-8")
    mapping_block = _speaker_mapping_block(text)
    if mapping_block is None:
        return SpeakerReviewSyncResult(mappings_upserted=0, segment_overrides_upserted=0)
    try:
        parsed = _parse_speaker_review(mapping_block)
    except yaml.YAMLError:
        conn = connect(config.database_path)
        try:
            initialize(conn)
            record_sync_log(
                config=config,
                conn=conn,
                day=day,
                source="speaker_mapping_review",
                target_id=day,
                status="failed",
                message=f"yaml parse failed: {day}",
            )
            conn.commit()
        finally:
            conn.close()
        return SpeakerReviewSyncResult(mappings_upserted=0, segment_overrides_upserted=0)
    unknown_person_ids = _unknown_person_references(parsed)
    if unknown_person_ids:
        conn = connect(config.database_path)
        try:
            initialize(conn)
            record_sync_log(
                config=config,
                conn=conn,
                day=day,
                source="speaker_mapping_review",
                target_id=day,
                status="failed",
                message=f"unknown person reference: {', '.join(unknown_person_ids)}",
            )
            conn.commit()
        finally:
            conn.close()
        return SpeakerReviewSyncResult(mappings_upserted=0, segment_overrides_upserted=0)
    mappings = {speaker: person_id for speaker, person_id in parsed.mappings.items() if person_id in parsed.persons}
    overrides = {segment_id: person_id for segment_id, person_id in parsed.segment_overrides.items() if person_id in parsed.persons}
    now = datetime.now(timezone.utc).isoformat()

    conn = connect(config.database_path)
    try:
        initialize(conn)
        for speaker, person_id in mappings.items():
            person = parsed.persons[person_id]
            _upsert_person(conn, person=person, now=now)
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
                (speaker, f"spmap_{speaker}", person.display_name, speaker, person.person_id, 1.0, "speaker_review", now, now),
            )
        for segment_id, person_id in overrides.items():
            person = parsed.persons[person_id]
            _upsert_person(conn, person=person, now=now)
            conn.execute(
                """
                insert into segment_person_overrides (segment_id, person_label, updated_at, person_id)
                values (?, ?, ?, ?)
                on conflict(segment_id) do update set
                  person_label = excluded.person_label,
                  updated_at = excluded.updated_at,
                  person_id = excluded.person_id
                """,
                (segment_id, person.display_name, now, person.person_id),
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
              coalesce(override.person_id, mapping.person_id) as person_id,
              ts.text,
              ts.start_ms,
              ts.end_ms
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            join sessions s on s.session_id = ts.session_id
            left join speaker_mappings mapping on mapping.speaker = ts.speaker
            left join segment_person_overrides override on override.segment_id = ts.segment_id
            where s.date_key = ?
            order by s.started_at, ts.start_ms, ts.segment_id
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


def _unknown_person_references(parsed: ParsedSpeakerReview) -> list[str]:
    referenced = set(parsed.mappings.values()) | set(parsed.segment_overrides.values())
    return sorted(person_id for person_id in referenced if person_id not in parsed.persons)


def _within_edit_grace(path: Path, *, edit_grace_seconds: int) -> bool:
    if edit_grace_seconds <= 0:
        return False
    return time.time() - path.stat().st_mtime < edit_grace_seconds


def _parse_speaker_review(text: str) -> ParsedSpeakerReview:
    yaml_data = _speaker_mapping_yaml(text)
    if yaml_data is not None:
        return _parse_yaml_speaker_review(yaml_data)
    mappings_by_label = _parse_mappings(text)
    raw_overrides = _parse_overrides(text)
    persons: dict[str, SpeakerPerson] = {}
    mappings: dict[str, str] = {}
    for speaker, label in mappings_by_label.items():
        person_id = _person_id_for_label(label)
        persons[person_id] = SpeakerPerson(person_id=person_id, display_name=label, is_self=person_id == "per_self")
        mappings[speaker] = person_id
    segment_overrides: dict[str, str] = {}
    for segment_id, (speaker, label) in raw_overrides.items():
        if label in {"self", "unknown"} or mappings_by_label.get(speaker, speaker) == label:
            continue
        person_id = _person_id_for_label(label)
        persons[person_id] = SpeakerPerson(person_id=person_id, display_name=label, is_self=person_id == "per_self")
        segment_overrides[segment_id] = person_id
    return ParsedSpeakerReview(mappings=mappings, persons=persons, segment_overrides=segment_overrides)


def _speaker_mapping_yaml(text: str) -> dict[str, object] | None:
    match = re.search(r"```yaml\n(?P<body>.*?)\n```", text, flags=re.DOTALL)
    if not match:
        return None
    loaded = yaml.safe_load(match.group("body"))
    return loaded if isinstance(loaded, dict) else None


def _parse_yaml_speaker_review(data: dict[str, object]) -> ParsedSpeakerReview:
    persons: dict[str, SpeakerPerson] = {}
    raw_persons = data.get("persons")
    if isinstance(raw_persons, dict):
        for person_id, raw_person in raw_persons.items():
            if not isinstance(person_id, str) or not isinstance(raw_person, dict):
                continue
            display_name = raw_person.get("display_name")
            if not isinstance(display_name, str) or not display_name:
                continue
            persons[person_id] = SpeakerPerson(
                person_id=person_id,
                display_name=display_name,
                is_self=bool(raw_person.get("is_self", False)),
            )
    mappings = _string_map(data.get("mappings"))
    segment_overrides = _string_map(data.get("segment_overrides"))
    return ParsedSpeakerReview(mappings=mappings, persons=persons, segment_overrides=segment_overrides)


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, str)}


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


def _default_person_id(speaker: str) -> str:
    return "per_self" if speaker in {"self", "spk_self"} else "per_unknown"


def _upsert_person(conn, *, person: SpeakerPerson, now: str) -> None:
    conn.execute(
        """
        insert into persons (person_id, display_name, person_type, is_self, public_identity_id, created_at, updated_at)
        values (?, ?, ?, ?, ?, ?, ?)
        on conflict(person_id) do update set
          display_name = excluded.display_name,
          updated_at = excluded.updated_at
        """,
        (
            person.person_id,
            person.display_name,
            "self" if person.is_self else "local",
            1 if person.is_self else 0,
            None,
            now,
            now,
        ),
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
