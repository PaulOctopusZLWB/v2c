from __future__ import annotations

import importlib.util
import io
import json
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
