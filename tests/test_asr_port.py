from __future__ import annotations

from personal_context_node.core.ports.asr import ASRSegment


def test_asr_segment_carries_speaker_label() -> None:
    # The diarized (Paraformer+CAM++) path stamps each sentence with its speaker cluster.
    seg = ASRSegment(text="你好", start_ms=0, end_ms=1000, speaker="spk_01")
    assert seg.speaker == "spk_01"


def test_asr_segment_speaker_defaults_to_self() -> None:
    # Back-compat: the existing SenseVoice/mock constructions omit speaker; the default must keep
    # them valid and preserve the single-owner default-self prior.
    seg = ASRSegment(text="你好", start_ms=0, end_ms=1000)
    assert seg.speaker == "self"
