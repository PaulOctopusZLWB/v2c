from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.transcript_review import review_segment
from personal_context_node.triage import session_triage
from personal_context_node.web.app import create_app


def _seed(config: AppConfig) -> None:
    """One session with segments covering every triage rule.

    seg_hi   conf .97, normal pace           -> high
    seg_mid  conf .85                        -> manual
    seg_none conf NULL                       -> manual
    seg_low  conf .41                        -> suspect (低置信)
    seg_dup  duplicate text of seg_hi        -> suspect (疑似幻听 · 重复)
    seg_fast 20 chars in 500ms (40 cps)      -> suspect (疑似幻听 · 语速)
    seg_gap  61s after previous, conf .5     -> suspect (低置信 + 上下文断裂)
    seg_vp   override 张三 vs mapping 李雷    -> suspect (说话人存疑 → 可能是 李雷)
    seg_nf   attributed 张三 + 负反馈         -> suspect (「不是 TA」反馈)
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('aud_1','d','/s',1,1,'/r','sha256:x',600000,'x','x','imported')"
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values ('ses_1','2087-05-10','2087-05-10T08:00:00+08:00','2087-05-10T08:10:00+08:00','derived',9,60000,'seg_hi','x','x')"
        )
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_zhang','张三','contact',0,'x','x')")
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_lei','李雷','contact',0,'x','x')")

        # (segment_id, start_ms, end_ms, text, confidence) — seg_dup 必须紧跟 seg_hi
        # (重复检测只看相邻段);seg_gap 与前一段相隔 >60s。
        segs = [
            ("seg_hi", 0, 4000, "今天先对齐排期,然后看联调计划。", 0.97),
            ("seg_dup", 4000, 8000, "今天先对齐排期,然后看联调计划。", 0.95),
            ("seg_mid", 8000, 12000, "后端接口这周五之前可以联调。", 0.85),
            ("seg_none", 12000, 16000, "移动端还需要两天时间。", None),
            ("seg_low", 16000, 20000, "嗯那个就是鉴全的部分。", 0.41),
            ("seg_fast", 20000, 20500, "这一段在半秒内塞进了整整二十个字符哦", 0.95),
            ("seg_gap", 82000, 86000, "回来继续说部署的问题。", 0.50),
            ("seg_vp", 86000, 90000, "我觉得可以先上灰度。", 0.96),
            ("seg_nf", 90000, 94000, "预算需要再确认一下。", 0.96),
        ]
        for i, (sid, start, end, text, conf) in enumerate(segs):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, "aud_1", f"chk_{sid}", "ses_1", start, end, text, "zh", f"spk_{i}", f"vp_{sid}", f"ev_{sid}", conf, "mock", "m", "v", 1, "x"),
            )
        # 声纹分歧: seg_vp override=张三, cluster mapping=李雷.
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, person_id, source, updated_at) values ('seg_vp','张三','per_zhang','voiceprint','x')"
        )
        conn.execute(
            "insert into speaker_mappings (speaker, person_label, speaker_cluster_id, person_id, confidence, source, updated_at) values ('spk_7','李雷','vp_seg_vp','per_lei',0.9,'auto','x')"
        )
        # 负反馈: seg_nf attributed 张三 (override), user said 不是 TA.
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, person_id, source, updated_at) values ('seg_nf','张三','per_zhang','voiceprint','x')"
        )
        conn.execute(
            "insert into segment_identity_negative_feedback (segment_id, person_id, session_id, updated_at) values ('seg_nf','per_zhang','ses_1','x')"
        )
        conn.commit()
    finally:
        conn.close()


def test_triage_bins_and_reasons(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed(config)

    payload = session_triage(config=config, session_id="ses_1")
    assert payload is not None
    by_id = {s["segment_id"]: s for s in payload["segments"]}

    assert by_id["seg_hi"]["bin"] == "high" and by_id["seg_hi"]["reasons"] == []
    assert by_id["seg_mid"]["bin"] == "manual"
    assert by_id["seg_none"]["bin"] == "manual"

    assert by_id["seg_low"]["bin"] == "suspect"
    assert by_id["seg_low"]["reasons"][0]["label"] == "置信 0.41"

    assert by_id["seg_dup"]["bin"] == "suspect"
    assert any(r["kind"] == "hallucination" for r in by_id["seg_dup"]["reasons"])

    assert by_id["seg_fast"]["bin"] == "suspect"
    assert any(r["kind"] == "hallucination" for r in by_id["seg_fast"]["reasons"])

    assert by_id["seg_gap"]["bin"] == "suspect"
    kinds = {r["kind"] for r in by_id["seg_gap"]["reasons"]}
    assert "low_confidence" in kinds and "context_break" in kinds

    assert by_id["seg_vp"]["bin"] == "suspect"
    assert by_id["seg_vp"]["reasons"][0]["label"] == "说话人存疑 → 可能是 李雷"
    assert by_id["seg_vp"]["suggested_speaker"] == {"person_id": "per_lei", "person_label": "李雷"}

    assert by_id["seg_nf"]["bin"] == "suspect"
    assert "不是 TA" in by_id["seg_nf"]["reasons"][0]["label"]

    summary = payload["summary"]
    assert summary["total"] == 9
    assert summary["bins"] == {"high": 1, "suspect": 6, "manual": 2}
    # Nothing reviewed yet -> pending counts mirror the bins.
    assert summary["pending_high"] == 1
    assert summary["pending_suspect"] == 6
    assert summary["pending_manual"] == 2


def test_triage_pending_counts_exclude_reviewed(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed(config)
    review_segment(config=config, segment_id="seg_hi", status="accepted", note="")
    review_segment(config=config, segment_id="seg_low", status="rejected", note="")

    payload = session_triage(config=config, session_id="ses_1")
    assert payload is not None
    summary = payload["summary"]
    # bins count ALL active segments; pending_* exclude the two reviewed ones.
    assert summary["bins"] == {"high": 1, "suspect": 6, "manual": 2}
    assert summary["pending_high"] == 0
    assert summary["pending_suspect"] == 5
    by_id = {s["segment_id"]: s for s in payload["segments"]}
    assert by_id["seg_hi"]["review_status"] == "accepted"
    assert by_id["seg_low"]["review_status"] == "rejected"


def test_triage_flags_zero_duration_long_text_as_hallucination(tmp_path: Path) -> None:
    """end_ms == start_ms(0ms)且 ≥8 字是「极短时长塞长文本」最极端形态,必须标幻听
    (旧代码把这条判据和 CPS 除法一起挡在 duration_ms > 0 之后,会漏标)。"""
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('a','d','/s',1,1,'/r','sha256:x',1,'x','x','imported')"
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values ('s0','2087-05-10','x','x','derived',1,0,'z1','x','x')"
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at, absolute_start_at) values ('z1','a','c','s0',5000,5000,'零时长却塞了很长一句幻听文本。','zh','spk_1','vp_z','e_z',0.96,'m','m','v',1,'x','2087-05-10T08:00:00+08:00')"
        )
        conn.commit()
    finally:
        conn.close()

    seg = session_triage(config=config, session_id="s0")["segments"][0]
    assert seg["bin"] == "suspect"
    assert any(r["kind"] == "hallucination" for r in seg["reasons"])


def test_triage_no_fanout_when_mappings_share_a_cluster(tmp_path: Path) -> None:
    """speaker_mappings.speaker_cluster_id 非唯一;两行映射同一 cluster 不得把段扇出成两行。"""
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed(config)
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into speaker_mappings (speaker, person_label, speaker_cluster_id, person_id, confidence, source, updated_at) values ('spk_x','张三','vp_seg_vp','per_zhang',0.8,'auto','x')"
        )
        conn.commit()
    finally:
        conn.close()

    payload = session_triage(config=config, session_id="ses_1")
    assert payload is not None
    ids = [s["segment_id"] for s in payload["segments"]]
    assert len(ids) == len(set(ids)) == 9  # no duplicates


def test_triage_route_and_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed(config)
    client = TestClient(create_app(config=config))

    resp = client.get("/api/sessions/ses_1/triage")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["session_id"] == "ses_1"
    assert payload["summary"]["bins"]["suspect"] == 6

    missing = client.get("/api/sessions/nope/triage")
    assert missing.status_code == 404
