# VoiceGuard AI — External Integration Notes

**Verified on:** 2026-11-24  
**Scope:** DetectifAI Aletheia API, Google Gen AI Python SDK (`google-genai`), Meta WhatsApp Cloud API, and Indian Cyber Crime reporting references.

---

## 1. DetectifAI Aletheia (Voice Deepfake Detection)

- **Endpoint:** `POST https://platform.detectif.ai/v1/deepfake/detect`
- **Authentication:** `Authorization: Bearer <DETECTIF_API_KEY>`
- **Optional Header:** `Idempotency-Key: <uuid4>` (recommended so retried requests are not processed twice)
- **Request Body:** `multipart/form-data` with audio file in field `file`
- **Supported Formats:** `WAV`, `MP3`, `M4A`, `FLAC`, `OGG`, `WebM`
- **Maximum Size:** Up to `100 MB` via REST API
- **Audio Duration:** `4–120` seconds per call (`>= 4 s` minimum; `>= 10 s` speech recommended for highest reliability)
- **Response Format (JSON):**
  - `request_id` (string; also returned in `X-Request-Id` response header)
  - `verdict` (string): `"real"` or `"fake"` (or nested under `result.verdict` in some documented variants)
  - `score` (float, `0.0`–`1.0`): probabilistic synthetic risk score where values closer to `1.0` indicate higher likelihood of synthetic/deepfake speech (or nested under `result.score`)
  - `label` (optional string): descriptive label such as `"genuine"` or `"synthetic"`
  - `segments` (optional array): `[{ "start": float, "end": float, "score": float }]`
- **Adapter Policy & Assumptions:**
  - Marked `# ASSUMPTION:` in `app/services/aletheia.py`:
    1. Both top-level (`verdict`, `score`) and nested (`result.verdict`, `result.score`) shapes appeared in DetectifAI documentation summaries; our adapter accepts either documented shape and rejects any other unexpected payload as `status="unrecognised"`.
    2. If `verdict` is `"fake"` / `"synthetic"` -> `SYNTHETIC_LIKELY`; if `"real"` / `"genuine"` -> `GENUINE_LIKELY`; if an explicit `"inconclusive"` / `"uncertain"` verdict is returned -> `INCONCLUSIVE`.
    3. We never synthesize or average scores across segments into a single fake score; we preserve the raw verdict and exact returned score with its documented meaning (`"Synthetic likelihood score (0.0 = genuine, 1.0 = synthetic)"`).
  - **Retry Policy:** Retry only on timeouts, HTTP `429`, and `5xx` (max 2 retries, exponential backoff + jitter). Never retry `4xx` validation/auth errors (`400`, `401`, `403`, `413`, `422`).

---

## 2. Google Gen AI Python SDK (`google-genai`)

- **SDK Package:** `google-genai` (`from google import genai`, `from google.genai import types`)
- **Async Client:** `client = genai.Client(api_key=...)` using `await client.aio.models.generate_content(...)`
- **Recommended Fast Audio-Capable Model (verified 2026-11-24):** `gemini-3.8-flash`
  - Supports native audio input (`Audio Input only`, 1M context window) and structured JSON outputs.
  - Supports `thinking_config=types.ThinkingConfig(thinking_level="minimal")` (or `"low"`) for low-latency reasoning.
  - Note: `gemini-2.5-flash` is deprecated and routes to `gemini-3.5-flash-lite` (also audio-capable for high-throughput/cost-sensitive workloads).
  - `# ASSUMPTION:` If a user configures an older or custom model ID that rejects `thinking_level`, our client gracefully retries once without `thinking_config` before failing.
- **Audio Input Limits & Delivery:**
  - **Inline bytes (`types.Part.from_bytes(data=..., mime_type=...)`):** Supported up to `20 MB` total request payload. Our 16 kHz mono compressed MP3/OGG copy for up to 10 minutes is ~1–3 MB, so inline bytes are preferred for speed and zero cloud storage persistence.
  - **Files API (`await client.aio.files.upload(...)`):** Required if payload exceeds `20 MB`. If a compressed file ever exceeds `18 MB`, upload via Files API and delete immediately in `finally`.
- **Structured JSON Output:**
  - `types.GenerateContentConfig(system_instruction=..., response_mime_type="application/json", response_schema=GeminiAudioAnalysis, temperature=0.0, ...)`
  - Validate parsed JSON with `GeminiAudioAnalysis.model_validate_json(...)` plus server-side post-validation guards (evidence-required downgrade, unintelligible-speech handling, one-shot JSON repair retry).

---

## 3. Meta WhatsApp Cloud API

- **Graph API Version (verified 2026-11-24):** `v25.0` (released 2026-02-18, supported through 2028-07-29; `v22.0`–`v25.0` active).
- **Webhook Verification (`GET /webhook`):**
  - Query params: `hub.mode`, `hub.verify_token`, `hub.challenge`.
  - Verify `hub.mode == "subscribe"` and constant-time match `hmac.compare_digest(hub.verify_token, WHATSAPP_VERIFY_TOKEN)`; respond `200` with plain-text `hub.challenge`, or `403` otherwise.
- **Webhook Signature (`POST /webhook`):**
  - Header: `X-Hub-Signature-256: sha256=<hex_digest>`
  - Compute HMAC-SHA256 over the **raw request body bytes** using `WHATSAPP_APP_SECRET`, compare using `hmac.compare_digest`.
- **Incoming Audio Payload:**
  - `entry[].changes[].value.messages[]` where `type == "audio"`, containing `audio.id`, `audio.mime_type`, and `audio.voice` (boolean).
  - Status updates arrive in `entry[].changes[].value.statuses[]` and are ignored quickly.
- **Media Retrieval:**
  1. `GET https://graph.facebook.com/{version}/{media_id}` with `Authorization: Bearer <WHATSAPP_ACCESS_TOKEN>` -> JSON containing temporary `url` (valid ~5 min download window; media IDs downloadable up to 7 days).
  2. `GET <url>` with `Authorization: Bearer <WHATSAPP_ACCESS_TOKEN>`, streamed with a hard `16 MB` size cap.
- **Supported Audio Formats & Limits:**
  - Outbound/business audio cap: `16 MB` (`audio/aac`, `audio/amr`, `audio/mpeg`, `audio/mp4`, `audio/ogg` Opus). Incoming user audio can technically reach 100 MB on Meta's side, so we enforce our `MAX_UPLOAD_MB=16` cap while streaming download.
  - Text message limit: `4096` characters max per message.
- **UX Endpoints (`POST https://graph.facebook.com/{version}/{phone_number_id}/messages`):**
  - **Mark read + typing indicator:** `{"messaging_product": "whatsapp", "status": "read", "message_id": "<wamid>", "typing_indicator": {"type": "text"}}` (dismissed on reply or after 25 s).
  - **Reply to original message:** include `"context": {"message_id": "<wamid>"}`.
  - **Interactive reply buttons:** `type: "interactive"`, `interactive.type: "button"`, up to 3 buttons, button `title` max 20 chars, callback returned in `message.interactive.button_reply.id`.
- **Token Expiry:** Temporary Meta developer tokens expire after ~24 hours; Graph `401` / error code `190` logs a clear warning to regenerate or switch to a permanent System User token.

---

## 4. India Cyber Crime Reporting Reference (verified 2026-11-24)

- **National Cyber Crime Helpline:** `1930` (24x7 helpline operated by I4C, Ministry of Home Affairs, India)
- **Official Portal:** `https://cybercrime.gov.in`
