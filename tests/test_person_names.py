from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.speaker_embeddings import put_embeddings_bulk
from personal_context_node.speaker_review import sync_speaker_review, upsert_segment_person_override, upsert_speaker_mapping
from personal_context_node.storage.sqlite import connect
from personal_context_node.web.app import create_app
from test_web_speaker_api import _insert_labeling_day


def _setup(tmp_path: Path):
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", edit_grace_seconds=0)
    _insert_labeling_day(config.database_path)
    client = TestClient(create_app(config=config))
    return config, client


def test_creation_rejects_blank_and_normalized_duplicate_names(tmp_path: Path) -> None:
    config, client = _setup(tmp_path)
    created = client.post("/api/persons", json={"display_name": " Alice "})
    assert created.status_code == 200 and created.json()["display_name"] == "Alice"
    assert client.post("/api/persons", json={"display_name": "  "}).status_code == 400
    for name in ("alice", "ＡＬＩＣＥ", "  Alice  "):
        response = client.post("/api/persons", json={"display_name": name})
        assert response.status_code == 409 and "已存在" in response.json()["detail"]
    conn = connect(config.database_path)
    try:
        assert conn.execute("select count(*) from persons").fetchone()[0] == 1
    finally:
        conn.close()


def test_rename_updates_display_labels_without_changing_identity_or_snapshots(tmp_path: Path) -> None:
    config, client = _setup(tmp_path)
    alice = client.post("/api/persons", json={"display_name": "Alice"}).json()["person_id"]
    bob = client.post("/api/persons", json={"display_name": "Bob"}).json()["person_id"]
    put_embeddings_bulk(config=config, items=[("seg_a", [1.0, 0.1]), ("seg_b", [1.0, 0.2])])
    frozen = tmp_path / "finalized.json"
    frozen.write_text('{"display_name":"Alice"}', encoding="utf-8")
    conn = connect(config.database_path)
    try:
        conn.executemany("insert into segment_person_overrides values (?,?,?,?,?)", [
            ("seg_a", "Alice", "old", alice, "manual"), ("seg_b", "Alice", "old", alice, "reviewed")
        ])
        conn.execute("insert into speaker_mappings (speaker,person_label,person_id,updated_at) values (?,?,?,?)", ("spk_b", "Alice", alice, "old"))
        conn.execute("insert into person_voiceprints (person_id,dim,vector,n_segments,updated_at) values (?,?,?,?,?)", (alice, 2, b"voice", 2, "old"))
        conn.execute("insert into session_participants (session_id,person_id,status,source,updated_at) values (?,?,?,?,?)", ("ses_lab", alice, "present", "manual", "old"))
        conn.execute("insert into session_finalizations values (?,?,?,?,?,?)", ("ses_lab", "old", "frozen.md", str(frozen), 1, 2))
        conn.commit()
    finally:
        conn.close()
    projection = {"session_ids": ["ses_lab"], "method": "pca"}
    before = client.post("/api/speakers/projection", json=projection).json()
    assert {p["person_label"] for p in before["points"]} == {"Alice"}
    duplicate = client.patch(f"/api/persons/{alice}", json={"display_name": " BOB ", "expected_display_name": "Alice"})
    assert duplicate.status_code == 409
    stale = client.patch(f"/api/persons/{alice}", json={"display_name": "Carol", "expected_display_name": "old view"})
    assert stale.status_code == 409
    response = client.patch(f"/api/persons/{alice}", json={"display_name": " Carol ", "expected_display_name": "Alice"})
    assert response.status_code == 200 and response.json()["display_name"] == "Carol"
    after = client.post("/api/speakers/projection", json=projection).json()
    assert {p["person_label"] for p in after["points"]} == {"Carol"}
    assert frozen.read_text(encoding="utf-8") == '{"display_name":"Alice"}'
    conn = connect(config.database_path)
    try:
        assert [tuple(r) for r in conn.execute("select person_id,person_label,source from segment_person_overrides order by segment_id")] == [(alice, "Carol", "manual"), (alice, "Carol", "reviewed")]
        assert tuple(conn.execute("select person_id,person_label from speaker_mappings").fetchone()) == (alice, "Carol")
        assert tuple(conn.execute("select person_id,vector from person_voiceprints").fetchone()) == (alice, b"voice")
        assert conn.execute("select person_id from session_participants").fetchone()[0] == alice
        assert conn.execute("select display_name from persons where person_id=?", (bob,)).fetchone()[0] == "Bob"
    finally:
        conn.close()


def test_failed_rename_rolls_back_all_label_changes(tmp_path: Path) -> None:
    config, _ = _setup(tmp_path)
    client = TestClient(create_app(config=config), raise_server_exceptions=False)
    person_id = client.post("/api/persons", json={"display_name": "Alice"}).json()["person_id"]
    conn = connect(config.database_path)
    try:
        conn.execute("insert into segment_person_overrides values (?,?,?,?,?)", ("seg_a", "Alice", "old", person_id, "manual"))
        conn.execute("create trigger reject_rename before update of person_label on segment_person_overrides begin select raise(abort,'simulated failure'); end")
        conn.commit()
    finally:
        conn.close()
    assert client.patch(f"/api/persons/{person_id}", json={"display_name": "Carol"}).status_code == 500
    conn = connect(config.database_path)
    try:
        assert conn.execute("select display_name from persons where person_id=?", (person_id,)).fetchone()[0] == "Alice"
        assert conn.execute("select person_label from segment_person_overrides where person_id=?", (person_id,)).fetchone()[0] == "Alice"
        assert conn.execute("select count(*) from person_name_overrides").fetchone()[0] == 0
    finally:
        conn.close()


def test_later_automatic_writes_use_current_name_not_a_stale_label(tmp_path: Path) -> None:
    config, client = _setup(tmp_path)
    person_id = client.post("/api/persons", json={"display_name": "Alice"}).json()["person_id"]
    assert client.patch(f"/api/persons/{person_id}", json={"display_name": "Carol"}).status_code == 200
    conn = connect(config.database_path)
    try:
        upsert_segment_person_override(conn, segment_id="seg_a", person_id=person_id, person_label="Alice", now="old", source="voiceprint")
        upsert_speaker_mapping(conn, speaker="spk_b", person_id=person_id, person_label="Alice", now="old")
        conn.commit()
        assert conn.execute("select person_label from segment_person_overrides where segment_id='seg_a'").fetchone()[0] == "Carol"
        assert conn.execute("select person_label from speaker_mappings where speaker='spk_b'").fetchone()[0] == "Carol"
    finally:
        conn.close()


def test_concurrent_creation_cannot_create_duplicate_people(tmp_path: Path) -> None:
    config, client = _setup(tmp_path)
    barrier = Barrier(2)
    def create(name: str) -> int:
        barrier.wait()
        return client.post("/api/persons", json={"display_name": name}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(create, ["Alice", " alice "]))
    assert statuses == [200, 409]
    conn = connect(config.database_path)
    try:
        assert conn.execute("select count(*) from persons").fetchone()[0] == 1
    finally:
        conn.close()


def test_concurrent_rename_and_creation_cannot_claim_the_same_name(tmp_path: Path) -> None:
    config, client = _setup(tmp_path)
    person_id = client.post("/api/persons", json={"display_name": "Alice"}).json()["person_id"]
    barrier = Barrier(2)
    def rename() -> int:
        barrier.wait()
        return client.patch(f"/api/persons/{person_id}", json={"display_name": "Carol"}).status_code
    def create() -> int:
        barrier.wait()
        return client.post("/api/persons", json={"display_name": "carol"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(rename), pool.submit(create)]
        assert sorted(result.result() for result in results) == [200, 409]
    conn = connect(config.database_path)
    try:
        assert conn.execute("select count(*) from persons where lower(display_name)='carol'").fetchone()[0] == 1
    finally:
        conn.close()


def test_rename_rejects_missing_empty_and_system_category(tmp_path: Path) -> None:
    _, client = _setup(tmp_path)
    assert client.patch("/api/persons/missing", json={"display_name": "Carol"}).status_code == 404
    person = client.post("/api/persons", json={"display_name": "Alice"}).json()["person_id"]
    assert client.patch(f"/api/persons/{person}", json={"display_name": "  "}).status_code == 400
    noise = client.post("/api/persons", json={"display_name": "噪音/多人", "person_type": "non_speaker"}).json()["person_id"]
    assert client.patch(f"/api/persons/{noise}", json={"display_name": "其他"}).status_code == 400


def test_new_person_from_identity_review_rejects_existing_name(tmp_path: Path) -> None:
    config, client = _setup(tmp_path)
    client.post("/api/persons", json={"display_name": "Alice"})
    response = client.post("/api/identity/confirm-candidate", json={"session_id": "ses_lab", "action": "new_person", "display_name": "alice", "segment_ids": ["seg_a"]})
    assert response.status_code == 409
    conn = connect(config.database_path)
    try:
        assert conn.execute("select count(*) from persons").fetchone()[0] == 1
        assert conn.execute("select count(*) from segment_person_overrides").fetchone()[0] == 0
    finally:
        conn.close()


def test_renamed_person_is_not_reverted_by_old_speaker_review(tmp_path: Path) -> None:
    config, client = _setup(tmp_path)
    person_id = client.post("/api/persons", json={"display_name": "Alice"}).json()["person_id"]
    assert client.patch(f"/api/persons/{person_id}", json={"display_name": "Carol"}).status_code == 200
    path = config.obsidian_vault / "90_System" / "Speaker_Review" / "2087-05-10.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        '<!-- pcn:speaker_mapping start date_key="2087-05-10" -->\n'
        '```yaml\nmappings:\n  spk_a: ' + person_id + '\n'
        'persons:\n  ' + person_id + ':\n    display_name: Alice\n    is_self: false\n'
        'segment_overrides:\n  seg_a: ' + person_id + '\n```\n'
        '<!-- pcn:speaker_mapping end date_key="2087-05-10" -->\n', encoding="utf-8"
    )
    result = sync_speaker_review(config=config, day="2087-05-10")
    assert result.mappings_upserted == result.segment_overrides_upserted == 1
    conn = connect(config.database_path)
    try:
        assert conn.execute("select display_name from persons where person_id=?", (person_id,)).fetchone()[0] == "Carol"
        assert conn.execute("select person_label from segment_person_overrides where segment_id='seg_a'").fetchone()[0] == "Carol"
        assert conn.execute("select person_label from speaker_mappings where speaker='spk_a'").fetchone()[0] == "Carol"
    finally:
        conn.close()


def test_duplicate_name_in_speaker_review_rolls_back_existing_attributions(tmp_path: Path) -> None:
    config, client = _setup(tmp_path)
    person_id = client.post("/api/persons", json={"display_name": "Alice"}).json()["person_id"]
    conn = connect(config.database_path)
    try:
        conn.execute("insert into segment_person_overrides values (?,?,?,?,?)", ("seg_a", "Alice", "old", person_id, "manual"))
        conn.commit()
    finally:
        conn.close()
    path = config.obsidian_vault / "90_System" / "Speaker_Review" / "2087-05-10.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        '<!-- pcn:speaker_mapping start date_key="2087-05-10" -->\n'
        '```yaml\nmappings:\n  spk_a: per_other\n'
        'persons:\n  per_other:\n    display_name: ALICE\n    is_self: false\n'
        'segment_overrides: {}\n```\n'
        '<!-- pcn:speaker_mapping end date_key="2087-05-10" -->\n', encoding="utf-8"
    )
    result = sync_speaker_review(config=config, day="2087-05-10")
    assert result.mappings_upserted == result.segment_overrides_upserted == 0
    conn = connect(config.database_path)
    try:
        assert conn.execute("select person_id from segment_person_overrides where segment_id='seg_a'").fetchone()[0] == person_id
        assert conn.execute("select count(*) from persons").fetchone()[0] == 1
    finally:
        conn.close()
