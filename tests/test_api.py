"""API tests for /health, missing-credentials behavior, and NDJSON streaming plumbing (HTTP mocked)."""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import wave
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.aletheia import AletheiaClient
from app.services.gemini_audio import GeminiAudioClient

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_wav_bytes(duration_s: float = 5.0, sample_rate: int = 16000) -> bytes:
    buf = BytesIO()
    n_frames = int(round(duration_s * sample_rate))
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


def test_health_zero_credentials_and_no_secret_leaks() -> None:
    settings = Settings(
        detectif_api_key="",
        gemini_api_key="",
        whatsapp_access_token="",
        whatsapp_phone_number_id="",
        whatsapp_verify_token="",
        whatsapp_app_secret="",
    )
    app = create_app(settings_override=settings)
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["integrations"]["aletheia"]["configured"] is False
        assert body["integrations"]["gemini"]["configured"] is False
        assert body["integrations"]["whatsapp"]["configured"] is False
        assert body["integrations"]["whatsapp"]["signature_verification"] is False
        assert "X-Content-Type-Options" in resp.headers
        assert "Content-Security-Policy" in resp.headers

        # UI loads with zero credentials
        ui = client.get("/")
        assert ui.status_code == 200
        assert "VoiceGuard AI" in ui.text


def test_analyze_missing_credentials_returns_clear_error() -> None:
    settings = Settings(detectif_api_key="", gemini_api_key="")
    app = create_app(settings_override=settings)
    wav_bytes = _make_wav_bytes(5.0)
    with TestClient(app) as client:
        resp = client.post(
            "/api/analyze",
            files={"file": ("test.wav", wav_bytes, "audio/wav")},
            data={"language": "en"},
        )
    assert resp.status_code == 503
    data = resp.json()
    assert "DETECTIF_API_KEY" in data["error"]
    assert "GEMINI_API_KEY" in data["error"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available on PATH")
def test_analyze_ndjson_event_order_with_mocked_engines() -> None:
    """Plumbing test only: both Aletheia (HTTP) and Gemini are mocked."""
    settings = Settings(
        detectif_api_key="test-detectif-key",
        gemini_api_key="test-gemini-key",
        region="IN",
    )
    aletheia_url = settings.aletheia_api_url
    aletheia_client = AletheiaClient(api_key="test-detectif-key", api_url=aletheia_url)

    async def fake_gemini_generate(**_kwargs) -> str:
        return json.dumps(
            {
                "speech_clarity": "clear",
                "language_detected": "English",
                "risk_level": "HIGH",
                "confidence": "high",
                "summary": "Caller demands urgent UPI transfer under digital arrest threat.",
                "indicators": [
                    {
                        "category": "digital_arrest",
                        "severity": "high",
                        "timestamp": "00:02",
                        "evidence": "You are under digital arrest, transfer via UPI now.",
                        "explanation": "Impersonates law enforcement to coerce payment.",
                    }
                ],
                "recommended_actions": ["Hang up immediately."],
                "limitations": "",
            }
        )

    gemini_client = GeminiAudioClient(api_key="test-gemini-key", generate_fn=fake_gemini_generate)
    app = create_app(
        settings_override=settings,
        aletheia_client=aletheia_client,
        gemini_client=gemini_client,
    )
    wav_bytes = _make_wav_bytes(5.0)

    with respx.mock(assert_all_called=True) as mock_router:
        mock_router.post(aletheia_url).mock(
            return_value=httpx.Response(200, json={"verdict": "fake", "score": 0.94})
        )
        with TestClient(app) as client:
            resp = client.post(
                "/api/analyze",
                files={"file": ("scam_test.wav", wav_bytes, "audio/wav")},
                data={"language": "en"},
            )

    assert resp.status_code == 200
    events = [json.loads(line) for line in resp.text.strip().splitlines() if line.strip()]
    event_types = [e["type"] for e in events]

    assert event_types[0] == "stage"
    assert "aletheia" in event_types
    assert "gemini" in event_types
    assert event_types[-1] == "report"

    report = events[-1]["report"]
    assert report["fusion"]["threat_level"] == "CRITICAL"  # HIGH escalated by SYNTHETIC_LIKELY
    assert report["aletheia"]["raw_verdict"] == "fake"
    assert report["aletheia"]["score"] == pytest.approx(0.94)


def test_webhook_routes_signature_enforcement() -> None:
    settings = Settings(
        whatsapp_verify_token="verify-123",
        whatsapp_app_secret="secret-xyz",
    )
    app = create_app(settings_override=settings)
    with TestClient(app) as client:
        ok = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-123",
                "hub.challenge": "999",
            },
        )
        assert ok.status_code == 200
        assert ok.text == "999"

        bad_token = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "999",
            },
        )
        assert bad_token.status_code == 403

        body = json.dumps({"entry": []}).encode("utf-8")
        bad_sig = client.post(
            "/webhook",
            content=body,
            headers={"X-Hub-Signature-256": "sha256=invalid"},
        )
        assert bad_sig.status_code == 403

        good_sig = "sha256=" + hmac.new(b"secret-xyz", body, hashlib.sha256).hexdigest()
        good_post = client.post(
            "/webhook",
            content=body,
            headers={"X-Hub-Signature-256": good_sig},
        )
        assert good_post.status_code == 200
