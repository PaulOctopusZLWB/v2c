from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from personal_context_node.adapters.emotion.command import PersistentCommandEmotionAdapter


def _write_fake_wrapper(tmp_path: Path, *, body: str) -> Path:
    """Write a tiny stand-in for funasr_emotion2vec_wrapper.py --server: a resident loop that
    reads one JSON line per input on stdin and prints one JSON line per output. No model involved."""
    script = tmp_path / "fake_emotion_wrapper.py"
    script.write_text(textwrap.dedent(body))
    return script


def test_classify_returns_label_and_scores(tmp_path: Path) -> None:
    # A resident loop echoing a fixed label + scores for any line, mirroring the real wrapper's
    # one-JSON-line-in / one-JSON-line-out protocol.
    script = _write_fake_wrapper(
        tmp_path,
        body="""
        import json, sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            out = {
                "segment_id": item.get("segment_id"),
                "label": "neutral",
                "scores": {"neutral": 0.7, "happy": 0.3},
            }
            sys.stdout.write(json.dumps(out) + "\\n")
            sys.stdout.flush()
        """,
    )
    adapter = PersistentCommandEmotionAdapter(command=[sys.executable, str(script)])
    try:
        result = adapter.classify("/some/audio.wav")
        assert result == {"label": "neutral", "scores": {"neutral": 0.7, "happy": 0.3}}
        # A second call reuses the resident subprocess (lazy-spawned once).
        again = adapter.classify("/other/audio.wav")
        assert again == {"label": "neutral", "scores": {"neutral": 0.7, "happy": 0.3}}
    finally:
        adapter.close()


def test_close_terminates_subprocess(tmp_path: Path) -> None:
    script = _write_fake_wrapper(
        tmp_path,
        body="""
        import json, sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(json.dumps({"label": "x", "scores": {"x": 1.0}}) + "\\n")
            sys.stdout.flush()
        """,
    )
    adapter = PersistentCommandEmotionAdapter(command=[sys.executable, str(script)])
    adapter.classify("/a.wav")  # lazy-spawn
    proc = adapter._proc
    assert proc is not None and proc.poll() is None  # running

    adapter.close()
    assert proc.poll() is not None  # process ended
    assert adapter._proc is None


def test_error_payload_raises(tmp_path: Path) -> None:
    script = _write_fake_wrapper(
        tmp_path,
        body="""
        import json, sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(json.dumps({"segment_id": "_", "error": "boom"}) + "\\n")
            sys.stdout.flush()
        """,
    )
    adapter = PersistentCommandEmotionAdapter(command=[sys.executable, str(script)])
    try:
        with pytest.raises(RuntimeError, match="boom"):
            adapter.classify("/a.wav")
    finally:
        adapter.close()


def test_empty_command_rejected() -> None:
    with pytest.raises(ValueError):
        PersistentCommandEmotionAdapter(command=[])


# ---------------------------------------------------------------------------
# classify_batch: one wire round-trip per chunk.


def test_classify_batch_round_trip(tmp_path: Path) -> None:
    script = _write_fake_wrapper(
        tmp_path,
        body="""
        import json, sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            results = []
            for entry in item["batch"]:
                results.append({"segment_id": entry["segment_id"], "label": "happy", "scores": {"happy": 0.9}})
            sys.stdout.write(json.dumps({"results": results}) + "\\n")
            sys.stdout.flush()
        """,
    )
    adapter = PersistentCommandEmotionAdapter(command=[sys.executable, str(script)])
    try:
        results = adapter.classify_batch([("s1", "/a.wav"), ("s2", "/b.wav")])
        assert [r["segment_id"] for r in results] == ["s1", "s2"]
        assert all(r["label"] == "happy" for r in results)
    finally:
        adapter.close()


def test_classify_batch_result_count_mismatch_raises_and_closes(tmp_path: Path) -> None:
    script = _write_fake_wrapper(
        tmp_path,
        body="""
        import json, sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(json.dumps({"results": []}) + "\\n")
            sys.stdout.flush()
        """,
    )
    adapter = PersistentCommandEmotionAdapter(command=[sys.executable, str(script)])
    with pytest.raises(RuntimeError, match="batch returned 0 results for 1 items"):
        adapter.classify_batch([("s1", "/a.wav")])
    assert adapter._proc is None
