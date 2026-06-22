from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app


def test_agent_import_codex_maps_parser_value_error_to_cli_error(tmp_path: Path) -> None:
    source = tmp_path / "bad-session.jsonl"
    source.write_text('{"type":"response_item","payload":{"type":"message"}}\n', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "import-codex",
            "--jsonl",
            str(source),
            "--data-dir",
            str(tmp_path / "data"),
            "--obsidian-vault",
            str(tmp_path / "vault"),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "missing session_meta" in result.output


def test_agent_show_maps_unknown_session_to_cli_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "agent",
            "show",
            "--session-id",
            "missing",
            "--data-dir",
            str(tmp_path / "data"),
            "--obsidian-vault",
            str(tmp_path / "vault"),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "unknown agent session: missing" in result.output
