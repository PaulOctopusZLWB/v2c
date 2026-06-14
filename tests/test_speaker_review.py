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
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_segments(config.database_path)

    review_path = publish_speaker_review(config=config, day="2087-05-10")

    assert review_path == config.obsidian_vault / "90_System" / "Speaker_Review" / "2087-05-10.md"
    text = review_path.read_text(encoding="utf-8")
    assert text.startswith(
        "---\n"
        "pcn_schema: markdown_note.v1\n"
        "note_type: speaker_review\n"
        "date_key: 2087-05-10\n"
        "generated_by: personal-context-node\n"
    )
    assert "\npcn_managed: true\n---\n" in text
    assert "```yaml\nmappings:\n  spk_self: per_self\n" in text
    assert "persons:\n  per_self:\n    display_name: self\n    is_self: true\n" in text
    assert "segment_overrides: {}\n" in text

    edited = text.replace("  spk_self: per_self", "  spk_self: per_paul")
    edited = edited.replace("segment_overrides: {}", "segment_overrides:\n  seg_guest: per_guest")
    edited = edited.replace(
        "  per_self:\n    display_name: self\n    is_self: true",
        "  per_self:\n"
        "    display_name: self\n"
        "    is_self: true\n"
        "  per_paul:\n"
        "    display_name: Paul\n"
        "    is_self: false\n"
        "  per_guest:\n"
        "    display_name: Guest\n"
        "    is_self: false",
    )
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


def test_speaker_review_sync_populates_person_cluster_and_attribution_view(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_segments(config.database_path)
    review_path = publish_speaker_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    edited = text.replace("  spk_self: per_self", "  spk_self: per_paul")
    edited = edited.replace("segment_overrides: {}", "segment_overrides:\n  seg_guest: per_guest")
    edited = edited.replace(
        "  per_self:\n    display_name: self\n    is_self: true",
        "  per_self:\n"
        "    display_name: self\n"
        "    is_self: true\n"
        "  per_paul:\n"
        "    display_name: Paul\n"
        "    is_self: false\n"
        "  per_guest:\n"
        "    display_name: Guest\n"
        "    is_self: false",
    )
    review_path.write_text(edited, encoding="utf-8")

    sync_speaker_review(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        persons = fetch_all(conn, "select display_name, is_self from persons order by display_name")
        clusters = fetch_all(conn, "select speaker_cluster_id, label from speaker_clusters")
        mappings = fetch_all(
            conn,
            """
            select speaker, person_label, speaker_mapping_id, speaker_cluster_id,
                   person_id, confidence, source, created_at
            from speaker_mappings
            """,
        )
        attribution = fetch_all(conn, "select segment_id, person_id, attribution_source from v_segment_attribution order by segment_id")
    finally:
        conn.close()

    assert persons == [{"display_name": "Guest", "is_self": 0}, {"display_name": "Paul", "is_self": 0}]
    assert clusters == [{"speaker_cluster_id": "spk_self", "label": "spk_self"}]
    assert mappings[0]["speaker"] == "spk_self"
    assert mappings[0]["person_label"] == "Paul"
    assert mappings[0]["speaker_mapping_id"] == "spmap_spk_self"
    assert mappings[0]["speaker_cluster_id"] == "spk_self"
    assert mappings[0]["person_id"].startswith("per_")
    assert mappings[0]["confidence"] == 1.0
    assert mappings[0]["source"] == "speaker_review"
    assert mappings[0]["created_at"]
    by_segment = {row["segment_id"]: row for row in attribution}
    assert by_segment["seg_self"]["attribution_source"] == "cluster_mapping"
    assert by_segment["seg_guest"]["attribution_source"] == "override"


def test_speaker_review_sync_reads_yaml_mapping_block(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_segments(config.database_path)
    review_path = publish_speaker_review(config=config, day="2087-05-10")
    review_path.write_text(
        "\n".join(
            [
                "---",
                "pcn_schema: markdown_note.v1",
                "note_type: speaker_review",
                "date_key: 2087-05-10",
                "generated_by: personal-context-node",
                "generated_at: 2087-05-10T00:00:00+00:00",
                "pcn_managed: true",
                "---",
                "",
                '<!-- pcn:speaker_mapping start date_key="2087-05-10" version="1" -->',
                "```yaml",
                "mappings:",
                "  spk_self: per_paul",
                "persons:",
                "  per_paul:",
                "    display_name: Paul",
                "    is_self: false",
                "  per_guest:",
                "    display_name: Guest",
                "    is_self: false",
                "segment_overrides:",
                "  seg_guest: per_guest",
                "```",
                '<!-- pcn:speaker_mapping end date_key="2087-05-10" -->',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = sync_speaker_review(config=config, day="2087-05-10")

    assert result.mappings_upserted == 1
    assert result.segment_overrides_upserted == 1
    materialized = materialized_transcript_segments(config=config, day="2087-05-10")
    by_id = {row["segment_id"]: row for row in materialized}
    assert by_id["seg_self"]["person_id"] == "per_paul"
    assert by_id["seg_self"]["effective_person"] == "Paul"
    assert by_id["seg_guest"]["person_id"] == "per_guest"
    assert by_id["seg_guest"]["effective_person"] == "Guest"


def test_speaker_review_sync_removes_deleted_mappings_without_deleting_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_segments(config.database_path)
    review_path = publish_speaker_review(config=config, day="2087-05-10")
    review_path.write_text(
        "\n".join(
            [
                "---",
                "pcn_schema: markdown_note.v1",
                "note_type: speaker_review",
                "date_key: 2087-05-10",
                "generated_by: personal-context-node",
                "generated_at: 2087-05-10T00:00:00+00:00",
                "pcn_managed: true",
                "---",
                "",
                '<!-- pcn:speaker_mapping start date_key="2087-05-10" version="1" -->',
                "```yaml",
                "mappings:",
                "  spk_self: per_paul",
                "persons:",
                "  per_paul:",
                "    display_name: Paul",
                "    is_self: false",
                "  per_guest:",
                "    display_name: Guest",
                "    is_self: false",
                "segment_overrides:",
                "  seg_guest: per_guest",
                "```",
                '<!-- pcn:speaker_mapping end date_key="2087-05-10" -->',
                "",
            ]
        ),
        encoding="utf-8",
    )
    sync_speaker_review(config=config, day="2087-05-10")

    review_path.write_text(
        "\n".join(
            [
                "---",
                "pcn_schema: markdown_note.v1",
                "note_type: speaker_review",
                "date_key: 2087-05-10",
                "generated_by: personal-context-node",
                "generated_at: 2087-05-10T00:00:00+00:00",
                "pcn_managed: true",
                "---",
                "",
                '<!-- pcn:speaker_mapping start date_key="2087-05-10" version="1" -->',
                "```yaml",
                "mappings: {}",
                "persons:",
                "  per_paul:",
                "    display_name: Paul",
                "    is_self: false",
                "  per_guest:",
                "    display_name: Guest",
                "    is_self: false",
                "segment_overrides: {}",
                "```",
                '<!-- pcn:speaker_mapping end date_key="2087-05-10" -->',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = sync_speaker_review(config=config, day="2087-05-10")

    assert result.mappings_upserted == 0
    assert result.segment_overrides_upserted == 0
    materialized = materialized_transcript_segments(config=config, day="2087-05-10")
    by_id = {row["segment_id"]: row for row in materialized}
    assert by_id["seg_self"]["effective_person"] == "spk_self"
    assert by_id["seg_guest"]["effective_person"] == "spk_self"
    conn = connect(config.database_path)
    try:
        raw_segments = fetch_all(conn, "select segment_id, speaker from transcript_segments order by segment_id")
        mappings = fetch_all(conn, "select speaker from speaker_mappings")
        overrides = fetch_all(conn, "select segment_id from segment_person_overrides")
    finally:
        conn.close()
    assert raw_segments == [
        {"segment_id": "seg_guest", "speaker": "spk_self"},
        {"segment_id": "seg_self", "speaker": "spk_self"},
    ]
    assert mappings == []
    assert overrides == []


def test_speaker_review_uses_session_date_key_for_cross_midnight_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_segments(
        config.database_path,
        recorded_at="2087-05-09T23:55:00+08:00",
        date_key="2087-05-10",
        session_id="ses_cross_midnight",
    )

    review_path = publish_speaker_review(config=config, day="2087-05-10")

    text = review_path.read_text(encoding="utf-8")
    assert "- seg_self | spk_self | 这是本人发言。" in text
    materialized = materialized_transcript_segments(config=config, day="2087-05-10")
    assert [row["segment_id"] for row in materialized] == ["seg_guest", "seg_self"]


def test_speaker_review_sync_ignores_text_outside_mapping_block(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_segments(config.database_path)
    review_path = publish_speaker_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    yaml_edit = text.replace("  spk_self: per_self", "  spk_self: per_paul").replace(
        "  per_self:\n    display_name: self\n    is_self: true",
        "  per_self:\n"
        "    display_name: self\n"
        "    is_self: true\n"
        "  per_paul:\n"
        "    display_name: Paul\n"
        "    is_self: false",
    )
    edited = "\n".join(
        [
            yaml_edit,
            "",
            "- spk_self: Free Text Person",
            "<!-- segment_id: seg_guest -->",
            "spk_self -> Free Text Override: 这行不在协议块里。",
        ]
    )
    review_path.write_text(edited, encoding="utf-8")

    result = sync_speaker_review(config=config, day="2087-05-10")

    assert result.mappings_upserted == 1
    assert result.segment_overrides_upserted == 0
    conn = connect(config.database_path)
    try:
        mappings = fetch_all(conn, "select speaker, person_label from speaker_mappings")
        overrides = fetch_all(conn, "select segment_id, person_label from segment_person_overrides")
    finally:
        conn.close()

    assert mappings == [{"speaker": "spk_self", "person_label": "Paul"}]
    assert overrides == []


def test_speaker_review_sync_logs_malformed_yaml_without_side_effects(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_segments(config.database_path)
    review_path = publish_speaker_review(config=config, day="2087-05-10")
    review_path.write_text(
        """
# 2087-05-10 Speaker Review

<!-- pcn:speaker_mapping start date_key="2087-05-10" version="1" -->
```yaml
mappings: [
persons:
  per_paul:
    display_name: Paul
    is_self: false
```
<!-- pcn:speaker_mapping end date_key="2087-05-10" -->
""".lstrip(),
        encoding="utf-8",
    )

    result = sync_speaker_review(config=config, day="2087-05-10")

    assert result.mappings_upserted == 0
    assert result.segment_overrides_upserted == 0
    conn = connect(config.database_path)
    try:
        mappings = fetch_all(conn, "select speaker from speaker_mappings")
        logs = fetch_all(conn, "select source, target_id, status, message from sync_logs")
    finally:
        conn.close()
    assert mappings == []
    assert logs == [
        {
            "source": "speaker_mapping_review",
            "target_id": "2087-05-10",
            "status": "failed",
            "message": "yaml parse failed: 2087-05-10",
        }
    ]
    sync_log_note = config.obsidian_vault / "90_System" / "Sync_Log" / "2087-05-10.md"
    assert sync_log_note.exists()
    sync_log_text = sync_log_note.read_text(encoding="utf-8")
    assert "note_type: sync_log" in sync_log_text
    assert "source: speaker_mapping_review" in sync_log_text
    assert "yaml parse failed: 2087-05-10" in sync_log_text


def test_speaker_review_sync_logs_unknown_person_reference_without_side_effects(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_segments(config.database_path)
    review_path = publish_speaker_review(config=config, day="2087-05-10")
    review_path.write_text(
        """
# 2087-05-10 Speaker Review

<!-- pcn:speaker_mapping start date_key="2087-05-10" version="1" -->
```yaml
mappings:
  spk_self: per_missing
persons:
  per_self:
    display_name: self
    is_self: true
segment_overrides:
  seg_guest: per_guest_missing
```
<!-- pcn:speaker_mapping end date_key="2087-05-10" -->
""".lstrip(),
        encoding="utf-8",
    )

    result = sync_speaker_review(config=config, day="2087-05-10")

    assert result.mappings_upserted == 0
    assert result.segment_overrides_upserted == 0
    conn = connect(config.database_path)
    try:
        mappings = fetch_all(conn, "select speaker from speaker_mappings")
        overrides = fetch_all(conn, "select segment_id from segment_person_overrides")
        logs = fetch_all(conn, "select source, target_id, status, message from sync_logs")
    finally:
        conn.close()
    assert mappings == []
    assert overrides == []
    assert logs == [
        {
            "source": "speaker_mapping_review",
            "target_id": "2087-05-10",
            "status": "failed",
            "message": "unknown person reference: per_guest_missing, per_missing",
        }
    ]
    sync_log_note = config.obsidian_vault / "90_System" / "Sync_Log" / "2087-05-10.md"
    assert "unknown person reference: per_guest_missing, per_missing" in sync_log_note.read_text(encoding="utf-8")


def test_speaker_review_sync_skips_recently_modified_review_file(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=120)
    _insert_segments(config.database_path)
    review_path = publish_speaker_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    review_path.write_text(text.replace("  spk_self: per_self", "  spk_self: per_paul"), encoding="utf-8")

    result = sync_speaker_review(config=config, day="2087-05-10")

    assert result.mappings_upserted == 0
    assert result.segment_overrides_upserted == 0
    conn = connect(config.database_path)
    try:
        mappings = fetch_all(conn, "select speaker from speaker_mappings")
        logs = fetch_all(conn, "select source, target_id, status, message from sync_logs")
    finally:
        conn.close()
    assert mappings == []
    assert logs == [
        {
            "source": "speaker_mapping_review",
            "target_id": "2087-05-10",
            "status": "skipped",
            "message": "review file modified within edit grace: 2087-05-10",
        }
    ]
    sync_log_note = config.obsidian_vault / "90_System" / "Sync_Log" / "2087-05-10.md"
    assert "review file modified within edit grace: 2087-05-10" in sync_log_note.read_text(encoding="utf-8")


def _insert_segments(
    database_path: Path,
    *,
    recorded_at: str = "2087-05-10T00:00:00+08:00",
    date_key: str = "2087-05-10",
    session_id: str = "ses_test",
) -> None:
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
                recorded_at,
                "2087-05-10T00:10:00+08:00",
                "imported",
            ),
        )
        conn.execute(
            """
            insert into sessions (
              session_id, date_key, started_at, ended_at, source,
              segment_count, active_speech_ms, first_segment_id,
              exclude_from_memory, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                date_key,
                recorded_at,
                recorded_at,
                "derived_from_segments",
                2,
                2000,
                "seg_self",
                0,
                "2087-05-10T00:10:00+08:00",
                "2087-05-10T00:10:00+08:00",
            ),
        )
        for segment_id, text in [("seg_self", "这是本人发言。"), ("seg_guest", "这句实际是客人说的。")]:
            conn.execute(
                """
                insert into transcript_segments (
                  segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text,
                  language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    "aud_test",
                    f"chk_{segment_id}",
                    session_id,
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


def test_sync_speaker_review_logs_duplicate_self_instead_of_crashing(tmp_path):
    # §29.6: a review marking two persons is_self must be reported to the sync log, not
    # crash sync_speaker_review (it runs inside daily_generate).
    from personal_context_node.config import AppConfig
    from personal_context_node.speaker_review import sync_speaker_review
    from personal_context_node.storage.sqlite import connect, fetch_all, initialize

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    review_dir = config.obsidian_vault / "90_System" / "Speaker_Review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "2087-05-10.md").write_text(
        "<!-- pcn:speaker_mapping start date_key=\"2087-05-10\" version=\"1\" -->\n"
        "```yaml\n"
        "mappings:\n"
        "  self: per_self\n"
        "persons:\n"
        "  per_self:\n"
        "    display_name: A\n"
        "    is_self: true\n"
        "  per_other:\n"
        "    display_name: B\n"
        "    is_self: true\n"
        "```\n"
        "<!-- pcn:speaker_mapping end date_key=\"2087-05-10\" -->\n",
        encoding="utf-8",
    )

    result = sync_speaker_review(config=config, day="2087-05-10")  # must not raise

    assert result.mappings_upserted == 0
    conn = connect(config.database_path)
    try:
        initialize(conn)
        logs = fetch_all(conn, "select status, message from sync_logs")
        persons = fetch_all(conn, "select count(*) as c from persons")
    finally:
        conn.close()
    assert any("is_self" in str(row["message"]) for row in logs)
    assert persons[0]["c"] == 0  # no side effect


def test_speaker_review_republish_preserves_confirmed_mapping(tmp_path):
    # §29.4.9: re-publishing the speaker review (as obsidian_publish does each cycle)
    # must reflect the confirmed DB mappings, not reset them to defaults — otherwise the
    # next sync reverts a user's real-name attribution (data loss).
    from personal_context_node.config import AppConfig
    from personal_context_node.speaker_review import publish_speaker_review, sync_speaker_review
    from personal_context_node.storage.sqlite import connect, fetch_all, initialize

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status)"
            " values ('a', 'DJI Mic 3', '/s', '/l', 'sha', 1000, '2087-05-10T08:00:00+08:00', '2087-05-10T09:00:00+08:00', 'imported')"
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at)"
            " values ('ses', '2087-05-10', '2087-05-10T08:00:00+08:00', '2087-05-10T08:10:00+08:00', 'x', 1, 1000, 'seg', 'n', 'n')"
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version)"
            " values ('seg', 'a', 'chk', 'ses', 0, 1000, 'hi', 'zh', 'spk_1', 'spk_1', 'ev', 0.9, 'm', 'm', 'v')"
        )
        conn.commit()
    finally:
        conn.close()

    review_path = publish_speaker_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    text = text.replace("  spk_1: per_unknown", "  spk_1: per_wang").replace(
        "  per_unknown:\n    display_name: unknown\n    is_self: false",
        "  per_unknown:\n    display_name: unknown\n    is_self: false\n  per_wang:\n    display_name: 王总\n    is_self: false",
    )
    review_path.write_text(text, encoding="utf-8")
    sync_speaker_review(config=config, day="2087-05-10")

    # Re-publish (obsidian_publish) then re-sync — mapping must survive.
    publish_speaker_review(config=config, day="2087-05-10")
    sync_speaker_review(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        mappings = fetch_all(conn, "select speaker, person_id from speaker_mappings where person_id is not null")
    finally:
        conn.close()
    assert {"speaker": "spk_1", "person_id": "per_wang"} in [dict(m) for m in mappings]


def test_speaker_review_reassigning_self_to_new_person_does_not_crash(tmp_path: Path) -> None:
    # idx_persons_self + §29.6: reassigning "self" to a renamed/new person must MOVE the self
    # designation, not raise IntegrityError (which would wedge the daily_generate task on every
    # retry). Round 1 establishes per_self (is_self=1); round 2 promotes per_paul to self.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_segments(config.database_path)
    publish_speaker_review(config=config, day="2087-05-10")
    sync_speaker_review(config=config, day="2087-05-10")  # round 1: per_self is_self=1

    review_path = publish_speaker_review(config=config, day="2087-05-10")
    text = review_path.read_text(encoding="utf-8")
    edited = text.replace("  spk_self: per_self", "  spk_self: per_paul")
    edited = edited.replace(
        "  per_self:\n    display_name: self\n    is_self: true",
        "  per_paul:\n    display_name: Paul\n    is_self: true",
    )
    review_path.write_text(edited, encoding="utf-8")

    result = sync_speaker_review(config=config, day="2087-05-10")  # must not raise IntegrityError
    assert result.mappings_upserted == 1

    conn = connect(config.database_path)
    try:
        selves = fetch_all(conn, "select person_id from persons where is_self = 1")
    finally:
        conn.close()
    assert [row["person_id"] for row in selves] == ["per_paul"]  # exactly one self, moved
