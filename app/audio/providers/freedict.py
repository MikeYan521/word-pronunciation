"""Free Dictionary API (dictionaryapi.dev) provider.

This is the MIT-licensed open-source project behind ``dictionaryapi.dev``; its
audio files are Wiktionary/Wikimedia recordings, reachable as
``https://api.dictionaryapi.dev/media/pronunciations/en/<word>-uk.mp3`` and
``-us.mp3``.  Both accents are requested independently and only the strictly
matching file is accepted, so a US clip is never used as a UK clip.

Reality check (measured): coverage is partial (many words have only one accent
or none) and the service intermittently returns HTTP 5xx.  It is therefore the
*secondary* provider.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from ... import config
from ..base import AudioResult, sniff_audio


class FreeDictProvider:
    name = "freedict"
    note = "Free Dictionary API (dictionaryapi.dev, MIT 开源项目，音频来自 Wiktionary/维基媒体)"

    # This service is noticeably slower and flakier than the primary provider,
    # so it gets a tighter budget.
    timeout = min(config.HTTP_TIMEOUT, 6.0)

    def _entries(self, word: str) -> list[dict] | None:
        url = "https://api.dictionaryapi.dev/api/v2/entries/en/" + urllib.parse.quote(word)
        request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    return None
                return json.loads(response.read())
        except Exception:
            return None

    def _candidate_urls(self, word: str, accent: str) -> list[str]:
        # Try the deterministic CDN path first: it avoids a second round trip to
        # a slow API when the file exists (the common case).
        direct = f"https://api.dictionaryapi.dev/media/pronunciations/en/{word}-{accent}.mp3"
        urls: list[str] = [direct]
        entries = self._entries(word) or []
        marker = f"-{accent}"
        for entry in entries:
            for phonetic in entry.get("phonetics", []) or []:
                audio = (phonetic.get("audio") or "").strip()
                if not audio:
                    continue
                low = audio.lower()
                if marker not in low:
                    continue
                # reject combined files such as `data-ie-uk-us.mp3`
                if "-uk-us" in low or "-us-uk" in low:
                    continue
                if audio not in urls:
                    urls.append(audio)
        return urls

    def fetch(self, word: str, accent: str) -> AudioResult:
        last_error = "no candidate"
        for url in self._candidate_urls(word, accent):
            request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    if response.status != 200:
                        last_error = f"HTTP {response.status}"
                        continue
                    data = response.read(config.MAX_AUDIO_BYTES + 1)
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                continue
            except Exception as exc:
                last_error = type(exc).__name__
                continue
            if len(data) < config.MIN_AUDIO_BYTES:
                last_error = "payload too small"
                continue
            mime = sniff_audio(data)
            if mime is None:
                last_error = "not audio"
                continue
            return AudioResult(True, self.name, accent, data=data, mime=mime, url=url,
                               source_note=self.note)
        return AudioResult(False, self.name, accent, error=last_error)
