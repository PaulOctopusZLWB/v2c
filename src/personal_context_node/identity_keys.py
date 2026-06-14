from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import did_key_from_public_key


def load_or_create_signing_key(config: AppConfig) -> Ed25519PrivateKey:
    key_path = config.signing_key_path
    key_path.parent.mkdir(parents=True, exist_ok=True)
    # The key directory must not be world/group traversable (§30.1: 0700).
    try:
        os.chmod(key_path.parent, 0o700)
    except OSError:
        pass
    if key_path.exists():
        return Ed25519PrivateKey.from_private_bytes(_decode_key(key_path.read_text(encoding="ascii").strip()))
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes_raw()
    key_path.write_text(base64.urlsafe_b64encode(raw).decode("ascii"), encoding="ascii")
    os.chmod(key_path, 0o600)
    return key


def derive_did(private_key: Ed25519PrivateKey) -> str:
    return did_key_from_public_key(private_key.public_key().public_bytes_raw())


def load_or_create_identity_did(config: AppConfig) -> str:
    """Return the local owner's did:key, derived from (and bound to) the signing key."""
    return derive_did(load_or_create_signing_key(config))


_PLACEHOLDER_OWNER_DID = AppConfig.model_fields["owner_did"].default


def effective_owner_did(config: AppConfig) -> str:
    """The did to sign/own events under.

    If owner_did is still the unbound placeholder default, derive the real did:key from
    the signing key so events self-certify (a did:key IS its public key, §30.3) — this
    keeps export/import working even before `pcn init` writes a bound owner_did. An
    explicitly configured owner_did is honored as-is.
    """
    if config.owner_did == _PLACEHOLDER_OWNER_DID:
        return load_or_create_identity_did(config)
    return config.owner_did


def _decode_key(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
