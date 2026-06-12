from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app


def test_model_smoke_cli_runs_configured_vad_and_asr_commands(tmp_path: Path) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFFfake")
    vad_script = tmp_path / "fake_vad.py"
    vad_script.write_text(
        """
import json
import sys
assert sys.argv[1].endswith("sample.wav")
print(json.dumps({"ranges": [{"start_ms": 10, "end_ms": 900}]}))
""".strip(),
        encoding="utf-8",
    )
    asr_script = tmp_path / "fake_asr.py"
    asr_script.write_text(
        """
import json
import sys
assert sys.argv[1].endswith("sample.wav")
print(json.dumps({
  "model_name": "sensevoice",
  "model_version": "smoke-test",
  "segments": [{"text": "模型 smoke 输出", "start_ms": 0, "end_ms": 900, "language": "zh"}]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{tmp_path / "data"}"
obsidian_vault = "{tmp_path / "vault"}"

[vad]
backend = "command"
command = "python3 {vad_script}"

[asr]
backend = "command"
command = "python3 {asr_script}"
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["model-smoke", "--config", str(config_path), "--audio-path", str(audio)])

    assert result.exit_code == 0, result.output
    assert "status=ok" in result.output
    assert "vad_backend=CommandVADAdapter" in result.output
    assert "speech_ranges=1" in result.output
    assert "asr_backend=CommandASRAdapter" in result.output
    assert "model_name=sensevoice" in result.output
    assert "model_version=smoke-test" in result.output
    assert "transcript_segments=1" in result.output
