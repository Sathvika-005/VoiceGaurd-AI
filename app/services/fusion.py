"""Deterministic, explainable fusion of Aletheia voice authenticity and Gemini content risk."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from app.services.aletheia import AletheiaAggregateResult, AuthenticityState
from app.services.gemini_audio import GeminiResult, IndicatorCategory, RiskLevel


class ThreatLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNDETERMINED = "UNDETERMINED"


_ESCALATE_ONE: dict[RiskLevel, ThreatLevel] = {
    RiskLevel.LOW: ThreatLevel.LOW,
    RiskLevel.MEDIUM: ThreatLevel.HIGH,
    RiskLevel.HIGH: ThreatLevel.CRITICAL,
    RiskLevel.CRITICAL: ThreatLevel.CRITICAL,
}

_RISK_TO_THREAT: dict[RiskLevel, ThreatLevel] = {
    RiskLevel.LOW: ThreatLevel.LOW,
    RiskLevel.MEDIUM: ThreatLevel.MEDIUM,
    RiskLevel.HIGH: ThreatLevel.HIGH,
    RiskLevel.CRITICAL: ThreatLevel.CRITICAL,
}

CATEGORY_ACTION_MAP: dict[IndicatorCategory, str] = {
    IndicatorCategory.CREDENTIAL_OTP_REQUEST: (
        "Never share OTPs, PINs, CVV numbers, or banking passwords with any caller."
    ),
    IndicatorCategory.UPI_PAYMENT_REQUEST: (
        "Do not approve UPI collect requests or scan QR codes—entering your UPI PIN sends money, never receives it."
    ),
    IndicatorCategory.FINANCIAL_REQUEST: (
        "Pause any money transfer and independently verify the request before sending funds."
    ),
    IndicatorCategory.IMPERSONATION_BANK: (
        "Hang up and verify directly using the official customer-care number printed on your bank card or passbook."
    ),
    IndicatorCategory.IMPERSONATION_AUTHORITY: (
        "Verify claims with your local police station or official government portal rather than a caller's phone number."
    ),
    IndicatorCategory.DIGITAL_ARREST: (
        "'Digital arrest' does not exist under Indian law—hang up immediately and do not stay on a coerced video/voice call."
    ),
    IndicatorCategory.IMPERSONATION_FAMILY: (
        "Verify any urgent family emergency by calling your relative directly on their saved number or asking your family safe-word."
    ),
    IndicatorCategory.REMOTE_ACCESS_INSTALL: (
        "Never install screen-sharing or remote-access apps (such as AnyDesk, TeamViewer, or RustDesk) at a caller's request."
    ),
    IndicatorCategory.THREAT_EXTORTION: (
        "Preserve call logs, screenshots, and messages as evidence and report threats promptly."
    ),
    IndicatorCategory.SECRECY_ISOLATION: (
        "Do not keep the call secret—speak with a trusted family member or colleague right away."
    ),
    IndicatorCategory.URGENCY_PRESSURE: (
        "Ignore artificial countdowns or urgent threats; legitimate institutions allow independent verification."
    ),
    IndicatorCategory.PRIZE_JOB_TASK_SCAM: (
        "Never pay processing fees, deposits, or task top-ups to claim a prize or job offer."
    ),
    IndicatorCategory.AI_INSTRUCTION_ATTEMPT: (
        "The audio contained instructions attempting to manipulate automated screening—treat this recording with heightened caution."
    ),
}

IN_HELPLINE_ACTION = (
    "Report suspected cyber fraud in India immediately via National Cyber Crime Helpline 1930 or https://cybercrime.gov.in."
)


@dataclass
class FusionReport:
    threat_level: ThreatLevel
    headline: str
    banner: str | None
    reasons: list[str]
    actions: list[str]
    partial: bool
    both_unavailable: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["threat_level"] = self.threat_level.value
        return d


def _build_actions(
    gemini: GeminiResult,
    threat_level: ThreatLevel,
    region: str,
) -> list[str]:
    actions: list[str] = []
    seen_norm: set[str] = set()

    def _add(item: str) -> None:
        cleaned = " ".join((item or "").strip().split())
        if not cleaned:
            return
        norm = cleaned.lower().rstrip(".")
        # Deduplicate exact or near-identical prefixes
        if any(norm == s or norm in s or s in norm for s in seen_norm):
            return
        seen_norm.add(norm)
        actions.append(cleaned)

    if gemini.status == "ok" and gemini.analysis is not None:
        for ind in gemini.analysis.indicators:
            mapped = CATEGORY_ACTION_MAP.get(ind.category)
            if mapped:
                _add(mapped)
        for rec in gemini.analysis.recommended_actions:
            _add(rec)

    if region.strip().upper() == "IN":
        _add(IN_HELPLINE_ACTION)

    if not actions and threat_level != ThreatLevel.LOW:
        _add("Verify the caller's identity through an independent, trusted channel before taking any action.")

    return actions[:6]


def fuse_results(
    aletheia: AletheiaAggregateResult,
    gemini: GeminiResult,
    *,
    region: str = "IN",
) -> FusionReport:
    """Deterministically fuse Aletheia voice authenticity and Gemini content risk."""
    aletheia_ok = aletheia.status == "ok" and aletheia.state != AuthenticityState.UNAVAILABLE
    gemini_ok = gemini.status == "ok" and gemini.analysis is not None

    # Case 1: Both engines unavailable
    if not aletheia_ok and not gemini_ok:
        reasons = []
        if aletheia.error_message:
            reasons.append(f"Aletheia unavailable: {aletheia.error_message}")
        if gemini.error_message:
            reasons.append(f"Gemini unavailable: {gemini.error_message}")
        if not reasons:
            reasons.append("Both analysis engines failed to return a result.")
        return FusionReport(
            threat_level=ThreatLevel.UNDETERMINED,
            headline="Analysis unavailable",
            banner="Both voice-authenticity and content-risk engines were unavailable.",
            reasons=reasons,
            actions=_build_actions(gemini, ThreatLevel.UNDETERMINED, region),
            partial=True,
            both_unavailable=True,
        )

    # Case 2: Gemini unavailable, Aletheia available
    if not gemini_ok:
        reasons: list[str] = []
        if aletheia.mixed and aletheia.flagged_ranges:
            headline = f"Voice authenticity varies across the recording ({', '.join(aletheia.flagged_ranges)})"
            reasons.append(
                f"Aletheia flagged synthetic speech in segment(s): {', '.join(aletheia.flagged_ranges)}."
            )
        elif aletheia.state == AuthenticityState.SYNTHETIC_LIKELY:
            headline = "Likely AI-generated voice (content not analysed)"
            reasons.append("Aletheia detected likely synthetic or manipulated speech.")
        elif aletheia.state == AuthenticityState.GENUINE_LIKELY:
            headline = "Natural-sounding voice (content not analysed)"
            reasons.append("Aletheia found no synthetic voice markers, but scam content could not be checked.")
        else:
            headline = "Authenticity could not be established (content not analysed)"
            reasons.append("Aletheia returned an inconclusive result across segments.")

        if gemini.error_message:
            reasons.append(f"Gemini unavailable: {gemini.error_message}")
        reasons.append(aletheia.evidence_note)

        return FusionReport(
            threat_level=ThreatLevel.UNDETERMINED,
            headline=headline,
            banner="Partial analysis: content not analysed",
            reasons=reasons,
            actions=_build_actions(gemini, ThreatLevel.UNDETERMINED, region),
            partial=True,
            both_unavailable=False,
        )

    # Case 3: Gemini available (with or without Aletheia)
    assert gemini.analysis is not None
    base_risk = gemini.analysis.risk_level
    reasons = []
    banner: str | None = None
    partial = False

    if not aletheia_ok:
        threat_level = _RISK_TO_THREAT[base_risk]
        partial = True
        banner = "Voice authenticity unavailable"
        if gemini.unintelligible_forced:
            headline = "Inconclusive"
        elif base_risk in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}:
            headline = "Suspicious communication content detected (voice authenticity unavailable)"
        else:
            headline = "No scam tactics detected (voice authenticity unavailable)"
        if aletheia.error_message:
            reasons.append(f"Aletheia unavailable: {aletheia.error_message}")
        reasons.append(f"Gemini assessed communication risk as {base_risk.value}.")
    elif aletheia.state == AuthenticityState.SYNTHETIC_LIKELY:
        if base_risk in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}:
            threat_level = _ESCALATE_ONE[base_risk]
            if gemini.unintelligible_forced:
                headline = "Inconclusive"
            elif aletheia.mixed and aletheia.flagged_ranges:
                headline = (
                    "Voice authenticity varies across the recording — escalated threat "
                    f"(flagged: {', '.join(aletheia.flagged_ranges)})"
                )
            else:
                headline = "AI-generated voice combined with scam tactics"

            if threat_level.value != base_risk.value:
                reasons.append(
                    f"Escalated from {base_risk.value} to {threat_level.value} because Aletheia detected "
                    "likely synthetic voice alongside suspicious communication tactics."
                )
            else:
                reasons.append(
                    "Remains CRITICAL: Aletheia detected likely synthetic voice alongside critical scam tactics."
                )
            if aletheia.flagged_ranges:
                reasons.append(
                    f"Flagged synthetic audio range(s): {', '.join(aletheia.flagged_ranges)}."
                )
        else:
            # SYNTHETIC_LIKELY + LOW stays LOW
            threat_level = ThreatLevel.LOW
            if gemini.unintelligible_forced:
                headline = "Inconclusive"
            elif aletheia.mixed and aletheia.flagged_ranges:
                headline = (
                    "Voice authenticity varies across the recording — no scam content found "
                    f"(flagged: {', '.join(aletheia.flagged_ranges)})"
                )
            else:
                headline = "AI-generated voice, no scam content found"
            reasons.append(
                "Aletheia detected likely synthetic speech, but Gemini found no coercive or scam tactics."
            )
            reasons.append(
                "Note: Benign synthetic voices exist (automated IVR alerts, announcements, accessibility TTS)."
            )
            if aletheia.flagged_ranges:
                reasons.append(
                    f"Flagged synthetic audio range(s): {', '.join(aletheia.flagged_ranges)}."
                )
    elif aletheia.state == AuthenticityState.INCONCLUSIVE:
        threat_level = _RISK_TO_THREAT[base_risk]
        if gemini.unintelligible_forced:
            headline = "Inconclusive"
        elif aletheia.mixed:
            headline = "Voice authenticity varies across the recording; authenticity could not be established"
        elif base_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            headline = "High-risk content; voice authenticity could not be established"
        else:
            headline = "Authenticity could not be established"
        reasons.append(
            f"Threat level follows Gemini's {base_risk.value} content assessment because voice authenticity was inconclusive."
        )
    else:
        # GENUINE_LIKELY
        threat_level = _RISK_TO_THREAT[base_risk]
        if gemini.unintelligible_forced:
            headline = "Inconclusive"
        elif base_risk in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}:
            headline = "Natural-sounding voice with suspicious content"
            reasons.append(
                "Aletheia found the voice likely natural, but human scammers still use real voices for fraud."
            )
        else:
            headline = "No synthetic voice or scam indicators detected"
            reasons.append(
                "Aletheia found the voice likely natural and Gemini found no scam indicators."
            )

    if gemini.downgraded_for_missing_evidence:
        reasons.append(
            "Gemini's high-risk rating was downgraded by one level because no concrete audible evidence was cited."
        )
    if aletheia_ok:
        reasons.append(aletheia.evidence_note)

    return FusionReport(
        threat_level=threat_level,
        headline=headline,
        banner=banner,
        reasons=reasons,
        actions=_build_actions(gemini, threat_level, region),
        partial=partial,
        both_unavailable=False,
    )
