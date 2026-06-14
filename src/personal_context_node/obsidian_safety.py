from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig


def assert_personal_context_vault(config: AppConfig) -> None:
    vault = config.obsidian_vault.expanduser()
    if _is_supcon_vault(vault):
        raise ValueError(f"refusing to write PersonalContext notes into Supcon vault: {vault}")


_SUPCON_VAULT = Path("/Users/paul/Documents/Obsidian/Supcon")


def _is_supcon_vault(path: Path) -> bool:
    # Guard the whole Supcon tree, not just a directory literally named "Supcon":
    # a vault configured as .../Obsidian/Supcon/<subdir> must also be refused (§10).
    # Compare case-insensitively: on a case-insensitive filesystem ".../supcon" and
    # ".../SUPCON" resolve to the same physical Supcon directory.
    resolved = path.expanduser().resolve(strict=False)
    supcon = _SUPCON_VAULT.resolve(strict=False)
    resolved_parts = [part.casefold() for part in resolved.parts]
    supcon_parts = [part.casefold() for part in supcon.parts]
    if resolved_parts[: len(supcon_parts)] == supcon_parts:
        return True
    return "supcon" in resolved_parts
