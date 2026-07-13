from __future__ import annotations

import hashlib


class MockEmbedAdapter:
    """Deterministic, dependency-free voiceprint stub for mock-backend runs and tests.

    The vector is derived from the audio path's sha256 so the same segment always embeds
    identically (stable across runs) while different segments differ — enough structure for
    E2E flows that exercise storage/attribution without loading a real CAM++ model.
    """

    dim = 8

    def embed(self, audio_path: str) -> list[float]:
        digest = hashlib.sha256(str(audio_path).encode("utf-8")).digest()
        return [byte / 255.0 for byte in digest[: self.dim]]

    def embed_batch(self, items: list[tuple[str, str]]) -> list[dict]:
        return [{"segment_id": segment_id, "embedding": self.embed(path)} for segment_id, path in items]

    def close(self) -> None:  # symmetric with the persistent command adapter
        pass
