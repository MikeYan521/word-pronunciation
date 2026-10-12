"""Youdao dictionary voice endpoint.

``https://dict.youdao.com/dictvoice?audio=<word>&type=1`` (British) and
``type=2`` (American).  These are real dictionary recordings - for a given word
the two files differ (verified in ``tools/verify_audio.py``), which is exactly
what the dual-accent requirement needs.

Limitations (documented rather than hidden):

* undocumented endpoint, no availability guarantee - results are cached;
* unknown words return HTTP 500, so "500" means "no audio", not "error";
* payload may be MP3 or WAV depending on the entry (sniffed, not trusted).
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from ... import config
from ..base import AudioResult, sniff_audio

ACCENT_TO_TYPE = {"uk": 1, "us": 2}


class YoudaoProvider:
    name = "youdao"
    note = "有道词典发音接口（真实录音，按 type=1 英式 / type=2 美式 分别获取）"

    def fetch(self, word: str, accent: str) -> AudioResult:
        if accent not in ACCENT_TO_TYPE:
            return AudioResult(False, self.name, accent, error="unknown accent")
        query = urllib.parse.urlencode({"audio": word, "type": ACCENT_TO_TYPE[accent]})
        url = f"https://dict.youdao.com/dictvoice?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": config.USER_AGENT,
                "Referer": "https://dict.youdao.com/",
                "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=config.HTTP_TIMEOUT) as response:
                if response.status != 200:
                    return AudioResult(False, self.name, accent, url=url,
                                       error=f"HTTP {response.status}")
                data = response.read(config.MAX_AUDIO_BYTES + 1)
        except urllib.error.HTTPError as exc:
            return AudioResult(False, self.name, accent, url=url, error=f"HTTP {exc.code}")
        except Exception as exc:
            return AudioResult(False, self.name, accent, url=url, error=type(exc).__name__)

        if len(data) < config.MIN_AUDIO_BYTES:
            return AudioResult(False, self.name, accent, url=url, error="payload too small")
        if len(data) > config.MAX_AUDIO_BYTES:
            return AudioResult(False, self.name, accent, url=url, error="payload too large")
        mime = sniff_audio(data)
        if mime is None:
            return AudioResult(False, self.name, accent, url=url, error="not audio")
        return AudioResult(True, self.name, accent, data=data, mime=mime, url=url, source_note=self.note)
