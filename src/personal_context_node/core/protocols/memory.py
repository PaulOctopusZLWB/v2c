from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
AnnotationType = Literal["confirm", "dispute", "comment", "supersede_reference"]
MemoryCardSourceType = Literal["confirmed_generated", "manual"]
SUPPORTED_VISIBILITY_TYPES = {"private", "public", "direct", "group"}


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


class Visibility(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = "private"
    direct_did: str | None = None
    group_id: str | None = None


class MemoryCard(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["memory_card.v1"] = "memory_card.v1"
    card_id: str
    owner_did: str
    claim_type: ClaimType
    claim: str
    subject: SubjectRef
    evidence_refs: list[EvidenceRef]
    source_type: MemoryCardSourceType = "confirmed_generated"
    candidate_claim: str | None = None
    confidence: float | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    visibility: Visibility = Field(default_factory=Visibility)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_visibility(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw_visibility = data.get("visibility", {"type": "private"})
        data = dict(data)
        data["visibility"] = _normalize_visibility(raw_visibility)
        return data

    @model_validator(mode="after")
    def require_generated_evidence(self) -> "MemoryCard":
        if self.source_type == "confirmed_generated" and not self.evidence_refs:
            raise ValueError("generated memory cards require at least one evidence reference")
        return self


def _normalize_visibility(value: object) -> dict[str, str]:
    if isinstance(value, str):
        return {"type": value if value in SUPPORTED_VISIBILITY_TYPES else "private"}
    if isinstance(value, dict):
        visibility_type = value.get("type")
        if visibility_type not in SUPPORTED_VISIBILITY_TYPES:
            return {"type": "private"}
        normalized = {"type": str(visibility_type)}
        if visibility_type == "direct" and value.get("direct_did"):
            normalized["direct_did"] = str(value["direct_did"])
        if visibility_type == "group" and value.get("group_id"):
            normalized["group_id"] = str(value["group_id"])
        return normalized
    return {"type": "private"}


class MemoryAnnotation(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["memory_annotation.v1"] = "memory_annotation.v1"
    annotation_id: str
    target_card_id: str
    author: str
    annotation_type: AnnotationType
    body: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryAnnotationRevocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["memory_annotation_revocation.v1"] = "memory_annotation_revocation.v1"
    annotation_id: str
    revoked_by: str
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryCardRevocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["memory_card_revocation.v1"] = "memory_card_revocation.v1"
    card_id: str
    revoked_by: str
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryCardMetadataUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["memory_card_metadata_update.v1"] = "memory_card_metadata_update.v1"
    card_id: str
    updated_by: str
    visibility: Visibility = Field(default_factory=Visibility)
    tags: list[str] = Field(default_factory=list)
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def normalize_visibility(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        data["visibility"] = _normalize_visibility(data.get("visibility", {"type": "private"}))
        return data


class MemoryCardSupersession(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["memory_card_supersession.v1"] = "memory_card_supersession.v1"
    card_id: str
    superseded_by_card_id: str
    superseded_by: str
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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


class IdentityKeyRotation(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["identity_key_rotation.v1"] = "identity_key_rotation.v1"
    old_identity_id: str
    new_identity_id: str
    new_public_key_multibase: str
    reason: str | None = None
    effective_at: datetime | None = None
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
    payload_encoding: str = "plain"
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
    payload_json = payload.model_dump(mode="json", exclude_none=True)
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
    for key in ("card_id", "annotation_id", "identity_id", "old_identity_id", "profile_id"):
        value = payload.get(key)
        if value:
            return str(value)
    raise ValueError("signed event payload requires an object id")


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise TypeError(f"cannot serialize {type(value)!r}")
