from __future__ import annotations

import hashlib
from pathlib import Path

from personal_context_node.adapters.archive.command import CommandArchiveAdapter


def test_command_archive_adapter_runs_command_then_verifies_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"raw audio bytes")
    archive_root = tmp_path / "nas"
    log_path = tmp_path / "args.log"
    script = tmp_path / "archive_copy.py"
    script.write_text(
        """
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
log_path = Path(sys.argv[3])
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(source, target)
log_path.write_text(f"{source}\\n{target}\\n", encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandArchiveAdapter(root=archive_root, command=["python3", str(script), "{source_path}", "{archive_path}", str(log_path)])

    result = adapter.archive_file(
        source_path=source,
        relative_path=Path("audio/raw/2087-05-10/source.wav"),
        expected_sha256=_sha256(source),
    )

    archive_path = archive_root / "audio" / "raw" / "2087-05-10" / "source.wav"
    assert result.verified is True
    assert result.archive_path == archive_path
    assert archive_path.read_bytes() == b"raw audio bytes"
    assert log_path.read_text(encoding="utf-8").splitlines() == [str(source), str(archive_path)]


def test_command_archive_adapter_reports_command_failure_without_marking_verified(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"raw audio bytes")
    script = tmp_path / "archive_fail.py"
    script.write_text(
        """
import sys
print("nas unavailable", file=sys.stderr)
raise SystemExit(23)
""".strip(),
        encoding="utf-8",
    )
    adapter = CommandArchiveAdapter(root=tmp_path / "nas", command=["python3", str(script), "{source_path}", "{archive_path}"])

    result = adapter.archive_file(
        source_path=source,
        relative_path=Path("audio/raw/2087-05-10/source.wav"),
        expected_sha256=_sha256(source),
    )

    assert result.verified is False
    assert result.archive_path == tmp_path / "nas" / "audio" / "raw" / "2087-05-10" / "source.wav"
    assert result.reason == "archive command failed with exit 23: nas unavailable"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"
