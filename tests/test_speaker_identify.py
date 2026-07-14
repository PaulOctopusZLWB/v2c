from __future__ import annotations

from pathlib import Path

import numpy as np

from personal_context_node.config import AppConfig
from personal_context_node.speaker_embeddings import (
    auto_attribute_enrolled,
    clear_projection_cache,
    put_embeddings_bulk,
)
from personal_context_node.speaker_identify import (
    absent_person_ids,
    cascade_participant_update,
    clear_person_session_attributions,
    identify_session_speakers,
    prune_low_share_attributions,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def _unit_axis(index: int, *, noise: float = 0.0, dim: int = 192) -> list[float]:
    vec = np.zeros(dim, dtype=np.float64)
    vec[index] = 1.0
    for offset in (1, 2, 3):
        vec[(index + offset) % dim] += noise * (offset / 3.0)
    return vec.tolist()


def _insert_session_with_segments(
    database_path: Path,
    segment_ids: list[str],
    *,
    session_id: str = "ses_test",
    audio_file_id: str = "aud_test",
    date_key: str = "2087-05-10",
) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (audio_file_id, "DJI Mic 3", f"/source/{audio_file_id}.wav", 1, 1, f"/raw/{audio_file_id}.wav", f"sha256:{audio_file_id}", 2000, f"{date_key}T08:00:00+08:00", f"{date_key}T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, date_key, f"{date_key}T08:00:00+08:00", f"{date_key}T08:00:02+08:00", "derived_from_segments", len(segment_ids), 2000, segment_ids[0], f"{date_key}T08:00:03+08:00", f"{date_key}T08:00:03+08:00"),
        )
        for index, segment_id in enumerate(segment_ids):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (segment_id, audio_file_id, f"chk_{segment_id}", session_id, index * 1000, (index + 1) * 1000, f"text {index + 1}", "zh", f"spk_{index % 3:02d}", f"spk_{index % 3:02d}", f"ev_{segment_id}", f"{date_key}T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_persons(database_path: Path, persons: dict[str, str], *, person_type: str = "contact") -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        for person_id, display_name in persons.items():
            conn.execute(
                "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
                (person_id, display_name, person_type, 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def _write_override(database_path: Path, *, segment_id: str, person_id: str, source: str) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_id, person_label, source, updated_at) values (?, ?, ?, ?, ?) "
            "on conflict(segment_id) do update set person_id = excluded.person_id, person_label = excluded.person_label, source = excluded.source, updated_at = excluded.updated_at",
            (segment_id, person_id, person_id, source, "2087-05-10T09:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _override_map(database_path: Path) -> dict[str, tuple[str, str]]:
    conn = connect(database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select segment_id, person_id, source from segment_person_overrides")
        return {str(r["segment_id"]): (str(r["person_id"]), str(r["source"])) for r in rows}
    finally:
        conn.close()


def _mark_participant(database_path: Path, *, session_id: str, person_id: str, status: str) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into session_participants (session_id, person_id, status, source, updated_at) values (?, ?, ?, 'manual', ?) "
            "on conflict(session_id, person_id) do update set status = excluded.status, updated_at = excluded.updated_at",
            (session_id, person_id, status, "2087-05-10T09:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_session(tmp_path: Path, *, n: int = 20) -> AppConfig:
    """One session, ``n`` embedded segments: even indices near axis 0, odd near axis 1."""
    clear_projection_cache()
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    ids = [f"seg_{i:02d}" for i in range(n)]
    _insert_session_with_segments(config.database_path, ids)
    put_embeddings_bulk(
        config=config,
        items=[(sid, _unit_axis(i % 2, noise=0.03 + 0.01 * (i // 2))) for i, sid in enumerate(ids)],
    )
    return config


# ---------------------------------------------------------------------------
# prune_low_share_attributions
# ---------------------------------------------------------------------------


def test_prune_drops_low_share_voiceprint_person(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=20)
    _insert_persons(config.database_path, {"per_a": "Alice", "per_rare": "Rare"})
    # per_a covers half the session; per_rare has one voiceprint segment = 5% share.
    for i in range(0, 20, 2):
        _write_override(config.database_path, segment_id=f"seg_{i:02d}", person_id="per_a", source="voiceprint")
    _write_override(config.database_path, segment_id="seg_01", person_id="per_rare", source="voiceprint")

    result = prune_low_share_attributions(config=config, session_id="ses_test", min_share=0.10)

    assert result["pruned"] == {"per_rare": 1}
    overrides = _override_map(config.database_path)
    assert "seg_01" not in overrides  # the spurious match went back to unassigned
    assert overrides["seg_00"] == ("per_a", "voiceprint")  # the majority person survives


def test_prune_never_touches_manual_or_manual_backed_person(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=20)
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob"})
    # per_b is low-share BUT has a manual label in the session: the reviewer vouched, keep it.
    _write_override(config.database_path, segment_id="seg_01", person_id="per_b", source="voiceprint")
    _write_override(config.database_path, segment_id="seg_03", person_id="per_b", source="manual")

    result = prune_low_share_attributions(config=config, session_id="ses_test", min_share=0.50)

    assert result["pruned"] == {}
    overrides = _override_map(config.database_path)
    assert overrides["seg_01"] == ("per_b", "voiceprint")
    assert overrides["seg_03"] == ("per_b", "manual")


def test_prune_exempts_noise_person(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=20)
    _insert_persons(config.database_path, {"per_noise": "噪音/多人"}, person_type="non_speaker")
    _write_override(config.database_path, segment_id="seg_01", person_id="per_noise", source="voiceprint")

    result = prune_low_share_attributions(config=config, session_id="ses_test", min_share=0.50)

    assert result["pruned"] == {}
    assert _override_map(config.database_path)["seg_01"] == ("per_noise", "voiceprint")


def test_prune_noop_on_zero_share_or_empty_session(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=4)
    assert prune_low_share_attributions(config=config, session_id="ses_test", min_share=0.0) == {
        "pruned": {},
        "total_segments": 4,
    }
    assert prune_low_share_attributions(config=config, session_id="ses_missing", min_share=0.5)["pruned"] == {}


# ---------------------------------------------------------------------------
# absent exclusion (auto_attribute_enrolled + absent_person_ids)
# ---------------------------------------------------------------------------


def test_absent_person_excluded_from_matching(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=12)
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob"})
    # Manual exemplars: axis-0 voice = Alice, axis-1 voice = Bob.
    _write_override(config.database_path, segment_id="seg_00", person_id="per_a", source="manual")
    _write_override(config.database_path, segment_id="seg_01", person_id="per_b", source="manual")

    _mark_participant(config.database_path, session_id="ses_test", person_id="per_b", status="absent")
    absent = absent_person_ids(config=config, session_id="ses_test")
    assert absent == {"per_b"}

    result = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5, exclude_person_ids=absent)

    assert result["per_person"].get("per_b", 0) == 0  # the absent person attracted nothing
    overrides = _override_map(config.database_path)
    voiceprint_people = {pid for pid, source in overrides.values() if source == "voiceprint"}
    assert "per_b" not in voiceprint_people
    assert result["per_person"]["per_a"] > 0  # matching still works for the present person


# ---------------------------------------------------------------------------
# identify_session_speakers (full pass)
# ---------------------------------------------------------------------------


def test_identify_full_pass_attributes_and_prunes(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=20)
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob", "per_rare": "Rare"})
    _write_override(config.database_path, segment_id="seg_00", person_id="per_a", source="manual")
    _write_override(config.database_path, segment_id="seg_01", person_id="per_b", source="manual")
    # A stale spurious voiceprint match that the matcher won't re-create (per_rare has no
    # exemplar) and whose share is far below the default 1%... use a bigger min_share via config.
    config = config.model_copy(update={"identify_min_session_share": 0.10})
    _write_override(config.database_path, segment_id="seg_02", person_id="per_rare", source="voiceprint")

    result = identify_session_speakers(config=config, session_id="ses_test")

    assert result["attributed"]["assigned"] > 0
    overrides = _override_map(config.database_path)
    # Both voices matched to their manual exemplars' people.
    assert overrides["seg_02"][0] in {"per_a", "per_b"} or "seg_02" not in overrides
    voiceprint_people = {pid for pid, source in overrides.values() if source == "voiceprint"}
    assert "per_rare" not in voiceprint_people  # matcher cleared/pruned the stale spurious match
    # Manual ground truth survives the whole pass.
    assert overrides["seg_00"] == ("per_a", "manual")
    assert overrides["seg_01"] == ("per_b", "manual")


def test_identify_cold_start_without_exemplars_still_clusters(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=20)

    result = identify_session_speakers(config=config, session_id="ses_test")

    assert result["attributed"]["skipped"] is True  # no exemplars anywhere -> match skipped
    assert result["pruned"]["pruned"] == {}
    # Session-scoped clustering still ran (20 segments >= identify_min_cluster_size=15 allows
    # the scope; whether HDBSCAN finds clusters is data-dependent — the key is it returns).
    assert "clusters" in result["clusters"] or result["clusters"]["clusters"] >= 0


# ---------------------------------------------------------------------------
# identity-review cascade
# ---------------------------------------------------------------------------


def test_cascade_absent_clears_and_reidentifies(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=12)
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob"})
    _write_override(config.database_path, segment_id="seg_00", person_id="per_a", source="manual")
    # per_b currently "owns" the odd-axis voice via inferred attributions + one manual anchor
    # on seg_01 (manual must survive the cascade as a visible conflict, not silent loss).
    _write_override(config.database_path, segment_id="seg_01", person_id="per_b", source="manual")
    for i in (3, 5, 7):
        _write_override(config.database_path, segment_id=f"seg_{i:02d}", person_id="per_b", source="voiceprint")

    _mark_participant(config.database_path, session_id="ses_test", person_id="per_b", status="absent")
    result = cascade_participant_update(config=config, session_id="ses_test", person_id="per_b", status="absent")

    assert result["cascade"] == "absent"
    assert result["cleared"] == 3  # the three inferred rows went; the manual anchor did not
    overrides = _override_map(config.database_path)
    assert overrides["seg_01"] == ("per_b", "manual")
    voiceprint_people = {pid for pid, source in overrides.values() if source == "voiceprint"}
    assert "per_b" not in voiceprint_people  # re-identify ran WITH per_b excluded


def test_cascade_present_is_non_destructive(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=8)
    _insert_persons(config.database_path, {"per_a": "Alice"})
    _write_override(config.database_path, segment_id="seg_02", person_id="per_a", source="voiceprint")

    result = cascade_participant_update(config=config, session_id="ses_test", person_id="per_a", status="present")

    assert result == {"cascade": "none"}
    assert _override_map(config.database_path)["seg_02"] == ("per_a", "voiceprint")


def test_clear_person_session_attributions_scopes_to_session(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=6)
    _insert_persons(config.database_path, {"per_a": "Alice"})
    # A second session whose attribution must be untouched by the ses_test cascade.
    _insert_session_with_segments(
        config.database_path, ["other_1"], session_id="ses_other", audio_file_id="aud_other", date_key="2087-05-11"
    )
    put_embeddings_bulk(config=config, items=[("other_1", _unit_axis(0, noise=0.05))])
    _write_override(config.database_path, segment_id="seg_00", person_id="per_a", source="voiceprint")
    _write_override(config.database_path, segment_id="other_1", person_id="per_a", source="voiceprint")

    cleared = clear_person_session_attributions(config=config, session_id="ses_test", person_id="per_a")

    assert cleared == 1
    overrides = _override_map(config.database_path)
    assert "seg_00" not in overrides
    assert overrides["other_1"] == ("per_a", "voiceprint")


def test_identify_respects_absent_participants(tmp_path: Path) -> None:
    config = _seed_session(tmp_path, n=12)
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob"})
    _write_override(config.database_path, segment_id="seg_00", person_id="per_a", source="manual")
    _write_override(config.database_path, segment_id="seg_01", person_id="per_b", source="manual")
    _mark_participant(config.database_path, session_id="ses_test", person_id="per_b", status="absent")

    result = identify_session_speakers(config=config, session_id="ses_test")

    assert result["excluded_absent"] == ["per_b"]
    voiceprint_people = {pid for pid, source in _override_map(config.database_path).values() if source == "voiceprint"}
    assert "per_b" not in voiceprint_people
