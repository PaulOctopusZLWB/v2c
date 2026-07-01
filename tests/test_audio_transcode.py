from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from personal_context_node import audio_transcode


def test_normalize_to_wav_rejects_missing_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio_transcode.shutil, "which", lambda _: None)
    monkeypatch.setattr(audio_transcode, "_imageio_ffmpeg_executable", lambda: None)
    src = tmp_path / "sample.m4a"
    src.write_bytes(b"fake-m4a")
    dst = tmp_path / "out.wav"

    with pytest.raises(RuntimeError, match="ffmpeg is required"):
        audio_transcode.normalize_to_wav(source_path=src, target_path=dst, timeout_seconds=1.0)


def test_normalize_to_wav_invokes_ffmpeg_and_writes_wav(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "sample.m4a"
    src.write_bytes(b"fake-m4a")
    dst = tmp_path / "out.wav"
    called: dict[str, object] = {}

    def fake_which(_: str) -> str | None:
        return "/usr/bin/ffmpeg"

    def fake_run_command(cmd: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
        called["cmd"] = cmd
        called["timeout"] = timeout_seconds
        dst.write_bytes(b"RIFF")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(audio_transcode.shutil, "which", fake_which)
    monkeypatch.setattr(audio_transcode, "run_command", fake_run_command)

    output = audio_transcode.normalize_to_wav(source_path=src, target_path=dst, timeout_seconds=12.0)

    assert output == dst
    assert output.exists()
    assert called["cmd"][0] == "/usr/bin/ffmpeg"
    assert called["timeout"] == 12.0


def test_normalize_to_wav_uses_bundled_ffmpeg_when_not_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "sample.m4a"
    src.write_bytes(b"fake-m4a")
    dst = tmp_path / "out.wav"
    called: dict[str, object] = {}

    def fake_run_command(cmd: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
        called["cmd"] = cmd
        dst.write_bytes(b"RIFF")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(audio_transcode.shutil, "which", lambda _: None)
    monkeypatch.setattr(audio_transcode, "_imageio_ffmpeg_executable", lambda: "/tmp/bundled-ffmpeg", raising=False)
    monkeypatch.setattr(audio_transcode, "run_command", fake_run_command)

    output = audio_transcode.normalize_to_wav(source_path=src, target_path=dst, timeout_seconds=12.0)

    assert output == dst
    assert called["cmd"][0] == "/tmp/bundled-ffmpeg"
