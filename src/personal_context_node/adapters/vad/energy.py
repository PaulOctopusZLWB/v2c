from __future__ import annotations

import wave
from pathlib import Path

from personal_context_node.core.ports.vad import SpeechRange, VADResult


class EnergyVadAdapter:
    """Simple local VAD used as a deterministic fallback and test adapter."""

    def __init__(
        self,
        *,
        frame_ms: int = 30,
        threshold: float = 0.03,
        merge_gap_ms: int = 250,
        min_speech_ms: int = 300,
    ) -> None:
        self.frame_ms = frame_ms
        self.threshold = threshold
        self.merge_gap_ms = merge_gap_ms
        self.min_speech_ms = min_speech_ms

    def detect(self, audio_path: Path) -> VADResult:
        with wave.open(str(audio_path), "rb") as wav:
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            if sample_width not in (2, 3, 4):
                raise ValueError(f"unsupported sample width: {sample_width}")
            frame_count = max(1, int(sample_rate * self.frame_ms / 1000))
            ranges: list[SpeechRange] = []
            active_start_ms: int | None = None
            cursor_ms = 0
            while True:
                data = wav.readframes(frame_count)
                if not data:
                    break
                rms = _rms(data, sample_width=sample_width, channels=channels) / _max_amplitude(sample_width)
                is_speech = rms >= self.threshold
                if is_speech and active_start_ms is None:
                    active_start_ms = cursor_ms
                if not is_speech and active_start_ms is not None:
                    ranges.append(SpeechRange(start_ms=active_start_ms, end_ms=cursor_ms))
                    active_start_ms = None
                cursor_ms += self.frame_ms
            if active_start_ms is not None:
                ranges.append(SpeechRange(start_ms=active_start_ms, end_ms=cursor_ms))
        # Merge adjacent bursts first, then drop anything still shorter than
        # min_speech_ms — otherwise short bursts that should merge are lost (§5).
        merged = self._merge(ranges)
        kept = [r for r in merged if r.end_ms - r.start_ms >= self.min_speech_ms]
        return VADResult(
            ranges=kept,
            backend=self.__class__.__name__,
            backend_version=None,
            config={
                "frame_ms": self.frame_ms,
                "threshold": self.threshold,
                "merge_gap_ms": self.merge_gap_ms,
                "min_speech_ms": self.min_speech_ms,
            },
            warnings=[],
        )

    def _merge(self, ranges: list[SpeechRange]) -> list[SpeechRange]:
        merged: list[SpeechRange] = []
        for speech_range in ranges:
            if merged and speech_range.start_ms - merged[-1].end_ms <= self.merge_gap_ms:
                merged[-1] = SpeechRange(start_ms=merged[-1].start_ms, end_ms=speech_range.end_ms)
            else:
                merged.append(speech_range)
        return merged


def _max_amplitude(sample_width: int) -> int:
    return 2 ** (sample_width * 8 - 1) - 1


def _rms(data: bytes, *, sample_width: int, channels: int) -> float:
    sample_total = 0
    sample_count = 0
    frame_width = sample_width * channels
    for frame_start in range(0, len(data) - frame_width + 1, frame_width):
        channel_sum = 0
        for channel in range(channels):
            sample_start = frame_start + channel * sample_width
            raw = data[sample_start : sample_start + sample_width]
            channel_sum += int.from_bytes(raw, byteorder="little", signed=True)
        mono_sample = channel_sum / channels
        sample_total += mono_sample * mono_sample
        sample_count += 1
    if sample_count == 0:
        return 0.0
    return (sample_total / sample_count) ** 0.5
