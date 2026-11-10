"""Person-name validation and transactional changes to the live identity records."""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


class DuplicatePersonName(ValueError):
    pass


class PersonNameChanged(ValueError):
    pass


class PersonNotFound(ValueError):
    pass


def person_name(name: str) -> tuple[str, str]:
    display_name = name.strip()
    key = unicodedata.normalize("NFKC", display_name).strip().casefold()
    if not key:
        raise ValueError("人物名称不能为空")
    return display_name, key


def matching_person(conn, name: str, *, exclude_person_id: str | None = None):
    """Compare every stored name, including historical non-normalized names."""
    _, key = person_name(name)
    for row in conn.execute("select person_id, display_name, person_type, is_self from persons"):
        stored_key = unicodedata.normalize("NFKC", str(row["display_name"]).strip()).strip().casefold()
        if row["person_id"] != exclude_person_id and stored_key == key:
            return row
    return None


def require_available_person_name(conn, name: str, *, exclude_person_id: str | None = None) -> str:
    display_name, _ = person_name(name)
    if matching_person(conn, display_name, exclude_person_id=exclude_person_id) is not None:
        raise DuplicatePersonName("人物名称已存在，请使用其他名称")
    return display_name


def create_person_record(
    *, config: AppConfig, display_name: str, person_type: str = "contact", reuse_existing: bool = False
) -> dict[str, object]:
    display_name, _ = person_name(display_name)
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute("begin immediate")
        existing = matching_person(conn, display_name)
        if existing is not None:
            if reuse_existing and (person_type not in {"non_speaker", "unknown_voice"} or existing["person_type"] == person_type):
                conn.commit()
                return {"person_id": str(existing["person_id"]), "display_name": str(existing["display_name"]),
                        "person_type": str(existing["person_type"]), "is_self": int(existing["is_self"])}
            raise DuplicatePersonName("人物名称已存在，请使用其他名称")
        person_id = f"per_{uuid4().hex}"
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, 0, ?, ?)",
            (person_id, display_name, person_type, now, now),
        )
        conn.commit()
        return {"person_id": person_id, "display_name": display_name, "person_type": person_type, "is_self": 0}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def rename_person_record(
    *, config: AppConfig, person_id: str, display_name: str, expected_display_name: str | None = None
) -> dict[str, object]:
    display_name, _ = person_name(display_name)
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute("begin immediate")
        row = conn.execute("select person_id, display_name, person_type, is_self from persons where person_id = ?", (person_id,)).fetchone()
        if row is None:
            raise PersonNotFound("人物不存在")
        if row["person_type"] in {"non_speaker", "unknown_voice"}:
            raise ValueError("噪音和未知声音是分类，不能作为人物改名")
        old_name = str(row["display_name"])
        if expected_display_name is not None and old_name != expected_display_name:
            raise PersonNameChanged("人物名称已变更，请刷新后重试")
        require_available_person_name(conn, display_name, exclude_person_id=person_id)
        if display_name != old_name:
            conn.execute("update persons set display_name = ?, updated_at = ? where person_id = ?", (display_name, now, person_id))
            conn.execute("update segment_person_overrides set person_label = ? where person_id = ? and person_label != ?", (display_name, person_id, display_name))
            conn.execute("update speaker_mappings set person_label = ? where person_id = ? and person_label != ?", (display_name, person_id, display_name))
            conn.execute(
                "insert into person_name_overrides (person_id, updated_at) values (?, ?) "
                "on conflict(person_id) do update set updated_at=excluded.updated_at", (person_id, now)
            )
        conn.commit()
        return {"person_id": person_id, "display_name": display_name, "person_type": str(row["person_type"]), "is_self": int(row["is_self"])}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
