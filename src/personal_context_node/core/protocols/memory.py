from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


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
LOCAL_PERSON_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])per_[A-Za-z0-9_]+(?![A-Za-z0-9])")

# did:key (Ed25519) codec — the DID *is* the public key (RFC: did:key, multicodec ed25519-pub 0xed01).
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_ED25519_MULTICODEC_PREFIX = b"\xed\x01"
_DID_KEY_PREFIX = "did:key:z"


def _base58btc_encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    encoded = ""
    while number > 0:
        number, remainder = divmod(number, 58)
        encoded = _BASE58_ALPHABET[remainder] + encoded
    leading_zeros = len(data) - len(data.lstrip(b"\x00"))
    return _BASE58_ALPHABET[0] * leading_zeros + encoded


def _base58btc_decode(value: str) -> bytes:
    number = 0
    for char in value:
        index = _BASE58_ALPHABET.find(char)
        if index < 0:
            raise ValueError(f"invalid base58 character: {char!r}")
        number = number * 58 + index
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeros = len(value) - len(value.lstrip(_BASE58_ALPHABET[0]))
    return b"\x00" * leading_zeros + decoded


def did_key_from_public_key(raw_public_key: bytes) -> str:
    if len(raw_public_key) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return _DID_KEY_PREFIX + _base58btc_encode(_ED25519_MULTICODEC_PREFIX + raw_public_key)


def public_key_bytes_from_did_key(did: str) -> bytes:
    if not did.startswith(_DID_KEY_PREFIX):
        raise ValueError(f"not a did:key Ed25519 identifier: {did}")
    decoded = _base58btc_decode(did[len(_DID_KEY_PREFIX) :])
    if decoded[:2] != _ED25519_MULTICODEC_PREFIX or len(decoded) != 34:
        raise ValueError(f"did:key is not an Ed25519 multicodec key: {did}")
    return decoded[2:]


def is_did_key(did: str) -> bool:
    try:
        public_key_bytes_from_did_key(did)
    except ValueError:
        return False
    return True


def public_key_text_from_did_key(did: str) -> str:
    return base64.urlsafe_b64encode(public_key_bytes_from_did_key(did)).decode("ascii").rstrip("=")


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
    visibility: str | None = None
    summary: str | None = None


class Visibility(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = "private"
    direct_did: str | None = None
    group_id: str | None = None


class MemoryCard(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_version: Literal["memory_card.v1"] = "memory_card.v1"
    card_id: str
    # The protocol field is `owner` (§17.1, §47.2); `owner_did` is the local field
    # name. Accept both on input, always serialize as the canonical `owner`.
    owner_did: str = Field(
        validation_alias=AliasChoices("owner", "owner_did"),
        serialization_alias="owner",
    )
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

    @model_validator(mode="after")
    def reject_local_person_subject_id(self) -> "MemoryCard":
        if self.subject.type == "person" and self.subject.id.startswith("per_"):
            raise ValueError("shared memory cards cannot expose local person id in subject")
        if self.subject.type == "person" and not (
            self.subject.id.startswith("did:key:") or self.subject.id.startswith("alias_")
        ):
            raise ValueError("shared memory cards require an indirect person reference")
        return self

    @model_validator(mode="after")
    def reject_local_person_tokens(self) -> "MemoryCard":
        _reject_local_person_token(self.claim, "claim")
        for evidence_ref in self.evidence_refs:
            _reject_local_person_token(evidence_ref.summary, "evidence summary")
        return self


def _normalize_visibility(value: object) -> dict[str, str]:
    if isinstance(value, Visibility):
        # A model instance (e.g. visibility=Visibility(type="public")) must not be
        # silently downgraded to private; normalize from its dict form.
        value = value.model_dump(exclude_none=True)
    if isinstance(value, str):
        return {"type": value if value in {"private", "public"} else "private"}
    if isinstance(value, dict):
        visibility_type = value.get("type")
        if visibility_type not in SUPPORTED_VISIBILITY_TYPES:
            return {"type": "private"}
        normalized = {"type": str(visibility_type)}
        if visibility_type == "direct":
            if not value.get("direct_did"):
                return {"type": "private"}
            normalized["direct_did"] = str(value["direct_did"])
        if visibility_type == "group":
            if not value.get("group_id"):
                return {"type": "private"}
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

    @model_validator(mode="after")
    def reject_local_person_tokens(self) -> "MemoryAnnotation":
        _reject_local_person_token(self.body, "annotation body")
        return self


def _reject_local_person_token(value: str | None, field_name: str) -> None:
    if value and LOCAL_PERSON_TOKEN_RE.search(value):
        raise ValueError(f"shared protocol {field_name} cannot expose local person token")


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
    model_config = ConfigDict(frozen=True, extra="allow")

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
    # RFC 8785 / JCS: sorted keys, no insignificant whitespace, UTF-8 strings
    # (no \uXXXX escaping of non-ASCII), and ES6-style number serialization so
    # integral floats render as `1` not `1.0` (§47.4.1).
    return _canonical_dumps(value).encode("utf-8")


def _canonical_dumps(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _canonical_number(value)
    if isinstance(value, datetime):
        return json.dumps(_json_default(value), ensure_ascii=False)
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(str(key), ensure_ascii=False) + ":" + _canonical_dumps(item)
            for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_dumps(item) for item in value) + "]"
    raise TypeError(f"cannot serialize {type(value)!r}")


def _canonical_number(value: float) -> str:
    # RFC 8785 numbers follow ECMAScript Number::toString: shortest round-tripping
    # digits, fixed notation for 1e-6 <= |x| < 1e21, exponential otherwise, with no
    # leading zero in the exponent (so `1e-7`, not `1e-07`).
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("non-finite numbers are not allowed in canonical JSON")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    _, digit_tuple, exponent = Decimal(repr(abs(value))).as_tuple()
    digits = list(digit_tuple)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    significant = "".join(str(digit) for digit in digits)
    k = len(significant)
    n = exponent + k  # value == significant × 10^(n-k)
    if k <= n <= 21:
        body = significant + "0" * (n - k)
    elif 0 < n <= 21:
        body = significant[:n] + "." + significant[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + significant
    else:
        ev = n - 1
        exp_str = ("+" if ev >= 0 else "-") + str(abs(ev))
        mantissa = significant if k == 1 else significant[0] + "." + significant[1:]
        body = mantissa + "e" + exp_str
    return sign + body


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
    # by_alias so memory cards serialize the canonical protocol field name `owner`.
    payload_json = payload.model_dump(mode="json", exclude_none=True, by_alias=True)
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


def verify_signed_event(event: SignedEvent, public_key_text: str | None = None) -> bool:
    # A did:key owner self-certifies: the verification key is derived from the DID
    # itself, never from a caller-supplied key. This binds owner_id to the signing
    # key and prevents importing a forged event that claims another identity (§30.3,
    # §40.2.1). Placeholder/non-did owners fall back to the supplied key (legacy).
    if is_did_key(event.owner_id):
        if event.signature.public_key_id != event.owner_id:
            return False
        try:
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes_from_did_key(event.owner_id))
        except ValueError:
            return False
    elif public_key_text is not None:
        public_key = _public_key_from_text(public_key_text)
    else:
        return False
    try:
        public_key.verify(_b64url_decode(event.signature.value), canonical_json_bytes(signing_body(event)))
    except InvalidSignature:
        return False
    return True


def materialize_cards(events: list[SignedEvent], public_keys_by_did: dict[str, str]) -> dict[str, MemoryCard]:
    # The materialized view is a deterministic function of the *trusted* event set,
    # independent of arrival order: per-owner events are applied by owner_sequence,
    # not by the signer-controlled created_at wall-clock (§43.2/43.3, §40.2.4).
    cards: dict[str, MemoryCard] = {}
    ordered = sorted(events, key=lambda item: (item.owner_id, item.owner_sequence, item.event_hash))
    for event in ordered:
        public_key = public_keys_by_did.get(event.signer_did)
        if not verify_signed_event(event, public_key):
            # untrusted/unverifiable events are excluded, not fatal
            continue
        if (
            event.event_type == "memory_card.created"
            and event.payload_encoding == "plain"
            and event.payload_type == "memory_card.v1"
        ):
            try:
                card = MemoryCard.model_validate(event.payload)
            except ValueError:
                # A verified event whose payload does not parse is excluded
                # (fail-closed), not fatal to the whole projection (§42).
                continue
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
