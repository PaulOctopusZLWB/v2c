"""Session finalization — the product's terminal artifact and its contract with codex.

产品是事实层:从音频到"可信的、带人名的会话记录"。定稿(finalize)把一个会话的事实冻结成
两个文件,落在项目数据目录下(``{data_dir}/exports/sessions/{date_key}/``):

- ``{session_id}.md`` — 人和 codex 都能读的原料:front matter(出席名单、时间、定稿时间)+
  带人名的**转写全文**。词汇表里只有人名和"声音A/B"——诊断用的 spk_*/vp_* 标签永不出现。
- ``{session_id}.json`` — 程序可读的同一份事实,外加 segment 级的音频引用(codex 需要回听
  证据时用)。

认知层(总结、观点、Obsidian 沉淀、跨日综合)是 codex 的事,产品到定稿为止。重复定稿是
幂等的:重新生成文件并更新 ``session_finalizations``(出席或归属改了之后重导即可)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def finalize_session(*, config: AppConfig, session_id: str) -> dict:
    """Freeze a session's facts into the export artifact pair; requires ≥1 present participant.

    Raises ``ValueError`` when the session is unknown or nobody is confirmed present (the
    reviewer's attendance verdict IS the finalization criterion — nothing else gates it).
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        session = conn.execute(
            "select session_id, date_key, started_at, ended_at, name from sessions where session_id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise ValueError(f"unknown session_id: {session_id}")

        participants = fetch_all(
            conn,
            """
            select sp.person_id, p.display_name, sp.status
            from session_participants sp join persons p on p.person_id = sp.person_id
            where sp.session_id = ? order by sp.status, p.display_name
            """,
            (session_id,),
        )
        present = [row for row in participants if row["status"] == "present"]
        if not present:
            raise ValueError("cannot finalize: no participant confirmed present yet")

        self_row = conn.execute("select display_name from persons where is_self = 1 limit 1").fetchone()
        self_name = str(self_row["display_name"]) if self_row is not None else "我"

        segments = fetch_all(
            conn,
            """
            select
              ts.segment_id, ts.text, ts.speaker,
              coalesce(ts.speaker_cluster_id, ts.speaker) as voice_key,
              ts.absolute_start_at, ts.absolute_end_at, ts.start_ms, ts.end_ms,
              o.person_id, o.person_label
            from transcript_segments ts
            left join segment_person_overrides o on o.segment_id = ts.segment_id
            where ts.session_id = ? and ts.is_active = 1
            order by coalesce(ts.absolute_start_at, ''), ts.start_ms, ts.segment_id
            """,
            (session_id,),
        )
    finally:
        conn.close()

    # Display names: attributed person > owner voice > "声音A/B/…" per diarization voice, in
    # order of first appearance. The machine's spk_*/vp_* labels never reach the artifact.
    voice_labels: dict[str, str] = {}

    def _voice_label(voice_key: str) -> str:
        if voice_key not in voice_labels:
            n = len(voice_labels)
            voice_labels[voice_key] = f"声音{chr(65 + n)}" if n < 26 else f"声音{n + 1}"
        return voice_labels[voice_key]

    export_segments: list[dict[str, object]] = []
    for row in segments:
        if row["person_id"] is not None:
            display = str(row["person_label"] or row["person_id"])
        elif str(row["speaker"]) == "self":
            display = self_name
        else:
            display = _voice_label(str(row["voice_key"]))
        export_segments.append(
            {
                "segment_id": str(row["segment_id"]),
                "speaker_display": display,
                "person_id": row["person_id"],
                "text": str(row["text"] or ""),
                "start_at": row["absolute_start_at"],
                "end_at": row["absolute_end_at"],
                "audio_url": f"/api/audio/segments/{row['segment_id']}",
            }
        )

    unidentified = [
        {"label": label, "segment_count": sum(1 for s in export_segments if s["speaker_display"] == label)}
        for label in voice_labels.values()
    ]
    finalized_at = datetime.now(timezone.utc).isoformat()
    attendance = {
        status: [
            {"person_id": str(row["person_id"]), "display_name": str(row["display_name"])}
            for row in participants
            if row["status"] == status
        ]
        for status in ("present", "absent", "uncertain")
    }

    export_dir = config.data_dir / "exports" / "sessions" / str(session["date_key"])
    export_dir.mkdir(parents=True, exist_ok=True)
    md_path = export_dir / f"{session_id}.md"
    json_path = export_dir / f"{session_id}.json"

    payload = {
        "session_id": session_id,
        "date_key": str(session["date_key"]),
        "name": session["name"],
        "started_at": str(session["started_at"]),
        "ended_at": str(session["ended_at"]),
        "finalized_at": finalized_at,
        "attendance": attendance,
        "unidentified_voices": unidentified,
        "segments": export_segments,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")

    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into session_finalizations
              (session_id, finalized_at, export_md_path, export_json_path, present_count, segment_count)
            values (?, ?, ?, ?, ?, ?)
            on conflict(session_id) do update set
              finalized_at = excluded.finalized_at,
              export_md_path = excluded.export_md_path,
              export_json_path = excluded.export_json_path,
              present_count = excluded.present_count,
              segment_count = excluded.segment_count
            """,
            (session_id, finalized_at, str(md_path), str(json_path), len(present), len(export_segments)),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "session_id": session_id,
        "finalized_at": finalized_at,
        "export_md_path": str(md_path),
        "export_json_path": str(json_path),
        "present_count": len(present),
        "segment_count": len(export_segments),
        "unidentified_voices": unidentified,
    }


def finalization_state(*, config: AppConfig, session_id: str) -> dict | None:
    """The session's finalization row as a dict, or None when never finalized."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute(
            "select finalized_at, export_md_path, export_json_path, present_count, segment_count "
            "from session_finalizations where session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "finalized_at": str(row["finalized_at"]),
        "export_md_path": str(row["export_md_path"]),
        "export_json_path": str(row["export_json_path"]),
        "present_count": int(row["present_count"]),
        "segment_count": int(row["segment_count"]),
    }


def _render_markdown(payload: dict) -> str:
    def _hhmm(value: object) -> str:
        text = str(value or "")
        return text[11:16] if len(text) >= 16 else text

    front = [
        "---",
        f"session_id: {payload['session_id']}",
        f"date: {payload['date_key']}",
        f"started_at: {payload['started_at']}",
        f"ended_at: {payload['ended_at']}",
        f"finalized_at: {payload['finalized_at']}",
        "present: [" + ", ".join(p["display_name"] for p in payload["attendance"]["present"]) + "]",
        "absent: [" + ", ".join(p["display_name"] for p in payload["attendance"]["absent"]) + "]",
        "unidentified_voices: ["
        + ", ".join(f"{v['label']}({v['segment_count']}段)" for v in payload["unidentified_voices"])
        + "]",
        "---",
        "",
    ]
    title = payload.get("name") or f"会话 {payload['date_key']} {_hhmm(payload['started_at'])}"
    lines = front + [f"# {title}", "", "## 出席", ""]
    for status, zh in (("present", "出现"), ("absent", "未出现"), ("uncertain", "不确定")):
        for person in payload["attendance"][status]:
            lines.append(f"- {person['display_name']}({zh})")
    if payload["unidentified_voices"]:
        for voice in payload["unidentified_voices"]:
            lines.append(f"- {voice['label']}(未识别,{voice['segment_count']} 段)")
    lines += ["", "## 转写全文", ""]
    for segment in payload["segments"]:
        stamp = _hhmm(segment["start_at"])
        prefix = f"[{stamp}] " if stamp else ""
        lines.append(f"- {prefix}**{segment['speaker_display']}**:{segment['text']}")
    lines.append("")
    return "\n".join(lines)
