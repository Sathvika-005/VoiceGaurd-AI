"""Unit tests for Meta WhatsApp webhook verification, signature checks, parsing, dedupe, and formatting."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx

from app.config import Settings
from app.limits import RuntimeLimits
from app.whatsapp import (
    WhatsAppClient,
    extract_webhook_events,
    format_whatsapp_report,
    process_incoming_message,
    split_whatsapp_message,
    verify_hub_signature,
    verify_webhook_challenge,
)


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_webhook_challenge_verification() -> None:
    ok, res = verify_webhook_challenge(
        mode="subscribe",
        verify_token="my-token",
        challenge="12345",
        expected_token="my-token",
    )
    assert ok is True
    assert res == "12345"

    bad_ok, _ = verify_webhook_challenge(
        mode="subscribe",
        verify_token="wrong",
        challenge="12345",
        expected_token="my-token",
    )
    assert bad_ok is False


def test_hub_signature_verification() -> None:
    body = b'{"object":"whatsapp_business_account"}'
    secret = "super-secret"
    sig = _sign(body, secret)

    assert verify_hub_signature(body, sig, secret) is True
    assert verify_hub_signature(body, "sha256=deadbeef", secret) is False
    assert verify_hub_signature(body, None, secret) is False
    # Unsigned allowed only when secret is empty
    assert verify_hub_signature(body, None, "") is True


def test_extract_webhook_events_all_types() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [{"id": "wamid.status1", "status": "delivered"}]
                        }
                    },
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.voice1",
                                    "from": "919999999999",
                                    "type": "audio",
                                    "audio": {"id": "media_123", "mime_type": "audio/ogg; codecs=opus", "voice": True},
                                },
                                {
                                    "id": "wamid.text1",
                                    "from": "919999999999",
                                    "type": "text",
                                    "text": {"body": "hi"},
                                },
                                {
                                    "id": "wamid.img1",
                                    "from": "919999999999",
                                    "type": "image",
                                    "image": {"id": "img_1"},
                                },
                            ]
                        }
                    },
                ]
            }
        ]
    }
    events = extract_webhook_events(payload)
    assert len(events) == 3
    assert events[0].msg_type == "audio"
    assert events[0].audio_id == "media_123"
    assert events[1].msg_type == "text"
    assert events[1].text_body == "hi"
    assert events[2].msg_type == "image"


def test_format_and_split_whatsapp_report() -> None:
    sample_report = {
        "aletheia": {
            "status": "ok",
            "state": "SYNTHETIC_LIKELY",
            "raw_verdict": "fake",
            "score": 0.91,
            "score_meaning": "Synthetic likelihood score (0.0 = genuine, 1.0 = synthetic)",
            "flagged_ranges": ["00:00–00:35"],
        },
        "gemini": {
            "status": "ok",
            "analysis": {
                "risk_level": "HIGH",
                "confidence": "high",
                "indicators": [
                    {
                        "category": "digital_arrest",
                        "timestamp": "00:14",
                        "evidence": "Stay on Skype for digital arrest verification.",
                    }
                ],
            },
        },
        "fusion": {
            "threat_level": "CRITICAL",
            "headline": "AI-generated voice combined with scam tactics",
            "reasons": ["Escalated from HIGH to CRITICAL because synthetic voice was detected."],
            "actions": [
                "Hang up immediately; digital arrest does not exist.",
                "Report via 1930 or cybercrime.gov.in.",
            ],
        },
    }
    msg = format_whatsapp_report(sample_report, "en")
    assert "VOICEGUARD AI: SECURITY REPORT" in msg
    assert "VOICE AUTHENTICITY (Aletheia)" in msg
    assert "fake" in msg
    assert "0.91" in msg
    assert "COMMUNICATION RISK (Gemini): HIGH" in msg
    assert "digital arrest" in msg
    assert "1930" in msg
    assert len(msg) <= 1400

    long_text = "\n".join(f"Line {i}: " + ("x" * 100) for i in range(60))
    chunks = split_whatsapp_message(long_text, max_len=4096)
    assert len(chunks) >= 2
    assert all(len(c) <= 4096 for c in chunks)


@pytest.mark.asyncio
async def test_whatsapp_dedupe_and_unsupported_reply_mocked() -> None:
    settings = Settings(
        whatsapp_access_token="wa-token",
        whatsapp_phone_number_id="100200300",
        meta_graph_api_version="v25.0",
    )
    limits = RuntimeLimits(
        rate_limit_per_hour=10,
        max_concurrent_analyses=2,
        cache_ttl_seconds=60,
    )
    wa_client = WhatsAppClient(
        access_token="wa-token",
        phone_number_id="100200300",
        graph_version="v25.0",
    )
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.dup_check_1",
                                    "from": "919876543210",
                                    "type": "image",
                                    "image": {"id": "img1"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    msg = extract_webhook_events(payload)[0]

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post(
            "https://graph.facebook.com/v25.0/100200300/messages"
        ).mock(return_value=httpx.Response(200, json={"messages": [{"id": "out_1"}]}))

        await process_incoming_message(msg, settings=settings, limits=limits, wa_client=wa_client)
        # Second delivery with same wamid must be deduplicated!
        await process_incoming_message(msg, settings=settings, limits=limits, wa_client=wa_client)

    assert route.call_count == 1
    sent_body = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert "voice note or audio file" in sent_body["text"]["body"].lower()
