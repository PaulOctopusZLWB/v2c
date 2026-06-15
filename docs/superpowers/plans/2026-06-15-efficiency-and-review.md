# Efficiency And Review Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce audio preprocessing memory pressure and establish the repeated three-review loop needed to drive the project toward production readiness.

**Architecture:** Optimize inside the existing audio preprocessing module first. Keep command-model daemonization deferred; review agents should be read-only and scoped by subsystem.

**Tech Stack:** Python wave/struct, pytest, multi-agent read-only review, existing Vite/Pytest verification.

---

## File Structure

- Modify `src/personal_context_node/config.py`: default `max_chunk_ms`.
- Modify `src/personal_context_node/audio_preprocessing.py`: bounded reads/conversion for PCM and IEEE float WAV chunks.
- Test `tests/test_config.py`: default chunk size.
- Test `tests/test_audio_preprocessing.py`: bounded conversion behavior and float metadata scanning.
- Use multi-agent read-only review after production safety, operations startup, Web usability, and efficiency batches.

## Task 1: Lower Production Chunk Default

**Files:**
- Modify: `tests/test_config.py`
- Modify: `src/personal_context_node/config.py`

- [ ] **Step 1: Write failing default chunk test**

Add to `tests/test_config.py`:

```python
def test_default_max_chunk_ms_is_bounded_for_production_audio() -> None:
    config = AppConfig()

    assert config.max_chunk_ms == 120_000
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_config.py::test_default_max_chunk_ms_is_bounded_for_production_audio
```

Expected: FAIL because default is currently `900_000`.

- [ ] **Step 3: Implement default**

In `src/personal_context_node/config.py`, change:

```python
max_chunk_ms: int = 900_000
```

to:

```python
max_chunk_ms: int = 120_000
```

- [ ] **Step 4: Run config tests**

```bash
uv run pytest -q tests/test_config.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/config.py tests/test_config.py
git commit -m "perf: lower default audio chunk duration"
```

## Task 2: Avoid Whole-File IEEE Float Reads

**Files:**
- Modify: `tests/test_audio_preprocessing.py`
- Modify: `src/personal_context_node/audio_preprocessing.py`

- [ ] **Step 1: Write metadata scanning unit test**

Add to `tests/test_audio_preprocessing.py`:

```python
def test_read_wav_metadata_stores_data_offset_not_payload(tmp_path: Path) -> None:
    path = tmp_path / "float.wav"
    _write_ieee_float_wav(path, samples=[0.1, 0.2, 0.3, 0.4], sample_rate=16000, channels=1)

    metadata = _read_wav_metadata(path)

    assert "data" not in metadata
    assert metadata["data_offset"] > 0
    assert metadata["data_size"] == 16
```

Add this helper near `_write_wav` in `tests/test_audio_preprocessing.py`:

```python
def _write_ieee_float_wav(path: Path, *, samples: list[float], sample_rate: int, channels: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(struct.pack("<f", sample) for sample in samples)
    byte_rate = sample_rate * channels * 4
    block_align = channels * 4
    fmt = struct.pack("<HHIIHH", 3, channels, sample_rate, byte_rate, block_align, 32)
    payload = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WAVE" + payload)
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_audio_preprocessing.py::test_read_wav_metadata_stores_data_offset_not_payload
```

Expected: FAIL because metadata currently stores full `data` bytes.

- [ ] **Step 3: Change metadata shape**

In `_read_wav_metadata`, replace:

```python
payload = b""
...
elif chunk_id == b"data":
    payload = chunk_data
...
if fmt is None or not payload:
    raise ValueError("invalid WAV file")
fmt["data"] = payload
```

with:

```python
data_offset: int | None = None
data_size = 0
...
elif chunk_id == b"data":
    data_offset = chunk_start
    data_size = chunk_size
...
if fmt is None or data_offset is None or data_size <= 0:
    raise ValueError("invalid WAV file")
fmt["data_offset"] = data_offset
fmt["data_size"] = data_size
```

- [ ] **Step 4: Read only requested float range**

In `_convert_ieee_float_wav_chunk`, replace:

```python
data = metadata["data"]
segment = data[start_frame * bytes_per_frame : (start_frame + frame_count) * bytes_per_frame]
```

with:

```python
data_offset = int(metadata["data_offset"])
data_size = int(metadata["data_size"])
start_byte = min(data_size, start_frame * bytes_per_frame)
end_byte = min(data_size, (start_frame + frame_count) * bytes_per_frame)
with source_path.open("rb") as handle:
    handle.seek(data_offset + start_byte)
    segment = handle.read(max(0, end_byte - start_byte))
```

- [ ] **Step 5: Run audio preprocessing tests**

```bash
uv run pytest -q tests/test_audio_preprocessing.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_context_node/audio_preprocessing.py tests/test_audio_preprocessing.py
git commit -m "perf: avoid whole file float wav reads"
```

## Task 3: Bound PCM Conversion Memory

**Files:**
- Modify: `src/personal_context_node/audio_preprocessing.py`
- Modify: `tests/test_audio_preprocessing.py`

- [ ] **Step 1: Add block conversion parity test**

Add:

```python
def test_pcm_conversion_matches_existing_output_for_small_blocks() -> None:
    frames = b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in [100, -100, 200, -200])

    whole = _convert_pcm_frames(
        frames,
        source_sample_rate=16000,
        source_channels=1,
        source_sample_width=2,
        target_sample_rate=16000,
        target_channels=1,
        target_sample_width=2,
    )
    blocked = _convert_pcm_frames_blocked(
        frames,
        source_sample_rate=16000,
        source_channels=1,
        source_sample_width=2,
        target_sample_rate=16000,
        target_channels=1,
        target_sample_width=2,
        block_frames=2,
    )

    assert blocked == whole
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_audio_preprocessing.py::test_pcm_conversion_matches_existing_output_for_small_blocks
```

Expected: FAIL because `_convert_pcm_frames_blocked` does not exist.

- [ ] **Step 3: Add bounded helper**

In `audio_preprocessing.py`, add:

```python
def _convert_pcm_frames_blocked(
    frames: bytes,
    *,
    source_sample_rate: int,
    source_channels: int,
    source_sample_width: int,
    target_sample_rate: int,
    target_channels: int,
    target_sample_width: int,
    block_frames: int = 16000,
) -> bytes:
    source_frame_width = source_sample_width * source_channels
    block_bytes = max(source_frame_width, block_frames * source_frame_width)
    converted = bytearray()
    for offset in range(0, len(frames), block_bytes):
        block = frames[offset : offset + block_bytes]
        converted.extend(
            _convert_pcm_frames(
                block,
                source_sample_rate=source_sample_rate,
                source_channels=source_channels,
                source_sample_width=source_sample_width,
                target_sample_rate=target_sample_rate,
                target_channels=target_channels,
                target_sample_width=target_sample_width,
            )
        )
    return bytes(converted)
```

- [ ] **Step 4: Use bounded helper in `_write_chunk`**

Replace:

```python
frames = _convert_pcm_frames(
```

with:

```python
frames = _convert_pcm_frames_blocked(
```

and pass existing arguments.

- [ ] **Step 5: Run audio tests**

```bash
uv run pytest -q tests/test_audio_preprocessing.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_context_node/audio_preprocessing.py tests/test_audio_preprocessing.py
git commit -m "perf: bound pcm conversion memory"
```

## Task 4: Verification Sweep

**Files:**
- No code edits unless verification exposes a regression.

- [ ] **Step 1: Run full Python suite**

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run Web suite and build**

```bash
cd web && npm test && npm run build
```

Expected: PASS.

- [ ] **Step 3: Run e2e**

```bash
cd web && npm run e2e
```

Expected: PASS when the deterministic e2e setup from the Web/operations plans is implemented. If it fails because a live external model stack is unavailable, stop this verification step and ask the user whether to start the live stack or accept the backend smoke below for this environment:

```bash
uv run pytest -q tests/test_web_e2e.py
```

- [ ] **Step 4: Commit verification-only fixes**

```bash
git status --short
```

Expected: no output after verification-only fixes have either been committed with exact file paths or no fixes were necessary. Do not create an empty commit.

If no code changed, do not create an empty commit.

## Task 5: Three-Review Loop

**Files:**
- No direct file edits by review subagents.

- [ ] **Step 1: Dispatch backend/data safety review**

Prompt:

```text
Perform a read-only production-readiness review focused on Python backend, SQLite/task processing, config, error handling, no-key LLM behavior, archive cleanup, command timeouts, and audio preprocessing memory. Do not edit files. Return actionable findings with file:line references, minimal fixes, and verification commands. Avoid broad wishlist items.
```

- [ ] **Step 2: Dispatch Web usability review**

Prompt:

```text
Perform a read-only production-readiness review focused on React/Vite control panel, API error states, import/run refresh, task diagnostics, accessibility, responsive layout, playback feedback, and frontend tests. Do not edit files. Return actionable findings with file:line references, minimal fixes, and verification commands. Avoid broad wishlist items.
```

- [ ] **Step 3: Dispatch operations/performance review**

Prompt:

```text
Perform a read-only production-readiness review focused on one-command startup, launchd, Docker context, device discovery/import, dependency setup, e2e validation, and efficiency bottlenecks. Do not edit files. Return actionable findings with file:line references, minimal fixes, and verification commands. Avoid broad wishlist items.
```

- [ ] **Step 4: Integrate findings**

For each concrete finding:

1. Write a failing test that proves the finding.
2. Run the test and confirm it fails for the expected reason.
3. Implement the smallest fix.
4. Run the targeted test.
5. Commit the fix.

- [ ] **Step 5: Repeat until clean**

Repeat Tasks 4 and 5 until all three review agents return no actionable production-readiness issues.

## Completion Evidence

The project cannot be marked complete until all of these are true:

- `uv run pytest -q` passes.
- `cd web && npm test && npm run build` passes.
- E2E path passes or has a deterministic local replacement committed and passing.
- Three independent read-only reviews return no actionable issues.
- `git status --short` is clean.
