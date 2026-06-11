from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel, ConfigDict, Field, field_validator


ClaimType = Literal[
    "fact",
    "preference",
    "decision",
    "commitment",
    "requirement",
    "observation",
    "todo",
    "relationship",
]


class SubjectRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    id: str
    label: str


class EvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    source_type: str
    source_id: str
    quote: str


class MemoryCard(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["memory_card.v1"] = "memory_card.v1"
    card_id: str
    owner_did: str
    claim_type: ClaimType
    claim: str
    subject: SubjectRef
    evidence_refs: list[EvidenceRef]
    candidate_claim: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("evidence_refs")
    @classmethod
    def require_evidence(cls, value: list[EvidenceRef]) -> list[EvidenceRef]:
        if not value:
            raise ValueError("generated memory cards require at least one evidence reference")
        return value


class IdentityPredecessor(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity_id: str
    rotation_event_hash: str


class IdentityProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["identity_profile.v1"] = "identity_profile.v1"
    identity_id: str
    display_name: str
    public_key_algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_multibase: str
    predecessor: IdentityPredecessor | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EventSignature(BaseModel):
    model_config = ConfigDict(frozen=True)

    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_id: str
    value: str


class SignedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    envelope_version: Literal["signed_event.v1"] = "signed_event.v1"
    event_type: str
    object_id: str
    object_version: int
    owner_id: str
    owner_sequence: int
    prev_event_hash: str | None
    payload_type: str
    payload_encoding: Literal["plain"] = "plain"
    payload: dict
    created_at: str
    signature: EventSignature

    @property
    def event_hash(self) -> str:
        return canonical_signing_body_hash(self)

    @property
    def event_id(self) -> str:
        return self.event_hash

    @property
    def signer_did(self) -> str:
        return self.owner_id


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def signing_body(event: SignedEvent) -> dict[str, object]:
    return event.model_dump(mode="json", exclude={"signature"})


def canonical_signing_body_hash(event: SignedEvent) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(signing_body(event))).hexdigest()}"


def create_signed_event(
    *,
    event_type: str,
    payload: BaseModel,
    signer_did: str,
    private_key: Ed25519PrivateKey | None = None,
    owner_sequence: int = 1,
    prev_event_hash: str | None = None,
    object_version: int = 1,
    created_at: datetime | None = None,
) -> tuple[SignedEvent, str]:
    key = private_key or Ed25519PrivateKey.generate()
    public_key = _public_key_to_text(key.public_key())
    created = created_at or datetime.now(timezone.utc)
    payload_json = payload.model_dump(mode="json")
    event_body: dict[str, object] = {
        "envelope_version": "signed_event.v1",
        "event_type": event_type,
        "object_id": _payload_object_id(payload_json),
        "object_version": object_version,
        "owner_id": signer_did,
        "owner_sequence": owner_sequence,
        "prev_event_hash": prev_event_hash,
        "payload_type": str(payload_json.get("schema_version", payload.__class__.__name__)),
        "payload_encoding": "plain",
        "payload": payload_json,
        "created_at": _json_default(created),
    }
    signature = key.sign(canonical_json_bytes(event_body))
    return (
        SignedEvent(
            **event_body,
            signature=EventSignature(
                public_key_id=signer_did,
                value=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
            ),
        ),
        public_key,
    )


def verify_signed_event(event: SignedEvent, public_key_text: str) -> bool:
    public_key = _public_key_from_text(public_key_text)
    try:
        public_key.verify(_b64url_decode(event.signature.value), canonical_json_bytes(signing_body(event)))
    except InvalidSignature:
        return False
    return True


def materialize_cards(events: list[SignedEvent], public_keys_by_did: dict[str, str]) -> dict[str, MemoryCard]:
    cards: dict[str, MemoryCard] = {}
    for event in sorted(events, key=lambda item: item.created_at):
        public_key = public_keys_by_did.get(event.signer_did)
        if public_key is None or not verify_signed_event(event, public_key):
            raise ValueError(f"invalid signed event: {event.event_id}")
        if event.event_type in {"memory_card.confirmed.v1", "memory_card.created"}:
            card = MemoryCard.model_validate(event.payload)
            cards[card.card_id] = card
    return cards


def _public_key_to_text(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _public_key_from_text(value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(_b64url_decode(value))


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _payload_object_id(payload: dict[str, object]) -> str:
    for key in ("card_id", "annotation_id", "identity_id", "profile_id"):
        value = payload.get(key)
        if value:
            return str(value)
    raise ValueError("signed event payload requires an object id")


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise TypeError(f"cannot serialize {type(value)!r}")
