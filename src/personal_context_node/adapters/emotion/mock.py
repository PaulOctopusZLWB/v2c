from __future__ import annotations


class MockEmotionAdapter:
    """Deterministic, dependency-free acoustic-emotion stub for mock-backend runs and tests."""

    label = "中立/neutral"

    def classify(self, audio_path: str) -> dict:
        return {"label": self.label, "scores": {self.label: 1.0}}

    def classify_batch(self, items: list[tuple[str, str]]) -> list[dict]:
        return [
            {"segment_id": segment_id, "label": self.label, "scores": {self.label: 1.0}}
            for segment_id, _path in items
        ]

    def close(self) -> None:  # symmetric with the persistent command adapter
        pass
