# Production Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local pipeline safe to run without an LLM key, with bounded command execution, immediate retries, safe archive cleanup, and deterministic config paths.

**Architecture:** Preserve the current ports-and-adapters structure. Keep fixture-backed mock adapters explicit, make rule-based LLM the default no-key path, and keep task/adapter failures visible through existing task status rows.

**Tech Stack:** Python 3.11+, Pydantic, Typer, SQLite, pytest, local command adapters.

---

## File Structure

- Modify `src/personal_context_node/config.py`: defaults, TOML loading for path resolution and command timeout.
- Modify `src/personal_context_node/pipeline_adapters.py`: pass command timeout to adapters and keep mock explicit.
- Modify `src/personal_context_node/tasks.py`: make manual retry immediate.
- Modify `src/personal_context_node/archive.py`: fail-closed cleanup checks.
- Modify `src/personal_context_node/adapters/asr/command.py`: subprocess timeout.
- Modify `src/personal_context_node/adapters/vad/command.py`: subprocess timeout.
- Modify `src/personal_context_node/adapters/llm/command.py`: subprocess timeout.
- Modify `src/personal_context_node/adapters/archive/command.py`: subprocess timeout.
- Modify `config/local.example.toml` and `config/funasr.example.toml`: default LLM to `rule_based` unless explicitly mock.
- Test `tests/test_config.py`: default LLM, path resolution, command timeout config.
- Test `tests/test_pipeline_adapters.py`: default build uses rule-based; explicit mock remains mock.
- Test `tests/test_mock_llm.py`: update default expectations and keep explicit mock test.
- Test `tests/test_tasks.py`: retry resets counters and availability.
- Test `tests/test_archive.py`: cleanup refuses unsafe path and hash mismatch.
- Test `tests/test_command_asr.py`, `tests/test_command_vad.py`, `tests/test_command_llm.py`, `tests/test_command_archive.py`: timeout behavior.

## Task 1: Default LLM Is Rule-Based

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_pipeline_adapters.py`
- Modify: `tests/test_mock_llm.py`
- Modify: `src/personal_context_node/config.py`
- Modify: `config/local.example.toml`
- Modify: `config/funasr.example.toml`

- [ ] **Step 1: Write failing config default test**

Add or update this test in `tests/test_config.py`:

```python
def test_app_config_defaults_are_production_safe_without_llm_key() -> None:
    config = AppConfig()

    assert config.vad_backend == "mock"
    assert config.asr_backend == "mock"
    assert config.llm_backend == "rule_based"
    assert config.llm_command is None
    assert "NO NAME" in config.dji_mic_3.volume_name_patterns
```

- [ ] **Step 2: Run the config test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_config.py::test_app_config_defaults_are_production_safe_without_llm_key
```

Expected: FAIL because `config.llm_backend` is currently `mock`.

- [ ] **Step 3: Write failing adapter construction tests**

In `tests/test_pipeline_adapters.py`, ensure these tests exist:

```python
from personal_context_node.adapters.llm.mock import MockLLMAdapter
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.config import AppConfig
from personal_context_node.pipeline_adapters import build_llm, build_pipeline_adapters


def test_build_llm_rule_based_returns_rule_based_adapter() -> None:
    assert isinstance(build_llm(llm_backend="rule_based", llm_command=None), RuleBasedLLMAdapter)


def test_pipeline_adapters_default_llm_is_rule_based() -> None:
    adapters = build_pipeline_adapters(config=AppConfig())

    assert isinstance(adapters.llm, RuleBasedLLMAdapter)


def test_build_llm_mock_remains_explicit_fixture_adapter() -> None:
    assert isinstance(build_llm(llm_backend="mock", llm_command=None), MockLLMAdapter)
```

- [ ] **Step 4: Run adapter tests and verify default test fails**

Run:

```bash
uv run pytest -q tests/test_pipeline_adapters.py::test_pipeline_adapters_default_llm_is_rule_based
```

Expected: FAIL because default pipeline adapters currently use `MockLLMAdapter`.

- [ ] **Step 5: Implement minimal default change**

In `src/personal_context_node/config.py`, change:

```python
llm_backend: str = "mock"
```

to:

```python
llm_backend: str = "rule_based"
```

- [ ] **Step 6: Update explicit mock expectations**

In `tests/test_mock_llm.py`, keep explicit mock assertions and change only the default expectation. The default test should become:

```python
def test_default_llm_backend_uses_rule_based_adapter() -> None:
    adapter = _build_llm(llm_backend=AppConfig().llm_backend, llm_command=None)

    assert isinstance(adapter, RuleBasedLLMAdapter)
```

Ensure this import is present at the top of `tests/test_mock_llm.py`:

```python
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
```

- [ ] **Step 7: Update example configs**

In `config/local.example.toml` and `config/funasr.example.toml`, change production defaults:

```toml
[llm]
backend = "rule_based"
```

Keep comments showing `command = "python3 scripts/llm_wrapper_example.py"` as an opt-in path.

- [ ] **Step 8: Run targeted LLM/config tests**

Run:

```bash
uv run pytest -q tests/test_config.py tests/test_pipeline_adapters.py tests/test_mock_llm.py tests/test_init_health_cli.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/personal_context_node/config.py config/local.example.toml config/funasr.example.toml tests/test_config.py tests/test_pipeline_adapters.py tests/test_mock_llm.py tests/test_init_health_cli.py
git commit -m "fix: default llm to rule based"
```

## Task 2: Resolve Config Paths Consistently

**Files:**
- Modify: `tests/test_config.py`
- Modify: `src/personal_context_node/config.py`

- [ ] **Step 1: Write failing relative path test**

Add to `tests/test_config.py`:

```python
def test_app_config_resolves_obsidian_and_archive_paths_relative_to_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        """
[paths]
data_dir = "pcn-data"
obsidian_vault = "vault"
nas_archive_root = "~/pcn-nas"
""".strip(),
        encoding="utf-8",
    )

    config = AppConfig.from_toml(config_path)

    assert config.obsidian_vault == tmp_path / "config" / "vault"
    assert config.nas_archive_root == (Path.home() / "pcn-nas").resolve(strict=False)
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
uv run pytest -q tests/test_config.py::test_app_config_resolves_obsidian_and_archive_paths_relative_to_config
```

Expected: FAIL because `obsidian_vault` and `nas_archive_root` are currently plain `Path(...)`.

- [ ] **Step 3: Implement minimal path resolution**

In `src/personal_context_node/config.py`, replace these two values:

```python
"obsidian_vault": Path(paths.get("obsidian_vault", cls.model_fields["obsidian_vault"].default)),
"nas_archive_root": Path(paths.get("nas_archive_root", cls.model_fields["nas_archive_root"].default)),
```

with:

```python
"obsidian_vault": _resolve_path(base_dir, paths.get("obsidian_vault", cls.model_fields["obsidian_vault"].default)),
"nas_archive_root": _resolve_path(base_dir, paths.get("nas_archive_root", cls.model_fields["nas_archive_root"].default)),
```

- [ ] **Step 4: Run config tests**

```bash
uv run pytest -q tests/test_config.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/config.py tests/test_config.py
git commit -m "fix: resolve configured vault and archive paths"
```

## Task 3: Add Configured Command Timeout

**Files:**
- Modify: `tests/test_config.py`
- Modify: `src/personal_context_node/config.py`

- [ ] **Step 1: Write failing command timeout config test**

Add to `tests/test_config.py`:

```python
def test_app_config_loads_command_timeout_seconds(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        """
[commands]
timeout_seconds = 12
""".strip(),
        encoding="utf-8",
    )

    config = AppConfig.from_toml(config_path)

    assert config.command_timeout_seconds == 12
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest -q tests/test_config.py::test_app_config_loads_command_timeout_seconds
```

Expected: FAIL because `command_timeout_seconds` does not exist.

- [ ] **Step 3: Implement config field and TOML loading**

In `AppConfig`, add:

```python
command_timeout_seconds: float = 3600.0
```

In `from_toml`, add:

```python
commands = raw.get("commands", {})
```

and include in `values`:

```python
"command_timeout_seconds": commands.get("timeout_seconds", cls.model_fields["command_timeout_seconds"].default),
```

- [ ] **Step 4: Run config tests**

```bash
uv run pytest -q tests/test_config.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/config.py tests/test_config.py
git commit -m "feat: configure command timeout"
```

## Task 4: Bound Command Adapter Runtime

**Files:**
- Modify: `src/personal_context_node/adapters/asr/command.py`
- Modify: `src/personal_context_node/adapters/vad/command.py`
- Modify: `src/personal_context_node/adapters/llm/command.py`
- Modify: `src/personal_context_node/adapters/archive/command.py`
- Modify: `src/personal_context_node/pipeline_adapters.py`
- Test: `tests/test_command_asr.py`
- Test: `tests/test_command_vad.py`
- Test: `tests/test_command_llm.py`
- Test: `tests/test_command_archive.py`

- [ ] **Step 1: Write failing ASR timeout test**

Add to `tests/test_command_asr.py`:

```python
def test_command_asr_adapter_times_out_hung_command(tmp_path: Path) -> None:
    chunk = tmp_path / "chunk.wav"
    chunk.write_bytes(b"fake wav")
    script = tmp_path / "hang_asr.py"
    script.write_text("import time\ntime.sleep(5)", encoding="utf-8")

    adapter = CommandASRAdapter(command=["python3", str(script)], timeout_seconds=0.01)

    try:
        adapter.transcribe(chunk)
    except RetryablePortError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("CommandASRAdapter did not time out a hung command")
```

- [ ] **Step 2: Write equivalent VAD, LLM, archive timeout tests**

Use the same `time.sleep(5)` script pattern:

```python
adapter = CommandVADAdapter(command=["python3", str(script)], timeout_seconds=0.01)
adapter.detect(chunk)
```

```python
adapter = CommandLLMAdapter(command=["python3", str(script)], timeout_seconds=0.01)
adapter.generate_daily_context(day="2087-05-10", transcript_segments=[])
```

```python
adapter = CommandArchiveAdapter(command=["python3", str(script)], root=tmp_path / "archive", timeout_seconds=0.01)
adapter.archive_file(source_path=source, relative_path=Path("x.wav"), expected_sha256="sha256:test")
```

Expected exception for VAD/LLM: `RetryablePortError` containing `timed out`. Expected archive result: `verified is False` and `reason` contains `timed out`.

- [ ] **Step 3: Run timeout tests and verify failure**

```bash
uv run pytest -q tests/test_command_asr.py::test_command_asr_adapter_times_out_hung_command
```

Expected: FAIL because adapter constructors do not accept `timeout_seconds`.

- [ ] **Step 4: Implement ASR timeout**

In `src/personal_context_node/adapters/asr/command.py`, update constructor:

```python
class CommandASRAdapter:
    def __init__(self, *, command: list[str], timeout_seconds: float = 3600.0) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
```

Update `subprocess.run(...)`:

```python
try:
    completed = subprocess.run(
        [*self.command, str(audio_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=self.timeout_seconds,
    )
except subprocess.TimeoutExpired as exc:
    raise RetryablePortError(f"ASR command timed out after {self.timeout_seconds:g}s") from exc
```

- [ ] **Step 5: Implement VAD/LLM/archive timeout using the same pattern**

For `CommandVADAdapter` and `CommandLLMAdapter`, raise `RetryablePortError`.

For `CommandArchiveAdapter`, catch `subprocess.TimeoutExpired` and return:

```python
return ArchiveResult(
    archive_path=archive_path,
    verified=False,
    reason=f"archive command timed out after {self.timeout_seconds:g}s",
)
```

- [ ] **Step 6: Pass timeout from pipeline builder**

In `build_vad`, `build_asr`, and `build_llm`, add `timeout_seconds: float = 3600.0` and pass it into command adapters.

In `build_pipeline_adapters`, pass:

```python
timeout_seconds=config.command_timeout_seconds
```

- [ ] **Step 7: Run adapter timeout tests**

```bash
uv run pytest -q tests/test_command_asr.py tests/test_command_vad.py tests/test_command_llm.py tests/test_command_archive.py tests/test_pipeline_adapters.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/personal_context_node/adapters/asr/command.py src/personal_context_node/adapters/vad/command.py src/personal_context_node/adapters/llm/command.py src/personal_context_node/adapters/archive/command.py src/personal_context_node/pipeline_adapters.py tests/test_command_asr.py tests/test_command_vad.py tests/test_command_llm.py tests/test_command_archive.py tests/test_pipeline_adapters.py
git commit -m "fix: time out external command adapters"
```

## Task 5: Manual Retry Is Immediate

**Files:**
- Modify: `tests/test_tasks.py`
- Modify: `src/personal_context_node/tasks.py`

- [ ] **Step 1: Write failing retry reset test**

Add to `tests/test_tasks.py`:

```python
def test_retry_task_resets_attempts_and_available_at_for_immediate_claim(tmp_path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        task_retry_backoff_seconds=3600,
    )
    created = enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="aud_retry")
    claimed = claim_next_task(config=config, task_type="vad", run_id="run_fail")
    assert claimed is not None
    fail_task(config=config, task_id=created.task_id, error="model busy", terminal=False, run_id="run_fail")

    retry_task(config=config, task_id=created.task_id)
    reclaimed = claim_next_task(config=config, task_type="vad", run_id="run_retry")

    assert reclaimed is not None
    assert reclaimed.task_id == created.task_id
    assert reclaimed.attempt_count == 1
```

- [ ] **Step 2: Run the test and verify failure**

```bash
uv run pytest -q tests/test_tasks.py::test_retry_task_resets_attempts_and_available_at_for_immediate_claim
```

Expected: FAIL because `available_at`, `retry_count`, and `attempt_count` are not all reset.

- [ ] **Step 3: Implement retry reset**

In `retry_task()`, add these fields to the SQL update:

```sql
retry_count = 0,
attempt_count = 0,
available_at = ?,
```

Pass `_now()` for both `available_at` and `updated_at`.

- [ ] **Step 4: Run task tests**

```bash
uv run pytest -q tests/test_tasks.py tests/test_process_retry_cli.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/tasks.py tests/test_tasks.py
git commit -m "fix: make manual task retry immediate"
```

## Task 6: Archive Cleanup Is Fail-Closed

**Files:**
- Modify: `tests/test_archive.py`
- Modify: `src/personal_context_node/archive.py`

- [ ] **Step 1: Write unsafe path deletion test**

Add to `tests/test_archive.py`:

```python
def test_cleanup_archived_audio_refuses_path_outside_raw_store(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    archive_root = tmp_path / "nas"
    archive_root.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"do not delete")
    expected = _sha256(outside)
    _insert_audio(config.database_path, outside, expected, status="cleanup_eligible", audio_file_id="aud_outside")
    archive_path = archive_root / "outside.wav"
    archive_path.write_bytes(outside.read_bytes())
    _insert_archive_record(
        config.database_path,
        audio_file_id="aud_outside",
        source_path=outside,
        archive_path=archive_path,
        sha256=expected,
        archived_at="2000-01-01T00:00:00+00:00",
    )

    result = cleanup_archived_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_root),
        archived_before=datetime.now(timezone.utc),
    )

    assert result.files_removed == 0
    assert outside.exists()
```

Use the existing `_insert_archive_record` helper in `tests/test_archive.py`; its keyword arguments are `audio_file_id`, `source_path`, `archive_path`, `sha256`, and `archived_at`.

- [ ] **Step 2: Write hash mismatch test**

Add:

```python
def test_cleanup_archived_audio_refuses_local_hash_mismatch(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    archive_root = tmp_path / "nas"
    archive_root.mkdir()
    raw = _write_raw(config, "mismatch.wav", b"changed local bytes")
    archive_path = archive_root / "audio" / "raw" / "2087-05-10" / "mismatch.wav"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(b"archived bytes")
    archive_sha = _sha256(archive_path)
    _insert_audio(config.database_path, raw, archive_sha, status="cleanup_eligible", audio_file_id="aud_mismatch")
    _insert_archive_record(
        config.database_path,
        audio_file_id="aud_mismatch",
        source_path=raw,
        archive_path=archive_path,
        sha256=archive_sha,
        archived_at="2000-01-01T00:00:00+00:00",
    )

    result = cleanup_archived_audio(
        config=config,
        archive=LocalFilesystemArchiveAdapter(root=archive_root),
        archived_before=datetime.now(timezone.utc),
    )

    assert result.files_removed == 0
    assert raw.exists()
```

- [ ] **Step 3: Run new tests and verify failure**

```bash
uv run pytest -q tests/test_archive.py::test_cleanup_archived_audio_refuses_path_outside_raw_store tests/test_archive.py::test_cleanup_archived_audio_refuses_local_hash_mismatch
```

Expected: FAIL because cleanup currently unlinks after only archive verification.

- [ ] **Step 4: Implement cleanup guard**

In `archive.py`, add:

```python
def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False
```

Before `local_path.unlink()`:

```python
if not _is_relative_to(local_path, config.raw_audio_dir):
    _record_cleanup_error(conn, audio_file_id=str(row["audio_file_id"]), archive_path=str(row["archive_path"]), error="local raw path outside raw audio dir")
    continue
if local_path.exists() and _sha256(local_path) != str(row["sha256"]):
    _record_cleanup_error(conn, audio_file_id=str(row["audio_file_id"]), archive_path=str(row["archive_path"]), error="local raw hash mismatch")
    continue
```

Add helper:

```python
def _record_cleanup_error(conn, *, audio_file_id: str, archive_path: str, error: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        update archive_records
        set status = 'cleanup_pending',
            last_error = ?,
            updated_at = ?
        where target_type = 'audio_file'
          and target_id = ?
          and archive_path = ?
        """,
        (error, now, audio_file_id, archive_path),
    )
```

- [ ] **Step 5: Run archive tests**

```bash
uv run pytest -q tests/test_archive.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_context_node/archive.py tests/test_archive.py
git commit -m "fix: guard archive cleanup deletion"
```

## Final Verification

- [ ] **Run full Python suite**

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Commit any final fixes**

```bash
git status --short
```

Expected: no output. If this prints changed files, inspect them with `git diff` and either commit the exact files with a message describing the verified fix or revert only changes created by this implementation pass.
