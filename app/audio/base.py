"""Provider interface for real (recorded) pronunciation audio.

A provider is a callable ``fetch(word, accent) -> AudioResult`` where *accent*
is ``"uk"`` or ``"us"``.  Providers must return genuine recordings for the
requested accent - never a substitute from the other accent, and never
text-to-speech output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class AudioResult:
    ok: bool
    provider: str
    accent: str
    data: bytes = b""
    mime: str = ""
    url: str = ""
    source_note: str = ""
    error: str = ""
    # True when the failure looks temporary (timeout/DNS/TLS/connection), so the
    # word must NOT be remembered as "this accent has no audio".
    retryable: bool = False


class Provider(Protocol):
    name: str
    note: str

    def fetch(self, word: str, accent: str) -> AudioResult:  # pragma: no cover
        ...


def sniff_audio(data: bytes) -> str | None:
    """Return a MIME type derived from the payload.

    Several dictionary CDNs label WAV data as ``audio/mpeg``; sniffing keeps
    browser playback reliable.
    """
    if len(data) < 12:
        return None
    head = data[:16]
    if head.startswith(b"ID3") or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if head.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"fLaC"):
        return "audio/flac"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if head[4:8] == b"ftyp":
        return "audio/mp4"
    if head.startswith(b"#!AMR"):
        return "audio/amr"
    return None


# MPEG Layer III bitrate tables (kbps), indexed by the 4-bit bitrate code.
# NOTE: Layer II uses a different table; using it here made every clip look
# several times shorter than reality.
_MP3_BITRATES_MPEG1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_MP3_BITRATES_MPEG2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
_MP3_SAMPLE_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _mp3_tables(version_bits: int) -> tuple[list[int], list[int]] | tuple[None, None]:
    """(bitrate table, sample-rate table) for an MPEG audio version code."""
    if version_bits == 3:                     # MPEG-1
        return _MP3_BITRATES_MPEG1, _MP3_SAMPLE_RATES[3]
    if version_bits == 2:                     # MPEG-2
        return _MP3_BITRATES_MPEG2, _MP3_SAMPLE_RATES[2]
    if version_bits == 0:                     # MPEG-2.5 (unofficial, still used)
        return _MP3_BITRATES_MPEG2, _MP3_SAMPLE_RATES[0]
    return None, None                         # 1 = reserved


def probe_duration(data: bytes, mime: str = "") -> float | None:
    """Best-effort duration in seconds.

    Used as a sanity check: a dictionary clip for a single word should last
    roughly 0.3-3 s.  Anything wildly outside that range usually means the
    response was an error page, an advertisement, or a whole phrase.
    """
    try:
        return _probe_duration(data, mime)
    except Exception:
        return None


def _probe_duration(data: bytes, mime: str = "") -> float | None:
    kind = mime or sniff_audio(data) or ""
    if kind == "audio/wav":
        return _wav_duration(data)
    if kind == "audio/mpeg":
        return _mp3_duration(data)
    if kind == "audio/ogg":
        # look for the last Ogg page granule position
        granule = None
        for marker in (b"OggS",):
            pos = data.rfind(marker)
            while pos > 0:
                if pos + 14 <= len(data):
                    value = int.from_bytes(data[pos + 6:pos + 14], "little")
                    if value and value != 0xFFFFFFFFFFFFFFFF:
                        granule = value
                        break
                pos = data.rfind(marker, 0, pos)
        if granule:
            return granule / 48000.0     # Vorbis granule is in samples @48 kHz
    return None


def _wav_duration(data: bytes) -> float | None:
    """Parse RIFF chunks directly.

    ``wave.open`` refuses some of the files served by the dictionary endpoints
    (non-standard fmt chunks / extra tags), so the header is read by hand.
    """
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    position = 12
    rate = channels = bits = None
    format_tag = 1
    data_size = None
    while position + 8 <= len(data):
        chunk_id = data[position:position + 4]
        chunk_size = int.from_bytes(data[position + 4:position + 8], "little")
        body = position + 8
        if chunk_id == b"fmt " and chunk_size >= 16 and body + 16 <= len(data):
            format_tag = int.from_bytes(data[body:body + 2], "little")
            channels = int.from_bytes(data[body + 2:body + 4], "little")
            rate = int.from_bytes(data[body + 4:body + 8], "little")
            bits = int.from_bytes(data[body + 14:body + 16], "little")
        elif chunk_id == b"data":
            data_size = chunk_size if chunk_size else (len(data) - body)
            break
        position = body + chunk_size + (chunk_size % 2)
    if not rate or not channels:
        return None
    if format_tag in (1, 3, 0xFFFE) and bits:      # PCM / float / extensible
        bytes_per_second = rate * channels * bits / 8
        if bytes_per_second and data_size is not None:
            return data_size / bytes_per_second
    if format_tag == 0x0055:                        # MPEG Layer III inside WAV
        mpeg = data[body:] if data_size is None else data[body:body + data_size]
        return _mp3_duration(mpeg)
    return None


def _mp3_duration(data: bytes) -> float | None:
    """Sum real frame durations (works for CBR and VBR alike)."""
    start = 0
    if data[:3] == b"ID3":
        # ID3v2 size is 4 syncsafe bytes after the version/flags
        if len(data) > 10:
            size = 0
            for byte in data[6:10]:
                size = (size << 7) | (byte & 0x7F)
            start = 10 + size

    position = start
    total = 0.0
    frames = 0
    limit = len(data) - 4
    while position < limit and frames < 20000:
        if data[position] != 0xFF or (data[position + 1] & 0xE0) != 0xE0:
            position += 1
            continue
        version_bits = (data[position + 1] >> 3) & 0x03
        layer_bits = (data[position + 1] >> 1) & 0x03
        bitrate_index = (data[position + 2] >> 4) & 0x0F
        rate_index = (data[position + 2] >> 2) & 0x03
        if version_bits == 1 or layer_bits != 1 or bitrate_index == 0 or rate_index == 3:
            position += 1
            continue
        table, rates = _mp3_tables(version_bits)
        if not rates or not table or rate_index >= len(rates) or bitrate_index >= len(table):
            position += 1
            continue
        samples_per_frame = 1152 if version_bits == 3 else 576
        frame_length = int(samples_per_frame / 8 * table[bitrate_index] * 1000 / rates[rate_index])
        frame_length += (data[position + 2] >> 1) & 1
        if frame_length <= 4:
            position += 1
            continue
        total += samples_per_frame / rates[rate_index]
        frames += 1
        position += frame_length

    if frames >= 3:
        return total

    # Fall back to a CBR estimate from the first valid frame.
    position = start
    while position < limit:
        if data[position] == 0xFF and (data[position + 1] & 0xE0) == 0xE0:
            version_bits = (data[position + 1] >> 3) & 0x03
            bitrate_index = (data[position + 2] >> 4) & 0x0F
            table, _rates = _mp3_tables(version_bits)
            if table and 0 < bitrate_index < len(table):
                bitrate = table[bitrate_index] * 1000
                if bitrate:
                    return (len(data) - position) * 8 / bitrate
        position += 1
    return None
