from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from personal_context_node.agent_session_types import AgentSessionDocument, AgentToolEvent, AgentTurn
from personal_context_node.agent_sessions import import_agent_session
from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.obsidian_agent_sessions import publish_agent_session_note


def _document() -> AgentSessionDocument:
    return AgentSessionDocument(
        session_id="thread_1",
        source_type="codex_jsonl",
        source_path="/tmp/session.jsonl",
        source_sha256="abc123",
        originator="Codex Desktop",
        cli_version="0.142.0-alpha.6",
        cwd="/repo",
        model="gpt-5.5",
        started_at="2026-06-22T02:11:53.245Z",
        ended_at="2026-06-22T02:13:01.000Z",
        title="用户问题",
        turns=[
            AgentTurn(
                turn_index=1,
                role="user",
                occurred_at="2026-06-22T02:12:21.053Z",
                text="用户问题",
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
                status="called",
            )
        ],
    )


def test_publish_agent_session_note_writes_dated_managed_note(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    import_agent_session(config=config, document=_document())

    note_path = publish_agent_session_note(config=config, agent_session_id="thread_1")

    assert note_path == config.obsidian_vault / "40_Agent_Sessions" / "2026-06-22" / "thread_1.md"
    note_text = note_path.read_text(encoding="utf-8")
    assert note_text.startswith("---\nnote_type: agent_session\n")
    assert '\n---\n<!-- pcn:block start id="agent_session" kind="managed" version="1" -->\n' in note_text
    assert note_text.endswith('<!-- pcn:block end id="agent_session" -->\n')
    assert "# Codex Session thread_1" in note_text
    assert "note_type: agent_session" in note_text
    assert "source_type: codex_jsonl" in note_text
    assert "- `2026-06-22T02:12:21.053Z` **user**: 用户问题" in note_text
    assert "- `2026-06-22T02:13:01.000Z` **assistant**: 助手回答" in note_text
    assert "`exec_command` status=called call_id=call_pwd" in note_text


def test_publish_agent_session_note_raises_for_unknown_session(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    import_agent_session(config=config, document=_document())

    with pytest.raises(ValueError, match="unknown agent session: missing"):
        publish_agent_session_note(config=config, agent_session_id="missing")


def test_publish_agent_session_note_rejects_unsafe_filename(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    import_agent_session(config=config, document=replace(_document(), session_id="../outside"))

    with pytest.raises(ValueError, match="unsafe agent session id"):
        publish_agent_session_note(config=config, agent_session_id="../outside")

    assert not (config.obsidian_vault / "outside.md").exists()
    assert not (config.obsidian_vault / "40_Agent_Sessions" / "outside.md").exists()


def test_publish_agent_session_note_refuses_supcon_vault(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "Supcon")
    import_agent_session(config=config, document=_document())

    with pytest.raises(ValueError, match="refusing to write PersonalContext notes into Supcon vault"):
        publish_agent_session_note(config=config, agent_session_id="thread_1")

    assert not (config.obsidian_vault / "40_Agent_Sessions").exists()


def test_agent_publish_maps_unknown_session_to_cli_error(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    import_agent_session(config=config, document=_document())

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "publish",
            "--session-id",
            "missing",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "unknown agent session: missing" in result.output


def test_agent_publish_missing_store_does_not_create_database(tmp_path: Path) -> None:
    data_dir = tmp_path / "missing-data"

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "publish",
            "--session-id",
            "missing",
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(tmp_path / "vault"),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "agent session store not found" in result.output
    assert not (data_dir / "db" / "personal_context.sqlite").exists()
