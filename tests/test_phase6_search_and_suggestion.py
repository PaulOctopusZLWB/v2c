from __future__ import annotations

import struct
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.cluster_suggestion import cluster_suggestion
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.transcript_fts import cjk_tokenize, fts_query
from personal_context_node.transcript_review import search_transcripts
from personal_context_node.web.app import create_app


def _vec(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def _seed(config: AppConfig) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('a','d','/s',1,1,'/r','sha256:x',1,'x','x','imported')"
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values ('ses_1','2087-05-10','2087-05-10T08:00:00+08:00','x','derived',3,3,'g1','x','x')"
        )
        segs = [
            ("g1", "今天讨论项目排期和联调计划。", "spk_1", "vp_a", "2087-05-10T08:00:00+08:00"),
            ("g2", "鉴权模块需要重构。", "spk_2", "vp_b", "2087-05-10T08:01:00+08:00"),
            ("g3", "The deploy pipeline is green.", "spk_1", "vp_a", "2087-05-10T08:02:00+08:00"),
        ]
        for sid, text, spk, cluster, at in segs:
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at, absolute_start_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, "a", f"c_{sid}", "ses_1", 0, 1000, text, "zh", spk, cluster, f"e_{sid}", 0.9, "m", "m", "v", 1, "x", at),
            )
        conn.commit()
    finally:
        conn.close()


def test_cjk_tokenize_and_query() -> None:
    assert cjk_tokenize("项目排期abc 123") == "项 目 排 期 abc 123"
    assert fts_query("排期") == '"排 期"'
    assert fts_query('a"b') == '"a b"'


def test_fts_search_matches_cjk_substring_and_latin(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed(config)

    # 两字中文子串(unicode61 下会失败的场景)。
    hits = search_transcripts(config=config, query="排期", limit=10)
    assert [h["segment_id"] for h in hits] == ["g1"]
    assert hits[0]["day"] == "2087-05-10" and hits[0]["speaker"] == "spk_1"

    # 拉丁 token。
    assert [h["segment_id"] for h in search_transcripts(config=config, query="deploy", limit=10)] == ["g3"]
    # 不存在的串。
    assert search_transcripts(config=config, query="不存在的词", limit=10) == []
    # 空查询短路。
    assert search_transcripts(config=config, query="   ", limit=10) == []


def test_fts_index_rebuilds_after_new_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed(config)
    assert len(search_transcripts(config=config, query="排期", limit=10)) == 1

    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at, absolute_start_at) values ('g4','a','c_g4','ses_1',0,1000,'排期还要再对一次。','zh','spk_1','vp_a','e_g4',0.9,'m','m','v',1,'x','2087-05-10T09:00:00+08:00')"
        )
        conn.commit()
    finally:
        conn.close()

    hits = search_transcripts(config=config, query="排期", limit=10)
    assert [h["segment_id"] for h in hits] == ["g4", "g1"]  # 最新在前


def test_cluster_suggestion_scores_best_person(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed(config)
    conn = connect(config.database_path)
    try:
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_a','张三','contact',0,'x','x')")
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_b','李雷','contact',0,'x','x')")
        # vp_a 的两段 embedding 都指向 x 轴;张三质心 = x 轴,李雷 = y 轴。
        conn.execute("insert into segment_embeddings (segment_id, model, dim, vector, created_at) values ('g1','campplus',3,?, 'x')", (_vec([1.0, 0.0, 0.0]),))
        conn.execute("insert into segment_embeddings (segment_id, model, dim, vector, created_at) values ('g3','campplus',3,?, 'x')", (_vec([0.9, 0.1, 0.0]),))
        conn.execute("insert into person_voiceprints (person_id, dim, vector, n_segments, updated_at) values ('per_a',3,?,2,'x')", (_vec([1.0, 0.0, 0.0]),))
        conn.execute("insert into person_voiceprints (person_id, dim, vector, n_segments, updated_at) values ('per_b',3,?,2,'x')", (_vec([0.0, 1.0, 0.0]),))
        conn.commit()
    finally:
        conn.close()

    payload = cluster_suggestion(config=config, cluster_id="vp_a")
    assert payload is not None
    assert payload["segment_count"] == 2 and payload["embedded_count"] == 2
    suggestion = payload["suggestion"]
    assert suggestion["person_id"] == "per_a" and suggestion["person_label"] == "张三"
    assert suggestion["score"] > 0.9

    # 无 embedding 的聚类:载荷存在但无建议。
    no_emb = cluster_suggestion(config=config, cluster_id="vp_b")
    assert no_emb is not None and no_emb["suggestion"] is None

    assert cluster_suggestion(config=config, cluster_id="vp_nope") is None


def test_cluster_suggestion_route(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed(config)
    client = TestClient(create_app(config=config))
    ok = client.get("/api/clusters/vp_a/suggestion")
    assert ok.status_code == 200, ok.text
    assert ok.json()["cluster_id"] == "vp_a"
    missing = client.get("/api/clusters/vp_nope/suggestion")
    assert missing.status_code == 404
