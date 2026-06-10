# Personal Context Node Architecture

## 1. Purpose

This project builds a local-first audio-to-context system centered on the owner.

The system ingests daily audio from DJI Mic 3, transcribes speech locally, creates daily context artifacts, extracts long-term memory candidates, syncs human-readable output to a dedicated Obsidian vault, and archives raw evidence to NAS. LLM-based text processing may use local or cloud models, but raw audio, ASR, diarization, transcripts, statistics, and local storage remain local.

The architecture must support a future small-team mode where multiple users run their own local nodes and exchange only confirmed atomic memory cards. The current scope defines the protocol and module boundaries, not the transport or service layer.

## 2. Boundary Conditions

| Area | Decision |
| --- | --- |
| Primary device | DJI Mic 3 |
| Recording pattern | The owner usually wears the microphone; other speakers may be captured indirectly |
| Daily duration | 4-8+ hours of audio possible |
| Effective speech ratio | Expected 10%-30% |
| Primary language | 95% Chinese, possible dialect scenarios |
| ASR locality | Must run locally |
| Speaker handling | Default owner-as-speaker, with anomaly-triggered diarization or review |
| LLM processing | May be local or cloud, but receives text only |
| Long-term memory | Requires human confirmation before becoming durable memory |
| Obsidian target | New dedicated vault, not the existing Supcon vault |
| Team sharing | Only confirmed memory cards are shareable |
| Team mutation rule | Cards are owner-owned; others can only annotate |

## 3. Recommended Stack

| Capability | Primary Choice | Secondary Choice |
| --- | --- | --- |
| ASR | FunASR + SenseVoice | faster-whisper large-v3-turbo |
| VAD | FunASR VAD or Silero VAD | Backend-specific VAD |
| Speaker diarization | FunASR CAM++ when needed | pyannote |
| Task state | SQLite | Append-only JSONL mirror |
| Human-readable output | Markdown in Obsidian vault | Static Markdown export |
| Raw archive | NAS after local processing | Local hot cache |
| Scheduling | macOS launchd jobs | Manual runner for debugging |

## 4. Architecture Principles

1. Domain code must not depend on FunASR, Obsidian, NAS, launchd, or a specific LLM SDK.
2. Integrations live behind ports and adapters.
3. Audio evidence and transcript evidence must remain traceable after summaries are generated.
4. Long-term memory must be confirmed by the owner before it is treated as durable context.
5. Protocol objects must be stable enough to wrap later as MCP resources or tools.
6. Sharing must be event-based and signed, but transport-independent.

## 5. Module Layout

```text
personal-context-node/
  core/
    domain/
      audio_file
      speech_segment
      speaker_cluster
      person
      transcript
      memory_card
      memory_annotation
      evidence_ref
    services/
      ingest_service
      vad_service
      transcription_service
      speaker_review_service
      summary_service
      memory_candidate_service
      obsidian_publish_service
      archive_service
    ports/
      asr_port
      vad_port
      diarization_port
      llm_port
      storage_port
      file_import_port
      obsidian_port
      archive_port
      identity_port
      signature_port
  adapters/
    asr/funasr_sensevoice/
    asr/faster_whisper/
    vad/silero/
    diarization/funasr_campp/
    diarization/pyannote/
    llm/openai/
    llm/local/
    storage/sqlite/
    obsidian/markdown/
    archive/nas/
    scheduler/launchd/
  protocols/
    memory_card.v1.schema.json
    memory_annotation.v1.schema.json
    signed_event.v1.schema.json
```

This structure is conceptual. The implementation can start smaller, but the dependency direction should remain the same:

```text
domain <- services <- adapters
ports are owned by core
adapters implement ports
```

## 6. Local Workflow

```text
DJI Mic 3 mounted
  -> ingest job detects mounted device
  -> new WAV files are copied into local raw archive
  -> file identity is recorded in SQLite
  -> VAD extracts speech ranges
  -> speech chunks are transcribed locally
  -> segments default to speaker=self when source is the owner's mic
  -> anomaly checks optionally trigger diarization
  -> transcripts and segment metadata are persisted
  -> LLM produces daily summaries and memory candidates from text
  -> Markdown is published to the PersonalContext Obsidian vault
  -> owner confirms or edits memory candidates
  -> confirmed memory cards are signed events
  -> processed raw audio and artifacts sync to NAS
```

## 7. Scheduling Model

Use `launchd` for periodic execution. Jobs should be idempotent and exit quickly when there is no work.

| Job | Trigger | Responsibility |
| --- | --- | --- |
| ingest | Periodic and/or mount-aware | Detect DJI Mic 3, copy new files, register them |
| process | Periodic | Process pending VAD and ASR tasks |
| daily | Daily schedule | Generate daily report only when new data exists |
| archive | Periodic or after processing | Sync completed artifacts to NAS |

Each job should use the SQLite task state table instead of assuming that a previous step finished successfully.

## 8. Storage Model

```text
data/
  audio/
    raw/YYYY-MM-DD/*.wav
    work/YYYY-MM-DD/*.wav
  transcripts/
    chunks/*.jsonl
    sessions/*.jsonl
  summaries/
    daily/*.md
  exports/
    memory_events/*.jsonl
  db/
    personal_context.sqlite
```

Obsidian vault:

```text
/Users/paul/Documents/Obsidian/PersonalContext/
  00_Inbox/
  10_Daily/
  20_Conversations/
  30_Memory_Candidates/
  40_Confirmed_Memory/
  90_System/
```

NAS stores cold raw audio, transcript exports, and event-log snapshots. Work files such as normalized WAV chunks are rebuildable and do not need long-term retention.

## 9. Speaker Model

The first version uses the microphone placement as the strongest prior:

1. Audio from the owner-worn DJI Mic 3 track defaults to `self`.
2. Other detected voices can be marked as `unknown` or temporary `spk_*` clusters.
3. Speaker correction happens in Markdown review files.
4. Multiple `spk_*` clusters may map to one person.
5. A single cluster may be overridden at segment level if it incorrectly merges people.

Example review file:

```md
## Speaker Mapping

- spk_001: self
- spk_002: Wang
- spk_003: Wang
- spk_004: unknown

## Segment Overrides

<!-- segment_id: 2026-06-10_000123 -->
spk_003 -> Li: 这件事我们下周再说。
```

## 10. Daily Markdown Output

Each daily note should include:

```md
# 2026-06-10 Daily Context

## Metrics
- Total recorded:
- Active speech:
- Self speech:
- Others speech:
- Silence ratio:

## People
| Person | Duration | Topics | Confidence |
| --- | ---: | --- | --- |

## Topics

## Decisions

## Todos

## Facts

## Inferences

## Memory Candidates

## Source Sessions
```

Facts must be grounded in transcript evidence. Inferences may include state, relationship, attention, or emotion patterns, but they must be labeled as inferences and include confidence.

## 11. Memory Card Protocol v1

### 11.1 Core Rules

1. A memory card is an atomic claim.
2. A card has exactly one owner.
3. Other users cannot edit the card; they may only create annotations.
4. Automatically generated cards must have at least one evidence reference.
5. Confirming a generated card may edit the final claim, but the original candidate claim must be retained.
6. Confirmed claim semantics should not be changed in place. If semantics change, create a new card and supersede the old card.
7. Card state is derived from a signed event log.

### 11.2 Claim Types

```text
fact
preference
decision
commitment
requirement
observation
todo
relationship
```

### 11.3 Subject Object

```json
{
  "type": "project",
  "id": "project_personal_context_node",
  "label": "Personal Context Node"
}
```

Allowed `subject.type` values:

```text
self
person
project
org
topic
system
relationship
```

### 11.4 Memory Card Payload

```json
{
  "schema_version": "memory_card.v1",
  "card_id": "mem_01J00000000000000000000000",
  "owner": {
    "id": "did:key:z6Mk...",
    "display_name": "Paul"
  },
  "subject": {
    "type": "project",
    "id": "project_personal_context_node",
    "label": "Personal Context Node"
  },
  "claim_type": "decision",
  "claim": "The v1 ASR backend is FunASR + SenseVoice.",
  "source_type": "confirmed_generated",
  "candidate_claim": "Paul may use FunASR for local ASR.",
  "confidence": 0.91,
  "evidence_refs": [
    {
      "evidence_id": "ev_01J00000000000000000000000",
      "visibility": "private",
      "summary": "Derived from local transcript on 2026-06-10."
    }
  ],
  "observed_at": "2026-06-10T16:40:00+08:00",
  "valid_from": "2026-06-10",
  "valid_until": null,
  "visibility": "team",
  "tags": ["asr", "local-first"],
  "created_at": "2026-06-10T17:10:00+08:00",
  "updated_at": "2026-06-10T17:10:00+08:00"
}
```

`source_type` values:

```text
generated
confirmed_generated
manual
imported
```

### 11.5 Evidence Reference

Evidence is local-first. Shared memory cards may include evidence metadata without exposing raw transcript or audio.

```json
{
  "evidence_id": "ev_01J00000000000000000000000",
  "owner_id": "did:key:z6Mk...",
  "source_type": "transcript_segment",
  "source_ref": "segment_2026-06-10_00123",
  "quote": "",
  "visibility": "private",
  "created_at": "2026-06-10T16:50:00+08:00"
}
```

### 11.6 Annotation Payload

```json
{
  "schema_version": "memory_annotation.v1",
  "annotation_id": "ann_01J00000000000000000000000",
  "target_card_id": "mem_01J00000000000000000000000",
  "author": {
    "id": "did:key:z6Mk...",
    "display_name": "Alice"
  },
  "annotation_type": "confirm",
  "body": "I agree this is the current ASR choice for v1.",
  "created_at": "2026-06-10T17:00:00+08:00"
}
```

Allowed `annotation_type` values:

```text
confirm
dispute
comment
supersede_reference
```

## 12. Signed Event Protocol v1

All shareable protocol objects are carried as signed events. Events are transport-independent.

### 12.1 Identity and Signature

| Field | Decision |
| --- | --- |
| Identity | Public-key identity |
| Signature algorithm | Ed25519 |
| Signature input | Canonical JSON payload |
| Encryption | Out of scope for v1 |
| Object IDs | ULID or UUIDv7 with type prefix |

### 12.2 Event Envelope

```json
{
  "envelope_version": "signed_event.v1",
  "event_id": "evt_01J00000000000000000000000",
  "event_type": "memory_card.created",
  "object_id": "mem_01J00000000000000000000000",
  "object_version": 1,
  "payload_type": "memory_card.v1",
  "payload": {},
  "created_at": "2026-06-10T17:10:00+08:00",
  "signature": {
    "algorithm": "Ed25519",
    "public_key_id": "did:key:z6Mk...",
    "value": "base64url-signature"
  }
}
```

### 12.3 Event Types

```text
identity_profile.published
memory_card.created
memory_card.confirmed
memory_card.metadata_updated
memory_card.superseded
memory_card.revoked
memory_annotation.created
memory_annotation.revoked
```

`memory_card.metadata_updated` may update metadata such as tags, visibility, confidence, and valid_until. It must not change the semantic claim. Semantic changes require a new card plus `memory_card.superseded`.

## 13. Failure and Recovery

| Failure | Required Behavior |
| --- | --- |
| Device unplugged mid-import | Keep partial copy out of the registered file table; retry later |
| Duplicate import | Detect by file metadata and content hash |
| ASR failure | Mark task failed with error, allow retry |
| LLM failure | Keep transcript complete; retry summary later |
| Speaker uncertainty | Preserve cluster and confidence; expose in review Markdown |
| NAS unavailable | Keep local archive state pending |
| Signature verification failure | Reject imported event from trusted materialized state |

## 14. Out of Scope for v1

1. Real-time transcription.
2. Web review UI.
3. Direct Apple Reminders or Calendar integration.
4. Team transport, MCP server, HTTP API, or P2P sync.
5. Raw audio sharing.
6. Encryption and access-control enforcement.
7. Full automatic identification of every other speaker.

## 15. Definition of Done for v1

1. Plugging in DJI Mic 3 can trigger idempotent local import.
2. New audio files are copied, hashed, registered, and processed exactly once.
3. VAD reduces long recordings before ASR.
4. FunASR + SenseVoice produces local Chinese transcripts.
5. Segment-level evidence is stored and can be traced from summaries.
6. Daily Markdown appears in the dedicated PersonalContext Obsidian vault.
7. Memory candidates require manual confirmation.
8. Confirmed memory cards are emitted as signed events.
9. Raw audio and durable artifacts can be archived to NAS.
10. The ASR backend can be swapped without changing domain objects or memory-card protocol.
