from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "run_sample_asr_batch.py"


def test_sample_asr_batch_writes_jsonl_csv_text_and_errors(tmp_path: Path) -> None:
    fake_package = _write_fake_funasr(tmp_path, fail_names={"bad.wav"})
    source = tmp_path / "sample_data"
    source.mkdir()
    (source / "ok.wav").write_bytes(b"RIFFok")
    (source / "bad.wav").write_bytes(b"RIFFbad")
    (source / "ignored.txt").write_text("not audio", encoding="utf-8")
    output = tmp_path / "out"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-dir",
            str(source),
            "--output-dir",
            str(output),
            "--model-version",
            "test-version",
            "--language",
            "zh",
        ],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(fake_package)},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "files_found=2" in result.stdout
    assert "files_transcribed=1" in result.stdout
    assert "files_failed=1" in result.stdout

    jsonl_records = _read_jsonl(output / "sample_transcripts.jsonl")
    assert jsonl_records == [
        {
            "file_name": "ok.wav",
            "model_name": "sensevoice",
            "model_version": "test-version",
            "segments": [
                {
                    "confidence": None,
                    "end_ms": 1200,
                    "language": "zh",
                    "speaker": "spk0",
                    "start_ms": 0,
                    "tags": ["zh", "Speech"],
                    "text": "第一句",
                },
                {
                    "confidence": None,
                    "end_ms": 2400,
                    "language": "zh",
                    "speaker": "spk1",
                    "start_ms": 1200,
                    "tags": ["yue", "Speech"],
                    "text": "Yeah.",
                },
            ],
            "source_path": str(source / "ok.wav"),
            "text": "第一句 Yeah.",
        }
    ]
    assert "ok.wav\t第一句 Yeah." in (output / "sample_transcripts.txt").read_text(encoding="utf-8")
    assert "bad.wav" in (output / "sample_transcript_errors.jsonl").read_text(encoding="utf-8")

    csv_text = (output / "sample_transcript_segments.csv").read_text(encoding="utf-8")
    assert "file_name,segment_index,start_ms,end_ms,language,speaker,text,tags_json,confidence,source_path" in csv_text
    assert "ok.wav,0,0,1200,zh,spk0,第一句" in csv_text


def test_sample_asr_batch_skips_existing_successful_jsonl_records(tmp_path: Path) -> None:
    fake_package = _write_fake_funasr(tmp_path, fail_names={"done.wav"})
    source = tmp_path / "sample_data"
    source.mkdir()
    (source / "done.wav").write_bytes(b"RIFFdone")
    (source / "new.wav").write_bytes(b"RIFFnew")
    output = tmp_path / "out"
    output.mkdir()
    (output / "sample_transcripts.jsonl").write_text(
        json.dumps({"file_name": "done.wav", "text": "existing"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-dir",
            str(source),
            "--output-dir",
            str(output),
        ],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(fake_package)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert "files_found=2" in result.stdout
    assert "files_skipped=1" in result.stdout
    assert "files_transcribed=1" in result.stdout
    records = _read_jsonl(output / "sample_transcripts.jsonl")
    assert [record["file_name"] for record in records] == ["done.wav", "new.wav"]


def _write_fake_funasr(tmp_path: Path, *, fail_names: set[str]) -> Path:
    fake_package = tmp_path / "fake_package"
    funasr_dir = fake_package / "funasr"
    funasr_dir.mkdir(parents=True)
    funasr_dir.joinpath("__init__.py").write_text(
        f"""
from pathlib import Path

FAIL_NAMES = {sorted(fail_names)!r}


class AutoModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def generate(self, input, **kwargs):
        name = Path(input).name
        if name in FAIL_NAMES:
            raise RuntimeError(f"forced failure for {{name}}")
        return [{{
            "text": "<|zh|><|Speech|>完整文本",
            "sentence_info": [
                {{"text": "<|zh|><|Speech|>第一句", "start": 0, "end": 1200, "spk": "spk0"}},
                {{"text": "<|yue|><|Speech|>Yeah.", "timestamp": [1200, 2400], "speaker": "spk1"}},
            ],
        }}]
""".strip(),
        encoding="utf-8",
    )
    return fake_package


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
