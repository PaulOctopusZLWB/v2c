from __future__ import annotations

from personal_context_node.core.ports.llm import (
    SessionSummary,
    SpeakerAnalysis,
    SpeakerViewpoint,
)


def test_session_summary_carries_per_speaker_analysis() -> None:
    s = SessionSummary(
        session_id="ses_1", headline="本地部署推进", summary="讨论本地化方案。",
        topics=["本地部署"], decisions=[], todos=[], open_questions=[],
        core_conclusions=["团队决定数据不出本机。"],
        per_speaker=[
            SpeakerAnalysis(
                speaker_cluster_id="spk_01",
                viewpoints=[SpeakerViewpoint(text="主张全部本地处理。", evidence_refs=["ev_1"])],
                sentiment="积极",
                stance="支持本地部署、对成本敏感",
                latent_needs=["更快的转写速度"],
            )
        ],
    )
    assert s.core_conclusions == ["团队决定数据不出本机。"]
    assert s.per_speaker[0].speaker_cluster_id == "spk_01"
    assert s.per_speaker[0].viewpoints[0].text == "主张全部本地处理。"
    assert s.per_speaker[0].viewpoints[0].evidence_refs == ["ev_1"]
    assert s.per_speaker[0].sentiment == "积极"
    assert s.per_speaker[0].latent_needs == ["更快的转写速度"]


def test_session_summary_per_speaker_defaults_empty_for_backcompat() -> None:
    # The rule_based / non-diarized GLM paths omit the per-speaker fields; defaults keep them valid.
    s = SessionSummary(
        session_id="ses_1", headline="h", summary="", topics=[], decisions=[], todos=[], open_questions=[],
    )
    assert s.core_conclusions == []
    assert s.per_speaker == []
