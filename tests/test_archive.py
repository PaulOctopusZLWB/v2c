from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.adapters.archive.local_filesystem import LocalFilesystemArchiveAdapter
from personal_context_node.archive import archive_completed_audio, cleanup_archived_audio, mark_cleanup_eligible_audio
from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import EvidenceRef, MemoryCard, SubjectRef, create_signed_event
from personal_context_node.core.ports.archive import ArchiveResult
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
        records = fetch_all(conn, "select status, verified, last_error from archive_records")
    finally:
        conn.close()
    assert audio == [{"status": "imported"}]
    assert records == [{"status": "pending", "verified": 0, "last_error": "archive root unavailable"}]


def test_archive_completed_audio_records_pending_error_when_archive_rejects_file(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    _insert_audio(config.database_path, raw_path, _sha256(raw_path), status="imported")

    result = archive_completed_audio(config=config, archive=RejectingArchive(root=tmp_path / "nas"))

    assert result.files_archived == 0
    assert result.files_pending == 1
    conn = connect(config.database_path)
    try:
        audio = fetch_all(conn, "select status from audio_files")
        records = fetch_all(
            conn,
            "select target_type, target_id, archive_path, status, verified, last_error from archive_records",
        )
    finally:
        conn.close()
    assert audio == [{"status": "imported"}]
    assert records == [
        {
            "target_type": "audio_file",
            "target_id": "aud_test",
            "archive_path": str(tmp_path / "nas" / "audio" / "raw" / "2087-05-10" / "sample.wav"),
            "status": "pending",
            "verified": 0,
            "last_error": "permission denied",
        }
    ]


def test_archive_completed_audio_retries_pending_audio_record_to_verified(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    _insert_audio(config.database_path, raw_path, _sha256(raw_path), status="imported")
    archive_root = tmp_path / "nas"

    first = archive_completed_audio(config=config, archive=RejectingArchive(root=archive_root))
    second = archive_completed_audio(config=config, archive=LocalFilesystemArchiveAdapter(root=archive_root))

    assert first.files_pending == 1
    assert second.files_archived == 1
    conn = connect(config.database_path)
    try:
        audio = fetch_all(conn, "select status from audio_files")
        records = fetch_all(conn, "select status, verified, last_error from archive_records")
    finally:
        conn.close()
    assert audio == [{"status": "archived"}]
    assert records == [{"status": "verified", "verified": 1, "last_error": None}]


def test_archive_completed_audio_only_processes_imported_audio(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    removed_raw = _write_raw(config, "removed.wav", b"removed local audio")
    imported_raw = _write_raw(config, "imported.wav", b"imported local audio")
    removed_sha256 = _sha256(removed_raw)
    imported_sha256 = _sha256(imported_raw)
    removed_raw.unlink()
    _insert_audio(config.database_path, removed_raw, removed_sha256, status="locally_removed", audio_file_id="aud_removed")
    _insert_audio(config.database_path, imported_raw, imported_sha256, status="imported", audio_file_id="aud_imported")
    archive_root = tmp_path / "nas" / "PersonalContext"

    result = archive_completed_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_root),
    )

    assert result.files_archived == 1
    assert result.files_pending == 0
    assert (archive_root / "audio" / "raw" / "2087-05-10" / "imported.wav").exists()
    assert not (archive_root / "audio" / "raw" / "2087-05-10" / "removed.wav").exists()


def test_local_filesystem_archive_adapter_verifies_existing_archive(tmp_path: Path) -> None:
    archive_root = tmp_path / "nas" / "PersonalContext"
    archive_path = archive_root / "audio" / "raw" / "2087-05-10" / "sample.wav"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(b"raw audio bytes")
    expected_sha256 = _sha256(archive_path)
    adapter = LocalFilesystemArchiveAdapter(root=archive_root)

    ok = adapter.verify_file(archive_path=archive_path, expected_sha256=expected_sha256)
    mismatch = adapter.verify_file(archive_path=archive_path, expected_sha256="sha256:bad")
    missing = adapter.verify_file(archive_path=archive_root / "missing.wav", expected_sha256=expected_sha256)

    assert ok.verified is True
    assert ok.archive_path == archive_path
    assert mismatch.verified is False
    assert mismatch.reason == "hash mismatch"
    assert missing.verified is False
    assert missing.reason == "archive file missing"


def test_local_filesystem_archive_adapter_returns_unverified_on_copy_error(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive-root-file"
    archive_root.write_text("not a directory", encoding="utf-8")
    source = tmp_path / "sample.wav"
    source.write_bytes(b"raw audio bytes")
    adapter = LocalFilesystemArchiveAdapter(root=archive_root)

    result = adapter.archive_file(
        source_path=source,
        relative_path=Path("audio/raw/sample.wav"),
        expected_sha256=_sha256(source),
    )

    assert result.verified is False
    assert result.archive_path == archive_root / "audio" / "raw" / "sample.wav"
    assert result.reason


def test_cleanup_archived_audio_removes_only_verified_retained_local_files(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    archive_root = tmp_path / "nas" / "PersonalContext"
    old_raw = _write_raw(config, "old.wav", b"old raw audio")
    recent_raw = _write_raw(config, "recent.wav", b"recent raw audio")
    imported_raw = _write_raw(config, "imported.wav", b"imported raw audio")
    _insert_audio(config.database_path, old_raw, _sha256(old_raw), status="cleanup_eligible", audio_file_id="aud_old")
    _insert_audio(config.database_path, recent_raw, _sha256(recent_raw), status="archived", audio_file_id="aud_recent")
    _insert_audio(config.database_path, imported_raw, _sha256(imported_raw), status="imported", audio_file_id="aud_imported")
    old_archive = _copy_archive(archive_root, old_raw)
    recent_archive = _copy_archive(archive_root, recent_raw)
    _insert_archive_record(config.database_path, audio_file_id="aud_old", source_path=old_raw, archive_path=old_archive, sha256=_sha256(old_archive), archived_at="2087-05-01T00:00:00+00:00")
    _insert_archive_record(config.database_path, audio_file_id="aud_recent", source_path=recent_raw, archive_path=recent_archive, sha256=_sha256(recent_archive), archived_at="2087-05-09T00:00:00+00:00")

    result = cleanup_archived_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_root),
        archived_before=datetime(2087, 5, 5, tzinfo=timezone.utc),
    )

    assert result.files_removed == 1
    assert result.files_pending == 0
    assert not old_raw.exists()
    assert recent_raw.exists()
    assert imported_raw.exists()
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select audio_file_id, status from audio_files order by audio_file_id")
    finally:
        conn.close()
    assert rows == [
        {"audio_file_id": "aud_imported", "status": "imported"},
        {"audio_file_id": "aud_old", "status": "locally_removed"},
        {"audio_file_id": "aud_recent", "status": "archived"},
    ]


def test_mark_cleanup_eligible_audio_marks_verified_retained_files(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    archive_root = tmp_path / "nas" / "PersonalContext"
    old_raw = _write_raw(config, "old.wav", b"old raw audio")
    recent_raw = _write_raw(config, "recent.wav", b"recent raw audio")
    _insert_audio(config.database_path, old_raw, _sha256(old_raw), status="archived", audio_file_id="aud_old")
    _insert_audio(config.database_path, recent_raw, _sha256(recent_raw), status="archived", audio_file_id="aud_recent")
    old_archive = _copy_archive(archive_root, old_raw)
    recent_archive = _copy_archive(archive_root, recent_raw)
    _insert_archive_record(config.database_path, audio_file_id="aud_old", source_path=old_raw, archive_path=old_archive, sha256=_sha256(old_archive), archived_at="2087-05-01T00:00:00+00:00")
    _insert_archive_record(config.database_path, audio_file_id="aud_recent", source_path=recent_raw, archive_path=recent_archive, sha256=_sha256(recent_archive), archived_at="2087-05-09T00:00:00+00:00")

    result = mark_cleanup_eligible_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_root),
        archived_before=datetime(2087, 5, 5, tzinfo=timezone.utc),
    )

    assert result.files_marked == 1
    assert result.files_pending == 1
    assert old_raw.exists()
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select audio_file_id, status from audio_files order by audio_file_id")
    finally:
        conn.close()
    assert rows == [
        {"audio_file_id": "aud_old", "status": "cleanup_eligible"},
        {"audio_file_id": "aud_recent", "status": "archived"},
    ]


def test_cleanup_archived_audio_does_not_remove_archived_before_eligibility(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    archive_root = tmp_path / "nas" / "PersonalContext"
    raw_path = _write_raw(config, "old.wav", b"old raw audio")
    _insert_audio(config.database_path, raw_path, _sha256(raw_path), status="archived", audio_file_id="aud_old")
    archive_path = _copy_archive(archive_root, raw_path)
    _insert_archive_record(config.database_path, audio_file_id="aud_old", source_path=raw_path, archive_path=archive_path, sha256=_sha256(archive_path), archived_at="2087-05-01T00:00:00+00:00")

    result = cleanup_archived_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_root),
        archived_before=datetime(2087, 5, 5, tzinfo=timezone.utc),
    )

    assert result.files_removed == 0
    assert result.files_pending == 0
    assert raw_path.exists()
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select status from audio_files where audio_file_id = 'aud_old'")
    finally:
        conn.close()
    assert rows == [{"status": "archived"}]


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
    assert result.memory_candidates_archived == 1
    transcripts_path = archive_root / "derived" / "transcript_segments.jsonl"
    summaries_path = archive_root / "derived" / "summaries.jsonl"
    candidates_path = archive_root / "derived" / "memory_candidates.jsonl"
    transcript_rows = [json.loads(line) for line in transcripts_path.read_text(encoding="utf-8").splitlines()]
    summary_rows = [json.loads(line) for line in summaries_path.read_text(encoding="utf-8").splitlines()]
    candidate_rows = [json.loads(line) for line in candidates_path.read_text(encoding="utf-8").splitlines()]
    assert transcript_rows[0]["segment_id"] == "seg_archive_test"
    assert transcript_rows[0]["text"] == "归档转写。"
    assert summary_rows[0]["summary_id"] == "sum_archive_test"
    assert candidate_rows[0]["candidate_id"] == "cand_archive_test"
    assert candidate_rows[0]["prompt_version"] == "llm_port.candidate_extraction.v1"
    assert "ev_archive_test" in candidate_rows[0]["evidence_refs_json"]

    conn = connect(config.database_path)
    try:
        records = fetch_all(conn, "select target_type, target_id, archive_path, verified from archive_records order by target_type")
    finally:
        conn.close()
    assert {"target_type": "transcript_segments", "target_id": "all", "archive_path": str(transcripts_path), "verified": 1} in records
    assert {"target_type": "summaries", "target_id": "all", "archive_path": str(summaries_path), "verified": 1} in records
    assert {"target_type": "memory_candidates", "target_id": "all", "archive_path": str(candidates_path), "verified": 1} in records


def _insert_audio(database_path: Path, raw_path: Path, sha256: str, *, status: str, audio_file_id: str = "aud_test") -> None:
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
                audio_file_id,
                "DJI Mic 3",
                f"/{raw_path.name}",
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


def _write_raw(config: AppConfig, filename: str, content: bytes) -> Path:
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / filename
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(content)
    return raw_path


class RejectingArchive:
    def __init__(self, *, root: Path) -> None:
        self.root = root

    def archive_file(self, *, source_path: Path, relative_path: Path, expected_sha256: str) -> ArchiveResult:
        return ArchiveResult(archive_path=self.root / relative_path, verified=False, reason="permission denied")

    def verify_file(self, *, archive_path: Path, expected_sha256: str) -> ArchiveResult:
        return ArchiveResult(archive_path=archive_path, verified=False, reason="permission denied")


def _copy_archive(archive_root: Path, source_path: Path) -> Path:
    archive_path = archive_root / "audio" / "raw" / "2087-05-10" / source_path.name
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(source_path.read_bytes())
    return archive_path


def _insert_archive_record(
    database_path: Path,
    *,
    audio_file_id: str,
    source_path: Path,
    archive_path: Path,
    sha256: str,
    archived_at: str,
) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into archive_records (
              archive_record_id, target_type, target_id, audio_file_id,
              source_path, archive_path, sha256, status, verified, archived_at,
              created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"arc_{audio_file_id}",
                "audio_file",
                audio_file_id,
                audio_file_id,
                str(source_path),
                str(archive_path),
                sha256,
                "verified",
                1,
                archived_at,
                archived_at,
                archived_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


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
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, source_type, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id, date_key,
              normalized_claim_hash, prompt_version, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cand_archive_test",
                "llm_daily_context",
                "归档候选记忆。",
                "observation",
                json.dumps({"type": "project", "id": "personal_context_node", "label": "Personal Context Node"}, ensure_ascii=False, sort_keys=True),
                0.8,
                json.dumps(
                    [
                        {
                            "evidence_id": "ev_archive_test",
                            "source_type": "transcript_segment",
                            "source_id": "seg_archive_test",
                            "quote": "归档转写。",
                        }
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "pending_review",
                None,
                "2087-05-10",
                "sha256:archive-test",
                "llm_port.candidate_extraction.v1",
                "2087-05-10T10:00:00+08:00",
                "2087-05-10T10:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
