from __future__ import annotations

from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_initialize_records_schema_migration_version(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        migrations = fetch_all(conn, "select version, name from schema_migrations order by version")
    finally:
        conn.close()

    assert migrations == [{"version": 1, "name": "base_schema"}]


def test_initialize_does_not_duplicate_schema_migration_rows(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        initialize(conn)

        migrations = fetch_all(conn, "select version, name from schema_migrations order by version")
    finally:
        conn.close()

    assert migrations == [{"version": 1, "name": "base_schema"}]


def test_initialize_memory_cards_schema_includes_source_type(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["source_type"]["notnull"] == 1
    assert column_by_name["source_type"]["dflt_value"] == "'confirmed_generated'"


def test_initialize_evidence_refs_schema_includes_design_columns(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(evidence_refs)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["source_ref"]["type"].lower() == "text"
    assert column_by_name["source_ref"]["notnull"] == 1
    assert column_by_name["owner_id"]["type"].lower() == "text"
    assert column_by_name["summary"]["type"].lower() == "text"
    assert column_by_name["created_at"]["type"].lower() == "text"
    assert column_by_name["created_at"]["notnull"] == 1


def test_initialize_memory_cards_schema_includes_current_version(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["current_version"]["type"].lower() == "integer"
    assert column_by_name["current_version"]["notnull"] == 1
    assert column_by_name["current_version"]["dflt_value"] == "1"


def test_initialize_memory_cards_schema_includes_owner_id(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["owner_id"]["type"].lower() == "text"
    assert column_by_name["owner_id"]["notnull"] == 1


def test_initialize_migrates_legacy_memory_cards_owner_id_before_owner_index(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        conn.execute(
            """
            create table memory_cards (
              card_id text primary key,
              current_version integer not null default 1,
              owner_did text not null,
              claim_type text not null,
              claim text not null,
              source_type text not null default 'confirmed_generated',
              subject_json text not null,
              evidence_refs_json text not null,
              visibility_json text not null default '{"type":"private"}',
              tags_json text not null default '[]',
              status text not null,
              source_event_hash text not null,
              created_at text not null,
              updated_at text not null default ''
            )
            """
        )
        conn.execute(
            """
            insert into memory_cards (
              card_id, owner_did, claim_type, claim, subject_json, evidence_refs_json,
              status, source_event_hash, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mem_legacy_owner",
                "did:key:legacy-owner",
                "requirement",
                "Legacy cards migrate owner_id before indexing.",
                '{"type":"project","id":"pcn"}',
                "[]",
                "active",
                "sha256:legacy",
                "2087-05-10T00:00:00Z",
            ),
        )
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
        indexes = fetch_all(conn, "pragma index_list(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    index_names = {row["name"] for row in indexes}
    assert column_by_name["owner_id"]["notnull"] == 1
    assert "idx_memory_cards_owner" in index_names


def test_initialize_memory_cards_schema_includes_confidence(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["confidence"]["type"].lower() == "real"
    assert column_by_name["confidence"]["notnull"] == 0


def test_initialize_memory_cards_schema_includes_temporal_bounds(tmp_path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        columns = fetch_all(conn, "pragma table_info(memory_cards)")
    finally:
        conn.close()

    column_by_name = {row["name"]: row for row in columns}
    assert column_by_name["observed_at"]["type"].lower() == "text"
    assert column_by_name["valid_from"]["type"].lower() == "text"
    assert column_by_name["valid_until"]["type"].lower() == "text"
