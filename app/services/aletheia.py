"""DetectifAI Aletheia voice-deepfake detection client and response adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.audio_tools import PreparedSegmentFile

logger = logging.getLogger("voiceguard.aletheia")

DEFAULT_ALETHEIA_API_URL = "https://platform.detectif.ai/v1/deepfake/detect"
_MARKDOWN_LINK_RE = re.compile(r"^\[([^\]]+)\]\((https?://[^)\s]+)\)$")

SCORE_MEANING = "Synthetic likelihood score (0.0 = genuine, 1.0 = synthetic)"
EVIDENCE_NOTE = "A detection score is evidence, not proof."


def normalize_aletheia_api_url(raw_url: str | None) -> str:
    """Normalize base URLs or accidental markdown links into the Aletheia detect endpoint."""
    cleaned = (raw_url or "").strip()
    if not cleaned:
        return DEFAULT_ALETHEIA_API_URL

    md_match = _MARKDOWN_LINK_RE.match(cleaned)
    if md_match:
        cleaned = md_match.group(2).strip()
    elif cleaned.startswith("<") and cleaned.endswith(">"):
        cleaned = cleaned[1:-1].strip()

    parsed = urlparse(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return cleaned

    path = parsed.path.rstrip("/")
    if not path or (
        parsed.netloc.lower() == "platform.detectif.ai"
        and path in {"/v1", "/v1/deepfake", "/deepfake", "/detect"}
    ):
        path = "/v1/deepfake/detect"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


class AuthenticityState(str, Enum):
    SYNTHETIC_LIKELY = "SYNTHETIC_LIKELY"
    GENUINE_LIKELY = "GENUINE_LIKELY"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class AletheiaSegmentResult:
    index: int
    start: float
    end: float
    time_range: str
    status: str  # ok | unavailable | unrecognised
    state: AuthenticityState
    raw_verdict: str | None = None
    score: float | None = None
    score_meaning: str = SCORE_MEANING
    request_id: str | None = None
    flagged_subsegments: list[dict[str, float]] = field(default_factory=list)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass
class AletheiaAggregateResult:
    status: str  # ok | unavailable | unrecognised
    state: AuthenticityState
    raw_verdict: str | None
    score: float | None
    score_meaning: str
    evidence_note: str
    segments: list[AletheiaSegmentResult]
    flagged_ranges: list[str]
    mixed: bool
    elapsed_ms: int
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "state": self.state.value,
            "raw_verdict": self.raw_verdict,
            "score": self.score,
            "score_meaning": self.score_meaning,
            "evidence_note": self.evidence_note,
            "segments": [seg.to_dict() for seg in self.segments],
            "flagged_ranges": self.flagged_ranges,
            "mixed": self.mixed,
            "elapsed_ms": self.elapsed_ms,
            "error_message": self.error_message,
        }


def parse_aletheia_response(
    payload: Any,
    *,
    header_request_id: str | None = None,
    segment_index: int = 0,
    start: float = 0.0,
    end: float = 0.0,
    time_range: str = "00:00–00:00",
) -> AletheiaSegmentResult:
    """Strictly adapt a documented DetectifAI Aletheia JSON payload.

    # ASSUMPTION: DetectifAI docs show both top-level (`verdict`, `score`) and
    # `result`-nested (`result.verdict`, `result.score`) structures; we accept
    # either documented structure and mark any other shape `unrecognised`.
    """
    if not isinstance(payload, dict):
        logger.warning("Aletheia schema mismatch: response JSON is not an object")
        return AletheiaSegmentResult(
            index=segment_index,
            start=start,
            end=end,
            time_range=time_range,
            status="unrecognised",
            state=AuthenticityState.UNAVAILABLE,
            error_message="Aletheia returned an unrecognised response.",
        )

    request_id = payload.get("request_id") or header_request_id
    container = payload.get("result") if isinstance(payload.get("result"), dict) else payload

    verdict_raw = container.get("verdict")
    score_raw = container.get("score")

    if not isinstance(verdict_raw, str) or not verdict_raw.strip():
        logger.warning("Aletheia schema mismatch: missing or non-string 'verdict'")
        return AletheiaSegmentResult(
            index=segment_index,
            start=start,
            end=end,
            time_range=time_range,
            status="unrecognised",
            state=AuthenticityState.UNAVAILABLE,
            request_id=str(request_id) if request_id else None,
            error_message="Aletheia returned an unrecognised response.",
        )

    verdict_norm = verdict_raw.strip().lower()
    if verdict_norm in {"fake", "synthetic"}:
        state = AuthenticityState.SYNTHETIC_LIKELY
    elif verdict_norm in {"real", "genuine"}:
        state = AuthenticityState.GENUINE_LIKELY
    elif verdict_norm in {"inconclusive", "uncertain"}:
        # # ASSUMPTION: Handle explicit inconclusive verdict defensively if returned.
        state = AuthenticityState.INCONCLUSIVE
    else:
        logger.warning("Aletheia schema mismatch: unexpected verdict '%s'", verdict_raw)
        return AletheiaSegmentResult(
            index=segment_index,
            start=start,
            end=end,
            time_range=time_range,
            status="unrecognised",
            state=AuthenticityState.UNAVAILABLE,
            request_id=str(request_id) if request_id else None,
            error_message="Aletheia returned an unrecognised response.",
        )

    score: float | None = None
    if score_raw is not None:
        if isinstance(score_raw, bool) or not isinstance(score_raw, (int, float)):
            logger.warning("Aletheia schema mismatch: non-numeric score")
            return AletheiaSegmentResult(
                index=segment_index,
                start=start,
                end=end,
                time_range=time_range,
                status="unrecognised",
                state=AuthenticityState.UNAVAILABLE,
                request_id=str(request_id) if request_id else None,
                error_message="Aletheia returned an unrecognised response.",
            )
        score = float(score_raw)
        if not math.isfinite(score) or score < 0.0 or score > 1.0:
            logger.warning("Aletheia schema mismatch: score out of [0, 1] range: %s", score)
            return AletheiaSegmentResult(
                index=segment_index,
                start=start,
                end=end,
                time_range=time_range,
                status="unrecognised",
                state=AuthenticityState.UNAVAILABLE,
                request_id=str(request_id) if request_id else None,
                error_message="Aletheia returned an unrecognised response.",
            )

    subsegments: list[dict[str, float]] = []
    raw_subs = container.get("segments")
    if isinstance(raw_subs, list):
        for item in raw_subs:
            if isinstance(item, dict) and "start" in item and "end" in item:
                try:
                    entry: dict[str, float] = {
                        "start": float(item["start"]),
                        "end": float(item["end"]),
                    }
                    if "score" in item and isinstance(item["score"], (int, float)):
                        entry["score"] = float(item["score"])
                    subsegments.append(entry)
                except (TypeError, ValueError):
                    continue

    return AletheiaSegmentResult(
        index=segment_index,
        start=start,
        end=end,
        time_range=time_range,
        status="ok",
        state=state,
        raw_verdict=verdict_raw.strip(),
        score=score,
        score_meaning=SCORE_MEANING,
        request_id=str(request_id) if request_id else None,
        flagged_subsegments=subsegments,
        error_message=None,
    )


def aggregate_segments(
    segments: list[AletheiaSegmentResult],
    elapsed_ms: int,
) -> AletheiaAggregateResult:
    """Aggregate per-segment Aletheia outcomes without fabricating average scores."""
    if not segments:
        return AletheiaAggregateResult(
            status="unavailable",
            state=AuthenticityState.UNAVAILABLE,
            raw_verdict=None,
            score=None,
            score_meaning=SCORE_MEANING,
            evidence_note=EVIDENCE_NOTE,
            segments=[],
            flagged_ranges=[],
            mixed=False,
            elapsed_ms=elapsed_ms,
            error_message="No audio segments were supplied to Aletheia.",
        )

    if len(segments) == 1:
        single = segments[0]
        flagged = [single.time_range] if single.state == AuthenticityState.SYNTHETIC_LIKELY else []
        return AletheiaAggregateResult(
            status=single.status,
            state=single.state,
            raw_verdict=single.raw_verdict,
            score=single.score,
            score_meaning=SCORE_MEANING,
            evidence_note=EVIDENCE_NOTE,
            segments=segments,
            flagged_ranges=flagged,
            mixed=False,
            elapsed_ms=elapsed_ms,
            error_message=single.error_message,
        )

    flagged_segs = [s for s in segments if s.state == AuthenticityState.SYNTHETIC_LIKELY]
    genuine_segs = [s for s in segments if s.state == AuthenticityState.GENUINE_LIKELY]
    inconclusive_segs = [s for s in segments if s.state == AuthenticityState.INCONCLUSIVE]
    unavail_segs = [s for s in segments if s.state == AuthenticityState.UNAVAILABLE]

    flagged_ranges = [s.time_range for s in flagged_segs]

    if len(unavail_segs) == len(segments):
        has_unrecognised = any(s.status == "unrecognised" for s in segments)
        first_err = next((s.error_message for s in segments if s.error_message), "Aletheia unavailable.")
        return AletheiaAggregateResult(
            status="unrecognised" if has_unrecognised else "unavailable",
            state=AuthenticityState.UNAVAILABLE,
            raw_verdict=None,
            score=None,
            score_meaning=SCORE_MEANING,
            evidence_note=EVIDENCE_NOTE,
            segments=segments,
            flagged_ranges=[],
            mixed=False,
            elapsed_ms=elapsed_ms,
            error_message=first_err,
        )

    if flagged_segs:
        mixed = len(flagged_segs) < len(segments)
        return AletheiaAggregateResult(
            status="ok",
            state=AuthenticityState.SYNTHETIC_LIKELY,
            raw_verdict=f"fake ({len(flagged_segs)}/{len(segments)} segments)",
            score=None,  # Never average scores into a made-up number
            score_meaning=SCORE_MEANING,
            evidence_note=EVIDENCE_NOTE,
            segments=segments,
            flagged_ranges=flagged_ranges,
            mixed=mixed,
            elapsed_ms=elapsed_ms,
            error_message=None,
        )

    if inconclusive_segs or unavail_segs:
        return AletheiaAggregateResult(
            status="ok",
            state=AuthenticityState.INCONCLUSIVE,
            raw_verdict=f"inconclusive ({len(genuine_segs)}/{len(segments)} genuine, partial/inconclusive)",
            score=None,
            score_meaning=SCORE_MEANING,
            evidence_note=EVIDENCE_NOTE,
            segments=segments,
            flagged_ranges=[],
            mixed=True,
            elapsed_ms=elapsed_ms,
            error_message=next((s.error_message for s in unavail_segs if s.error_message), None),
        )

    return AletheiaAggregateResult(
        status="ok",
        state=AuthenticityState.GENUINE_LIKELY,
        raw_verdict=f"real (all {len(segments)} segments)",
        score=None,
        score_meaning=SCORE_MEANING,
        evidence_note=EVIDENCE_NOTE,
        segments=segments,
        flagged_ranges=[],
        mixed=False,
        elapsed_ms=elapsed_ms,
        error_message=None,
    )


class AletheiaClient:
    """Async HTTP client for DetectifAI Aletheia with bounded retries and concurrency."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = DEFAULT_ALETHEIA_API_URL,
        timeout_seconds: float = 15.0,
        max_retries: int = 1,
        max_concurrency: int = 3,
        debug_raw_responses: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.api_url = normalize_aletheia_api_url(api_url)
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_concurrency = max(1, max_concurrency)
        self.debug_raw_responses = debug_raw_responses
        self.transport = transport

    async def detect_segment(
        self,
        segment: PreparedSegmentFile,
        client: httpx.AsyncClient,
    ) -> AletheiaSegmentResult:
        """Send one audio clip to DetectifAI Aletheia with retry on timeout/429/5xx only."""
        idempotency_key = str(uuid.uuid4())
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Idempotency-Key": idempotency_key,
        }
        audio_bytes = segment.path.read_bytes()
        filename = segment.path.name

        last_error = "DetectifAI Aletheia request failed."
        for attempt in range(self.max_retries + 1):
            try:
                files = {"file": (filename, audio_bytes, segment.mime_type)}
                resp = await client.post(
                    self.api_url,
                    headers=headers,
                    files=files,
                )
            except httpx.TimeoutException:
                last_error = "DetectifAI Aletheia request timed out."
                if attempt < self.max_retries:
                    await asyncio.sleep(_backoff_delay(attempt))
                    continue
                return _unavailable_segment(segment, last_error)
            except httpx.TransportError as exc:
                logger.warning("Aletheia transport error: %s", type(exc).__name__)
                last_error = "Network error while contacting DetectifAI Aletheia."
                if attempt < self.max_retries:
                    await asyncio.sleep(_backoff_delay(attempt))
                    continue
                return _unavailable_segment(segment, last_error)

            status = resp.status_code
            body_snippet = " ".join((resp.text or "").split())[:500]
            if status < 200 or status >= 300:
                logger.warning(
                    "Aletheia error status=%s url=%s body=%s",
                    status,
                    self.api_url,
                    body_snippet,
                )

            if status in (401, 403):
                detail = f" Response: {body_snippet}" if body_snippet else ""
                return _unavailable_segment(
                    segment,
                    f"DetectifAI API key rejected (HTTP {status} from {self.api_url}). Check DETECTIF_API_KEY.{detail}",
                )
            if status == 429:
                detail = f" Response: {body_snippet}" if body_snippet else ""
                last_error = f"DetectifAI Aletheia rate limit reached (HTTP 429 from {self.api_url}). Please retry shortly.{detail}"
                if attempt < self.max_retries:
                    await asyncio.sleep(_backoff_delay(attempt))
                    continue
                return _unavailable_segment(segment, last_error)
            if 500 <= status < 600:
                detail = f" Response: {body_snippet}" if body_snippet else ""
                last_error = f"DetectifAI Aletheia service error (HTTP {status} from {self.api_url}).{detail}"
                if attempt < self.max_retries:
                    await asyncio.sleep(_backoff_delay(attempt))
                    continue
                return _unavailable_segment(segment, last_error)
            if 400 <= status < 500:
                # Never retry 4xx validation/client errors
                detail = f" — {body_snippet}" if body_snippet else ""
                return _unavailable_segment(
                    segment,
                    f"DetectifAI Aletheia rejected the audio clip (HTTP {status} from {self.api_url}){detail}",
                )
            if status < 200 or status >= 300:
                detail = f" — {body_snippet}" if body_snippet else ""
                return _unavailable_segment(
                    segment,
                    f"Unexpected response status from Aletheia (HTTP {status} from {self.api_url}){detail}",
                )

            try:
                payload = resp.json()
            except (json.JSONDecodeError, ValueError):
                logger.warning("Aletheia returned non-JSON HTTP %s body", status)
                return AletheiaSegmentResult(
                    index=segment.spec.index,
                    start=segment.spec.start,
                    end=segment.spec.end,
                    time_range=segment.spec.label,
                    status="unrecognised",
                    state=AuthenticityState.UNAVAILABLE,
                    request_id=resp.headers.get("X-Request-Id"),
                    error_message="Aletheia returned an unrecognised response.",
                )

            if self.debug_raw_responses:
                logger.debug("Aletheia raw response: %s", json.dumps(payload)[:1000])

            return parse_aletheia_response(
                payload,
                header_request_id=resp.headers.get("X-Request-Id"),
                segment_index=segment.spec.index,
                start=segment.spec.start,
                end=segment.spec.end,
                time_range=segment.spec.label,
            )

        return _unavailable_segment(segment, last_error)

    async def analyze_segments(
        self,
        segments: list[PreparedSegmentFile],
    ) -> AletheiaAggregateResult:
        started = time.perf_counter()
        if not self.api_key:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            seg_results = [
                _unavailable_segment(seg, "Missing credential: DETECTIF_API_KEY")
                for seg in segments
            ]
            return aggregate_segments(seg_results, elapsed_ms)

        sem = asyncio.Semaphore(self.max_concurrency)
        timeout = httpx.Timeout(
            connect=10.0,
            read=self.timeout_seconds,
            write=30.0,
            pool=10.0,
        )

        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:

            async def _run_one(seg: PreparedSegmentFile) -> AletheiaSegmentResult:
                async with sem:
                    return await self.detect_segment(seg, client)

            results = await asyncio.gather(*(_run_one(seg) for seg in segments))

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        ordered = sorted(results, key=lambda r: r.index)
        return aggregate_segments(ordered, elapsed_ms)


def _unavailable_segment(segment: PreparedSegmentFile, message: str) -> AletheiaSegmentResult:
    return AletheiaSegmentResult(
        index=segment.spec.index,
        start=segment.spec.start,
        end=segment.spec.end,
        time_range=segment.spec.label,
        status="unavailable",
        state=AuthenticityState.UNAVAILABLE,
        error_message=message,
    )


def _backoff_delay(attempt: int) -> float:
    base = 0.4 * (2**attempt)
    jitter = random.uniform(0.0, 0.2)
    return base + jitter
