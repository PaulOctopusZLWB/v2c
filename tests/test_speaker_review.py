from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.speaker_review import (
    materialized_transcript_segments,
    publish_speaker_review,
    sync_speaker_review,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_speaker_review_mapping_and_segment_override_are_materialized(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_segments(config.database_path)

    review_path = publish_speaker_review(config=config, day="2087-05-10")

    assert review_path == config.obsidian_vault / "90_System" / "Speaker_Review" / "2087-05-10.md"
    text = review_path.read_text(encoding="utf-8")
    assert "- spk_self: self" in text
    assert "<!-- segment_id: seg_guest -->" in text

    edited = text.replace("- spk_self: self", "- spk_self: Paul")
    edited = edited.replace("<!-- segment_id: seg_guest -->\nspk_self -> self:", "<!-- segment_id: seg_guest -->\nspk_self -> Guest:")
    review_path.write_text(edited, encoding="utf-8")

    result = sync_speaker_review(config=config, day="2087-05-10")

    assert result.mappings_upserted == 1
    assert result.segment_overrides_upserted == 1

    materialized = materialized_transcript_segments(config=config, day="2087-05-10")
    by_id = {row["segment_id"]: row for row in materialized}
    assert by_id["seg_self"]["speaker"] == "spk_self"
    assert by_id["seg_self"]["effective_person"] == "Paul"
    assert by_id["seg_guest"]["speaker"] == "spk_self"
    assert by_id["seg_guest"]["effective_person"] == "Guest"

    conn = connect(config.database_path)
    try:
        raw = fetch_all(conn, "select segment_id, speaker from transcript_segments order by segment_id")
    finally:
        conn.close()
    assert raw == [
        {"segment_id": "seg_guest", "speaker": "spk_self"},
        {"segment_id": "seg_self", "speaker": "spk_self"},
    ]


def _insert_segments(database_path: Path) -> None:
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
                "/local.wav",
                "sha256:test",
                1000,
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:10:00+08:00",
                "imported",
            ),
        )
        for segment_id, text in [("seg_self", "这是本人发言。"), ("seg_guest", "这句实际是客人说的。")]:
            conn.execute(
                """
                insert into transcript_segments (
                  segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
                  language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    "aud_test",
                    f"chk_{segment_id}",
                    0,
                    1000,
                    text,
                    "zh",
                    "spk_self",
                    f"ev_{segment_id}",
                    0.99,
                    "MockASRAdapter",
                    "mock-asr",
                    "test",
                ),
            )
        conn.commit()
    finally:
        conn.close()
