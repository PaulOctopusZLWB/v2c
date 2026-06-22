from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentTurn:
    turn_index: int
    role: str
    occurred_at: str
    text: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentToolEvent:
    event_index: int
    occurred_at: str
    tool_name: str
    call_id: str | None
    arguments: dict[str, object]
    output_text: str | None
    status: str


@dataclass(frozen=True)
class AgentSessionDocument:
    session_id: str
    source_type: str
    source_path: str
    source_sha256: str
    originator: str | None
    cli_version: str | None
    cwd: str | None
    model: str | None
    started_at: str
    ended_at: str | None
    title: str | None
    turns: list[AgentTurn]
    tool_events: list[AgentToolEvent]

    @property
    def searchable_text(self) -> str:
        parts: list[str] = []
        for turn in self.turns:
            parts.append(turn.text)
        for event in self.tool_events:
            if event.output_text:
                parts.append(event.output_text)
        return "\n".join(parts)
