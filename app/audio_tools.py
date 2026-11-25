"""Audio validation, probing, segmentation planning, and ffmpeg preprocessing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("voiceguard.audio")

ALETHEIA_ACCEPTED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}
# ffprobe format_name substrings corresponding to Aletheia-supported containers
ALETHEIA_ACCEPTED_FORMATS = {"wav", "mp3", "m4a", "mp4", "flac", "ogg", "webm", "matroska"}


class AudioValidationError(ValueError):
    """Raised when an uploaded or downloaded audio file fails validation."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AudioSizeError(AudioValidationError):
    """Raised when streaming input exceeds configured byte limit."""

    def __init__(self, max_mb: int) -> None:
        super().__init__(
            f"Audio file exceeds the maximum allowed size of {max_mb} MB.",
            status_code=413,
        )


@dataclass(frozen=True)
class AudioMetadata:
    duration_seconds: float
    codec: str
    sample_rate: int | None
    channels: int | None
    format_name: str
    file_size_bytes: int
    sha256: str


@dataclass(frozen=True)
class AudioSegmentSpec:
    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    @property
    def label(self) -> str:
        return f"{format_mmss(self.start)}–{format_mmss(self.end)}"


@dataclass(frozen=True)
class PreparedSegmentFile:
    spec: AudioSegmentSpec
    path: Path
    mime_type: str


class _AsyncReadableUpload(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


def format_mmss(seconds: float) -> str:
    """Format seconds as MM:SS."""
    total = max(0, int(round(seconds)))
    mins, secs = divmod(total, 60)
    return f"{mins:02d}:{secs:02d}"


def ffmpeg_available() -> bool:
    """Check whether both ffmpeg and ffprobe executables are on PATH."""
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


async def save_upload_stream(
    upload: _AsyncReadableUpload,
    dest_path: Path,
    max_bytes: int,
    max_mb: int,
    chunk_size: int = 65536,
) -> tuple[int, str]:
    """Stream an uploaded file to disk while enforcing max_bytes and computing SHA-256."""
    total = 0
    digest = hashlib.sha256()
    with dest_path.open("wb") as handle:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise AudioSizeError(max_mb)
            digest.update(chunk)
            handle.write(chunk)

    if total == 0:
        raise AudioValidationError("Uploaded audio file is empty (0 bytes).")
    return total, digest.hexdigest()


async def save_httpx_stream(
    response,
    dest_path: Path,
    max_bytes: int,
    max_mb: int,
) -> tuple[int, str]:
    """Stream an httpx response body to disk while enforcing max_bytes and computing SHA-256."""
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise AudioSizeError(max_mb)
        except ValueError:
            pass

    total = 0
    digest = hashlib.sha256()
    with dest_path.open("wb") as handle:
        async for chunk in response.aiter_bytes(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise AudioSizeError(max_mb)
            digest.update(chunk)
            handle.write(chunk)

    if total == 0:
        raise AudioValidationError("Downloaded audio file is empty (0 bytes).")
    return total, digest.hexdigest()


def compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _probe_duration_with_ffmpeg(file_path: Path) -> float:
    """Fallback duration probe for containers lacking header duration (e.g. Chrome WebM)."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return 0.0
    cmd = [
        ffmpeg_bin,
        "-v",
        "info",
        "-i",
        str(file_path),
        "-vn",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if proc.returncode != 0:
        return 0.0
    matches = _FFMPEG_TIME_RE.findall(proc.stderr or "")
    if not matches:
        return 0.0
    hours_s, mins_s, secs_s = matches[-1]
    try:
        return int(hours_s) * 3600.0 + int(mins_s) * 60.0 + float(secs_s)
    except ValueError:
        return 0.0


def probe_audio_sync(file_path: Path, sha256_hex: str | None = None) -> AudioMetadata:
    """Inspect a media file with ffprobe and verify it has a valid audio stream."""
    if not file_path.exists():
        raise AudioValidationError("Audio file could not be found on disk.")
    file_size = file_path.stat().st_size
    if file_size == 0:
        raise AudioValidationError("Audio file is empty (0 bytes).")

    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        raise AudioValidationError(
            "ffprobe is not installed on the server. Install ffmpeg to validate audio.",
            status_code=500,
        )

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,sample_rate,channels,duration",
        "-show_entries",
        "format=duration,format_name",
        "-of",
        "json",
        str(file_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    if proc.returncode != 0:
        raise AudioValidationError(
            "Could not read audio file. It appears corrupted or in an unsupported container."
        )

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AudioValidationError("Could not parse audio stream metadata.") from exc

    streams = data.get("streams") or []
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise AudioValidationError(
            "No audio stream found in this file (video-only or non-audio files cannot be analysed)."
        )

    primary = audio_streams[0]
    fmt = data.get("format") or {}

    duration_raw = fmt.get("duration") or primary.get("duration")
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        duration = 0.0

    if duration <= 0.0:
        duration = _probe_duration_with_ffmpeg(file_path)

    if duration <= 0.0:
        raise AudioValidationError(
            "Could not determine audio duration or the audio stream has zero length."
        )

    sample_rate: int | None = None
    if primary.get("sample_rate"):
        try:
            sample_rate = int(primary["sample_rate"])
        except (TypeError, ValueError):
            sample_rate = None

    channels: int | None = None
    if primary.get("channels"):
        try:
            channels = int(primary["channels"])
        except (TypeError, ValueError):
            channels = None

    return AudioMetadata(
        duration_seconds=duration,
        codec=str(primary.get("codec_name") or "unknown"),
        sample_rate=sample_rate,
        channels=channels,
        format_name=str(fmt.get("format_name") or "unknown"),
        file_size_bytes=file_size,
        sha256=sha256_hex or compute_file_sha256(file_path),
    )


async def probe_audio(file_path: Path, sha256_hex: str | None = None) -> AudioMetadata:
    return await asyncio.to_thread(probe_audio_sync, file_path, sha256_hex)


def plan_segments(
    duration_seconds: float,
    *,
    min_seconds: float = 4.0,
    max_segment_seconds: float = 120.0,
    target_window_seconds: float = 100.0,
    max_total_seconds: float = 600.0,
    enable_segmentation: bool = True,
    max_segments: int = 6,
) -> list[AudioSegmentSpec]:
    """Pure, deterministic segment planner respecting Aletheia's 4-120s constraints."""
    if duration_seconds < min_seconds:
        raise AudioValidationError(
            f"Audio is too short ({duration_seconds:.1f} s). "
            f"Minimum required length is {int(min_seconds)} seconds (never padded)."
        )

    if duration_seconds <= max_segment_seconds:
        return [AudioSegmentSpec(index=0, start=0.0, end=round(duration_seconds, 3))]

    if duration_seconds > max_total_seconds:
        max_mins = max_total_seconds / 60.0
        raise AudioValidationError(
            f"Audio is {duration_seconds:.1f} s, exceeding the maximum allowed length of "
            f"{max_mins:g} minutes ({int(max_total_seconds)} s)."
        )

    if not enable_segmentation:
        raise AudioValidationError(
            f"Audio is {duration_seconds:.1f} s, exceeding Aletheia's {int(max_segment_seconds)} s "
            "single-clip limit while segmentation is disabled."
        )

    if target_window_seconds <= min_seconds or target_window_seconds > max_segment_seconds:
        raise ValueError("target_window_seconds must be between min_seconds and max_segment_seconds")

    windows: list[tuple[float, float]] = []
    cursor = 0.0
    while cursor < duration_seconds:
        end = min(duration_seconds, cursor + target_window_seconds)
        windows.append((round(cursor, 3), round(end, 3)))
        cursor += target_window_seconds

    # Merge a tail shorter than min_seconds into the previous window if within max_segment_seconds
    if len(windows) >= 2:
        last_start, last_end = windows[-1]
        if (last_end - last_start) < min_seconds:
            prev_start, _ = windows[-2]
            if (last_end - prev_start) <= max_segment_seconds:
                windows[-2] = (prev_start, last_end)
                windows.pop()
            else:
                # Rebalance last two windows evenly if merging would exceed 120s
                total_tail = last_end - prev_start
                mid = round(prev_start + total_tail / 2.0, 3)
                windows[-2] = (prev_start, mid)
                windows[-1] = (mid, last_end)

    if len(windows) > max_segments:
        raise AudioValidationError(
            f"Recording requires {len(windows)} segments, exceeding the cap of {max_segments} Aletheia calls."
        )

    return [
        AudioSegmentSpec(index=idx, start=start, end=end)
        for idx, (start, end) in enumerate(windows)
    ]


def is_aletheia_native_format(file_path: Path, metadata: AudioMetadata) -> bool:
    """Return True if the source file can be sent untouched to Aletheia."""
    ext = file_path.suffix.lower()
    if ext not in ALETHEIA_ACCEPTED_EXTENSIONS:
        return False
    fmt_tokens = {tok.strip().lower() for tok in metadata.format_name.split(",")}
    # WebM often reports 'matroska,webm' in ffprobe; M4A often reports 'mov,mp4,m4a,3gp,3g2,mj2'
    if ext == ".webm" and "webm" in fmt_tokens:
        return True
    if ext == ".m4a" and ("m4a" in fmt_tokens or "mp4" in fmt_tokens):
        return True
    return bool(fmt_tokens & ALETHEIA_ACCEPTED_FORMATS)


def mime_type_for_extension(ext: str) -> str:
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
    }.get(ext.lower(), "application/octet-stream")


def _run_ffmpeg(cmd: list[str], err_msg: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        logger.warning("ffmpeg failed: %s", (proc.stderr or "")[-400:])
        raise AudioValidationError(err_msg, status_code=500)


def prepare_aletheia_inputs_sync(
    source_path: Path,
    metadata: AudioMetadata,
    segments: list[AudioSegmentSpec],
    work_dir: Path,
) -> list[PreparedSegmentFile]:
    """Prepare audio files for Aletheia with minimal processing.

    - Single segment in a natively accepted format -> untouched original file.
    - Otherwise -> lossless PCM16 WAV preserving original sample rate.
    """
    if len(segments) == 1 and is_aletheia_native_format(source_path, metadata):
        return [
            PreparedSegmentFile(
                spec=segments[0],
                path=source_path,
                mime_type=mime_type_for_extension(source_path.suffix),
            )
        ]

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise AudioValidationError(
            "ffmpeg is required to process or segment this audio format.",
            status_code=500,
        )

    prepared: list[PreparedSegmentFile] = []
    for seg in segments:
        out_wav = work_dir / f"aletheia_seg_{seg.index}.wav"
        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{seg.start:.3f}",
            "-t",
            f"{seg.duration:.3f}",
            "-i",
            str(source_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
        ]
        if metadata.sample_rate:
            cmd.extend(["-ar", str(metadata.sample_rate)])
        cmd.append(str(out_wav))
        _run_ffmpeg(cmd, "Failed to prepare lossless audio segment for voice authenticity check.")
        prepared.append(
            PreparedSegmentFile(spec=seg, path=out_wav, mime_type="audio/wav")
        )
    return prepared


async def prepare_aletheia_inputs(
    source_path: Path,
    metadata: AudioMetadata,
    segments: list[AudioSegmentSpec],
    work_dir: Path,
) -> list[PreparedSegmentFile]:
    return await asyncio.to_thread(
        prepare_aletheia_inputs_sync, source_path, metadata, segments, work_dir
    )


def prepare_gemini_audio_sync(source_path: Path, work_dir: Path) -> tuple[Path, str]:
    """Convert audio into one compact 16 kHz mono MP3 (or OGG fallback) for Gemini."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise AudioValidationError(
            "ffmpeg is required to prepare audio for content analysis.",
            status_code=500,
        )

    out_mp3 = work_dir / "gemini_16k_mono.mp3"
    cmd_mp3 = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(out_mp3),
    ]
    proc = subprocess.run(cmd_mp3, capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode == 0 and out_mp3.exists() and out_mp3.stat().st_size > 0:
        return out_mp3, "audio/mp3"

    # Fallback to OGG Opus if libmp3lame is unavailable in a minimal ffmpeg build
    out_ogg = work_dir / "gemini_16k_mono.ogg"
    cmd_ogg = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libopus",
        "-b:a",
        "24k",
        str(out_ogg),
    ]
    _run_ffmpeg(cmd_ogg, "Failed to transcode audio to 16 kHz mono for content analysis.")
    return out_ogg, "audio/ogg"


async def prepare_gemini_audio(source_path: Path, work_dir: Path) -> tuple[Path, str]:
    return await asyncio.to_thread(prepare_gemini_audio_sync, source_path, work_dir)
