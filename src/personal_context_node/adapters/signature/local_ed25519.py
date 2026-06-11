from __future__ import annotations

import base64

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.signature import IdentityProfile, UnsignedEvent, VerificationResult
from personal_context_node.core.protocols.memory import SignedEvent, create_signed_event, verify_signed_event
from personal_context_node.identity_keys import load_or_create_signing_key


class LocalEd25519SignatureAdapter:
    def __init__(self, *, config: AppConfig) -> None:
        self.config = config

    def load_identity(self) -> IdentityProfile:
        key = load_or_create_signing_key(self.config)
        public_key = base64.urlsafe_b64encode(key.public_key().public_bytes_raw()).decode("ascii").rstrip("=")
        return IdentityProfile(identity_id=self.config.owner_did, public_key=public_key)

    def sign_event(self, unsigned_event: UnsignedEvent) -> SignedEvent:
        key = load_or_create_signing_key(self.config)
        event, _ = create_signed_event(
            event_type=unsigned_event.event_type,
            payload=unsigned_event.payload,
            signer_did=self.config.owner_did,
            private_key=key,
            owner_sequence=unsigned_event.owner_sequence,
            prev_event_hash=unsigned_event.prev_event_hash,
            object_version=unsigned_event.object_version,
            created_at=unsigned_event.created_at,
        )
        return event

    def verify_event(self, event: SignedEvent) -> VerificationResult:
        identity = self.load_identity()
        if event.signature.public_key_id != identity.identity_id:
            return VerificationResult(verified=False, reason="unknown identity")
        if not verify_signed_event(event, identity.public_key):
            return VerificationResult(verified=False, reason="invalid signature")
        return VerificationResult(verified=True)
