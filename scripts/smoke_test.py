"""Real end-to-end smoke test running VoiceGuard AI pipeline with .env credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure repo root is importable when run as `python scripts/smoke_test.py`
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import pipeline  # noqa: E402
from app.audio_tools import AudioValidationError, compute_file_sha256  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.limits import RuntimeLimits  # noqa: E402
from app.whatsapp import format_whatsapp_report  # noqa: E402


async def _run_smoke(audio_path: Path, language: str, json_out: bool) -> int:
    settings = get_settings()
    missing = settings.missing_analysis_credentials()
    if missing:
        print(
            f"Note: missing credential(s) in .env: {', '.join(missing)}. "
            "Missing engines will report as unavailable."
        )

    limits = RuntimeLimits(
        rate_limit_per_hour=settings.rate_limit_per_hour,
        max_concurrent_analyses=settings.max_concurrent_analyses,
        cache_ttl_seconds=0,
    )

    async def _progress(evt: dict) -> None:
        etype = evt.get("type")
        if etype == "stage":
            print(f"[{evt.get('stage')}] {evt.get('message')}")
        elif etype in {"aletheia", "gemini"}:
            res = evt.get("result") or {}
            print(f"[{etype}] status={res.get('status')} elapsed={res.get('elapsed_ms')} ms")

    sha = compute_file_sha256(audio_path)
    try:
        report = await pipeline.analyze(
            audio_path,
            original_filename=audio_path.name,
            sha256_hex=sha,
            settings=settings,
            limits=limits,
            language=language,
            progress_cb=_progress,
        )
    except AudioValidationError as exc:
        print(f"ERROR ({exc.status_code}): {exc.message}", file=sys.stderr)
        return 1

    print("\n--- WHATSAPP-FORMATTED REPORT ---")
    print(format_whatsapp_report(report, language=language))
    print("\n--- TIMINGS (ms) ---")
    print(json.dumps(report["timings_ms"], indent=2))

    if json_out:
        print("\n--- FULL JSON REPORT ---")
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the real VoiceGuard AI pipeline against a local audio file using .env credentials."
    )
    parser.add_argument("audio_file", type=Path, help="Path to local audio file (>= 4 seconds).")
    parser.add_argument("--lang", default="en", help="Report language (en, hi, kn, ta, te, auto).")
    parser.add_argument("--json", action="store_true", help="Also print the full JSON report.")
    args = parser.parse_args()

    if not args.audio_file.exists():
        print(f"File not found: {args.audio_file}", file=sys.stderr)
        return 1

    return asyncio.run(_run_smoke(args.audio_file.resolve(), args.lang, args.json))


if __name__ == "__main__":
    raise SystemExit(main())
