from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from personal_context_node.core.protocols.memory import SignedEvent


@dataclass(frozen=True)
class IdentityProfile:
    identity_id: str
    public_key: str
    public_key_algorithm: str = "Ed25519"


@dataclass(frozen=True)
class UnsignedEvent:
    event_type: str
    payload: BaseModel
    owner_sequence: int = 1
    prev_event_hash: str | None = None
    object_version: int = 1
    created_at: datetime | None = None


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    reason: str | None = None


class SignaturePort(Protocol):
    def load_identity(self) -> IdentityProfile:
        """Load the active signing identity profile."""

    def sign_event(self, unsigned_event: UnsignedEvent) -> SignedEvent:
        """Sign an event with the active identity."""

    def verify_event(self, event: SignedEvent) -> VerificationResult:
        """Verify a signed event against known identity material."""
