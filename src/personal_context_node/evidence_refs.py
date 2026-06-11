from __future__ import annotations

import json
import sqlite3

from personal_context_node.core.protocols.memory import EvidenceRef


def evidence_ids_from_candidate_json(evidence_refs_json: str) -> list[str]:
    refs = json.loads(evidence_refs_json)
    ids: list[str] = []
    for item in refs:
        if isinstance(item, str):
            ids.append(item)
            continue
        if isinstance(item, dict) and item.get("evidence_id"):
            ids.append(str(item["evidence_id"]))
    return ids


def hydrate_candidate_evidence_refs(conn: sqlite3.Connection, evidence_refs_json: str) -> list[EvidenceRef]:
    refs = json.loads(evidence_refs_json)
    if refs and all(isinstance(item, dict) for item in refs):
        return [EvidenceRef.model_validate(item) for item in refs]

    ids = evidence_ids_from_candidate_json(evidence_refs_json)
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        select evidence_id, source_type, source_id, quote, summary
        from evidence_refs
        where evidence_id in ({placeholders})
        """,
        tuple(ids),
    ).fetchall()
    by_id = {str(row["evidence_id"]): row for row in rows}
    evidence_refs: list[EvidenceRef] = []
    for evidence_id in ids:
        row = by_id.get(evidence_id)
        if row is None:
            continue
        evidence_refs.append(
            EvidenceRef(
                evidence_id=str(row["evidence_id"]),
                source_type=str(row["source_type"]),
                source_id=str(row["source_id"]),
                quote=str(row["quote"] or ""),
                summary=str(row["summary"]) if row["summary"] is not None else None,
            )
        )
    return evidence_refs
