"""Meetings input: Notiv's transcription used as a library, plus speaker-labelled transcripts.

Notiv (NOTIV_PATH) is loaded as-is: its `core/lecture` folder is imported under the package name
`notiv_lecture`, so it never collides with Daelys's own `core` package, and nothing is copied or
stubbed. Notiv returns timestamped text without speakers; speakers come from labelled transcripts
("Name: text"), mapped to users per tenant.
"""

import asyncio
import importlib
import re
import subprocess
import sys
import tempfile
import types
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from core.config import get_settings
from core.errors import NotConfigured

FFMPEG_TIMEOUT_SECONDS = 600
MAX_LINES = 3000
_LABELLED = re.compile(
    r"^\s*(?:\[?(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})\]?\s*[-\u2013]?\s*)?(?P<speaker>[^:\n\[\]]{1,60}?)\s*:\s+(?P<text>\S.*)$"
)


@dataclass(frozen=True)
class Line:
    n: int
    speaker: str | None
    text: str
    start: float | None = None


@cache
def notiv_transcriber() -> Callable[..., Any]:
    """Notiv's `transcribe(wav_path, language=None, ...)`, or NotConfigured with the reason."""
    path = get_settings().notiv_path
    if not path:
        raise NotConfigured("NOTIV_PATH is not set (path to the Notiv repository)")
    lecture_dir = Path(path) / "core" / "lecture"
    if not (lecture_dir / "transcribe.py").is_file():
        raise NotConfigured(f"Notiv transcription not found at {lecture_dir}/transcribe.py")
    if "notiv_lecture" not in sys.modules:
        package = types.ModuleType("notiv_lecture")
        package.__path__ = [str(lecture_dir)]  # a package without running Notiv's __init__
        sys.modules["notiv_lecture"] = package
    try:
        module = importlib.import_module("notiv_lecture.transcribe")
    except ImportError as exc:
        raise NotConfigured(f"Notiv's transcription dependencies are missing: {exc}") from exc
    transcribe: Callable[..., Any] = module.transcribe
    return transcribe


def to_wav(source: Path, target: Path) -> None:
    """16 kHz mono WAV, the input Notiv's transcriber expects."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(target),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=FFMPEG_TIMEOUT_SECONDS, capture_output=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise NotConfigured("ffmpeg is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"could not decode audio: {exc.stderr.decode(errors='replace')[:300]}") from exc


def _transcribe_sync(audio: bytes, suffix: str, language: str | None) -> list[Line]:
    transcribe = notiv_transcriber()
    with tempfile.TemporaryDirectory() as tmp:
        src, wav = Path(tmp) / f"in{suffix}", Path(tmp) / "audio.wav"
        src.write_bytes(audio)
        to_wav(src, wav)
        segments = transcribe(str(wav), language=language)
    return [Line(n + 1, None, s.text, s.start) for n, s in enumerate(segments)]


async def transcribe_audio(audio: bytes, suffix: str, language: str | None) -> list[Line]:
    """Runs Notiv's (CPU-heavy, blocking) transcription off the event loop."""
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", suffix):
        raise ValueError("unsupported audio file extension")
    return await asyncio.to_thread(_transcribe_sync, audio, suffix, language)


def _seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    parts = [int(p) for p in ts.split(":")]
    total = 0
    for p in parts:
        total = total * 60 + p
    return float(total)


def parse_transcript(raw: str) -> list[Line]:
    """`[00:12] Arjun: we're blocked on keys` -> Line(speaker="Arjun"). Unlabelled lines keep speaker None."""
    lines: list[Line] = []
    for chunk in raw.splitlines():
        if not chunk.strip():
            continue
        m = _LABELLED.match(chunk)
        if m and not m.group("speaker").strip().startswith(("http", "www")):
            lines.append(
                Line(
                    len(lines) + 1,
                    m.group("speaker").strip(),
                    m.group("text").strip(),
                    _seconds(m.group("ts")),
                )
            )
        else:
            lines.append(Line(len(lines) + 1, None, chunk.strip()))
        if len(lines) > MAX_LINES:
            raise ValueError(f"transcript longer than {MAX_LINES} lines")
    return lines


def render(lines: list[Line]) -> str:
    """Numbered transcript as stored and as the model sees it: `[L12] Arjun: ...`."""
    return "\n".join(f"[L{line.n}] {line.speaker or 'Unknown'}: {line.text}" for line in lines)
