from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "funasr_emotion2vec_wrapper", Path("scripts/funasr_emotion2vec_wrapper.py")
)
ew = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ew)


def test_normalize_emotion_picks_dominant() -> None:
    labels = ["生气/angry", "中立/neutral", "开心/happy"]
    scores = [0.1, 0.7, 0.2]
    dominant, scores_dict = ew.normalize_emotion(labels, scores)
    assert dominant == "中立/neutral"
    assert scores_dict == {"生气/angry": 0.1, "中立/neutral": 0.7, "开心/happy": 0.2}
    assert all(isinstance(v, float) for v in scores_dict.values())


def test_normalize_emotion_casts_to_float() -> None:
    import numpy as np

    labels = ["a", "b"]
    scores = np.asarray([0.25, 0.75], dtype="float32")
    dominant, scores_dict = ew.normalize_emotion(labels, scores)
    assert dominant == "b"
    assert scores_dict == {"a": 0.25, "b": 0.75}
    assert all(isinstance(v, float) for v in scores_dict.values())


def test_server_loop_emits_one_json_per_line() -> None:
    class FakeModel:
        def generate(self, audio_path, **kw):
            # one record carrying labels + scores for this audio path. The neutral score
            # scales with the path length so we can assert it threads through to the model.
            return [
                {
                    "labels": ["生气/angry", "中立/neutral", "开心/happy"],
                    "scores": [0.1, float(len(audio_path)) / 10.0, 0.2],
                }
            ]

    stdin = io.StringIO(
        json.dumps({"segment_id": "seg-1", "audio_path": "/tmp/a.wav"}) + "\n"
        + json.dumps({"segment_id": "seg-2", "audio_path": "/tmp/bb.wav"}) + "\n"
    )
    stdout = io.StringIO()
    ew.run_server(FakeModel(), stdin, stdout)

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["segment_id"] == "seg-1"
    assert lines[1]["segment_id"] == "seg-2"
    for line in lines:
        assert "label" in line
        assert isinstance(line["scores"], dict)
        assert set(line["scores"]) == {"生气/angry", "中立/neutral", "开心/happy"}
    # The neutral score is len(audio_path)/10 — both paths are >=10 chars so neutral (>=1.0)
    # dominates angry (0.1) and happy (0.2); confirms audio_path threaded through to generate().
    assert lines[0]["label"] == "中立/neutral"
    assert lines[1]["label"] == "中立/neutral"
    assert lines[0]["scores"]["中立/neutral"] == 1.0  # len("/tmp/a.wav") == 10


def test_server_loop_reports_per_item_error_without_crashing() -> None:
    class FlakyModel:
        def __init__(self):
            self.calls = 0

        def generate(self, audio_path, **kw):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return [{"labels": ["中立/neutral", "开心/happy"], "scores": [0.4, 0.6]}]

    stdin = io.StringIO(
        json.dumps({"segment_id": "bad", "audio_path": "/tmp/x.wav"}) + "\n"
        + json.dumps({"segment_id": "good", "audio_path": "/tmp/y.wav"}) + "\n"
    )
    stdout = io.StringIO()
    ew.run_server(FlakyModel(), stdin, stdout)

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == 2  # daemon survived the first bad item
    assert lines[0]["segment_id"] == "bad" and "boom" in lines[0]["error"]
    assert lines[1]["segment_id"] == "good"
    assert lines[1]["label"] == "开心/happy"
    assert lines[1]["scores"] == {"中立/neutral": 0.4, "开心/happy": 0.6}


def test_server_loop_ignores_blank_lines() -> None:
    class FakeModel:
        def generate(self, audio_path, **kw):
            return [{"labels": ["中立/neutral"], "scores": [1.0]}]

    stdin = io.StringIO(
        json.dumps({"segment_id": "s", "audio_path": "/tmp/a.wav"}) + "\n\n"
    )
    stdout = io.StringIO()
    ew.run_server(FakeModel(), stdin, stdout)
    assert len(stdout.getvalue().splitlines()) == 1


def test_maybe_half_is_noop_for_fp32() -> None:
    class FakeModel:
        pass

    model = FakeModel()
    assert ew.maybe_half(model, "fp32") is model


def test_maybe_half_casts_reachable_torch_modules_to_fp16() -> None:
    import torch.nn as nn

    class FakeAutoModel:
        def __init__(self):
            self.model = nn.Linear(4, 4)

    model = FakeAutoModel()
    result = ew.maybe_half(model, "fp16")
    assert result is model
    assert str(model.model.weight.dtype) == "torch.float16"


def test_maybe_half_falls_back_to_fp32_on_conversion_failure(caplog) -> None:
    orig_cast = ew._cast_model_half

    def _boom(_model) -> None:
        raise RuntimeError("MPS does not support fp16 for this op")

    ew._cast_model_half = _boom
    try:
        with caplog.at_level("WARNING"):
            result = ew.maybe_half(object(), "fp16")
        assert result is not None
        assert any("fp16 conversion failed" in message for message in caplog.messages)
    finally:
        ew._cast_model_half = orig_cast


def test_wrapper_cli_rejects_invalid_precision_value() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/funasr_emotion2vec_wrapper.py", "--precision", "int8"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_wrapper_cli_accepts_valid_precision_choices() -> None:
    for value in ("fp32", "fp16"):
        result = subprocess.run(
            [sys.executable, "scripts/funasr_emotion2vec_wrapper.py", "--precision", value],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
        )
        assert result.returncode == 2
        assert "invalid choice" not in result.stderr
        assert "this wrapper only runs in --server mode" in result.stderr


# ---------------------------------------------------------------------------
# Batch protocol: {"batch": [...]} in -> {"results": [...]} out (per-item loop inside).


def test_run_server_batch_line_classifies_each_item_independently() -> None:
    class FakeModel:
        def __init__(self):
            self.calls: list[str] = []

        def generate(self, audio_path, **kw):
            self.calls.append(audio_path)
            if audio_path == "/bad.wav":
                raise RuntimeError("decode failure")
            return [{"labels": ["开心/happy", "难过/sad"], "scores": [0.8, 0.2]}]

    model = FakeModel()
    stdin = io.StringIO(
        json.dumps({"batch": [
            {"segment_id": "s1", "audio_path": "/a.wav"},
            {"segment_id": "s2", "audio_path": "/bad.wav"},
            {"segment_id": "s3", "audio_path": "/c.wav"},
        ]}) + "\n"
    )
    stdout = io.StringIO()
    ew.run_server(model, stdin, stdout)

    lines = stdout.getvalue().splitlines()
    assert len(lines) == 1  # one line in -> ONE line out
    results = json.loads(lines[0])["results"]
    assert [r["segment_id"] for r in results] == ["s1", "s2", "s3"]
    assert results[0]["label"] == "开心/happy"
    assert "decode failure" in results[1]["error"]  # one bad item errors ONLY itself
    assert results[2]["label"] == "开心/happy"
    assert model.calls == ["/a.wav", "/bad.wav", "/c.wav"]  # per-item loop (no true batch API)
