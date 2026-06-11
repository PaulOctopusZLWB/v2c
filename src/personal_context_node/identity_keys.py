from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig


def load_or_create_signing_key(config: AppConfig) -> Ed25519PrivateKey:
    key_path = config.signing_key_path
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return Ed25519PrivateKey.from_private_bytes(_decode_key(key_path.read_text(encoding="ascii").strip()))
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes_raw()
    key_path.write_text(base64.urlsafe_b64encode(raw).decode("ascii"), encoding="ascii")
    os.chmod(key_path, 0o600)
    return key


def _decode_key(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
