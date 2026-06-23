from __future__ import annotations

from pathlib import Path

import pytest

from personal_context_node.agent_session_types import AgentSessionDocument, AgentToolEvent, AgentTurn
from personal_context_node.agent_sessions import import_agent_session
from personal_context_node.config import AppConfig
from personal_context_node.supcon_agent_session_index import (
    publish_supcon_agent_session_indexes,
    render_supcon_agent_session_index,
)


def _document(
    *,
    session_id: str = "thread_1",
    source_path: str = "/tmp/session.jsonl",
    source_sha256: str = "abc123",
    user_text: str = "用户问题",
) -> AgentSessionDocument:
    return AgentSessionDocument(
        session_id=session_id,
        source_type="codex_jsonl",
        source_path=source_path,
        source_sha256=source_sha256,
        originator="Codex Desktop",
        cli_version="0.142.0-alpha.6",
        cwd="/Users/paul/Documents/v2c",
        model="gpt-5.5",
        started_at="2026-06-22T02:11:53.245Z",
        ended_at="2026-06-22T02:13:01.000Z",
        title=user_text,
        turns=[
            AgentTurn(
                turn_index=1,
                role="user",
                occurred_at="2026-06-22T02:12:21.053Z",
                text=user_text,
            ),
            AgentTurn(
                turn_index=2,
                role="assistant",
                occurred_at="2026-06-22T02:13:01.000Z",
                text="助手回答",
            ),
        ],
        tool_events=[
            AgentToolEvent(
                event_index=1,
                occurred_at="2026-06-22T02:12:25.176Z",
                tool_name="exec_command",
                call_id="call_pwd",
                arguments={"cmd": "pwd"},
                output_text="/repo",
                status="completed",
            )
        ],
    )


def test_render_supcon_agent_session_index_contains_traceable_evidence(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "PersonalContext")
    import_agent_session(config=config, document=_document(user_text="把 Codex 工作会话导入 PCN。"))

    markdown = render_supcon_agent_session_index(config=config, day="2026-06-22")

    assert markdown.startswith("---\nnote_type: codex_agent_session_index\n")
    assert "# Codex 工作会话 2026-06-22" in markdown
    assert "agent_session_id: thread_1" in markdown
    assert "source_path: /tmp/session.jsonl" in markdown
    assert "source_sha256: abc123" in markdown
    assert "cwd: /Users/paul/Documents/v2c" in markdown
    assert "model: gpt-5.5" in markdown
    assert "message_count: 2" in markdown
    assert "tool_event_count: 1" in markdown
    assert "thread_1:turn:1" in markdown
    assert "把 Codex 工作会话导入 PCN。" in markdown
    assert "opaque-private-field" not in markdown


def test_publish_supcon_agent_session_indexes_writes_deterministic_chinese_path(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "PersonalContext")
    import_agent_session(config=config, document=_document())
    supcon_vault = tmp_path / "Supcon"
    (supcon_vault / ".obsidian").mkdir(parents=True)

    first_paths = publish_supcon_agent_session_indexes(
        config=config, days=["2026-06-22"], supcon_vault=supcon_vault
    )
    first_text = first_paths[0].read_text(encoding="utf-8")
    second_paths = publish_supcon_agent_session_indexes(
        config=config, days=["2026-06-22"], supcon_vault=supcon_vault
    )
    second_text = second_paths[0].read_text(encoding="utf-8")

    assert first_paths == [supcon_vault / "Codex 工作会话" / "2026-06-22.md"]
    assert second_paths == first_paths
    assert second_text == first_text


def test_publish_supcon_agent_session_indexes_requires_obsidian_vault(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "PersonalContext")
    import_agent_session(config=config, document=_document())

    with pytest.raises(ValueError, match="Supcon Obsidian vault not found"):
        publish_supcon_agent_session_indexes(
            config=config, days=["2026-06-22"], supcon_vault=tmp_path / "not-a-vault"
        )
