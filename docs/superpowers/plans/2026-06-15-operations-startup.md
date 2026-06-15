# Operations Startup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one-command Web startup, launchd scheduling, Docker builds, and device import safe and repeatable on a local macOS production machine.

**Architecture:** Keep launchd generation in `launchd.py`, CLI option semantics in `cli.py`, and shell startup behavior in `scripts/start-web.sh`. Do not move runtime orchestration into the frontend or add new services.

**Tech Stack:** Python, Typer, plistlib, launchd, Bash, Docker Compose, pytest.

---

## File Structure

- Modify `src/personal_context_node/launchd.py`: absolute `uv`, environment PATH, absolute logs, log directory creation, optional source dir.
- Modify `src/personal_context_node/cli.py`: config-aware path options default to `None`; launchd source dir optional.
- Modify `src/personal_context_node/init_health.py`: generated config should remain aligned with default LLM and paths.
- Modify `scripts/start-web.sh`: build Web app when `web/dist` is missing.
- Add `.dockerignore`: exclude local runtime data and caches.
- Modify `src/personal_context_node/adapters/file_import/local_directory.py`: collision-safe copy path.
- Test `tests/test_launchd.py`, `tests/test_launchd_cli.py`, `tests/test_launchd_web.py`.
- Test `tests/test_config_cli.py`, `tests/test_init_health_cli.py`.
- Test `tests/test_local_file_import_adapter.py`, `tests/test_ingest_file_import_port.py`.
- Test `tests/test_dockerfile.py`.

## Task 1: Launchd Plists Are Self-Contained

**Files:**
- Modify: `tests/test_launchd.py`
- Modify: `src/personal_context_node/launchd.py`

- [ ] **Step 1: Write failing plist environment test**

Add to `tests/test_launchd.py`:

```python
import plistlib


def test_render_plist_includes_environment_path_and_absolute_logs(tmp_path: Path) -> None:
    job = LaunchdJob(
        label="com.personal-context-node.test",
        command=["/opt/homebrew/bin/uv", "run", "pcn", "health"],
        start_interval_seconds=60,
        working_directory=str(tmp_path),
        log_directory=str(tmp_path / "logs" / "launchd"),
    )

    payload = plistlib.loads(render_plist(job))

    assert payload["ProgramArguments"][0] == "/opt/homebrew/bin/uv"
    assert payload["EnvironmentVariables"]["PATH"]
    assert payload["StandardOutPath"] == str(tmp_path / "logs" / "launchd" / "com.personal-context-node.test.out.log")
    assert payload["StandardErrorPath"] == str(tmp_path / "logs" / "launchd" / "com.personal-context-node.test.err.log")
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_launchd.py::test_render_plist_includes_environment_path_and_absolute_logs
```

Expected: FAIL because `EnvironmentVariables` is absent.

- [ ] **Step 3: Implement PATH field**

In `LaunchdJob`, add:

```python
environment_path: str = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
```

In `render_plist`, add:

```python
"EnvironmentVariables": {"PATH": job.environment_path},
```

- [ ] **Step 4: Run launchd unit tests**

```bash
uv run pytest -q tests/test_launchd.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/launchd.py tests/test_launchd.py
git commit -m "fix: include launchd environment path"
```

## Task 2: Resolve uv and Create Log Directory

**Files:**
- Modify: `tests/test_launchd_cli.py`
- Modify: `src/personal_context_node/launchd.py`
- Modify: `src/personal_context_node/cli.py`

- [ ] **Step 1: Write failing CLI plist test**

In `tests/test_launchd_cli.py`, add:

```python
def test_launchd_write_plists_uses_absolute_uv_and_creates_log_dir(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "plists"
    data_dir = tmp_path / "data"

    result = runner.invoke(
        app,
        [
            "launchd-write-plists",
            "--output-dir",
            str(output_dir),
            "--working-directory",
            str(tmp_path),
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(tmp_path / "vault"),
            "--archive-root",
            str(tmp_path / "nas"),
        ],
    )

    assert result.exit_code == 0
    assert (data_dir / "logs" / "launchd").is_dir()
    plist = plistlib.loads((output_dir / "com.personal-context-node.process.plist").read_bytes())
    assert Path(plist["ProgramArguments"][0]).is_absolute()
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_launchd_cli.py::test_launchd_write_plists_uses_absolute_uv_and_creates_log_dir
```

Expected: FAIL because command starts with bare `uv` and log dir is not created.

- [ ] **Step 3: Implement uv resolution**

In `launchd.py`, add:

```python
def _resolve_uv() -> str:
    resolved = shutil.which("uv")
    return resolved or "uv"
```

At the top of `write_launchd_plists`, add:

```python
uv_bin = _resolve_uv()
Path(log_directory).mkdir(parents=True, exist_ok=True)
```

Replace each command prefix:

```python
"uv",
```

with:

```python
uv_bin,
```

- [ ] **Step 4: Run launchd tests**

```bash
uv run pytest -q tests/test_launchd.py tests/test_launchd_cli.py tests/test_launchd_web.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/launchd.py tests/test_launchd_cli.py
git commit -m "fix: write self-contained launchd plists"
```

## Task 3: Scheduled Ingest Uses Device Discovery Unless Source Is Explicit

**Files:**
- Modify: `tests/test_launchd_cli.py`
- Modify: `src/personal_context_node/launchd.py`
- Modify: `src/personal_context_node/cli.py`

- [ ] **Step 1: Write failing no-source-dir plist test**

Add:

```python
def test_launchd_ingest_omits_source_dir_when_not_explicit(tmp_path: Path) -> None:
    paths = write_launchd_plists(
        output_dir=tmp_path / "plists",
        working_directory=str(tmp_path),
        data_dir=str(tmp_path / "data"),
        obsidian_vault=str(tmp_path / "vault"),
        source_dir=None,
        archive_root=str(tmp_path / "nas"),
        config_path="config/local.example.toml",
    )

    ingest = plistlib.loads(next(p for p in paths if "ingest" in p.name).read_bytes())

    assert "--source-dir" not in ingest["ProgramArguments"]
    assert "--config" in ingest["ProgramArguments"]
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_launchd_cli.py::test_launchd_ingest_omits_source_dir_when_not_explicit
```

Expected: FAIL because `write_launchd_plists` requires `source_dir: str`.

- [ ] **Step 3: Make source_dir optional**

Change signature:

```python
source_dir: str | None,
```

Build source args:

```python
source_args = ["--source-dir", source_dir] if source_dir else []
```

Use:

```python
*source_args,
```

inside ingest command.

- [ ] **Step 4: Change CLI default**

In `launchd-write-plists` Typer command, change `source_dir` default from `/Volumes/DJI` to `None`:

```python
source_dir: Path | None = typer.Option(None, help="Optional fixed source directory. Omit to use configured device discovery."),
```

Pass `str(source_dir) if source_dir else None`.

- [ ] **Step 5: Run launchd CLI tests**

```bash
uv run pytest -q tests/test_launchd_cli.py tests/test_launchd.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/personal_context_node/launchd.py src/personal_context_node/cli.py tests/test_launchd_cli.py
git commit -m "fix: let scheduled ingest use device discovery"
```

## Task 4: Config-Aware CLI Path Options Do Not Override Config By Default

**Files:**
- Modify: `tests/test_config_cli.py`
- Modify: `src/personal_context_node/cli.py`

- [ ] **Step 1: Write failing config override test**

Add to `tests/test_config_cli.py`:

```python
def test_process_run_uses_config_paths_when_cli_paths_omitted(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        f"""
[paths]
data_dir = "{tmp_path / 'configured-data'}"
obsidian_vault = "{tmp_path / 'configured-vault'}"
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["process-run", "--config", str(config_path), "--dry-run"])

    assert result.exit_code == 0
    assert (tmp_path / "configured-data" / "db" / "personal_context.sqlite").exists()
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_config_cli.py::test_process_run_uses_config_paths_when_cli_paths_omitted
```

Expected: FAIL if CLI path defaults override TOML values and create SQLite under the default data directory instead of `configured-data`.

- [ ] **Step 3: Change config-aware path defaults**

In `ingest-import`, `process-run`, `archive`, and `launchd-write-plists` Typer commands, use:

```python
data_dir: Path | None = typer.Option(None, help="Override data directory."),
obsidian_vault: Path | None = typer.Option(None, help="Override Obsidian vault."),
```

Pass them through `AppConfig.from_toml(..., data_dir=data_dir, obsidian_vault=obsidian_vault)` so `None` means "use config".

- [ ] **Step 4: Run CLI config tests**

```bash
uv run pytest -q tests/test_config_cli.py tests/test_cli.py tests/test_process_run_cli.py tests/test_archive_cli.py tests/test_ingest_cli.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/cli.py tests/test_config_cli.py
git commit -m "fix: preserve configured paths in cli commands"
```

## Task 5: start-web Builds or Fails Clearly

**Files:**
- Modify: `scripts/start-web.sh`
- Modify: `tests/test_launchd_web.py` or add `tests/test_start_web_script.py`

- [ ] **Step 1: Write shell script text test**

Add `tests/test_start_web_script.py`:

```python
from pathlib import Path


def test_start_web_builds_frontend_when_dist_missing() -> None:
    script = Path("scripts/start-web.sh").read_text(encoding="utf-8")

    assert "web/dist/index.html" in script
    assert "npm --prefix web run build" in script
    assert "npm --prefix web install" in script
    assert "exit 1" in script
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_start_web_script.py
```

Expected: FAIL because `start-web.sh` does not check/build the frontend.

- [ ] **Step 3: Implement build guard**

In `scripts/start-web.sh`, after `CONFIG=...`, add:

```bash
if [ ! -f web/dist/index.html ]; then
  echo "web/dist is missing; building the control panel first."
  if [ ! -d web/node_modules ]; then
    echo "Installing frontend dependencies: npm --prefix web install"
    npm --prefix web install || { echo "frontend dependency install failed"; exit 1; }
  fi
  echo "Building frontend: npm --prefix web run build"
  npm --prefix web run build || { echo "frontend build failed"; exit 1; }
fi
```

- [ ] **Step 4: Run script test**

```bash
uv run pytest -q tests/test_start_web_script.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/start-web.sh tests/test_start_web_script.py
git commit -m "fix: build web panel before startup"
```

## Task 6: Add Docker Context Exclusions

**Files:**
- Create: `.dockerignore`
- Modify: `tests/test_dockerfile.py`

- [ ] **Step 1: Write failing `.dockerignore` test**

Add to `tests/test_dockerfile.py`:

```python
def test_dockerignore_excludes_local_runtime_data() -> None:
    ignored = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    for required in [".venv/", ".tmp/", "data/", "sample_data/", "web/node_modules/", "web/dist/", ".pytest_cache/", ".ruff_cache/"]:
        assert required in ignored
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_dockerfile.py::test_dockerignore_excludes_local_runtime_data
```

Expected: FAIL because `.dockerignore` is missing.

- [ ] **Step 3: Add `.dockerignore`**

Create `.dockerignore`:

```dockerignore
.DS_Store
.venv/
.pytest_cache/
.ruff_cache/
__pycache__/
*.py[cod]
.tmp/
data/
sample_data/
build/
audio/
web/node_modules/
web/dist/
web/*.tsbuildinfo
*.log
```

- [ ] **Step 4: Run Dockerfile tests**

```bash
uv run pytest -q tests/test_dockerfile.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .dockerignore tests/test_dockerfile.py
git commit -m "build: add dockerignore for local runtime data"
```

## Task 7: Device Import Is Collision-Safe

**Files:**
- Modify: `tests/test_local_file_import_adapter.py`
- Modify: `src/personal_context_node/adapters/file_import/local_directory.py`

- [ ] **Step 1: Write failing same-name collision test**

Add:

```python
def test_copy_to_raw_store_keeps_existing_file_when_name_collides(tmp_path: Path) -> None:
    device = MountedDevice(device_id="dev", label="DJI Mic 3", root_path=tmp_path / "device")
    first_source = _stable_source(device, tmp_path / "first" / "TX01_MIC001_20870510_120000_orig.wav")
    second_source = _stable_source(device, tmp_path / "second" / "TX01_MIC001_20870510_120000_orig.wav")
    adapter = LocalDirectoryFileImportAdapter(device_roots=[], device_label="DJI Mic 3")
    destination = tmp_path / "data" / "audio" / "raw"

    first = adapter.copy_to_raw_store(first_source, destination)
    second = adapter.copy_to_raw_store(second_source, destination)

    assert first.local_raw_path != second.local_raw_path
    assert first.local_raw_path.exists()
    assert second.local_raw_path.exists()
```

Add these imports to `tests/test_local_file_import_adapter.py`:

```python
from personal_context_node.core.ports.file_import import MountedDevice, SourceAudioFile, StableSourceAudioFile
```

Add this helper near `_write_tiny_wav`:

```python
def _stable_source(device: MountedDevice, path: Path) -> StableSourceAudioFile:
    _write_tiny_wav(path)
    stat = path.stat()
    return StableSourceAudioFile(
        source=SourceAudioFile(
            device=device,
            source_path=path,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        ),
        stable_checked_at="2087-05-10T12:00:00+08:00",
    )
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_local_file_import_adapter.py::test_copy_to_raw_store_keeps_existing_file_when_name_collides
```

Expected: FAIL because second copy overwrites the first path.

- [ ] **Step 3: Implement unique destination path**

In `local_directory.py`, add:

```python
def _unique_destination_path(target_dir: Path, source_name: str) -> Path:
    candidate = target_dir / source_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        next_candidate = target_dir / f"{stem}_{counter}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        counter += 1
```

Use it in `copy_to_raw_store`:

```python
local_raw_path = _unique_destination_path(target_dir, source.source.source_path.name)
```

- [ ] **Step 4: Run import adapter tests**

```bash
uv run pytest -q tests/test_local_file_import_adapter.py tests/test_ingest_file_import_port.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/adapters/file_import/local_directory.py tests/test_local_file_import_adapter.py
git commit -m "fix: avoid raw import filename collisions"
```

## Final Verification

- [ ] **Run targeted operations tests**

```bash
uv run pytest -q tests/test_config_cli.py tests/test_init_health_cli.py tests/test_launchd.py tests/test_launchd_cli.py tests/test_launchd_web.py tests/test_start_web_script.py tests/test_local_file_import_adapter.py tests/test_ingest_file_import_port.py tests/test_dockerfile.py
```

Expected: PASS.

- [ ] **Run plist smoke check**

```bash
uv run pcn launchd-write-plists --config config/local.example.toml --output-dir /tmp/pcn-launchd-check
plutil -lint /tmp/pcn-launchd-check/*.plist
```

Expected: `OK` for every plist.

- [ ] **Run Docker config check**

```bash
docker compose config
```

Expected: command succeeds. If Docker is not available, stop this verification step and ask the user whether to install/start Docker or accept Python test coverage for this environment.
