"""FastAPI entry point, NDJSON streaming API, health endpoint, and WhatsApp webhook."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import pipeline
from app.audio_tools import AudioValidationError, ffmpeg_available, save_upload_stream
from app.config import Settings, get_settings
from app.limits import RuntimeLimits, hash_identifier
from app.whatsapp import (
    WhatsAppClient,
    extract_webhook_events,
    process_incoming_message,
    verify_hub_signature,
    verify_webhook_challenge,
)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

logger = logging.getLogger("voiceguard.main")


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def create_app(
    settings_override: Settings | None = None,
    *,
    aletheia_client: Any = None,
    gemini_client: Any = None,
    wa_client: WhatsAppClient | None = None,
) -> FastAPI:
    """Application factory supporting clean test injection and zero-credential startup."""
    settings = settings_override or get_settings()
    _configure_logging(settings.log_level)

    limits = RuntimeLimits(
        rate_limit_per_hour=settings.rate_limit_per_hour,
        max_concurrent_analyses=settings.max_concurrent_analyses,
        cache_ttl_seconds=settings.cache_ttl_seconds,
    )
    resolved_wa_client = wa_client or WhatsAppClient(
        access_token=settings.whatsapp_access_token,
        phone_number_id=settings.whatsapp_phone_number_id,
        graph_version=settings.meta_graph_api_version,
    )

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        if not settings.whatsapp_signature_enforced:
            logger.warning(
                "WHATSAPP_APP_SECRET is not set; incoming POST /webhook signatures will NOT be verified."
            )
        yield
        tasks: set[asyncio.Task[Any]] = app_instance.state.bg_tasks
        for task in list(tasks):
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(
        title="VoiceGuard AI",
        description="WhatsApp-first and web voice-scam shield powered by DetectifAI Aletheia and Google Gemini.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.limits = limits
    app.state.aletheia_client = aletheia_client
    app.state.gemini_client = gemini_client
    app.state.wa_client = resolved_wa_client
    app.state.bg_tasks: set[asyncio.Task[Any]] = set()

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
        # Allow OpenAPI /docs inline scripts only on /docs & /redoc; strict self-only CSP elsewhere
        if request.url.path in {"/docs", "/redoc", "/openapi.json"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: https:; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net;"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; "
                "media-src 'self' blob:; "
                "connect-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self';"
            )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_path = TEMPLATES_DIR / "index.html"
        if not html_path.exists():
            raise HTTPException(status_code=500, detail="UI template not found.")
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> JSONResponse:
        cfg: Settings = app.state.settings
        return JSONResponse(
            content={
                "status": "ok",
                "integrations": {
                    "aletheia": {"configured": cfg.aletheia_configured},
                    "gemini": {"configured": cfg.gemini_configured},
                    "whatsapp": {
                        "configured": cfg.whatsapp_configured,
                        "signature_verification": cfg.whatsapp_signature_enforced,
                    },
                },
                "ffmpeg_available": ffmpeg_available(),
                "region": cfg.region.upper(),
                "max_upload_mb": cfg.max_upload_mb,
            }
        )

    @app.post("/api/analyze")
    async def analyze_audio_endpoint(
        request: Request,
        file: UploadFile = File(...),
        language: str = Form("en"),
    ) -> Response:
        cfg: Settings = app.state.settings
        rt_limits: RuntimeLimits = app.state.limits

        # Check credentials early when neither engine is configured and no test mocks are injected
        missing = cfg.missing_analysis_credentials()
        if (
            len(missing) == 2
            and app.state.aletheia_client is None
            and app.state.gemini_client is None
        ):
            missing_list = ", ".join(missing)
            return JSONResponse(
                status_code=503,
                content={
                    "error": f"Missing required credentials: {missing_list}. Set them in .env and restart the server.",
                    "missing_credentials": missing,
                },
            )

        client_ip = request.client.host if request.client else "unknown"
        ip_hash = hash_identifier(client_ip, cfg.app_secret_key)
        allowed, retry_after = await rt_limits.web_rate_limiter.allow(ip_hash)
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "error": f"Rate limit exceeded. Please try again in {retry_after} seconds."
                },
            )

        safe_name = Path(file.filename or "recording.wav").name
        suffix = Path(safe_name).suffix.lower() or ".wav"
        if len(suffix) > 10 or not suffix.startswith("."):
            suffix = ".wav"

        tmp_dir = tempfile.TemporaryDirectory(prefix="vg_web_")
        dest_path = Path(tmp_dir.name) / f"upload{suffix}"
        try:
            _, sha256_hex = await save_upload_stream(
                file,
                dest_path,
                max_bytes=cfg.max_upload_bytes,
                max_mb=cfg.max_upload_mb,
            )
        except AudioValidationError as exc:
            tmp_dir.cleanup()
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.message},
            )
        except Exception:
            tmp_dir.cleanup()
            return JSONResponse(
                status_code=400,
                content={"error": "Failed to read uploaded audio file."},
            )

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def _progress(event: dict[str, Any]) -> None:
            await queue.put(event)

        async def _worker() -> None:
            try:
                await pipeline.analyze(
                    dest_path,
                    original_filename=safe_name,
                    sha256_hex=sha256_hex,
                    settings=cfg,
                    limits=rt_limits,
                    language=language,
                    progress_cb=_progress,
                    aletheia_client=app.state.aletheia_client,
                    gemini_client=app.state.gemini_client,
                )
            except AudioValidationError as exc:
                await queue.put({"type": "error", "message": exc.message, "status_code": exc.status_code})
            except Exception as exc:
                logger.exception("Web analysis failed unexpectedly: %s", type(exc).__name__)
                await queue.put(
                    {
                        "type": "error",
                        "message": "Unexpected server error while analysing audio.",
                        "status_code": 500,
                    }
                )
            finally:
                tmp_dir.cleanup()
                await queue.put(None)

        worker_task = asyncio.create_task(_worker())

        async def _ndjson_stream() -> AsyncIterator[bytes]:
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
            finally:
                if not worker_task.done():
                    worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)

        return StreamingResponse(
            _ndjson_stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/webhook")
    async def whatsapp_verify(
        hub_mode: str | None = Query(default=None, alias="hub.mode"),
        hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
        hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    ) -> Response:
        cfg: Settings = app.state.settings
        ok, result = verify_webhook_challenge(
            mode=hub_mode,
            verify_token=hub_verify_token,
            challenge=hub_challenge,
            expected_token=cfg.whatsapp_verify_token,
        )
        if not ok:
            raise HTTPException(status_code=403, detail=result)
        return PlainTextResponse(content=result, status_code=200)

    @app.post("/webhook")
    async def whatsapp_webhook(request: Request) -> JSONResponse:
        cfg: Settings = app.state.settings
        rt_limits: RuntimeLimits = app.state.limits
        raw_body = await request.body()

        sig_header = request.headers.get("X-Hub-Signature-256")
        if cfg.whatsapp_signature_enforced and not verify_hub_signature(
            raw_body, sig_header, cfg.whatsapp_app_secret
        ):
            raise HTTPException(status_code=403, detail="Invalid X-Hub-Signature-256 signature.")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON webhook payload.") from exc

        events = extract_webhook_events(payload)
        for msg in events:
            task = asyncio.create_task(
                process_incoming_message(
                    msg,
                    settings=cfg,
                    limits=rt_limits,
                    wa_client=app.state.wa_client,
                )
            )
            app.state.bg_tasks.add(task)
            task.add_done_callback(app.state.bg_tasks.discard)

        return JSONResponse(content={"status": "ok"}, status_code=200)

    return app


app = create_app()
