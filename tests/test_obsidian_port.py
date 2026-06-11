from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.adapters.obsidian.local_markdown import LocalMarkdownObsidianAdapter


def test_local_markdown_obsidian_adapter_reads_review_blocks(tmp_path: Path) -> None:
    review_path = tmp_path / "30_Memory_Candidates" / "2087-05-10.md"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(
        """
# Review

<!-- pcn:review start type="memory_candidate" candidate_id="cand_001" version="1" -->
```yaml
action: confirm
claim: "用户要求本地处理。"
claim_type: requirement
```
<!-- pcn:review end candidate_id="cand_001" -->
""".lstrip(),
        encoding="utf-8",
    )

    blocks = LocalMarkdownObsidianAdapter(vault_root=tmp_path).read_review_blocks(review_path)

    assert len(blocks) == 1
    assert blocks[0].block_type == "memory_candidate"
    assert blocks[0].target_id == "cand_001"
    assert blocks[0].version == "1"
    assert 'claim: "用户要求本地处理。"' in blocks[0].body


def test_local_markdown_obsidian_adapter_lists_changed_review_notes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    changed = vault / "30_Memory_Candidates" / "2087-05-10.md"
    old = vault / "90_System" / "Speaker_Review" / "2087-05-09.md"
    unrelated = vault / "10_Daily" / "2087-05-10.md"
    for path in [changed, old, unrelated]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# note\n", encoding="utf-8")
    os.utime(changed, (3_700_000_000, 3_700_000_000))
    os.utime(old, (3_600_000_000, 3_600_000_000))
    os.utime(unrelated, (3_800_000_000, 3_800_000_000))

    notes = LocalMarkdownObsidianAdapter(vault_root=vault).list_changed_review_notes(
        since=datetime.fromtimestamp(3_650_000_000, tz=timezone.utc)
    )

    assert notes == [changed]
