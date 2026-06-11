from __future__ import annotations

import hashlib
import json
from pathlib import Path

from personal_context_node.adapters.archive.local_filesystem import LocalFilesystemArchiveAdapter
from personal_context_node.archive import archive_completed_audio
from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import EvidenceRef, MemoryCard, SubjectRef, create_signed_event
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_archive_completed_audio_copies_and_hash_verifies(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    _insert_audio(config.database_path, raw_path, _sha256(raw_path), status="imported")
    archive_root = tmp_path / "nas" / "PersonalContext"

    result = archive_completed_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_root),
    )

    assert result.files_archived == 1
    assert result.files_pending == 0
    archived = archive_root / "audio" / "raw" / "2087-05-10" / "sample.wav"
    assert archived.read_bytes() == b"raw audio bytes"

    conn = connect(config.database_path)
    try:
        audio = fetch_all(conn, "select status from audio_files")
        records = fetch_all(
            conn,
            """
            select target_type, target_id, archive_path, status, verified, last_error, created_at, updated_at
            from archive_records
            """,
        )
    finally:
        conn.close()
    assert audio == [{"status": "archived"}]
    assert records == [
        {
            "target_type": "audio_file",
            "target_id": "aud_test",
            "archive_path": str(archived),
            "status": "verified",
            "verified": 1,
            "last_error": None,
            "created_at": records[0]["created_at"],
            "updated_at": records[0]["updated_at"],
        }
    ]
    assert records[0]["created_at"]
    assert records[0]["updated_at"]


def test_archive_unavailable_does_not_mark_audio_archived(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    _insert_audio(config.database_path, raw_path, _sha256(raw_path), status="imported")
    unavailable_root = tmp_path / "missing" / "PersonalContext"

    result = archive_completed_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=unavailable_root, require_existing_root=True),
    )

    assert result.files_archived == 0
    assert result.files_pending == 1
    conn = connect(config.database_path)
    try:
        audio = fetch_all(conn, "select status from audio_files")
        records = fetch_all(conn, "select verified from archive_records")
    finally:
        conn.close()
    assert audio == [{"status": "imported"}]
    assert records == []


def test_archive_completed_audio_exports_signed_events_jsonl(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    _insert_audio(config.database_path, raw_path, _sha256(raw_path), status="imported")
    _insert_signed_memory_event(config.database_path)
    archive_root = tmp_path / "nas" / "PersonalContext"

    result = archive_completed_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_root),
    )

    assert result.files_archived == 1
    assert result.events_archived == 1
    events_path = archive_root / "events" / "signed_events.jsonl"
    exported = events_path.read_text(encoding="utf-8").splitlines()
    assert len(exported) == 1
    assert json.loads(exported[0])["event_type"] == "memory_card.created"

    conn = connect(config.database_path)
    try:
        records = fetch_all(conn, "select target_type, target_id, archive_path, verified from archive_records order by target_type")
    finally:
        conn.close()
    assert {"target_type": "signed_events", "target_id": "all", "archive_path": str(events_path), "verified": 1} in records


def test_archive_completed_audio_exports_transcripts_and_summaries_jsonl(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    _insert_audio(config.database_path, raw_path, _sha256(raw_path), status="imported")
    _insert_transcript_and_summary(config.database_path)
    archive_root = tmp_path / "nas" / "PersonalContext"

    result = archive_completed_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_root),
    )

    assert result.files_archived == 1
    assert result.transcripts_archived == 1
    assert result.summaries_archived == 1
    transcripts_path = archive_root / "derived" / "transcript_segments.jsonl"
    summaries_path = archive_root / "derived" / "summaries.jsonl"
    transcript_rows = [json.loads(line) for line in transcripts_path.read_text(encoding="utf-8").splitlines()]
    summary_rows = [json.loads(line) for line in summaries_path.read_text(encoding="utf-8").splitlines()]
    assert transcript_rows[0]["segment_id"] == "seg_archive_test"
    assert transcript_rows[0]["text"] == "归档转写。"
    assert summary_rows[0]["summary_id"] == "sum_archive_test"

    conn = connect(config.database_path)
    try:
        records = fetch_all(conn, "select target_type, target_id, archive_path, verified from archive_records order by target_type")
    finally:
        conn.close()
    assert {"target_type": "transcript_segments", "target_id": "all", "archive_path": str(transcripts_path), "verified": 1} in records
    assert {"target_type": "summaries", "target_id": "all", "archive_path": str(summaries_path), "verified": 1} in records


def _insert_audio(database_path: Path, raw_path: Path, sha256: str, *, status: str) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_test",
                "DJI Mic 3",
                "/source.wav",
                str(raw_path),
                sha256,
                1000,
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:10:00+08:00",
                status,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _insert_signed_memory_event(database_path: Path) -> None:
    card = MemoryCard(
        card_id="mem_archive_test",
        owner_did="did:key:archive-test",
        claim_type="decision",
        claim="Archive signed events.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_archive_test",
                source_type="transcript_segment",
                source_id="seg_archive_test",
                quote="Archive signed events.",
            )
        ],
    )
    event, public_key = create_signed_event(event_type="memory_card.created", payload=card, signer_did=card.owner_did)
    conn = connect(database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=event, public_key=public_key)
        conn.commit()
    finally:
        conn.close()


def _insert_transcript_and_summary(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_archive_test",
                "aud_test",
                "chk_archive_test",
                0,
                1000,
                "归档转写。",
                "zh",
                "self",
                "ev_archive_test",
                0.99,
                "MockASRAdapter",
                "mock-asr",
                "test",
            ),
        )
        conn.execute(
            """
            insert into summaries (
              summary_id, summary_type, target_type, target_id, prompt_version,
              model_name, content_json, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sum_archive_test",
                "daily",
                "date_key",
                "2087-05-10",
                "llm_port.daily_summary.v1",
                "rule_based",
                json.dumps({"headline": "归档日报", "summary": "归档摘要。"}, ensure_ascii=False, sort_keys=True),
                "2087-05-10T10:00:00+08:00",
                "2087-05-10T10:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
