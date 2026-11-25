"""Meta WhatsApp Cloud API webhook verification, media download, and messaging client."""

from __future__ import annotations

import hashlib
import hmac
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app import pipeline
from app.audio_tools import AudioValidationError, save_httpx_stream
from app.config import Settings
from app.i18n import SUPPORTED_REPORT_LANGUAGES, get_label, language_display_name, normalize_language
from app.limits import RuntimeLimits, hash_identifier

logger = logging.getLogger("voiceguard.whatsapp")

_MIME_TO_EXT: dict[str, str] = {
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
}


@dataclass(frozen=True)
class IncomingWhatsAppMessage:
    message_id: str
    from_number: str
    msg_type: str
    text_body: str = ""
    audio_id: str | None = None
    audio_mime_type: str | None = None
    button_reply_id: str | None = None
    button_reply_title: str | None = None


def verify_webhook_challenge(
    *,
    mode: str | None,
    verify_token: str | None,
    challenge: str | None,
    expected_token: str,
) -> tuple[bool, str]:
    """Validate Meta GET /webhook verification handshake."""
    if not expected_token.strip():
        return False, "WHATSAPP_VERIFY_TOKEN is not configured."
    if mode != "subscribe" or not verify_token or challenge is None:
        return False, "Missing or invalid hub verification parameters."
    if not hmac.compare_digest(verify_token, expected_token):
        return False, "Invalid verify token."
    return True, challenge


def verify_hub_signature(
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
) -> bool:
    """Verify Meta X-Hub-Signature-256 HMAC-SHA256 header over raw body bytes."""
    secret = app_secret.strip()
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature_header.strip(), expected)


def extract_webhook_events(payload: dict[str, Any]) -> list[IncomingWhatsAppMessage]:
    """Extract actionable incoming messages from a WhatsApp webhook payload.

    Quickly ignores status/delivery notifications.
    """
    events: list[IncomingWhatsAppMessage] = []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return events

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            messages = value.get("messages")
            if not isinstance(messages, list):
                # Status/delivery webhooks only have `statuses`, ignore quickly
                continue
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_id = str(msg.get("id") or "").strip()
                sender = str(msg.get("from") or "").strip()
                msg_type = str(msg.get("type") or "unknown").strip().lower()
                if not msg_id or not sender:
                    continue

                text_body = ""
                audio_id: str | None = None
                audio_mime: str | None = None
                btn_id: str | None = None
                btn_title: str | None = None

                if msg_type == "text":
                    text_obj = msg.get("text") or {}
                    if isinstance(text_obj, dict):
                        text_body = str(text_obj.get("body") or "").strip()
                elif msg_type == "audio":
                    audio_obj = msg.get("audio") or {}
                    if isinstance(audio_obj, dict):
                        raw_id = audio_obj.get("id")
                        audio_id = str(raw_id).strip() if raw_id else None
                        raw_mime = audio_obj.get("mime_type")
                        audio_mime = str(raw_mime).strip() if raw_mime else None
                elif msg_type == "interactive":
                    inter = msg.get("interactive") or {}
                    if isinstance(inter, dict):
                        btn = inter.get("button_reply") or {}
                        if isinstance(btn, dict):
                            btn_id = str(btn.get("id") or "").strip() or None
                            btn_title = str(btn.get("title") or "").strip() or None

                events.append(
                    IncomingWhatsAppMessage(
                        message_id=msg_id,
                        from_number=sender,
                        msg_type=msg_type,
                        text_body=text_body,
                        audio_id=audio_id,
                        audio_mime_type=audio_mime,
                        button_reply_id=btn_id,
                        button_reply_title=btn_title,
                    )
                )
    return events


def format_whatsapp_report(report: dict[str, Any], language: str = "en") -> str:
    """Format an analysis report cleanly for WhatsApp (~<=1400 chars target)."""
    lang = normalize_language(language)
    aletheia = report.get("aletheia") or {}
    gemini = report.get("gemini") or {}
    fusion = report.get("fusion") or {}

    lines: list[str] = [f"🛡️ {get_label(lang, 'report_title')}"]

    # Aletheia section
    lines.append(f"\n🎙️ {get_label(lang, 'voice_auth_header')}")
    if aletheia.get("status") == "ok":
        verdict = aletheia.get("raw_verdict") or aletheia.get("state")
        score = aletheia.get("score")
        score_text = f"{score:.2f}" if isinstance(score, (int, float)) else "per-segment"
        meaning = aletheia.get("score_meaning") or "0=genuine, 1=synthetic"
        lines.append(f"Result: {verdict}   Score: {score_text} ({meaning})")
        flagged = aletheia.get("flagged_ranges") or []
        if flagged:
            lines.append(f"Flagged segments: {', '.join(flagged)}")
    else:
        err = aletheia.get("error_message") or "Service unavailable"
        lines.append(f"Unavailable: {err}")

    # Gemini section
    analysis = gemini.get("analysis")
    if gemini.get("status") == "ok" and isinstance(analysis, dict):
        risk = analysis.get("risk_level", "UNDETERMINED")
        conf = analysis.get("confidence", "low")
        lines.append(
            f"\n⚠️ {get_label(lang, 'comm_risk_header')}: {risk} (confidence {conf})"
        )
        indicators = analysis.get("indicators") or []
        if indicators:
            lines.append(f"\n🔍 {get_label(lang, 'indicators_header')}:")
            for ind in indicators[:3]:
                cat = str(ind.get("category") or "other").replace("_", " ")
                ev = str(ind.get("evidence") or "").strip()
                if len(ev) > 90:
                    ev = ev[:87] + "..."
                ts = ind.get("timestamp")
                ts_part = f" (~{ts})" if ts else ""
                lines.append(f"• {cat}: {ev}{ts_part}")
    else:
        err = gemini.get("error_message") or "Service unavailable"
        lines.append(f"\n⚠️ {get_label(lang, 'comm_risk_header')}: Unavailable: {err}")

    # Fusion assessment
    headline = fusion.get("headline") or "Assessment complete"
    reasons = fusion.get("reasons") or []
    reasoning_snip = " ".join(reasons[:2]).strip()
    if len(reasoning_snip) > 220:
        reasoning_snip = reasoning_snip[:217] + "..."
    lines.append(f"\n🧭 {get_label(lang, 'assessment_header')}: {headline}")
    if reasoning_snip:
        lines.append(reasoning_snip)

    # Actions
    actions = fusion.get("actions") or []
    if actions:
        lines.append(f"\n✅ {get_label(lang, 'actions_header')}:")
        for act in actions[:3]:
            act_text = act if len(act) <= 130 else act[:127] + "..."
            lines.append(f"• {act_text}")

    lines.append(f"\nℹ️ {get_label(lang, 'disclaimer')}")
    full_text = "\n".join(lines)
    if len(full_text) <= 1400:
        return full_text

    # Trim reasons/actions slightly if slightly over 1400 while preserving core sections
    compact_lines: list[str] = []
    for ln in lines:
        if len("\n".join(compact_lines) + "\n" + ln) <= 1380 or ln.startswith("ℹ️"):
            compact_lines.append(ln)
    return "\n".join(compact_lines)


def split_whatsapp_message(text: str, max_len: int = 4096) -> list[str]:
    """Split a long message on line boundaries so each chunk fits Meta's 4096-char cap."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        addition = len(line) + (1 if current else 0)
        if addition > max_len:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            for idx in range(0, len(line), max_len):
                chunks.append(line[idx : idx + max_len])
            continue
        if current_len + addition > max_len:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += addition
    if current:
        chunks.append("\n".join(current))
    return chunks or [text[:max_len]]


def _extension_from_mime(mime_type: str | None) -> str:
    if not mime_type:
        return ".ogg"
    base = mime_type.split(";")[0].strip().lower()
    return _MIME_TO_EXT.get(base, ".ogg")


def _check_graph_auth_error(resp: httpx.Response) -> None:
    if resp.status_code == 401:
        logger.error("WhatsApp token expired/invalid; regenerate it")
        return
    try:
        data = resp.json()
        err = data.get("error") or {}
        if err.get("code") == 190:
            logger.error("WhatsApp token expired/invalid; regenerate it")
    except Exception:
        pass


class WhatsAppClient:
    """Async Graph API client for WhatsApp Cloud API."""

    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        graph_version: str = "v25.0",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.access_token = access_token.strip()
        self.phone_number_id = phone_number_id.strip()
        self.graph_version = graph_version.strip() or "v25.0"
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.access_token and self.phone_number_id)

    @property
    def messages_url(self) -> str:
        return f"https://graph.facebook.com/{self.graph_version}/{self.phone_number_id}/messages"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def mark_read_and_typing(self, message_id: str) -> None:
        if not self.configured:
            return
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self.transport) as client:
                resp = await client.post(self.messages_url, headers=self._headers(), json=payload)
                _check_graph_auth_error(resp)
        except Exception as exc:
            logger.debug("WhatsApp mark-read/typing non-fatal failure: %s", type(exc).__name__)

    async def send_text(
        self,
        to_number: str,
        body: str,
        *,
        reply_to_message_id: str | None = None,
    ) -> bool:
        if not self.configured:
            logger.warning("Cannot send WhatsApp message: credentials not configured")
            return False
        chunks = split_whatsapp_message(body, max_len=4096)
        async with httpx.AsyncClient(timeout=15.0, transport=self.transport) as client:
            for idx, chunk in enumerate(chunks):
                payload: dict[str, Any] = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to_number,
                    "type": "text",
                    "text": {"preview_url": False, "body": chunk},
                }
                if reply_to_message_id and idx == 0:
                    payload["context"] = {"message_id": reply_to_message_id}
                try:
                    resp = await client.post(
                        self.messages_url,
                        headers=self._headers(),
                        json=payload,
                    )
                    _check_graph_auth_error(resp)
                    if resp.status_code < 200 or resp.status_code >= 300:
                        logger.warning("WhatsApp send_text failed with HTTP %s", resp.status_code)
                        return False
                except httpx.HTTPError as exc:
                    logger.warning("WhatsApp send_text network error: %s", type(exc).__name__)
                    return False
        return True

    async def send_report_buttons(
        self,
        to_number: str,
        *,
        body_text: str = "Need guidance on what to do next?",
    ) -> bool:
        if not self.configured:
            return False
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": "vg_next_steps", "title": "Next steps"},
                        },
                        {
                            "type": "reply",
                            "reply": {"id": "vg_how_to_report", "title": "How to report"},
                        },
                    ]
                },
            },
        }
        try:
            async with httpx.AsyncClient(timeout=15.0, transport=self.transport) as client:
                resp = await client.post(self.messages_url, headers=self._headers(), json=payload)
                _check_graph_auth_error(resp)
                return 200 <= resp.status_code < 300
        except httpx.HTTPError:
            return False

    async def download_media(
        self,
        media_id: str,
        dest_dir: Path,
        *,
        fallback_mime: str | None,
        max_bytes: int,
        max_mb: int,
    ) -> tuple[Path, str]:
        """Fetch WhatsApp media metadata URL and stream binary audio to dest_dir."""
        meta_url = f"https://graph.facebook.com/{self.graph_version}/{media_id}"
        auth_headers = {"Authorization": f"Bearer {self.access_token}"}

        async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
            meta_resp = await client.get(meta_url, headers=auth_headers)
            _check_graph_auth_error(meta_resp)
            if meta_resp.status_code == 404:
                raise AudioValidationError("WhatsApp media ID was not found or has expired.")
            if meta_resp.status_code < 200 or meta_resp.status_code >= 300:
                raise AudioValidationError(
                    f"Could not retrieve WhatsApp media details (HTTP {meta_resp.status_code})."
                )

            try:
                meta_json = meta_resp.json()
            except ValueError as exc:
                raise AudioValidationError("WhatsApp returned invalid media metadata.") from exc

            download_url = meta_json.get("url")
            if not isinstance(download_url, str) or not download_url.startswith("https://"):
                raise AudioValidationError(
                    "WhatsApp media metadata did not contain a valid download URL."
                )

            mime_type = str(meta_json.get("mime_type") or fallback_mime or "audio/ogg")
            ext = _extension_from_mime(mime_type)
            dest_path = dest_dir / f"wa_media{ext}"

            async with client.stream("GET", download_url, headers=auth_headers) as stream_resp:
                _check_graph_auth_error(stream_resp)
                if stream_resp.status_code < 200 or stream_resp.status_code >= 300:
                    raise AudioValidationError(
                        f"Failed to download WhatsApp audio (HTTP {stream_resp.status_code}, URL may have expired)."
                    )
                _, sha256_hex = await save_httpx_stream(
                    stream_resp,
                    dest_path,
                    max_bytes=max_bytes,
                    max_mb=max_mb,
                )

        return dest_path, sha256_hex


async def process_incoming_message(
    msg: IncomingWhatsAppMessage,
    *,
    settings: Settings,
    limits: RuntimeLimits,
    wa_client: WhatsAppClient,
) -> None:
    """Handle one incoming WhatsApp message asynchronously."""
    # Deduplicate BEFORE processing
    is_new = await limits.webhook_dedupe.mark_if_new(msg.message_id, ttl_override=86400)
    if not is_new:
        return

    sender_digits = "".join(ch for ch in msg.from_number if ch.isdigit())
    allowed = settings.allowed_numbers_set
    if allowed and sender_digits not in allowed:
        return

    sender_hash = hash_identifier(msg.from_number, settings.app_secret_key)
    stored_lang, _ = await limits.whatsapp_user_lang.get(sender_hash)
    user_lang = normalize_language(stored_lang, default=settings.default_report_language)

    # Interactive button replies (answered from 30 min in-memory cache, zero model call)
    if msg.msg_type == "interactive" and msg.button_reply_id:
        if msg.button_reply_id == "vg_how_to_report":
            key = "how_to_report_in" if settings.region.upper() == "IN" else "how_to_report_generic"
            await wa_client.send_text(msg.from_number, get_label(user_lang, key))
            return
        if msg.button_reply_id == "vg_next_steps":
            cached_rep, _ = await limits.whatsapp_last_report.get(sender_hash)
            if not cached_rep:
                await wa_client.send_text(msg.from_number, get_label(user_lang, "no_recent_report"))
                return
            fusion = cached_rep.get("fusion") or {}
            actions = fusion.get("actions") or []
            reasons = fusion.get("reasons") or []
            parts = ["✅ Recommended Next Steps:"]
            for act in actions:
                parts.append(f"• {act}")
            if reasons:
                parts.append("\nWhy this rating:")
                for r in reasons[:3]:
                    parts.append(f"• {r}")
            await wa_client.send_text(msg.from_number, "\n".join(parts))
            return

    # Text commands
    if msg.msg_type == "text":
        text_lower = msg.text_body.strip().lower()
        if text_lower in {"hi", "hello", "hey", "help", "start", "/start"}:
            await wa_client.send_text(msg.from_number, get_label(user_lang, "welcome"))
            return
        if text_lower.startswith("lang "):
            code = text_lower.split(None, 1)[1].strip()
            if code in SUPPORTED_REPORT_LANGUAGES:
                await limits.whatsapp_user_lang.set(sender_hash, code)
                reply = get_label(code, "lang_updated").format(
                    language=language_display_name(code)
                )
                await wa_client.send_text(msg.from_number, reply)
            else:
                await wa_client.send_text(msg.from_number, get_label(user_lang, "lang_invalid"))
            return
        await wa_client.send_text(msg.from_number, get_label(user_lang, "unsupported_type"))
        return

    # Unsupported non-audio types
    if msg.msg_type != "audio":
        await wa_client.send_text(msg.from_number, get_label(user_lang, "unsupported_type"))
        return

    # Audio message handling
    if not msg.audio_id:
        await wa_client.send_text(
            msg.from_number,
            "Could not process this audio message because Meta's media ID was missing.",
            reply_to_message_id=msg.message_id,
        )
        return

    allowed_rate, _ = await limits.whatsapp_rate_limiter.allow(sender_hash)
    if not allowed_rate:
        await wa_client.send_text(
            msg.from_number,
            get_label(user_lang, "rate_limited"),
            reply_to_message_id=msg.message_id,
        )
        return

    logger.info("Processing WhatsApp audio message sender=%s", sender_hash)
    await wa_client.mark_read_and_typing(msg.message_id)
    await wa_client.send_text(
        msg.from_number,
        get_label(user_lang, "acknowledgement"),
        reply_to_message_id=msg.message_id,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="vg_wa_") as tmp_dir_str:
            audio_path, sha256_hex = await wa_client.download_media(
                msg.audio_id,
                Path(tmp_dir_str),
                fallback_mime=msg.audio_mime_type,
                max_bytes=settings.max_upload_bytes,
                max_mb=settings.max_upload_mb,
            )
            report = await pipeline.analyze(
                audio_path,
                original_filename=audio_path.name,
                sha256_hex=sha256_hex,
                settings=settings,
                limits=limits,
                language=user_lang,
            )
    except AudioValidationError as exc:
        await wa_client.send_text(
            msg.from_number,
            f"⚠️ Could not analyse audio: {exc.message}",
            reply_to_message_id=msg.message_id,
        )
        return
    except Exception as exc:
        logger.exception("Unexpected error processing WhatsApp audio sender=%s: %s", sender_hash, type(exc).__name__)
        await wa_client.send_text(
            msg.from_number,
            "⚠️ An unexpected error occurred while analysing your audio. Please try again shortly.",
            reply_to_message_id=msg.message_id,
        )
        return

    await limits.whatsapp_last_report.set(sender_hash, report, ttl_override=1800)
    formatted = format_whatsapp_report(report, language=user_lang)
    await wa_client.send_text(
        msg.from_number,
        formatted,
        reply_to_message_id=msg.message_id,
    )
    await wa_client.send_report_buttons(msg.from_number)
