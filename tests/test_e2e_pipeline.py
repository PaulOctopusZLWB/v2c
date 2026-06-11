from __future__ import annotations

import wave
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.storage.sqlite import connect, fetch_all


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)


def test_first_milestone_runs_end_to_end_with_mock_adapters(tmp_path: Path) -> None:
    source_dir = tmp_path / "mounted_dji"
    source_wav = source_dir / "TX02_MIC001_20870510_173550_orig.wav"
    _write_tiny_wav(source_wav)

    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "PersonalContext",
        source_device="DJI Mic 3",
        owner_did="did:key:test-owner",
    )

    result = run_first_milestone(config=config, source_dir=source_dir, confirm_first_candidate=True)

    assert result.imported_files == 1
    assert result.transcript_segments >= 1
    assert result.memory_candidates >= 1
    assert result.signed_events >= 1

    db = connect(config.database_path)
    try:
        audio_files = fetch_all(db, "select source_device, sha256 from audio_files")
        segments = fetch_all(db, "select chunk_id, start_ms, end_ms, absolute_start_at, absolute_end_at from transcript_segments")
        candidates = fetch_all(db, "select status, evidence_refs_json, date_key, prompt_version from memory_candidates")
        evidence_refs = fetch_all(db, "select evidence_id, source_type, source_id, source_ref, quote from evidence_refs")
        events = fetch_all(db, "select event_type, owner_sequence, trust_status from signed_events")
    finally:
        db.close()

    assert audio_files == [{"source_device": "DJI Mic 3", "sha256": audio_files[0]["sha256"]}]
    assert segments[0] == {
        "chunk_id": segments[0]["chunk_id"],
        "start_ms": 0,
        "end_ms": 3000,
        "absolute_start_at": "2025-06-10T17:35:50+08:00",
        "absolute_end_at": "2025-06-10T17:35:53+08:00",
    }
    assert str(segments[0]["chunk_id"]).startswith("chk_")
    assert candidates[0]["status"] == "confirmed"
    assert candidates[0]["date_key"] == "2025-06-10"
    assert candidates[0]["prompt_version"] == "llm_port.candidate_extraction.v1"
    assert "seg_" in candidates[0]["evidence_refs_json"]
    assert evidence_refs == [
        {
            "evidence_id": evidence_refs[0]["evidence_id"],
            "source_type": "transcript_segment",
            "source_id": evidence_refs[0]["source_id"],
            "source_ref": evidence_refs[0]["source_id"],
            "quote": evidence_refs[0]["quote"],
        }
    ]
    assert evidence_refs[0]["evidence_id"].startswith("ev_seg_")
    assert evidence_refs[0]["source_id"].startswith("seg_")
    assert "需要生成本地上下文和记忆候选" in str(evidence_refs[0]["quote"])
    assert events == [{"event_type": "memory_card.created", "owner_sequence": 1, "trust_status": "trusted"}]

    daily_note = config.obsidian_vault / "10_Daily" / "2025-06-10.md"
    assert daily_note.exists()
    text = daily_note.read_text(encoding="utf-8")
    assert "# 2025-06-10 Daily Context" in text
    assert "## Memory Candidates" in text
    assert "TX02_MIC001_20870510_173550_orig.wav" in text
