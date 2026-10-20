"""Explicit, audited merge of unfinalized meetings; automatic derivation stays conservative."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


def merge_sessions(*, config: AppConfig, session_ids: list[str], reason: str,
                   revise_finalized: bool = False) -> dict:
    ids = list(dict.fromkeys(session_ids))
    if len(ids) < 2 or not reason.strip():
        raise ValueError("select at least two sessions and provide the confirmed merge reason")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute("""create table if not exists session_merge_history (
            merge_id text primary key, target_session_id text not null,
            payload_json text not null, created_at text not null)""")
        conn.commit()
        conn.execute("begin immediate")
        marks = ",".join("?" for _ in ids)
        rows = [dict(r) for r in conn.execute(
            f"select * from sessions where session_id in ({marks}) order by started_at,session_id", ids)]
        if len(rows) != len(ids) or len({r["date_key"] for r in rows}) != 1:
            raise ValueError("sessions must exist and belong to the same day")
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        # Published/edited artifacts need a separate revision workflow, never silently relocate them.
        finalizations = [dict(r) for r in conn.execute(
            f"select * from session_finalizations where session_id in ({marks})", ids)]
        if finalizations and not revise_finalized:
            raise ValueError("merge blocked by existing session_finalizations artifacts")
        for table in ("session_viewpoint_state", "session_batch_results"):
            if table in tables and conn.execute(
                f"select 1 from {table} where session_id in ({marks}) limit 1", ids
            ).fetchone():
                raise ValueError(f"merge blocked by existing {table} artifacts")
        if conn.execute(f"""select 1 from summaries where target_type='session'
            and target_id in ({marks}) limit 1""", ids).fetchone():
            raise ValueError("merge blocked by existing session summaries")
        if conn.execute(f"""select 1 from tasks where target_type='session'
            and target_id in ({marks}) and status='running' limit 1""", ids).fetchone():
            raise ValueError("wait for running session tasks before merging")
        if "knowledge_jobs" in tables and conn.execute(
            f"select 1 from knowledge_jobs where session_id in ({marks}) and status='running' limit 1", ids
        ).fetchone():
            raise ValueError("wait for running knowledge jobs before merging")
        participants = [dict(r) for r in conn.execute(
            f"select * from session_participants where session_id in ({marks})", ids)]
        statuses: dict[str, set[str]] = {}
        for p in participants:
            statuses.setdefault(p["person_id"], set()).add(p["status"])
        if any(len(values) > 1 for values in statuses.values()):
            raise ValueError("resolve conflicting attendance decisions before merging")
        names = {r["name"] for r in rows if r["name"]}
        if len(names) > 1:
            raise ValueError("resolve different meeting names before merging")
        target = rows[0]["session_id"]
        sources = [sid for sid in ids if sid != target]
        source_marks = ",".join("?" for _ in sources)
        assignments = [dict(r) for r in conn.execute(
            f"select segment_id,session_id from transcript_segments where session_id in ({marks})", ids)]
        now = datetime.now(timezone.utc).isoformat()
        merge_id = "merge_" + uuid4().hex
        archived_exports = []
        for finalization in finalizations:
            for field in ("export_md_path", "export_json_path"):
                original = Path(finalization[field])
                if not original.is_file():
                    raise ValueError(f"cannot preserve missing finalized export: {original}")
                archive = config.data_dir / "exports" / "session-merge-history" / merge_id / (
                    finalization["session_id"] + (".md" if field == "export_md_path" else ".json"))
                archive.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, archive)
                archived_exports.append({"original": str(original), "snapshot": str(archive)})
        receipt = {"session_id": target, "source_session_ids": sources, "reason": reason.strip(),
                   "sessions_before": rows, "participants_before": participants,
                   "assignments_before": assignments, "finalizations_before": finalizations,
                   "archived_exports": archived_exports}
        conn.execute("insert into session_merge_history values(?,?,?,?)",
                     (merge_id, target, json.dumps(receipt, ensure_ascii=False), now))
        # A decision for a smaller source cannot finalize the newly combined meeting.
        conn.execute(f"delete from session_finalizations where session_id in ({marks})", ids)
        for p in participants:
            conn.execute("""insert or ignore into session_participants
                (session_id,person_id,status,source,note,updated_at) values(?,?,?,?,?,?)""",
                (target, p["person_id"], p["status"], p["source"], p["note"], p["updated_at"]))
        conn.execute(f"delete from session_participants where session_id in ({source_marks})", sources)
        for table in ("transcript_segments", "segment_identity_negative_feedback", "segment_attribution_skips"):
            if table in tables:
                conn.execute(f"update {table} set session_id=? where session_id in ({source_marks})", [target, *sources])
        count, speech = conn.execute("""select count(*),coalesce(sum(end_ms-start_ms),0)
            from transcript_segments where session_id=? and is_active=1""", (target,)).fetchone()
        primary = conn.execute("""select a.person_id from transcript_segments t
            join v_segment_attribution a on a.segment_id=t.segment_id
            where t.session_id=? and t.is_active=1 and a.person_id is not null
            group by a.person_id order by count(*) desc,a.person_id limit 1""", (target,)).fetchone()
        # A name pins the explicit user decision against subsequent automatic re-derivation.
        name = next(iter(names), rows[0]["started_at"][11:16] + " 会话")
        conn.execute("""update sessions set ended_at=?,segment_count=?,active_speech_ms=?,
            name=?,exclude_from_memory=?,updated_at=?,primary_person_id=? where session_id=?""",
            (max(r["ended_at"] for r in rows), count, speech, name,
             max(r["exclude_from_memory"] for r in rows), now, primary[0] if primary else None, target))
        conn.execute(f"""update tasks set status='failed_terminal',last_error=?,updated_at=?
            where target_type='session' and target_id in ({source_marks})
            and status not in ('succeeded','failed_terminal')""",
            [f"Session merged into {target}", now, *sources])
        # Keep immutable evidence revisions and past results attached to their original IDs.
        # Invalidate current heads/jobs; they cannot describe the newly merged evidence.
        for table in ("session_evidence_heads", "knowledge_coverage"):
            if table in tables:
                conn.execute(f"delete from {table} where session_id in ({marks})", ids)
        if "knowledge_jobs" in tables:
            conn.execute(f"""update knowledge_jobs set status='superseded',updated_at=?
                where session_id in ({marks}) and status in
                ('pending','queued','blocked','ready','interrupted','failed','needs_revision')""", [now, *ids])
        conn.execute(f"delete from sessions where session_id in ({source_marks})", sources)
        conn.commit()
        return {"session_id": target, "merged_session_ids": sources, "segment_count": count}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
