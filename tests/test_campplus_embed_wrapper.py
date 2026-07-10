from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location(
    "funasr_campplus_embed_wrapper", Path("scripts/funasr_campplus_embed_wrapper.py")
)
ew = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ew)


def test_normalize_embedding_flattens() -> None:
    # numpy array shape (1, 192) -> 192-length list of floats equal to the input
    arr = np.arange(192, dtype="float32").reshape(1, 192)
    out = ew.normalize_embedding(arr)
    assert isinstance(out, list)
    assert len(out) == 192
    assert all(isinstance(v, float) for v in out)
    assert out == [float(v) for v in range(192)]

    # plain nested list -> flattened 1-D list of floats
    assert ew.normalize_embedding([[1.0, 2.0, 3.0]]) == [1.0, 2.0, 3.0]

    # a fake tensor object exposing .detach().cpu().numpy() -> flattened
    class FakeTensor:
        def __init__(self, data):
            self._data = data

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.asarray(self._data)

    fake = FakeTensor([[10.0, 20.0, 30.0]])
    assert ew.normalize_embedding(fake) == [10.0, 20.0, 30.0]


def test_server_loop_emits_one_json_per_line() -> None:
    class FakeModel:
        def generate(self, *, input, **kw):
            # one record carrying the spk_embedding for this audio path
            return [{"spk_embedding": np.asarray([[float(len(input)), 1.0, 2.0]])}]

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
        assert len(line["embedding"]) == 3
        assert all(isinstance(v, float) for v in line["embedding"])
    # input path threaded through to model.generate (len("/tmp/a.wav") == 10)
    assert lines[0]["embedding"][0] == 10.0


def test_server_loop_reports_per_item_error_without_crashing() -> None:
    class FlakyModel:
        def __init__(self):
            self.calls = 0

        def generate(self, *, input, **kw):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return [{"spk_embedding": np.asarray([[1.0, 2.0, 3.0]])}]

    stdin = io.StringIO(
        json.dumps({"segment_id": "bad", "audio_path": "/tmp/x.wav"}) + "\n"
        + json.dumps({"segment_id": "good", "audio_path": "/tmp/y.wav"}) + "\n"
    )
    stdout = io.StringIO()
    ew.run_server(FlakyModel(), stdin, stdout)

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == 2  # daemon survived the first bad item
    assert lines[0]["segment_id"] == "bad" and "boom" in lines[0]["error"]
    assert lines[1]["segment_id"] == "good" and lines[1]["embedding"] == [1.0, 2.0, 3.0]


def test_server_loop_ignores_blank_lines() -> None:
    class FakeModel:
        def generate(self, *, input, **kw):
            return [{"spk_embedding": np.asarray([[1.0]])}]

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
    # Without --server this hits parser.error("this wrapper only runs in --server mode") for a
    # valid choice, but an INVALID --precision value must be rejected by argparse itself (exit 2,
    # "invalid choice") before that check even runs.
    result = subprocess.run(
        [sys.executable, "scripts/funasr_campplus_embed_wrapper.py", "--precision", "int8"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_wrapper_cli_accepts_valid_precision_choices() -> None:
    for value in ("fp32", "fp16"):
        result = subprocess.run(
            [sys.executable, "scripts/funasr_campplus_embed_wrapper.py", "--precision", value],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
        )
        # Valid --precision parses fine; falls through to the (unrelated) --server-required error.
        assert result.returncode == 2
        assert "invalid choice" not in result.stderr
        assert "this wrapper only runs in --server mode" in result.stderr
