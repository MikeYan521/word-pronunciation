import hashlib
import io
import json
import unittest
import urllib.error
from pathlib import Path

from app import config, db
from app.audio.base import AudioResult, probe_duration, sniff_audio
from app.audio.providers.freedict import FreeDictProvider
from app.audio.providers.youdao import YoudaoProvider
from app.audio.service import AudioService, is_transient_error
from tests.base import TempDataTestCase

def make_mp3(frames: int = 40, bitrate_index: int = 9, rate_index: int = 0) -> bytes:
    """A structurally valid MPEG-1 Layer III stream (~26 ms per frame)."""
    import struct
    frame_length = int(1152 / 8 * (128 * 1000) / 44100)   # 128 kbps @ 44.1 kHz
    header = bytes([0xFF, 0xFB, (bitrate_index << 4) | (rate_index << 2), 0x00])
    frame = header + b"\x00" * (frame_length - len(header))
    id3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
    return id3 + frame * frames


MP3 = make_mp3(40)
WAV = b"RIFF" + (1000).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 1200


def make_wav(seconds: float, rate: int = 8000) -> bytes:
    import struct
    frames = int(seconds * rate)
    payload = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(payload))
    return header + payload


class FakeProvider:
    def __init__(self, name, results):
        self.name = name
        self.note = f"fake {name}"
        self.results = results
        self.calls = []

    def fetch(self, word, accent):
        self.calls.append((word, accent))
        result = self.results.get(accent)
        if result is None:
            return AudioResult(False, self.name, accent, error="no audio")
        if isinstance(result, str):
            return AudioResult(False, self.name, accent, error=result)
        return AudioResult(True, self.name, accent, data=result, mime=sniff_audio(result) or "audio/mpeg",
                           url=f"https://example.invalid/{word}-{accent}.mp3", source_note=self.note)


class TestSniff(unittest.TestCase):
    def test_detects_mp3_wav_ogg(self):
        self.assertEqual(sniff_audio(MP3), "audio/mpeg")
        self.assertEqual(sniff_audio(WAV), "audio/wav")
        self.assertEqual(sniff_audio(b"OggS" + b"\x00" * 40), "audio/ogg")
        self.assertIsNone(sniff_audio(b"<html>nope</html>"))
        self.assertIsNone(sniff_audio(b"tiny"))

    def test_duration_measurement(self):
        wav = make_wav(1.5)
        self.assertEqual(sniff_audio(wav), "audio/wav")
        self.assertAlmostEqual(probe_duration(wav, "audio/wav"), 1.5, places=2)
        self.assertIsNone(probe_duration(b"OggS" + b"\x00" * 40))


class TestAudioStatus(unittest.TestCase):
    """Tri-state audio status: never claim "无音频" for an unprobed word."""

    def row(self, **kwargs):
        base = {
            "id": 1, "lemma": "water", "in_dict": 1, "favorite": 0, "mastered": 0,
            "uk_audio": None, "uk_audio_source": None, "uk_audio_missing": 0,
            "us_audio": None, "us_audio_source": None, "us_audio_missing": 0,
        }
        base.update(kwargs)
        return base

    def test_unprobed_word_is_unknown_not_missing(self):
        word = db.word_to_api(self.row())
        self.assertEqual(word["uk_audio_status"], "unknown")
        self.assertEqual(word["us_audio_status"], "unknown")
        self.assertFalse(word["has_uk_audio"])

    def test_cached_audio_is_available(self):
        word = db.word_to_api(self.row(uk_audio="a.mp3", uk_audio_source="youdao"))
        self.assertEqual(word["uk_audio_status"], "available")
        self.assertTrue(word["has_uk_audio"])

    def test_confirmed_missing_is_reported_as_missing(self):
        word = db.word_to_api(self.row(uk_audio_missing=1))
        self.assertEqual(word["uk_audio_status"], "missing")
        self.assertFalse(word["has_uk_audio"])

    def test_transient_errors_are_retryable(self):
        for error in ["youdao:TimeoutError", "freedict:URLError",
                      "gaierror", "The read operation timed out"]:
            self.assertTrue(is_transient_error(error), error)
        for error in ["youdao:HTTP 500", "freedict:HTTP 404", "not audio",
                      "implausible_duration=12.00s"]:
            self.assertFalse(is_transient_error(error), error)


class TestRetryableFlag(TempDataTestCase):
    def test_timeout_marks_result_retryable(self):
        provider = FakeProvider("flaky", {"uk": "TimeoutError"})
        service = AudioService(providers=[provider])
        cached, result = service.resolve("water", "uk")
        self.assertIsNone(cached)
        self.assertTrue(result.retryable)

    def test_definitive_failure_is_not_retryable(self):
        provider = FakeProvider("empty", {"uk": "HTTP 500"})
        service = AudioService(providers=[provider])
        cached, result = service.resolve("water", "uk")
        self.assertIsNone(cached)
        self.assertFalse(result.retryable)


class TestAudioService(TempDataTestCase):
    def test_dual_sources_are_independent(self):
        primary = FakeProvider("primary", {"uk": None, "us": MP3})
        secondary = FakeProvider("secondary", {"uk": WAV})
        service = AudioService(providers=[primary, secondary])
        uk, _ = service.resolve("water", "uk")
        us, _ = service.resolve("water", "us")
        self.assertIsNotNone(uk)
        self.assertIsNotNone(us)
        self.assertEqual(uk.provider, "secondary")     # only the provider that has UK
        self.assertEqual(us.provider, "primary")
        self.assertNotEqual(uk.path, us.path)
        self.assertEqual(Path(uk.path).suffix, ".wav")
        self.assertEqual(Path(us.path).suffix, ".mp3")

    def test_missing_accent_is_not_substituted(self):
        provider = FakeProvider("only-us", {"us": MP3})
        service = AudioService(providers=[provider])
        uk, result = service.resolve("colour", "uk")
        self.assertIsNone(uk)
        self.assertFalse(result.ok)
        self.assertIn("no audio", result.error)

    def test_cache_prevents_second_fetch(self):
        provider = FakeProvider("p", {"uk": MP3, "us": MP3})
        service = AudioService(providers=[provider])
        service.resolve("hello", "uk")
        service.resolve("hello", "uk")
        self.assertEqual(len([c for c in provider.calls if c[1] == "uk"]), 1)
        cached = service.cached("hello", "uk")
        self.assertIsNotNone(cached)
        meta = json.loads((config.AUDIO_CACHE_DIR / f"{service.key('hello', 'uk')}.json").read_text("utf-8"))
        self.assertEqual(meta["provider"], "p")
        self.assertEqual(meta["accent"], "uk")

    def test_force_refresh_refetches(self):
        provider = FakeProvider("p", {"uk": MP3})
        service = AudioService(providers=[provider])
        service.resolve("hello", "uk")
        service.resolve("hello", "uk", force=True)
        self.assertEqual(len(provider.calls), 2)

    def test_implausible_clip_is_rejected_and_falls_through(self):
        """A 30-second 'word' recording is not a dictionary clip."""
        bad = FakeProvider("bad", {"uk": make_wav(30.0)})
        good = FakeProvider("good", {"uk": make_wav(0.8)})
        service = AudioService(providers=[bad, good])
        cached, _ = service.resolve("water", "uk")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.provider, "good")
        self.assertAlmostEqual(cached.duration, 0.8, places=2)

    def test_all_providers_implausible_reports_error(self):
        bad = FakeProvider("bad", {"uk": make_wav(12.0)})
        service = AudioService(providers=[bad])
        cached, result = service.resolve("water", "uk")
        self.assertIsNone(cached)
        self.assertIn("implausible_duration", result.error)

    def test_small_payload_is_rejected_by_provider(self):
        class StubResponse(io.BytesIO):
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        import urllib.request
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *a, **k: StubResponse(b"tiny")
            result = YoudaoProvider().fetch("hello", "uk")
        finally:
            urllib.request.urlopen = original
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "payload too small")

    def test_youdao_rejects_html_and_accepts_mp3(self):
        class StubResponse(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        import urllib.request
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = lambda *a, **k: StubResponse(b"<html>" + b"x" * 5000)
            bad = YoudaoProvider().fetch("hello", "uk")
            urllib.request.urlopen = lambda *a, **k: StubResponse(MP3)
            good = YoudaoProvider().fetch("hello", "uk")
        finally:
            urllib.request.urlopen = original
        self.assertFalse(bad.ok)
        self.assertEqual(bad.error, "not audio")
        self.assertTrue(good.ok)
        self.assertEqual(good.mime, "audio/mpeg")

    def test_youdao_http_500_means_no_audio(self):
        def raise_500(*args, **kwargs):
            raise urllib.error.HTTPError("u", 500, "err", {}, None)

        import urllib.request
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = raise_500
            result = YoudaoProvider().fetch("asdkjfh", "uk")
        finally:
            urllib.request.urlopen = original
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "HTTP 500")

    def test_freedict_rejects_combined_accent_files(self):
        provider = FreeDictProvider()
        provider._entries = lambda word: [{
            "phonetics": [
                {"audio": "https://api.dictionaryapi.dev/media/pronunciations/en/data-ie-uk-us.mp3"},
                {"audio": "https://api.dictionaryapi.dev/media/pronunciations/en/data-us.mp3"},
                {"audio": "https://api.dictionaryapi.dev/media/pronunciations/en/data-uk.mp3"},
            ]
        }]
        us = provider._candidate_urls("data", "us")
        uk = provider._candidate_urls("data", "uk")
        self.assertEqual(us[0].endswith("data-us.mp3"), True)
        self.assertFalse(any("uk-us" in url for url in us))
        self.assertFalse(any("uk-us" in url for url in uk))


@unittest.skipUnless(
    __import__("os").environ.get("VOCAB_LIVE_TESTS") == "1",
    "set VOCAB_LIVE_TESTS=1 to run live network audio checks",
)
class TestLiveAudio(unittest.TestCase):
    def test_live_dual_accent(self):
        service = AudioService()
        for word in ["water", "schedule", "opportunity"]:
            uk, _ = service.resolve(word, "uk")
            us, _ = service.resolve(word, "us")
            self.assertIsNotNone(uk, f"no UK audio for {word}")
            self.assertIsNotNone(us, f"no US audio for {word}")
            uk_bytes = Path(uk.path).read_bytes()
            us_bytes = Path(us.path).read_bytes()
            self.assertNotEqual(hashlib.md5(uk_bytes).hexdigest(),
                                hashlib.md5(us_bytes).hexdigest(),
                                f"UK and US audio identical for {word}")


if __name__ == "__main__":
    unittest.main()
