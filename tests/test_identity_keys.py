from __future__ import annotations

import stat

from personal_context_node.config import AppConfig
from personal_context_node.identity_keys import load_or_create_signing_key


def test_load_or_create_signing_key_persists_private_key_with_strict_permissions(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    first = load_or_create_signing_key(config)
    second = load_or_create_signing_key(config)

    assert first.public_key().public_bytes_raw() == second.public_key().public_bytes_raw()
    assert config.signing_key_path.exists()
    assert stat.S_IMODE(config.signing_key_path.stat().st_mode) == 0o600
