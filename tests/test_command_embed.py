from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from personal_context_node.adapters.embed.command import PersistentCommandEmbedAdapter


def _write_fake_wrapper(tmp_path: Path, *, body: str) -> Path:
    """Write a tiny stand-in for funasr_campplus_embed_wrapper.py --server: a resident loop that
    reads one JSON line per input on stdin and prints one JSON line per output. No model involved."""
    script = tmp_path / "fake_embed_wrapper.py"
    script.write_text(textwrap.dedent(body))
    return script


def test_embed_returns_embedding(tmp_path: Path) -> None:
    # A resident loop echoing a fixed 4-float embedding for any line, mirroring the real wrapper's
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
            out = {"segment_id": item.get("segment_id"), "embedding": [0.1, 0.2, 0.3, 0.4]}
            sys.stdout.write(json.dumps(out) + "\\n")
            sys.stdout.flush()
        """,
    )
    adapter = PersistentCommandEmbedAdapter(command=[sys.executable, str(script)])
    try:
        vector = adapter.embed("/some/audio.wav")
        assert vector == [0.1, 0.2, 0.3, 0.4]
        # A second call reuses the resident subprocess (lazy-spawned once).
        again = adapter.embed("/other/audio.wav")
        assert again == [0.1, 0.2, 0.3, 0.4]
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
            sys.stdout.write(json.dumps({"embedding": [1.0]}) + "\\n")
            sys.stdout.flush()
        """,
    )
    adapter = PersistentCommandEmbedAdapter(command=[sys.executable, str(script)])
    adapter.embed("/a.wav")  # lazy-spawn
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
    adapter = PersistentCommandEmbedAdapter(command=[sys.executable, str(script)])
    try:
        with pytest.raises(RuntimeError, match="boom"):
            adapter.embed("/a.wav")
    finally:
        adapter.close()


def test_empty_command_rejected() -> None:
    with pytest.raises(ValueError):
        PersistentCommandEmbedAdapter(command=[])


# ---------------------------------------------------------------------------
# embed_batch: one wire round-trip per bucket.


def test_embed_batch_round_trip(tmp_path: Path) -> None:
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
                if entry["audio_path"].endswith("bad.wav"):
                    results.append({"segment_id": entry["segment_id"], "error": "corrupt"})
                else:
                    results.append({"segment_id": entry["segment_id"], "embedding": [0.1, 0.2]})
            sys.stdout.write(json.dumps({"results": results}) + "\\n")
            sys.stdout.flush()
        """,
    )
    adapter = PersistentCommandEmbedAdapter(command=[sys.executable, str(script)])
    try:
        results = adapter.embed_batch([("s1", "/a.wav"), ("s2", "/bad.wav"), ("s3", "/c.wav")])
        # Per-item error entries ride the result list without raising -- only protocol-level
        # failures raise (see mismatch test below).
        assert [r["segment_id"] for r in results] == ["s1", "s2", "s3"]
        assert results[0]["embedding"] == [0.1, 0.2]
        assert results[1]["error"] == "corrupt"
        assert results[2]["embedding"] == [0.1, 0.2]
    finally:
        adapter.close()


def test_embed_batch_result_count_mismatch_raises_and_closes(tmp_path: Path) -> None:
    script = _write_fake_wrapper(
        tmp_path,
        body="""
        import json, sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(json.dumps({"results": [{"segment_id": "only-one", "embedding": [1.0]}]}) + "\\n")
            sys.stdout.flush()
        """,
    )
    adapter = PersistentCommandEmbedAdapter(command=[sys.executable, str(script)])
    with pytest.raises(RuntimeError, match="batch returned 1 results for 2 items"):
        adapter.embed_batch([("s1", "/a.wav"), ("s2", "/b.wav")])
    # The stream can no longer be trusted to stay in sync -> the server was killed.
    assert adapter._proc is None


def test_embed_batch_empty_items_is_noop() -> None:
    # A command that could never spawn successfully: proof the empty batch never touches it.
    adapter = PersistentCommandEmbedAdapter(command=["/nonexistent/embed-server"])
    assert adapter.embed_batch([]) == []
    assert adapter._proc is None
