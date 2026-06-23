# Codex Agent Session Evidence Design

Date: 2026-06-23

## Decision

Use approach A: Evidence-first + skill summarization.

Codex is not the PCN background analysis engine for this workflow. PCN stores Codex work sessions as traceable evidence. Codex then uses the `obsidian-interaction` skill interactively to read, summarize, and write Supcon notes with human review.

## Boundaries

- PCN owns evidence capture, identity, idempotency, and provenance.
- Codex owns interpretation and synthesis during an interactive session.
- Supcon owns durable project knowledge, summaries, daily briefs, and reviewable markdown output.
- Durable memory is created only after explicit human confirmation.

This keeps generated interpretation separate from source evidence. A Codex-written note can cite imported work sessions, but it is not itself treated as a source fact until reviewed.

## Target Vault Layout

Imported Codex work-session indexes are written under:

```text
/Users/paul/Documents/Obsidian/Supcon/Codex 工作会话/
```

Project notes elsewhere in Supcon should link to these session index notes instead of duplicating raw session logs.

## Workflow

1. Batch import recent Codex JSONL files into PCN.
2. Store each session in SQLite as:
   - `agent_sessions`
   - `agent_turns`
   - `agent_tool_events`
   - `evidence_refs` with `source_type = agent_session_turn`
3. Generate a compact Supcon index note for each day or project scope under `Codex 工作会话/`.
4. In an interactive Codex run, use `obsidian-interaction` to read the relevant session index plus target Supcon notes.
5. Write or update a project summary note with explicit source links:
   - Codex session id
   - key turn id or turn number
   - related Supcon note path
6. Promote only reviewed conclusions into stable project notes or long-term memory.

## Components

### Codex JSONL Import

Existing parser and storage code remains the source of truth for raw imported sessions:

- `src/personal_context_node/codex_session_jsonl.py`
- `src/personal_context_node/agent_sessions.py`
- `src/personal_context_node/obsidian_agent_sessions.py`

The next implementation should add a batch import command instead of changing the existing single-file import behavior.

### Supcon Session Index

The index note is not a full transcript dump. It should contain:

- session id
- source path and source sha256
- started time
- cwd
- inferred project label when available
- first user prompt or title
- short list of important turns
- link target for deeper inspection

The note should be small enough to use as context for later Codex summarization.

### Interactive Summary

The summarization step is intentionally not a daemon task. It runs in a human-supervised Codex thread using `obsidian-interaction`, because the output may affect project memory and should preserve Supcon note style.

## Data Flow

```text
Codex JSONL
  -> PCN parser
  -> SQLite agent session tables
  -> evidence_refs
  -> Supcon/Codex 工作会话 index note
  -> Codex + obsidian-interaction interactive summary
  -> Supcon project note
  -> optional human-confirmed memory
```

## Error Handling

- Re-importing the same source identity is idempotent.
- Re-importing the same session id with a different path or sha256 is rejected.
- Unsafe session ids must not become filenames.
- Supcon writes must follow `obsidian-interaction` safety rules:
  - read target notes first
  - avoid bulk edits without confirmation
  - preserve note style
  - cite source notes and session evidence
- If the PCN database and Supcon index disagree, PCN SQLite is the evidence source of truth and the index should be regenerated.

## Testing

Implementation should include focused tests for:

- batch import selects multiple JSONL files and skips already-imported sessions
- batch import rejects source identity conflicts without partial writes
- Supcon index rendering is deterministic
- index filenames are safe for Chinese project names and Codex UUID-like session ids
- generated index notes contain source sha256 and session ids
- existing single-session import/show/publish tests still pass

Manual verification should include:

- run targeted Python tests
- import a temporary sample set
- inspect generated Supcon index markdown
- run `git diff --check`

## Non-goals

- Do not replace `LLMPort` with Codex in this design.
- Do not auto-write project conclusions into Supcon without interactive review.
- Do not store encrypted reasoning content from Codex JSONL.
- Do not turn every tool event into a long Obsidian dump by default.
- Do not publish to `/Users/paul/Documents/Obsidian/PersonalContext` for this Supcon workflow.

## Open Implementation Choice

The batch import command can support both date-window and explicit-path modes:

```text
pcn agent import-codex-batch --since 2026-06-20
pcn agent import-codex-batch --jsonl-dir /Users/paul/.codex/sessions/2026/06/23
```

The first implementation should favor explicit directory or file glob inputs. Automatic discovery of every Codex session path can be added after the import/index contract is stable.
