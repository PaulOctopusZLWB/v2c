from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

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


class SignedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["signed_event.v1"] = "signed_event.v1"
    event_id: str
    event_type: str
    signer_did: str
    created_at: datetime
    payload: dict
    signature: str


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def create_signed_event(
    *,
    event_type: str,
    payload: BaseModel,
    signer_did: str,
    private_key: Ed25519PrivateKey | None = None,
) -> tuple[SignedEvent, str]:
    key = private_key or Ed25519PrivateKey.generate()
    public_key = _public_key_to_text(key.public_key())
    event_body = {
        "schema_version": "signed_event.v1",
        "event_id": f"evt_{uuid4().hex}",
        "event_type": event_type,
        "signer_did": signer_did,
        "created_at": datetime.now(timezone.utc),
        "payload": payload.model_dump(mode="json"),
    }
    signature = key.sign(canonical_json_bytes(event_body))
    return (
        SignedEvent(
            **event_body,
            signature=base64.urlsafe_b64encode(signature).decode("ascii"),
        ),
        public_key,
    )


def verify_signed_event(event: SignedEvent, public_key_text: str) -> bool:
    public_key = _public_key_from_text(public_key_text)
    body = event.model_dump(mode="json", exclude={"signature"})
    try:
        public_key.verify(base64.urlsafe_b64decode(event.signature), canonical_json_bytes(body))
    except InvalidSignature:
        return False
    return True


def materialize_cards(events: list[SignedEvent], public_keys_by_did: dict[str, str]) -> dict[str, MemoryCard]:
    cards: dict[str, MemoryCard] = {}
    for event in sorted(events, key=lambda item: item.created_at):
        public_key = public_keys_by_did.get(event.signer_did)
        if public_key is None or not verify_signed_event(event, public_key):
            raise ValueError(f"invalid signed event: {event.event_id}")
        if event.event_type == "memory_card.confirmed.v1":
            card = MemoryCard.model_validate(event.payload)
            cards[card.card_id] = card
    return cards


def _public_key_to_text(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _public_key_from_text(value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(value))


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise TypeError(f"cannot serialize {type(value)!r}")
