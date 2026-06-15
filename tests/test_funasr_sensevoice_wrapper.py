from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("funasr_wrapper", Path("scripts/funasr_sensevoice_wrapper.py"))
fw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fw)


def test_resolve_device_prefers_mps_when_available() -> None:
    assert fw.resolve_device("mps", mps_available=lambda: True) == "mps"
    assert fw.resolve_device("mps", mps_available=lambda: False) == "cpu"  # graceful fallback
    assert fw.resolve_device("cpu", mps_available=lambda: True) == "cpu"   # explicit override respected


def test_run_server_emits_one_result_line_per_chunk_path(tmp_path: Path) -> None:
    class FakeModel:
        def generate(self, *, input, **kw):
            return [{"text": f"<|zh|>转写 {input}", "timestamp": [0, 1000]}]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    b = tmp_path / "b.wav"; b.write_bytes(b"")
    stdin = io.StringIO(f"{a}\n\n{b}\n")   # blank line ignored
    stdout = io.StringIO()

    fw.run_server(FakeModel(), stdin, stdout, language="zh")

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["segments"][0]["text"] == f"转写 {a}"
    assert lines[0]["model_name"] == "sensevoice"


def test_run_server_reports_per_chunk_error_without_crashing(tmp_path: Path) -> None:
    class BoomModel:
        def generate(self, *, input, **kw):
            raise RuntimeError("decode failed")

    x = tmp_path / "x.wav"; x.write_bytes(b"")
    stdout = io.StringIO()
    fw.run_server(BoomModel(), io.StringIO(f"{x}\n"), stdout, language="zh")

    out = json.loads(stdout.getvalue())
    assert "error" in out and "decode failed" in out["error"]
    assert not out.get("terminal")  # a model decode failure is transient -> retryable, NOT terminal


def test_run_server_flags_missing_chunk_file_as_terminal(tmp_path: Path) -> None:
    # A missing chunk path is permanently-unsupported input (it will never appear on retry):
    # the server must emit a terminal-flagged error WITHOUT invoking the model, mirroring the
    # one-shot path's exit-code-3 contract so the task fails fast instead of retrying to exhaustion.
    class NeverCalledModel:
        def generate(self, *, input, **kw):
            raise AssertionError("model.generate must not run for a missing chunk file")

    missing = tmp_path / "gone.wav"  # never created
    stdout = io.StringIO()
    fw.run_server(NeverCalledModel(), io.StringIO(f"{missing}\n"), stdout, language="zh")

    out = json.loads(stdout.getvalue())
    assert out.get("terminal") is True
    assert "does not exist" in out["error"]


def test_funasr_sensevoice_wrapper_normalizes_sentence_info(tmp_path: Path) -> None:
    fake_package = tmp_path / "fake_package"
    funasr_dir = fake_package / "funasr"
    funasr_dir.mkdir(parents=True)
    (funasr_dir / "__init__.py").write_text(
        """
print("funasr import noise")

class AutoModel:
    def __init__(self, **kwargs):
        print("funasr init noise")
        self.kwargs = kwargs

    def generate(self, input, **kwargs):
        print("funasr generate noise")
        return [{
            "text": "<|zh|><|EMO_UNKNOWN|><|Speech|><|withitn|>完整文本",
            "sentence_info": [
                {"text": "<|zh|><|EMO_UNKNOWN|><|Speech|><|withitn|>第一句", "start": 0, "end": 1200, "spk": "spk0"},
                {"text": "<|yue|><|EMO_UNKNOWN|><|Speech|><|withitn|>Yeah.", "timestamp": [1200, 2400], "speaker": "spk1"},
            ],
        }]
""",
        encoding="utf-8",
    )
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFFfake")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/funasr_sensevoice_wrapper.py",
            str(audio),
            "--model",
            "iic/SenseVoiceSmall",
            "--model-version",
            "test-version",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONPATH": str(fake_package)},
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "model_name": "sensevoice",
        "model_version": "test-version",
        "segments": [
            {
                "text": "第一句",
                "tags": ["zh", "EMO_UNKNOWN", "Speech", "withitn"],
                "start_ms": 0,
                "end_ms": 1200,
                "confidence": None,
                "language": "zh",
                "speaker": "spk0",
            },
            {
                "text": "Yeah.",
                "tags": ["yue", "EMO_UNKNOWN", "Speech", "withitn"],
                "start_ms": 1200,
                "end_ms": 2400,
                "confidence": None,
                "language": "zh",
                "speaker": "spk1",
            },
        ],
    }
    assert result.stderr.splitlines() == ["funasr import noise", "funasr init noise", "funasr generate noise"]


def test_funasr_sensevoice_wrapper_reports_missing_dependency(tmp_path: Path) -> None:
    fake_package = tmp_path / "fake_package"
    funasr_dir = fake_package / "funasr"
    funasr_dir.mkdir(parents=True)
    (funasr_dir / "__init__.py").write_text('raise ImportError("blocked funasr import")\n', encoding="utf-8")
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFFfake")

    result = subprocess.run(
        [sys.executable, "scripts/funasr_sensevoice_wrapper.py", str(audio)],
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONPATH": str(fake_package)},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "FunASR is not installed" in result.stderr


def test_run_server_honors_batch_size_s(tmp_path: Path) -> None:
    captured: dict = {}

    class FakeModel:
        def generate(self, *, input, **kw):
            captured.update(kw)
            return [{"text": "x", "timestamp": [0, 1]}]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    fw.run_server(FakeModel(), io.StringIO(f"{a}\n"), io.StringIO(), language="zh", batch_size_s=42)

    assert captured["batch_size_s"] == 42


def test_run_server_redirects_model_stdout_away_from_protocol(tmp_path: Path) -> None:
    # FunASR/tqdm may print to stdout during inference; run_server must redirect it so the
    # one-line-per-result protocol stream stays pure JSON.
    class NoisyModel:
        def generate(self, *, input, **kw):
            print("FUNASR PROGRESS noise")  # would corrupt the JSON line if not redirected
            return [{"text": "x", "timestamp": [0, 1]}]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    out = io.StringIO()
    fw.run_server(NoisyModel(), io.StringIO(f"{a}\n"), out, language="zh")

    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    json.loads(lines[0])  # pure JSON, no "noise" prefix
    assert "noise" not in out.getvalue()


def test_run_server_uses_given_model_version(tmp_path: Path) -> None:
    class FakeModel:
        def generate(self, *, input, **kw):
            return [{"text": "x", "timestamp": [0, 1]}]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    out = io.StringIO()
    fw.run_server(FakeModel(), io.StringIO(f"{a}\n"), out, language="zh", model_version="custom-v9")

    assert json.loads(out.getvalue())["model_version"] == "custom-v9"


def test_server_mode_reports_missing_funasr_clearly(tmp_path: Path) -> None:
    import os

    # Shadow funasr with a stub that raises ImportError so the --server import-guard fires.
    (tmp_path / "funasr.py").write_text("raise ImportError('blocked for test')\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", "")}

    proc = subprocess.run(
        [sys.executable, "scripts/funasr_sensevoice_wrapper.py", "--server"],
        input="", capture_output=True, text=True, env=env,
    )

    assert proc.returncode == 2
    assert "FunASR is not installed" in proc.stderr
