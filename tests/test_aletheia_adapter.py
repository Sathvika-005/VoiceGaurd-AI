"""Unit tests for the DetectifAI Aletheia adapter, retry policy, and aggregation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.audio_tools import AudioSegmentSpec, PreparedSegmentFile
from app.config import Settings
from app.services.aletheia import (
    AletheiaClient,
    AletheiaSegmentResult,
    AuthenticityState,
    aggregate_segments,
    normalize_aletheia_api_url,
    parse_aletheia_response,
)

ALETHEIA_URL = "https://platform.detectif.ai/v1/deepfake/detect"


def _dummy_segment(tmp_path: Path, index: int = 0, start: float = 0.0, end: float = 10.0) -> PreparedSegmentFile:
    audio_file = tmp_path / f"seg_{index}.wav"
    audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    spec = AudioSegmentSpec(index=index, start=start, end=end)
    return PreparedSegmentFile(spec=spec, path=audio_file, mime_type="audio/wav")


def test_parse_aletheia_documented_top_level_fake() -> None:
    # Fixture derived from DetectifAI Aletheia REST response docs
    payload = {
        "request_id": "req_8f14e45fceea167a5a36dedd",
        "verdict": "fake",
        "score": 0.88,
        "label": "synthetic",
        "segments": [{"start": 8.0, "end": 16.0, "score": 0.93}],
    }
    res = parse_aletheia_response(payload, time_range="00:00–00:20")
    assert res.status == "ok"
    assert res.state == AuthenticityState.SYNTHETIC_LIKELY
    assert res.raw_verdict == "fake"
    assert res.score == pytest.approx(0.88)
    assert res.request_id == "req_8f14e45fceea167a5a36dedd"
    assert len(res.flagged_subsegments) == 1


def test_parse_aletheia_documented_nested_real() -> None:
    # Fixture derived from DetectifAI Aletheia docs nested result variant
    payload = {
        "request_id": "req_real_01",
        "result": {
            "verdict": "real",
            "score": 0.07,
        },
    }
    res = parse_aletheia_response(payload)
    assert res.status == "ok"
    assert res.state == AuthenticityState.GENUINE_LIKELY
    assert res.raw_verdict == "real"
    assert res.score == pytest.approx(0.07)


@pytest.mark.parametrize(
    "bad_payload",
    [
        "not-a-dict",
        {},
        {"verdict": "maybe_ai_who_knows", "score": 0.5},
        {"verdict": "fake", "score": "high"},
        {"verdict": "real", "score": 1.5},
        {"verdict": "real", "score": -0.1},
    ],
)
def test_parse_aletheia_unknown_shape_returns_unrecognised(bad_payload) -> None:
    res = parse_aletheia_response(bad_payload)
    assert res.status == "unrecognised"
    assert res.state == AuthenticityState.UNAVAILABLE
    assert "unrecognised" in (res.error_message or "").lower()


def test_aggregate_segments_rules() -> None:
    s0 = AletheiaSegmentResult(0, 0.0, 100.0, "00:00–01:40", "ok", AuthenticityState.GENUINE_LIKELY, "real", 0.1)
    s1 = AletheiaSegmentResult(1, 100.0, 200.0, "01:40–03:20", "ok", AuthenticityState.SYNTHETIC_LIKELY, "fake", 0.92)
    s2 = AletheiaSegmentResult(2, 200.0, 250.0, "03:20–04:10", "ok", AuthenticityState.GENUINE_LIKELY, "real", 0.12)

    # Any flagged -> SYNTHETIC_LIKELY + flagged_ranges + never averaged score
    agg_flagged = aggregate_segments([s0, s1, s2], elapsed_ms=250)
    assert agg_flagged.state == AuthenticityState.SYNTHETIC_LIKELY
    assert agg_flagged.flagged_ranges == ["01:40–03:20"]
    assert agg_flagged.mixed is True
    assert agg_flagged.score is None

    # All genuine -> GENUINE_LIKELY
    s1_real = AletheiaSegmentResult(1, 100.0, 200.0, "01:40–03:20", "ok", AuthenticityState.GENUINE_LIKELY, "real", 0.15)
    agg_real = aggregate_segments([s0, s1_real, s2], elapsed_ms=200)
    assert agg_real.state == AuthenticityState.GENUINE_LIKELY
    assert agg_real.mixed is False
    assert agg_real.flagged_ranges == []

    # Mixed genuine + unavailable -> INCONCLUSIVE
    s1_unavail = AletheiaSegmentResult(1, 100.0, 200.0, "01:40–03:20", "unavailable", AuthenticityState.UNAVAILABLE, error_message="timeout")
    agg_mixed = aggregate_segments([s0, s1_unavail], elapsed_ms=300)
    assert agg_mixed.state == AuthenticityState.INCONCLUSIVE
    assert agg_mixed.mixed is True


@pytest.mark.asyncio
async def test_aletheia_http_401_no_retry_and_clear_message(tmp_path: Path) -> None:
    seg = _dummy_segment(tmp_path)
    client = AletheiaClient(api_key="bad-key", api_url=ALETHEIA_URL, max_retries=2)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post(ALETHEIA_URL).mock(
            return_value=httpx.Response(401, json={"error": "invalid token"})
        )
        res = await client.analyze_segments([seg])

    assert route.call_count == 1  # Never retry 401
    assert res.status == "unavailable"
    assert res.state == AuthenticityState.UNAVAILABLE
    assert "DETECTIF_API_KEY" in (res.error_message or "")


@pytest.mark.asyncio
async def test_aletheia_http_429_and_5xx_retries_and_recovers(tmp_path: Path, monkeypatch) -> None:
    async def _fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.aletheia.asyncio.sleep", _fast_sleep)

    seg = _dummy_segment(tmp_path)
    client = AletheiaClient(api_key="test-key", api_url=ALETHEIA_URL, max_retries=2)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post(ALETHEIA_URL).mock(
            side_effect=[
                httpx.Response(429, json={"error": "rate limit"}),
                httpx.Response(503, json={"error": "upstream down"}),
                httpx.Response(200, json={"verdict": "fake", "score": 0.91}),
            ]
        )
        res = await client.analyze_segments([seg])

    assert route.call_count == 3
    assert res.status == "ok"
    assert res.state == AuthenticityState.SYNTHETIC_LIKELY
    assert res.score == pytest.approx(0.91)


@pytest.mark.asyncio
async def test_aletheia_timeout_retries_and_reports_unavailable(tmp_path: Path, monkeypatch) -> None:
    async def _fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.aletheia.asyncio.sleep", _fast_sleep)

    seg = _dummy_segment(tmp_path)
    client = AletheiaClient(api_key="test-key", api_url=ALETHEIA_URL, max_retries=2)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post(ALETHEIA_URL).mock(
            side_effect=httpx.ReadTimeout("timed out")
        )
        res = await client.analyze_segments([seg])

    assert route.call_count == 3
    assert res.status == "unavailable"
    assert "timed out" in (res.error_message or "").lower()


@pytest.mark.asyncio
async def test_aletheia_base_url_normalization_and_bearer_auth(tmp_path: Path) -> None:
    expected = "https://platform.detectif.ai/v1/deepfake/detect"
    assert normalize_aletheia_api_url("https://platform.detectif.ai") == expected
    assert normalize_aletheia_api_url("https://platform.detectif.ai/") == expected
    assert normalize_aletheia_api_url("https://platform.detectif.ai/v1") == expected
    assert normalize_aletheia_api_url("https://platform.detectif.ai/v1/deepfake") == expected
    assert normalize_aletheia_api_url("[https://platform.detectif.ai](https://platform.detectif.ai)") == expected
    assert normalize_aletheia_api_url("") == expected

    settings = Settings(detectif_api_key="my-secret", aletheia_api_url="https://platform.detectif.ai")
    assert settings.aletheia_api_url == expected

    seg = _dummy_segment(tmp_path)
    client = AletheiaClient(api_key="my-secret", api_url=settings.aletheia_api_url)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post(expected).mock(
            return_value=httpx.Response(200, json={"verdict": "real", "score": 0.05})
        )
        res = await client.analyze_segments([seg])

    assert route.call_count == 1
    sent_auth = route.calls[0].request.headers.get("Authorization")
    assert sent_auth == "Bearer my-secret"
    assert res.status == "ok"
    assert res.state == AuthenticityState.GENUINE_LIKELY

