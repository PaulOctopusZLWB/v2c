"""SQLite FTS5 transcript search (design handoff Phase 6, ⌘K 语义检索第一版).

中文没有空格分词,FTS5 的 unicode61 会把整段 CJK 当成一个 token —— 所以这里用
经典的「逐字空格化」方案:索引与查询都把 CJK 字符拆成单字 token(拉丁/数字串保持
原样),查询编译成短语 match(保证连续子串语义,等价于原 LIKE 行为但走倒排索引)。

纯增量:一张独立的 FTS5 虚表 + 计数比对的惰性重建;FTS5 不可用时调用方回退 LIKE。
"""

from __future__ import annotations

import re
import sqlite3

_CJK = r"〇㐀-䶿一-鿿豈-﫿"
_CJK_CHAR = re.compile(rf"[{_CJK}]")
# 切分为:单个 CJK 字符 | 连续的非 CJK 非空白串。
_TOKEN = re.compile(rf"[{_CJK}]|[^\s{_CJK}]+")


def cjk_tokenize(text: str) -> str:
    """「项目排期abc」 → 「项 目 排 期 abc」— index/query 共用的规范形。"""
    return " ".join(_TOKEN.findall(text))


def fts_query(query: str) -> str:
    """User query → FTS5 phrase match(引号内逐字短语,保持连续子串语义)。"""
    normalized = cjk_tokenize(query).replace('"', " ")
    return f'"{normalized}"' if normalized else ""


def fts_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("create virtual table if not exists _fts_probe using fts5(x)")
        conn.execute("drop table if exists _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def ensure_fts_index(conn: sqlite3.Connection) -> None:
    """Create + lazily rebuild the FTS index when it drifts from transcript_segments.

    单用户本地库,行数在万级:计数不一致时整体重建(秒级)比维护触发器简单可靠。
    """
    conn.execute(
        """
        create virtual table if not exists transcript_fts
        using fts5(segment_id unindexed, text_tokens)
        """
    )
    fts_count = conn.execute("select count(*) from transcript_fts").fetchone()[0]
    seg_count = conn.execute("select count(*) from transcript_segments where is_active = 1").fetchone()[0]
    if int(fts_count) == int(seg_count):
        return
    conn.execute("delete from transcript_fts")
    for row in conn.execute("select segment_id, text from transcript_segments where is_active = 1"):
        conn.execute(
            "insert into transcript_fts (segment_id, text_tokens) values (?, ?)",
            (row["segment_id"], cjk_tokenize(str(row["text"] or ""))),
        )
    conn.commit()


def search_fts(conn: sqlite3.Connection, *, query: str, limit: int) -> list[dict[str, object]] | None:
    """FTS5 搜索;返回 None 时调用方回退 LIKE。结果最新在前。

    回退场景:FTS5 不可用,或查询里没有任何可索引 token(纯符号,如字面 「%」 —
    unicode61 把符号当分隔符,FTS 无从匹配,LIKE 的字面转义语义更合适)。
    """
    if not fts_available(conn):
        return None
    if not re.search(rf"[0-9A-Za-z{_CJK}]", query):
        return None
    match = fts_query(query)
    if not match:
        return []
    ensure_fts_index(conn)
    rows = conn.execute(
        """
        select ts.segment_id, ts.session_id, s.date_key as day, ts.speaker, ts.text,
               ts.absolute_start_at
        from transcript_fts f
        join transcript_segments ts on ts.segment_id = f.segment_id
        join sessions s on s.session_id = ts.session_id
        where transcript_fts match ? and ts.is_active = 1
        order by ts.absolute_start_at desc
        limit ?
        """,
        (match, limit),
    ).fetchall()
    return [dict(row) for row in rows]
