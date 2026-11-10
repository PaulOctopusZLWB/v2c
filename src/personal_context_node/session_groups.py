"""Read-only meeting groups over source sessions; never move or rewrite evidence."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta


class _UnavailableGroup(ValueError):
    pass


def _time(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def session_groups(conn, session_ids: list[str], *, gap_minutes: int = 20) -> list[dict]:
    """Group whole source sessions only, leaving partial/stale reviewed boundaries visible.

    Accepted logical meetings win over inferred continuity. An inferred group is limited to
    adjacent files on one recorder and only machine-owned, unnamed sessions. No rows change.
    """
    ids = list(dict.fromkeys(session_ids))
    if len(ids) < 2:
        return []
    marks = ",".join("?" for _ in ids)
    rows = [dict(row) for row in conn.execute(f"""
        select s.session_id,s.date_key,s.name,s.started_at,s.ended_at,s.segment_count,
          (s.name is not null
            or exists(select 1 from session_finalizations f where f.session_id=s.session_id)
            or exists(select 1 from session_participants p where p.session_id=s.session_id)
            or exists(select 1 from session_viewpoint_state v where v.session_id=s.session_id)
            or exists(select 1 from summaries x where x.target_type='session' and x.target_id=s.session_id)
          ) as pinned
        from sessions s where s.session_id in ({marks}) order by s.started_at,s.session_id
    """, ids)]
    by_id = {str(row["session_id"]): row for row in rows}
    tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    groups: list[dict] = []
    protected: set[str] = set()
    claimed: set[str] = set()
    malformed = False

    def make_group(group_id: str, title: str | None, source: str, members: list[str]) -> dict:
        members.sort(key=lambda sid: (str(by_id[sid]["started_at"]), sid))
        selected = [by_id[sid] for sid in members]
        return {
            "group_id": group_id, "title": title, "source": source,
            "date_key": selected[0]["date_key"], "started_at": selected[0]["started_at"],
            "ended_at": max(str(row["ended_at"]) for row in selected),
            "segment_count": sum(int(row["segment_count"]) for row in selected),
            "session_ids": members,
        }

    if {"logical_meetings", "logical_meeting_revisions"}.issubset(tables):
        accepted = conn.execute("""select m.meeting_id,m.title,r.payload_json
            from logical_meetings m join logical_meeting_revisions r on r.revision_id=m.current_revision_id
            where m.state='accepted' order by m.created_at,m.meeting_id""").fetchall()
        for meeting in accepted:
            try:
                spans = json.loads(meeting["payload_json"])["spans"]
                if not isinstance(spans, list) or not spans:
                    raise ValueError("empty spans")
                members = {str(span["session_id"]) for span in spans}
                protected.update(members)
                if len(members) < 2 or not members.issubset(by_id) or members & claimed:
                    continue
                if len({by_id[sid]["date_key"] for sid in members}) != 1:
                    continue
                if "session_evidence_heads" not in tables:
                    continue
                selected: dict[str, set[str]] = {sid: set() for sid in members}
                for span in spans:
                    sid = str(span["session_id"])
                    segment_ids = span["segment_ids"]
                    if not isinstance(segment_ids, list) or not segment_ids or not all(isinstance(x, str) for x in segment_ids):
                        raise ValueError("invalid span")
                    if selected[sid].intersection(segment_ids):
                        raise ValueError("overlapping span")
                    selected[sid].update(segment_ids)
                    head = conn.execute("select revision_id from session_evidence_heads where session_id=?", (sid,)).fetchone()
                    if head is None or head[0] != span.get("source_revision_id"):
                        raise _UnavailableGroup("stale source")
                for sid in members:
                    active = {str(row[0]) for row in conn.execute(
                        "select segment_id from transcript_segments where session_id=? and is_active=1", (sid,)
                    )}
                    if active != selected[sid]:
                        raise _UnavailableGroup("partial source")
                    by_id[sid]["segment_count"] = len(active)
                groups.append(make_group(str(meeting["meeting_id"]), str(meeting["title"]), "confirmed", list(members)))
                claimed.update(members)
            except _UnavailableGroup:
                continue
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                # Preserve the source rows when a reviewed boundary is partial or stale.
                malformed = True

    # A malformed accepted boundary must not be crossed by automatic inference.
    if malformed:
        return groups

    files: dict[str, list[dict]] = {}
    for row in conn.execute(f"""select t.session_id,a.audio_file_id,a.source_device,a.recorded_at,a.duration_ms,
            min(t.absolute_start_at) as first_at,max(t.absolute_end_at) as last_at,count(*) as n
        from transcript_segments t join audio_files a on a.audio_file_id=t.audio_file_id
        where t.is_active=1 and t.session_id in ({marks})
        group by t.session_id,a.audio_file_id order by a.recorded_at,a.audio_file_id""", ids):
        files.setdefault(str(row["session_id"]), []).append(dict(row))
    for sid, entries in files.items():
        by_id[sid]["segment_count"] = sum(int(entry["n"]) for entry in entries)

    def joins(left: dict, right: dict) -> bool:
        left_id, right_id = str(left["session_id"]), str(right["session_id"])
        if left_id in protected or right_id in protected or left_id in claimed or right_id in claimed:
            return False
        if left["pinned"] or right["pinned"] or left["date_key"] != right["date_key"]:
            return False
        previous, following = files.get(left_id, []), files.get(right_id, [])
        if not previous or not following:
            return False
        a, b = previous[-1], following[0]
        if not a["source_device"] or a["source_device"] != b["source_device"] or a["audio_file_id"] == b["audio_file_id"]:
            return False
        try:
            physical_gap = (_time(b["recorded_at"]) - (_time(a["recorded_at"]) + timedelta(milliseconds=int(a["duration_ms"])))).total_seconds()
            speech_gap = (_time(right["started_at"]) - _time(left["ended_at"])).total_seconds()
            return abs(physical_gap) <= 2 and -2 <= speech_gap <= gap_minutes * 60
        except (TypeError, ValueError, OverflowError):
            return False

    chain: list[str] = []
    for row in rows:
        sid = str(row["session_id"])
        if chain and joins(by_id[chain[-1]], row):
            chain.append(sid)
        else:
            if len(chain) > 1:
                digest = hashlib.blake2b("\0".join(chain).encode(), digest_size=10).hexdigest()
                groups.append(make_group("recording_" + digest, None, "continuous", chain))
            chain = [sid]
    if len(chain) > 1:
        digest = hashlib.blake2b("\0".join(chain).encode(), digest_size=10).hexdigest()
        groups.append(make_group("recording_" + digest, None, "continuous", chain))
    return sorted(groups, key=lambda group: str(group["started_at"]))
