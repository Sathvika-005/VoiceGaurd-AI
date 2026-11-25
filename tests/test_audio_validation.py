"""Unit tests for audio validation, streaming size caps, and segment planning."""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from app.audio_tools import (
    AudioSizeError,
    AudioValidationError,
    is_aletheia_native_format,
    plan_segments,
    prepare_aletheia_inputs_sync,
    prepare_gemini_audio_sync,
    probe_audio_sync,
    save_upload_stream,
)

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _write_silence_wav(path: Path, duration_s: float, sample_rate: int = 16000) -> Path:
    n_frames = int(round(duration_s * sample_rate))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return path


class _FakeUpload:
    def __init__(self, data: bytes, chunk_size: int = 4096) -> None:
        self._data = data
        self._pos = 0
        self._chunk_size = chunk_size

    async def read(self, size: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        step = self._chunk_size if size <= 0 else min(size, self._chunk_size)
        chunk = self._data[self._pos : self._pos + step]
        self._pos += len(chunk)
        return chunk


@pytest.mark.asyncio
async def test_save_upload_stream_rejects_oversized_early(tmp_path: Path) -> None:
    upload = _FakeUpload(b"a" * (1024 * 50), chunk_size=8192)
    with pytest.raises(AudioSizeError) as exc_info:
        await save_upload_stream(upload, tmp_path / "big.wav", max_bytes=16384, max_mb=1)
    assert "1 MB" in exc_info.value.message


@pytest.mark.asyncio
async def test_save_upload_stream_rejects_empty(tmp_path: Path) -> None:
    upload = _FakeUpload(b"")
    with pytest.raises(AudioValidationError):
        await save_upload_stream(upload, tmp_path / "empty.wav", max_bytes=1024, max_mb=1)


@pytest.mark.parametrize(
    ("duration", "enable_seg", "expected_ranges"),
    [
        (4.0, True, [(0.0, 4.0)]),
        (45.5, True, [(0.0, 45.5)]),
        (120.0, True, [(0.0, 120.0)]),
        (120.1, True, [(0.0, 100.0), (100.0, 120.1)]),
        (203.9, True, [(0.0, 100.0), (100.0, 203.9)]),  # 3.9s tail merged into previous!
        (204.0, True, [(0.0, 100.0), (100.0, 200.0), (200.0, 204.0)]),  # 4.0s tail kept!
        (600.0, True, [(0.0, 100.0), (100.0, 200.0), (200.0, 300.0), (300.0, 400.0), (400.0, 500.0), (500.0, 600.0)]),
    ],
)
def test_plan_segments_valid_cases(
    duration: float,
    enable_seg: bool,
    expected_ranges: list[tuple[float, float]],
) -> None:
    segments = plan_segments(duration, enable_segmentation=enable_seg)
    assert [(s.start, s.end) for s in segments] == expected_ranges
    for seg in segments:
        assert seg.duration >= 4.0
        assert seg.duration <= 120.0


@pytest.mark.parametrize(
    ("duration", "enable_seg", "max_segments"),
    [
        (3.9, True, 6),
        (0.5, True, 6),
        (120.1, False, 6),
        (600.1, True, 6),
        (250.0, True, 2),
    ],
)
def test_plan_segments_rejections(
    duration: float,
    enable_seg: bool,
    max_segments: int,
) -> None:
    with pytest.raises(AudioValidationError):
        plan_segments(duration, enable_segmentation=enable_seg, max_segments=max_segments)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available on PATH")
def test_probe_audio_duration_boundaries_and_corrupt_files(tmp_path: Path) -> None:
    short_wav = _write_silence_wav(tmp_path / "short.wav", 3.9)
    meta_short = probe_audio_sync(short_wav)
    assert meta_short.duration_seconds == pytest.approx(3.9, abs=0.05)
    with pytest.raises(AudioValidationError):
        plan_segments(meta_short.duration_seconds)

    ok_wav = _write_silence_wav(tmp_path / "ok.wav", 4.0)
    meta_ok = probe_audio_sync(ok_wav)
    assert meta_ok.duration_seconds == pytest.approx(4.0, abs=0.05)
    assert meta_ok.codec == "pcm_s16le"
    assert meta_ok.sample_rate == 16000
    assert is_aletheia_native_format(ok_wav, meta_ok)
    assert len(plan_segments(meta_ok.duration_seconds)) == 1

    # Corrupt file disguised with .wav extension
    corrupt = tmp_path / "fake.wav"
    corrupt.write_bytes(b"not really a wav file at all")
    with pytest.raises(AudioValidationError):
        probe_audio_sync(corrupt)

    # Empty file
    empty = tmp_path / "zero.wav"
    empty.write_bytes(b"")
    with pytest.raises(AudioValidationError):
        probe_audio_sync(empty)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available on PATH")
def test_prepare_aletheia_untouched_single_and_gemini_compressed(tmp_path: Path) -> None:
    wav_path = _write_silence_wav(tmp_path / "sample.wav", 5.0, sample_rate=44100)
    meta = probe_audio_sync(wav_path)
    segments = plan_segments(meta.duration_seconds)

    aletheia_files = prepare_aletheia_inputs_sync(wav_path, meta, segments, tmp_path)
    assert len(aletheia_files) == 1
    assert aletheia_files[0].path == wav_path  # untouched!

    gem_path, gem_mime = prepare_gemini_audio_sync(wav_path, tmp_path)
    assert gem_path.exists()
    assert gem_mime in ("audio/mp3", "audio/ogg")
    gem_meta = probe_audio_sync(gem_path)
    assert gem_meta.sample_rate in (16000, 48000)  # opus resamples to 48k internally, mp3 stays 16k
    assert gem_meta.channels == 1


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not available on PATH")
def test_probe_headerless_webm_duration_fallback(tmp_path: Path) -> None:
    # Piped WebM output omits container duration metadata, mimicking Chrome MediaRecorder
    webm_path = tmp_path / "mic_stream.webm"
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4.5",
            "-c:a",
            "libopus",
            "-f",
            "webm",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    webm_path.write_bytes(proc.stdout)

    meta = probe_audio_sync(webm_path)
    assert meta.duration_seconds == pytest.approx(4.5, abs=0.2)
    assert len(plan_segments(meta.duration_seconds)) == 1

