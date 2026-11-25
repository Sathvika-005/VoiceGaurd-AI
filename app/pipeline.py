"""Shared analysis orchestration pipeline used by both Web UI and WhatsApp."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.audio_tools import (
    AudioMetadata,
    AudioValidationError,
    plan_segments,
    prepare_aletheia_inputs,
    prepare_gemini_audio,
    probe_audio,
)
from app.config import Settings
from app.i18n import normalize_language
from app.limits import RuntimeLimits
from app.services.aletheia import (
    AletheiaAggregateResult,
    AletheiaClient,
    AuthenticityState,
    EVIDENCE_NOTE,
    SCORE_MEANING,
)
from app.services.fusion import FusionReport, fuse_results
from app.services.gemini_audio import GeminiAudioClient, GeminiResult

logger = logging.getLogger("voiceguard.pipeline")

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _emit(cb: ProgressCallback | None, event: dict[str, Any]) -> None:
    if cb is not None:
        await cb(event)


def _serialize_metadata(meta: AudioMetadata, filename: str) -> dict[str, Any]:
    return {
        "filename": filename,
        "duration_seconds": round(meta.duration_seconds, 2),
        "codec": meta.codec,
        "sample_rate": meta.sample_rate,
        "channels": meta.channels,
        "format_name": meta.format_name,
        "file_size_bytes": meta.file_size_bytes,
    }


async def analyze(
    audio_path: Path,
    *,
    original_filename: str,
    sha256_hex: str | None,
    settings: Settings,
    limits: RuntimeLimits,
    language: str | None = None,
    progress_cb: ProgressCallback | None = None,
    aletheia_client: AletheiaClient | None = None,
    gemini_client: GeminiAudioClient | None = None,
) -> dict[str, Any]:
    """Run validation, parallel Aletheia + Gemini analysis, and fusion on an audio file."""
    started_total = time.perf_counter()
    report_lang = normalize_language(language, default=settings.default_report_language)

    missing_creds = settings.missing_analysis_credentials()
    if (
        len(missing_creds) == 2
        and aletheia_client is None
        and gemini_client is None
    ):
        missing_str = ", ".join(missing_creds)
        raise AudioValidationError(
            f"Missing required credentials: {missing_str}. Configure them in .env before analysing audio.",
            status_code=503,
        )

    await _emit(progress_cb, {"type": "stage", "stage": "validate", "message": "Probing audio stream..."})

    meta = await probe_audio(audio_path, sha256_hex=sha256_hex)
    segments = plan_segments(
        meta.duration_seconds,
        min_seconds=settings.min_audio_seconds,
        max_segment_seconds=settings.max_aletheia_seconds,
        max_total_seconds=settings.max_total_audio_seconds,
        enable_segmentation=settings.enable_segmentation,
    )

    cache_key = f"{meta.sha256}:{report_lang}:{settings.region.upper()}"
    cached_report, cache_age = await limits.result_cache.get(cache_key)
    if cached_report is not None:
        cached_copy = dict(cached_report)
        cached_copy["cached"] = True
        cached_copy["cache_age_seconds"] = cache_age
        await _emit(progress_cb, {"type": "aletheia", "result": cached_copy["aletheia"], "cached": True})
        await _emit(progress_cb, {"type": "gemini", "result": cached_copy["gemini"], "cached": True})
        await _emit(progress_cb, {"type": "stage", "stage": "fusion", "message": "Loaded cached assessment."})
        await _emit(progress_cb, {"type": "report", "report": cached_copy})
        return cached_copy

    aletheia = aletheia_client or AletheiaClient(
        api_key=settings.detectif_api_key,
        api_url=settings.aletheia_api_url,
        max_concurrency=3,
        debug_raw_responses=settings.debug_raw_responses,
    )
    gemini = gemini_client or GeminiAudioClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        debug_raw_responses=settings.debug_raw_responses,
    )

    async with limits.analysis_semaphore:
        with tempfile.TemporaryDirectory(prefix="voiceguard_") as tmp_dir_str:
            work_dir = Path(tmp_dir_str)
            await _emit(
                progress_cb,
                {
                    "type": "stage",
                    "stage": "preprocess",
                    "message": f"Preparing {len(segments)} audio segment(s)...",
                    "segment_count": len(segments),
                },
            )

            prep_started = time.perf_counter()
            aletheia_files, (gemini_path, gemini_mime) = await asyncio.gather(
                prepare_aletheia_inputs(audio_path, meta, segments, work_dir),
                prepare_gemini_audio(audio_path, work_dir),
            )
            prep_ms = int((time.perf_counter() - prep_started) * 1000)

            await _emit(
                progress_cb,
                {
                    "type": "stage",
                    "stage": "engines",
                    "message": "Running Aletheia and Gemini in parallel...",
                },
            )

            async def _run_aletheia() -> AletheiaAggregateResult:
                started_a = time.perf_counter()
                try:
                    res = await asyncio.wait_for(
                        aletheia.analyze_segments(aletheia_files),
                        timeout=15.0,
                    )
                except asyncio.TimeoutError:
                    res = AletheiaAggregateResult(
                        status="unavailable",
                        state=AuthenticityState.UNAVAILABLE,
                        raw_verdict=None,
                        score=None,
                        score_meaning=SCORE_MEANING,
                        evidence_note=EVIDENCE_NOTE,
                        segments=[],
                        flagged_ranges=[],
                        mixed=False,
                        elapsed_ms=int((time.perf_counter() - started_a) * 1000),
                        error_message="DetectifAI Aletheia timed out after 15 seconds.",
                    )
                except Exception as exc:
                    logger.warning("Aletheia task failed: %s", type(exc).__name__)
                    res = AletheiaAggregateResult(
                        status="unavailable",
                        state=AuthenticityState.UNAVAILABLE,
                        raw_verdict=None,
                        score=None,
                        score_meaning=SCORE_MEANING,
                        evidence_note=EVIDENCE_NOTE,
                        segments=[],
                        flagged_ranges=[],
                        mixed=False,
                        elapsed_ms=int((time.perf_counter() - started_a) * 1000),
                        error_message=f"DetectifAI Aletheia error: {type(exc).__name__}",
                    )
                await _emit(progress_cb, {"type": "aletheia", "result": res.to_dict(), "cached": False})
                return res

            async def _on_gemini_retry(msg: str) -> None:
                await _emit(progress_cb, {"type": "stage", "stage": "engines", "message": msg})

            async def _run_gemini() -> GeminiResult:
                started_g = time.perf_counter()
                try:
                    kwargs: dict[str, Any] = {"report_language": report_lang}
                    if gemini_client is None:
                        kwargs["on_retry"] = _on_gemini_retry
                    res = await asyncio.wait_for(
                        gemini.analyze_audio(
                            gemini_path,
                            gemini_mime,
                            **kwargs,
                        ),
                        timeout=240.0,
                    )
                except asyncio.TimeoutError:
                    res = GeminiResult(
                        status="unavailable",
                        analysis=None,
                        elapsed_ms=int((time.perf_counter() - started_g) * 1000),
                        error_message="Gemini Native Audio timed out after 240 seconds.",
                    )
                except Exception as exc:
                    logger.warning("Gemini task failed: %s", type(exc).__name__)
                    res = GeminiResult(
                        status="unavailable",
                        analysis=None,
                        elapsed_ms=int((time.perf_counter() - started_g) * 1000),
                        error_message=f"Gemini Native Audio error: {type(exc).__name__}",
                    )
                await _emit(progress_cb, {"type": "gemini", "result": res.to_dict(), "cached": False})
                return res

            aletheia_res, gemini_res = await asyncio.gather(_run_aletheia(), _run_gemini())

    await _emit(progress_cb, {"type": "stage", "stage": "fusion", "message": "Combining engine verdicts..."})
    fusion: FusionReport = fuse_results(aletheia_res, gemini_res, region=settings.region)
    total_ms = int((time.perf_counter() - started_total) * 1000)

    report: dict[str, Any] = {
        "audio": _serialize_metadata(meta, original_filename),
        "report_language": report_lang,
        "region": settings.region.upper(),
        "cached": False,
        "cache_age_seconds": 0,
        "aletheia": aletheia_res.to_dict(),
        "gemini": gemini_res.to_dict(),
        "fusion": fusion.to_dict(),
        "timings_ms": {
            "preprocess": prep_ms,
            "aletheia": aletheia_res.elapsed_ms,
            "gemini": gemini_res.elapsed_ms,
            "total": total_ms,
        },
    }

    # Cache only when both engines succeeded cleanly
    if not fusion.both_unavailable and aletheia_res.status == "ok" and gemini_res.status == "ok":
        await limits.result_cache.set(cache_key, report)

    await _emit(progress_cb, {"type": "report", "report": report})
    return report
