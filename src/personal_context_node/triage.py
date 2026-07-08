"""Rule-based AI pre-review (预审) triage for a session's transcript segments.

第一版是纯规则(无 LLM 调用),把每个活跃段分箱:
  - ``high``    — 置信度 ≥ HIGH_CONFIDENCE 且无任何可疑信号:建议直接批量接受(前端折叠)。
  - ``suspect`` — 命中至少一个可疑信号(低置信 / 声纹分歧 / 疑似幻听·上下文断裂):前置人工审。
  - ``manual``  — 中间地带(置信一般、无信号,或没有置信数据):正常人工审。

每个 reason 带 ``kind``(机器可判)与 ``label``(直接可显示的中文,如「置信 0.41」)。
``suggested_speaker`` 在声纹分歧能给出更可能人选时返回;``suggested_text`` 预留给
后续接入 ASR 备选/LLM 纠错(本版恒为 None,前端「采纳 AI 修正」按钮只在其存在时出现)。

只读:不写任何表,不改 schema。
"""

from __future__ import annotations

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize

# 分箱阈值(与设计稿一致:高置信 ≥0.92 折叠;低于 0.75 视为低置信可疑)。
HIGH_CONFIDENCE = 0.92
LOW_CONFIDENCE = 0.75
# 疑似幻听:语速离谱(字符/秒)或极短时长塞长文本;以及与相邻段完全重复。
HALLUCINATION_CPS = 12.0
HALLUCINATION_MIN_MS = 400
HALLUCINATION_MIN_CHARS = 8
# 上下文断裂:距上一段超过该间隔且自身置信不高。
CONTEXT_GAP_MS = 60_000
CONTEXT_GAP_CONFIDENCE = 0.85


def session_triage(*, config: AppConfig, session_id: str) -> dict[str, object] | None:
    """Compute the triage payload for one session; None when the session is unknown."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        known = fetch_all(conn, "select 1 from sessions where session_id = ?", (session_id,))
        if not known:
            return None
        rows = fetch_all(
            conn,
            """
            select
              ts.segment_id,
              ts.text,
              ts.speaker,
              coalesce(ts.speaker_cluster_id, ts.speaker) as cluster_id,
              ts.start_ms,
              ts.end_ms,
              ts.absolute_start_at,
              ts.confidence,
              coalesce(r.status, 'pending_review') as review_status,
              o.person_id as override_person_id,
              o.person_label as override_person_label,
              m.person_id as mapping_person_id,
              mp.display_name as mapping_person_label,
              -- 用户对「当前归属人」的负反馈(不是 TA)= 强烈的说话人存疑信号。
              exists(
                select 1 from segment_identity_negative_feedback nf
                where nf.segment_id = ts.segment_id
                  and nf.person_id = coalesce(o.person_id, m.person_id)
              ) as neg_feedback,
              -- 归属人被身份审核标记为「不在场」。
              exists(
                select 1 from session_participants sp
                where sp.session_id = ts.session_id
                  and sp.person_id = coalesce(o.person_id, m.person_id)
                  and sp.status = 'absent'
              ) as attributed_absent
            from transcript_segments ts
            left join transcript_segment_reviews r on r.segment_id = ts.segment_id
            left join segment_person_overrides o on o.segment_id = ts.segment_id
            -- speaker_mappings 的 speaker_cluster_id 非唯一(多个 diarizer speaker 可映射
            -- 同一 cluster);先按 cluster 去重,否则 join 会把段扇出成多行。
            left join (
              select speaker_cluster_id, min(person_id) as person_id
              from speaker_mappings
              where person_id is not null and speaker_cluster_id is not null
              group by speaker_cluster_id
            ) m on m.speaker_cluster_id = coalesce(ts.speaker_cluster_id, ts.speaker)
            left join persons mp on mp.person_id = m.person_id
            where ts.session_id = ? and ts.is_active = 1
            order by ts.absolute_start_at, ts.start_ms, ts.segment_id
            """,
            (session_id,),
        )
    finally:
        conn.close()

    segments: list[dict[str, object]] = []
    reason_counts: dict[str, int] = {}
    bins = {"high": 0, "suspect": 0, "manual": 0}
    prev_text: str | None = None
    prev_end_ms: int | None = None

    for row in rows:
        reasons: list[dict[str, str]] = []
        suggested_speaker: dict[str, object] | None = None
        confidence = row["confidence"]
        text = str(row["text"] or "").strip()
        duration_ms = max(0, int(row["end_ms"]) - int(row["start_ms"]))

        # 规则 1 — 低置信分箱。
        if confidence is not None and float(confidence) < LOW_CONFIDENCE:
            reasons.append({"kind": "low_confidence", "label": f"置信 {float(confidence):.2f}"})

        # 规则 2 — 声纹分歧(负反馈 > 不在场 > override/mapping 不一致)。
        attributed_id = row["override_person_id"] or row["mapping_person_id"]
        if attributed_id and row["neg_feedback"]:
            reasons.append({"kind": "speaker_doubt", "label": "说话人存疑 · 有「不是 TA」反馈"})
        elif attributed_id and row["attributed_absent"]:
            reasons.append({"kind": "speaker_doubt", "label": "说话人存疑 · 已标记不在场"})
        elif (
            row["override_person_id"]
            and row["mapping_person_id"]
            and row["override_person_id"] != row["mapping_person_id"]
        ):
            label = row["mapping_person_label"] or str(row["mapping_person_id"])
            reasons.append({"kind": "speaker_doubt", "label": f"说话人存疑 → 可能是 {label}"})
            suggested_speaker = {
                "person_id": row["mapping_person_id"],
                "person_label": label,
            }

        # 规则 3 — 疑似幻听 / 上下文断裂。
        hallucination = False
        if text:
            # 语速离谱(需要正时长做除法);以及极短时长塞长文本(含 0ms 这一最极端形态,
            # 不走除法,所以不能被 duration_ms > 0 一起挡掉)。
            if duration_ms > 0 and len(text) / (duration_ms / 1000) > HALLUCINATION_CPS:
                hallucination = True
            elif duration_ms < HALLUCINATION_MIN_MS and len(text) >= HALLUCINATION_MIN_CHARS:
                hallucination = True
        if text and prev_text is not None and text == prev_text and len(text) >= HALLUCINATION_MIN_CHARS:
            hallucination = True  # 与相邻段完全重复(ASR 幻听常见形态)
        context_break = (
            prev_end_ms is not None
            and int(row["start_ms"]) - prev_end_ms > CONTEXT_GAP_MS
            and (confidence is None or float(confidence) < CONTEXT_GAP_CONFIDENCE)
        )
        if hallucination and context_break:
            reasons.append({"kind": "hallucination", "label": "疑似幻听 · 与上下文断裂"})
        elif hallucination:
            reasons.append({"kind": "hallucination", "label": "疑似幻听"})
        elif context_break:
            reasons.append({"kind": "context_break", "label": "与上下文断裂"})

        if reasons:
            bin_ = "suspect"
        elif confidence is not None and float(confidence) >= HIGH_CONFIDENCE:
            bin_ = "high"
        else:
            bin_ = "manual"

        bins[bin_] += 1
        for reason in reasons:
            reason_counts[reason["kind"]] = reason_counts.get(reason["kind"], 0) + 1

        segments.append(
            {
                "segment_id": row["segment_id"],
                "bin": bin_,
                "reasons": reasons,
                "confidence": float(confidence) if confidence is not None else None,
                "review_status": row["review_status"],
                "suggested_text": None,
                "suggested_speaker": suggested_speaker,
            }
        )
        prev_text = text or prev_text
        prev_end_ms = int(row["end_ms"])

    pending = [s for s in segments if s["review_status"] == "pending_review"]
    return {
        "session_id": session_id,
        "thresholds": {"high": HIGH_CONFIDENCE, "low": LOW_CONFIDENCE},
        "summary": {
            "total": len(segments),
            "bins": bins,
            "pending_high": sum(1 for s in pending if s["bin"] == "high"),
            "pending_suspect": sum(1 for s in pending if s["bin"] == "suspect"),
            "pending_manual": sum(1 for s in pending if s["bin"] == "manual"),
            "reasons": reason_counts,
        },
        "segments": segments,
    }
