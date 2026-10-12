"""Audio resolution service: provider chain + verification + disk cache.

Every audio clip is verified before it is reported as available:

* HTTP status must be 200,
* payload size must be within sane bounds,
* the magic bytes must identify a real audio container (MP3/WAV/OGG/...).

The MIME type is derived from the payload rather than trusted from the server,
because at least one provider mislabels WAV data as ``audio/mpeg``.  Cached
files are served from ``data/audio`` so repeat playback is instant and offline.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import config
from .base import AudioResult, probe_duration, sniff_audio
from .providers import FreeDictProvider, YoudaoProvider

EXT_BY_MIME = {
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/webm": ".webm",
}

# Plausibility window for a single-word dictionary recording.
MIN_CLIP_SECONDS = 0.12
MAX_CLIP_SECONDS = 5.0

# Failure text that means "the network/provider misbehaved", i.e. retry later
# instead of remembering the word as having no recording.
TRANSIENT_MARKERS = (
    "timeout", "timed out", "urlerror", "connection", "ssl", "gaierror",
    "remotedisconnected", "reset", "temporarily", "network",
)


def is_transient_error(error: str) -> bool:
    text = (error or "").lower()
    return any(marker in text for marker in TRANSIENT_MARKERS)


@dataclass
class CachedAudio:
    path: Path
    mime: str
    provider: str
    url: str
    note: str
    duration: float | None = None


class AudioService:
    def __init__(self, providers=None) -> None:
        # Order matters: the first provider is the cheapest/most reliable one.
        # Youdao answers in ~0.3 s and covers nearly every textbook word; the
        # open Free Dictionary API is used to fill gaps (and is CC-licensed, so
        # it is preferred where both exist).  Swap the list, or the providers
        # themselves, without touching any other module.
        self.providers = providers or [YoudaoProvider(), FreeDictProvider()]
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # -- cache ---------------------------------------------------------
    @staticmethod
    def key(word: str, accent: str) -> str:
        return hashlib.sha1(f"{accent}|{word.strip().lower()}".encode("utf-8")).hexdigest()

    def _meta_path(self, key: str) -> Path:
        return config.AUDIO_CACHE_DIR / f"{key}.json"

    def _find_cached_file(self, key: str) -> Path | None:
        for ext in EXT_BY_MIME.values():
            candidate = config.AUDIO_CACHE_DIR / f"{key}{ext}"
            if candidate.exists() and candidate.stat().st_size >= config.MIN_AUDIO_BYTES:
                return candidate
        return None

    def cached(self, word: str, accent: str) -> CachedAudio | None:
        key = self.key(word, accent)
        path = self._find_cached_file(key)
        if not path:
            return None
        meta = {}
        meta_path = self._meta_path(key)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
            except ValueError:
                meta = {}
        mime = meta.get("mime") or sniff_audio(path.read_bytes()[:64]) or "audio/mpeg"
        return CachedAudio(path, mime, meta.get("provider", "cache"),
                           meta.get("url", ""), meta.get("note", ""), meta.get("duration"))

    def _store(self, word: str, accent: str, result: AudioResult) -> CachedAudio:
        key = self.key(word, accent)
        ext = EXT_BY_MIME.get(result.mime, ".mp3")
        path = config.AUDIO_CACHE_DIR / f"{key}{ext}"
        config.ensure_dirs()
        path.write_bytes(result.data)
        self._meta_path(key).write_text(
            json.dumps(
                {
                    "word": word,
                    "accent": accent,
                    "mime": result.mime,
                    "provider": result.provider,
                    "url": result.url,
                    "note": result.source_note,
                    "bytes": len(result.data),
                    "duration": probe_duration(result.data, result.mime),
                },
                ensure_ascii=False,
                indent=1,
            ),
            "utf-8",
        )
        return CachedAudio(path, result.mime, result.provider, result.url, result.source_note,
                           probe_duration(result.data, result.mime))

    # -- resolution ----------------------------------------------------
    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def resolve(self, word: str, accent: str, force: bool = False) -> tuple[CachedAudio | None, AudioResult]:
        """Return (cached audio, provider result). Cached audio is None on failure."""
        if accent not in ("uk", "us"):
            return None, AudioResult(False, "none", accent, error="unknown accent")
        if not force:
            hit = self.cached(word, accent)
            if hit:
                return hit, AudioResult(True, hit.provider, accent, mime=hit.mime,
                                        url=hit.url, source_note=hit.note)

        key = self.key(word, accent)
        with self._lock_for(key):
            if not force:
                hit = self.cached(word, accent)
                if hit:
                    return hit, AudioResult(True, hit.provider, accent, mime=hit.mime,
                                            url=hit.url, source_note=hit.note)
            errors: list[str] = []
            for provider in self.providers:
                result = provider.fetch(word, accent)
                if not (result.ok and result.data):
                    errors.append(f"{provider.name}:{result.error}")
                    continue
                duration = probe_duration(result.data, result.mime)
                if duration is not None and not (MIN_CLIP_SECONDS <= duration <= MAX_CLIP_SECONDS):
                    # Almost certainly not a single-word recording.
                    errors.append(f"{provider.name}:implausible_duration={duration:.2f}s")
                    continue
                stored = self._store(word, accent, result)
                return stored, result
            joined = "; ".join(errors) or "no audio"
            return None, AudioResult(False, "none", accent, error=joined,
                                     retryable=is_transient_error(joined))

    def availability(self, word: str, accent: str) -> dict:
        cached_audio, result = self.resolve(word, accent)
        if cached_audio:
            return {
                "available": True,
                "accent": accent,
                "provider": cached_audio.provider,
                "source_note": cached_audio.note,
                "url": f"/api/audio?word={word}&accent={accent}",
            }
        return {"available": False, "accent": accent, "error": result.error}


_SERVICE: AudioService | None = None


def get_service() -> AudioService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = AudioService()
    return _SERVICE


def tts_voices() -> dict:
    """Which system speech-synthesis voices exist?

    This runs in the browser, so the server can only report that the fallback
    is *possible*; the UI checks ``speechSynthesis.getVoices()`` for a real
    ``en-GB``/``en-US`` voice before labelling anything as UK/US.
    """
    return {
        "server_side": False,
        "mode": "browser_speech_synthesis",
        "note": (
            "备用方案：浏览器语音合成。仅当系统中确实存在 en-GB / en-US 语音时才会启用，"
            "并会在界面上明确标注为“合成语音”，不会冒充真实英美口音录音。"
        ),
        "checked_on": "client",
    }
