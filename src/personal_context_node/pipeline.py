from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryCard,
    SubjectRef,
    create_signed_event,
    verify_signed_event,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class FirstMilestoneResult:
    imported_files: int
    transcript_segments: int
    memory_candidates: int
    signed_events: int


def run_first_milestone(
    *,
    config: AppConfig,
    source_dir: Path,
    confirm_first_candidate: bool = False,
) -> FirstMilestoneResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        imported = _import_wavs(conn, config, source_dir)
        _mock_transcribe(conn)
        _create_memory_candidates(conn)
        if confirm_first_candidate:
            _confirm_first_candidate(conn, config.owner_did)
        _publish_daily_notes(conn, config)
        return FirstMilestoneResult(
            imported_files=imported,
            transcript_segments=_count(conn, "transcript_segments"),
            memory_candidates=_count(conn, "memory_candidates"),
            signed_events=_count(conn, "signed_events"),
        )
    finally:
        conn.close()


def _import_wavs(conn: sqlite3.Connection, config: AppConfig, source_dir: Path) -> int:
    imported = 0
    for source_path in sorted(source_dir.glob("*.wav")):
        sha256 = _sha256(source_path)
        existing = conn.execute(
            "select 1 from audio_files where source_path = ? and sha256 = ?",
            (str(source_path), sha256),
        ).fetchone()
        if existing:
            continue
        recorded_date = _recorded_date_from_name(source_path)
        local_dir = config.raw_audio_dir / recorded_date
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / source_path.name
        shutil.copy2(source_path, local_path)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"aud_{uuid4().hex}",
                config.source_device,
                str(source_path),
                str(local_path),
                sha256,
                _duration_ms(source_path),
                f"{recorded_date}T00:00:00+08:00",
                datetime.now(timezone.utc).isoformat(),
                "imported",
            ),
        )
        imported += 1
    conn.commit()
    return imported


def _mock_transcribe(conn: sqlite3.Connection) -> None:
    rows = fetch_all(
        conn,
        """
        select audio_file_id, local_raw_path
        from audio_files
        where audio_file_id not in (select audio_file_id from transcript_segments)
        order by local_raw_path
        """,
    )
    for row in rows:
        source_name = Path(row["local_raw_path"]).name
        segment_id = f"seg_{uuid4().hex}"
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, start_ms, end_ms, text, language, speaker, evidence_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment_id,
                row["audio_file_id"],
                0,
                3000,
                f"模拟转写：{source_name} 需要生成本地上下文和记忆候选。",
                "zh",
                "self",
                f"ev_{segment_id}",
            ),
        )
    conn.commit()


def _create_memory_candidates(conn: sqlite3.Connection) -> None:
    rows = fetch_all(
        conn,
        """
        select ts.segment_id, ts.evidence_id, ts.text
        from transcript_segments ts
        where ts.evidence_id not in (
          select json_extract(value, '$.evidence_id')
          from memory_candidates, json_each(memory_candidates.evidence_refs_json)
        )
        order by ts.segment_id
        """,
    )
    for row in rows:
        evidence = [
            {
                "evidence_id": row["evidence_id"],
                "source_type": "transcript_segment",
                "source_id": row["segment_id"],
                "quote": row["text"],
            }
        ]
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"cand_{uuid4().hex}",
                "用户正在建设 Personal Context Node 的本地音频上下文系统。",
                "observation",
                json.dumps(
                    {"type": "project", "id": "personal_context_node", "label": "Personal Context Node"},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                0.8,
                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                "pending_review",
                None,
            ),
        )
    conn.commit()


def _confirm_first_candidate(conn: sqlite3.Connection, owner_did: str) -> None:
    row = conn.execute(
        """
        select candidate_id, candidate_claim, claim_type, subject_json, evidence_refs_json
        from memory_candidates
        where status = 'pending_review'
        order by candidate_id
        limit 1
        """
    ).fetchone()
    if row is None:
        return
    evidence_refs = [EvidenceRef.model_validate(item) for item in json.loads(row["evidence_refs_json"])]
    card = MemoryCard(
        card_id=f"mem_{uuid4().hex}",
        owner_did=owner_did,
        claim_type=row["claim_type"],
        claim=row["candidate_claim"],
        subject=SubjectRef.model_validate(json.loads(row["subject_json"])),
        evidence_refs=evidence_refs,
        candidate_claim=row["candidate_claim"],
    )
    event, public_key = create_signed_event(
        event_type="memory_card.confirmed.v1",
        payload=card,
        signer_did=owner_did,
    )
    conn.execute(
        """
        insert into signed_events (
          event_id, event_type, signer_did, payload_json, signature, public_key, verified
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.event_type,
            event.signer_did,
            json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
            event.signature,
            public_key,
            1 if verify_signed_event(event, public_key) else 0,
        ),
    )
    conn.execute(
        "update memory_candidates set status = 'confirmed', memory_card_id = ? where candidate_id = ?",
        (card.card_id, row["candidate_id"]),
    )
    conn.commit()


def _publish_daily_notes(conn: sqlite3.Connection, config: AppConfig) -> None:
    for folder in ["00_Inbox", "10_Daily", "20_Conversations", "30_Memory_Candidates", "40_Confirmed_Memory", "90_System"]:
        (config.obsidian_vault / folder).mkdir(parents=True, exist_ok=True)

    rows = fetch_all(
        conn,
        """
        select af.local_raw_path, af.recorded_at, ts.text, ts.speaker, mc.candidate_claim, mc.status
        from audio_files af
        join transcript_segments ts on ts.audio_file_id = af.audio_file_id
        left join memory_candidates mc on mc.evidence_refs_json like '%' || ts.segment_id || '%'
        order by af.recorded_at, af.local_raw_path
        """,
    )
    by_day: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_day.setdefault(row["recorded_at"][:10], []).append(row)

    for day, day_rows in by_day.items():
        note = config.obsidian_vault / "10_Daily" / f"{day}.md"
        lines = [
            f"# {day} Daily Context",
            "",
            "## Metrics",
            f"- Total imported files: {len({row['local_raw_path'] for row in day_rows})}",
            f"- Transcript segments: {len(day_rows)}",
            "",
            "## Transcript",
        ]
        for row in day_rows:
            lines.append(f"- `{Path(row['local_raw_path']).name}` [{row['speaker']}]: {row['text']}")
        lines.extend(["", "## Memory Candidates"])
        for row in day_rows:
            if row["candidate_claim"]:
                lines.append(f"- [{row['status']}] {row['candidate_claim']}")
        note.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        return round(wav.getnframes() / wav.getframerate() * 1000)


def _recorded_date_from_name(path: Path) -> str:
    match = re.search(r"_(\d{8})_", path.name)
    if not match:
        return datetime.now().date().isoformat()
    raw = match.group(1)
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"select count(*) from {table}").fetchone()[0])
