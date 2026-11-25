"""Table-driven unit tests for deterministic fusion rules."""

from __future__ import annotations

import json
import re

import pytest

from app.services.aletheia import (
    EVIDENCE_NOTE,
    SCORE_MEANING,
    AletheiaAggregateResult,
    AletheiaSegmentResult,
    AuthenticityState,
)
from app.services.fusion import ThreatLevel, fuse_results
from app.services.gemini_audio import (
    ConfidenceLevel,
    GeminiAudioAnalysis,
    GeminiResult,
    IndicatorCategory,
    IndicatorSeverity,
    RiskLevel,
    ScamIndicator,
    SpeechClarity,
)


def _aletheia(
    state: AuthenticityState,
    *,
    status: str = "ok",
    mixed: bool = False,
    flagged_ranges: list[str] | None = None,
    err: str | None = None,
) -> AletheiaAggregateResult:
    seg = AletheiaSegmentResult(
        index=0,
        start=0.0,
        end=30.0,
        time_range="00:00–00:30",
        status=status,
        state=state,
        raw_verdict="fake" if state == AuthenticityState.SYNTHETIC_LIKELY else "real",
        score=0.9 if state == AuthenticityState.SYNTHETIC_LIKELY else 0.1,
        error_message=err,
    )
    return AletheiaAggregateResult(
        status=status,
        state=state,
        raw_verdict=seg.raw_verdict if status == "ok" else None,
        score=seg.score if status == "ok" else None,
        score_meaning=SCORE_MEANING,
        evidence_note=EVIDENCE_NOTE,
        segments=[seg],
        flagged_ranges=flagged_ranges or (["00:00–00:30"] if state == AuthenticityState.SYNTHETIC_LIKELY else []),
        mixed=mixed,
        elapsed_ms=100,
        error_message=err,
    )


def _gemini(
    risk: RiskLevel | None,
    *,
    status: str = "ok",
    clarity: SpeechClarity = SpeechClarity.CLEAR,
    unintelligible_forced: bool = False,
    err: str | None = None,
) -> GeminiResult:
    if status != "ok" or risk is None:
        return GeminiResult(
            status="unavailable",
            analysis=None,
            elapsed_ms=120,
            error_message=err or "Gemini error",
        )
    indicators = []
    if risk != RiskLevel.LOW:
        indicators.append(
            ScamIndicator(
                category=IndicatorCategory.DIGITAL_ARREST,
                severity=IndicatorSeverity.HIGH,
                timestamp="00:10",
                evidence="Stay on video call under digital arrest or face warrant.",
                explanation="Coercive digital arrest claim.",
            )
        )
    analysis = GeminiAudioAnalysis(
        speech_clarity=clarity,
        language_detected="English",
        risk_level=risk,
        confidence=ConfidenceLevel.LOW if unintelligible_forced else ConfidenceLevel.HIGH,
        summary="Test summary.",
        indicators=indicators,
        recommended_actions=["Hang up immediately."],
        limitations="",
    )
    return GeminiResult(
        status="ok",
        analysis=analysis,
        elapsed_ms=150,
        unintelligible_forced=unintelligible_forced,
    )


@pytest.mark.parametrize(
    ("auth_state", "gem_risk", "expected_threat"),
    [
        (AuthenticityState.GENUINE_LIKELY, RiskLevel.LOW, ThreatLevel.LOW),
        (AuthenticityState.GENUINE_LIKELY, RiskLevel.MEDIUM, ThreatLevel.MEDIUM),
        (AuthenticityState.GENUINE_LIKELY, RiskLevel.HIGH, ThreatLevel.HIGH),
        (AuthenticityState.GENUINE_LIKELY, RiskLevel.CRITICAL, ThreatLevel.CRITICAL),
        (AuthenticityState.SYNTHETIC_LIKELY, RiskLevel.LOW, ThreatLevel.LOW),
        (AuthenticityState.SYNTHETIC_LIKELY, RiskLevel.MEDIUM, ThreatLevel.HIGH),
        (AuthenticityState.SYNTHETIC_LIKELY, RiskLevel.HIGH, ThreatLevel.CRITICAL),
        (AuthenticityState.SYNTHETIC_LIKELY, RiskLevel.CRITICAL, ThreatLevel.CRITICAL),
        (AuthenticityState.INCONCLUSIVE, RiskLevel.LOW, ThreatLevel.LOW),
        (AuthenticityState.INCONCLUSIVE, RiskLevel.MEDIUM, ThreatLevel.MEDIUM),
        (AuthenticityState.INCONCLUSIVE, RiskLevel.HIGH, ThreatLevel.HIGH),
        (AuthenticityState.INCONCLUSIVE, RiskLevel.CRITICAL, ThreatLevel.CRITICAL),
    ],
)
def test_fusion_matrix_all_combinations(
    auth_state: AuthenticityState,
    gem_risk: RiskLevel,
    expected_threat: ThreatLevel,
) -> None:
    report = fuse_results(_aletheia(auth_state), _gemini(gem_risk), region="IN")
    assert report.threat_level == expected_threat
    assert report.partial is False
    assert report.both_unavailable is False
    # Never outputs a percentage anywhere
    serialized = json.dumps(report.to_dict())
    assert "%" not in serialized
    assert not re.search(r"\bpercent\b", serialized, re.IGNORECASE)


def test_fusion_synthetic_low_headline_and_benign_note() -> None:
    report = fuse_results(_aletheia(AuthenticityState.SYNTHETIC_LIKELY), _gemini(RiskLevel.LOW))
    assert report.threat_level == ThreatLevel.LOW
    assert report.headline == "AI-generated voice, no scam content found"
    assert any("benign" in r.lower() for r in report.reasons)


def test_fusion_genuine_suspicious_headline() -> None:
    report = fuse_results(_aletheia(AuthenticityState.GENUINE_LIKELY), _gemini(RiskLevel.MEDIUM))
    assert report.headline == "Natural-sounding voice with suspicious content"


def test_fusion_partial_failures() -> None:
    # Gemini unavailable -> UNDETERMINED + banner
    r1 = fuse_results(
        _aletheia(AuthenticityState.SYNTHETIC_LIKELY),
        _gemini(None, status="unavailable", err="quota"),
    )
    assert r1.threat_level == ThreatLevel.UNDETERMINED
    assert r1.partial is True
    assert r1.both_unavailable is False
    assert r1.banner == "Partial analysis: content not analysed"

    # Aletheia unavailable -> level from Gemini + banner
    r2 = fuse_results(
        _aletheia(AuthenticityState.UNAVAILABLE, status="unavailable", err="timeout"),
        _gemini(RiskLevel.HIGH),
    )
    assert r2.threat_level == ThreatLevel.HIGH
    assert r2.partial is True
    assert r2.both_unavailable is False
    assert r2.banner == "Voice authenticity unavailable"

    # Both unavailable -> error state
    r3 = fuse_results(
        _aletheia(AuthenticityState.UNAVAILABLE, status="unavailable", err="timeout"),
        _gemini(None, status="unavailable", err="quota"),
    )
    assert r3.threat_level == ThreatLevel.UNDETERMINED
    assert r3.both_unavailable is True


def test_fusion_segmented_mixed_and_unintelligible_and_india_actions() -> None:
    mixed = fuse_results(
        _aletheia(
            AuthenticityState.SYNTHETIC_LIKELY,
            mixed=True,
            flagged_ranges=["01:40–03:20"],
        ),
        _gemini(RiskLevel.MEDIUM),
        region="IN",
    )
    assert mixed.threat_level == ThreatLevel.HIGH
    assert "varies across the recording" in mixed.headline.lower()
    assert "01:40–03:20" in mixed.headline
    actions_joined = " ".join(mixed.actions)
    assert "1930" in actions_joined
    assert "cybercrime.gov.in" in actions_joined
    assert len(mixed.actions) <= 6

    unint = fuse_results(
        _aletheia(AuthenticityState.GENUINE_LIKELY),
        _gemini(RiskLevel.LOW, clarity=SpeechClarity.UNINTELLIGIBLE, unintelligible_forced=True),
    )
    assert unint.headline == "Inconclusive"
