from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "funasr_paraformer_diarize_wrapper", Path("scripts/funasr_paraformer_diarize_wrapper.py")
)
fw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fw)


def test_resolve_device_prefers_mps_when_available() -> None:
    assert fw.resolve_device("mps", mps_available=lambda: True) == "mps"
    assert fw.resolve_device("mps", mps_available=lambda: False) == "cpu"  # graceful fallback
    assert fw.resolve_device("cpu", mps_available=lambda: True) == "cpu"   # explicit override respected


def test_normalize_diarized_maps_two_speakers_by_first_appearance() -> None:
    sentence_info = [
        {"text": "<|zh|>你好", "start": 0, "end": 1000, "spk": 0, "timestamp": [[0, 1000]]},
        {"text": "<|zh|>再见", "start": 1000, "end": 2000, "spk": 1, "timestamp": [[1000, 2000]]},
    ]
    segments = fw.normalize_diarized(sentence_info)
    assert [s["speaker"] for s in segments] == ["spk_01", "spk_02"]
    assert [s["text"] for s in segments] == ["你好", "再见"]
    assert [(s["start_ms"], s["end_ms"]) for s in segments] == [(0, 1000), (1000, 2000)]
    assert all(s["language"] == "zh" for s in segments)
    assert all(s["confidence"] is None for s in segments)


def test_normalize_diarized_orders_by_first_appearance_not_int_value() -> None:
    # spk sequence [2, 2, 0] -> "spk_01", "spk_01", "spk_02"
    sentence_info = [
        {"text": "<|zh|>一", "start": 0, "end": 100, "spk": 2},
        {"text": "<|zh|>二", "start": 100, "end": 200, "spk": 2},
        {"text": "<|zh|>三", "start": 200, "end": 300, "spk": 0},
    ]
    segments = fw.normalize_diarized(sentence_info)
    assert [s["speaker"] for s in segments] == ["spk_01", "spk_01", "spk_02"]


def test_normalize_diarized_single_speaker_collapses_to_self() -> None:
    sentence_info = [
        {"text": "<|zh|>一", "start": 0, "end": 100, "spk": 0},
        {"text": "<|zh|>二", "start": 100, "end": 200, "spk": 0},
    ]
    segments = fw.normalize_diarized(sentence_info)
    assert [s["speaker"] for s in segments] == ["self", "self"]


def test_normalize_diarized_missing_spk_collapses_to_self() -> None:
    sentence_info = [
        {"text": "<|zh|>一", "start": 0, "end": 100},
        {"text": "<|zh|>二", "start": 100, "end": 200},
    ]
    segments = fw.normalize_diarized(sentence_info)
    assert [s["speaker"] for s in segments] == ["self", "self"]


def test_normalize_diarized_skips_non_dict_and_defaults_missing_keys() -> None:
    sentence_info = [
        "not a dict",
        {"text": "<|zh|>有效", "spk": 0},
        {"text": "<|zh|>另一个", "spk": 1},
    ]
    segments = fw.normalize_diarized(sentence_info)
    assert [s["text"] for s in segments] == ["有效", "另一个"]
    assert [s["speaker"] for s in segments] == ["spk_01", "spk_02"]
    assert segments[0]["start_ms"] == 0 and segments[0]["end_ms"] == 0


def test_server_mode_accepts_preset_spk_num_flag(tmp_path: Path) -> None:
    # build_asr appends --preset-spk-num when config.asr_preset_spk_num is set; the wrapper's
    # argparse MUST accept it, or the resident server exits 2 on "unrecognized arguments" before
    # it can boot and every diarize task fails. Shadow funasr so we reach the import guard fast
    # (argparse runs BEFORE the funasr import, so an unrecognized flag would fail earlier).
    (tmp_path / "funasr.py").write_text("raise ImportError('blocked for test')\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", "")}

    proc = subprocess.run(
        [sys.executable, "scripts/funasr_paraformer_diarize_wrapper.py", "--server", "--preset-spk-num", "2"],
        input="", capture_output=True, text=True, env=env,
    )

    assert "unrecognized arguments" not in proc.stderr  # argparse accepted --preset-spk-num
    assert proc.returncode == 2 and "FunASR is not installed" in proc.stderr  # reached the import guard


def test_run_server_forwards_preset_spk_num_to_generate(tmp_path: Path) -> None:
    captured: dict = {}

    class FakeModel:
        def generate(self, *, input, **kw):
            captured.update(kw)
            return [{"sentence_info": [{"text": "x", "start": 0, "end": 1, "spk": 0}]}]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    fw.run_server(FakeModel(), io.StringIO(f"{a}\n"), io.StringIO(), language="zh", preset_spk_num=3)
    assert captured.get("preset_spk_num") == 3


def test_run_server_omits_preset_spk_num_when_none(tmp_path: Path) -> None:
    captured: dict = {}

    class FakeModel:
        def generate(self, *, input, **kw):
            captured.update(kw)
            return [{"sentence_info": []}]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    fw.run_server(FakeModel(), io.StringIO(f"{a}\n"), io.StringIO(), language="zh")
    assert "preset_spk_num" not in captured  # not passed when unset (FunASR auto-clusters)


def test_run_server_emits_one_result_line_per_path_with_speakers(tmp_path: Path) -> None:
    class FakeModel:
        def generate(self, *, input, **kw):
            return [{
                "sentence_info": [
                    {"text": "<|zh|>你好", "start": 0, "end": 1000, "spk": 0},
                    {"text": "<|zh|>再见", "start": 1000, "end": 2000, "spk": 1},
                ]
            }]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    b = tmp_path / "b.wav"; b.write_bytes(b"")
    stdin = io.StringIO(f"{a}\n\n{b}\n")  # blank line ignored
    stdout = io.StringIO()

    fw.run_server(FakeModel(), stdin, stdout, language="zh")

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["model_name"] == "paraformer-diarize"
    assert [s["speaker"] for s in lines[0]["segments"]] == ["spk_01", "spk_02"]
    assert lines[0]["segments"][0]["text"] == "你好"


def test_run_server_flags_missing_file_as_terminal_without_calling_model(tmp_path: Path) -> None:
    class NeverCalledModel:
        def generate(self, *, input, **kw):
            raise AssertionError("model.generate must not run for a missing file")

    missing = tmp_path / "gone.wav"  # never created
    stdout = io.StringIO()
    fw.run_server(NeverCalledModel(), io.StringIO(f"{missing}\n"), stdout, language="zh")

    out = json.loads(stdout.getvalue())
    assert out.get("terminal") is True
    assert "does not exist" in out["error"]


def test_run_server_model_raises_is_retryable_and_loop_survives(tmp_path: Path) -> None:
    class FlakyModel:
        def __init__(self):
            self.calls = 0

        def generate(self, *, input, **kw):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("decode failed")
            return [{"sentence_info": [{"text": "<|zh|>好", "start": 0, "end": 1, "spk": 0}]}]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    b = tmp_path / "b.wav"; b.write_bytes(b"")
    stdout = io.StringIO()
    fw.run_server(FlakyModel(), io.StringIO(f"{a}\n{b}\n"), stdout, language="zh")

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == 2
    assert "error" in lines[0] and "decode failed" in lines[0]["error"]
    assert not lines[0].get("terminal")  # model decode failure is transient -> retryable
    assert "segments" in lines[1]  # loop survived, second good path produced a result


def test_run_server_uses_given_model_version(tmp_path: Path) -> None:
    class FakeModel:
        def generate(self, *, input, **kw):
            return [{"sentence_info": [{"text": "<|zh|>好", "start": 0, "end": 1, "spk": 0}]}]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    out = io.StringIO()
    fw.run_server(FakeModel(), io.StringIO(f"{a}\n"), out, language="zh", model_version="custom-v9")

    assert json.loads(out.getvalue())["model_version"] == "custom-v9"


def test_run_server_redirects_model_stdout_away_from_protocol(tmp_path: Path) -> None:
    class NoisyModel:
        def generate(self, *, input, **kw):
            print("FUNASR PROGRESS noise")  # would corrupt the JSON line if not redirected
            return [{"sentence_info": [{"text": "<|zh|>好", "start": 0, "end": 1, "spk": 0}]}]

    a = tmp_path / "a.wav"; a.write_bytes(b"")
    out = io.StringIO()
    fw.run_server(NoisyModel(), io.StringIO(f"{a}\n"), out, language="zh")

    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    json.loads(lines[0])  # pure JSON, no "noise" prefix
    assert "noise" not in out.getvalue()
