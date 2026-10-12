"""本地预览"纯前端版"（模拟 Netlify：静态托管 + 两个无状态函数）。

    python tools/serve_static.py            # http://127.0.0.1:8080

它故意**不**提供 /api/health，因此前端会进入 static 模式（浏览器内解析 PDF、
数据存 IndexedDB）。/api/dict 与 /api/audio 用请求时用项目里的 Python 模块
顶替 Netlify 函数，方便在没有 Node 工具链的机器上验证整个流程。
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import dictionary  # noqa: E402
from app.audio.service import AudioService  # noqa: E402

WEB_DIR = ROOT / "web"
SERVICE = AudioService()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt, *args):  # 安静一点
        if "/api/" in (self.path or ""):
            return
        super().log_message(fmt, *args)

    def _json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        if parsed.path == "/api/health":
            # 模拟"没有 Python 后端"的 Netlify 环境
            self._json({"error": "静态模式（本地预览）"}, status=404)
            return

        if parsed.path == "/api/dict":
            word = (params.get("word") or "").strip().lower()
            if not word:
                self._json({"error": "缺少 word"}, status=400)
                return
            info = dictionary.lookup(word)
            self._json({
                "word": word,
                "meaning_cn": info.meaning_cn,
                "pos": info.pos,
                "uk_ipa": info.uk_ipa,
                "us_ipa": info.us_ipa,
                "definitions_en": info.definitions_en,
                "meaning_source": info.meaning_source,
                "ipa_source": info.ipa_source,
            }, extra_headers={"Cache-Control": "public, max-age=86400"})
            return

        if parsed.path == "/api/audio":
            word = (params.get("word") or "").strip().lower()
            accent = (params.get("accent") or "").lower()
            if not word or accent not in ("uk", "us"):
                self._json({"error": "word 与 accent=uk|us 必填"}, status=400)
                return
            cached, result = SERVICE.resolve(word, accent, force=params.get("refresh") == "1")
            if cached is None:
                self._json({"error": "没有可用的发音音频", "accent": accent, "word": word,
                            "detail": result.error, "retryable": bool(result.retryable)}, status=404)
                return
            data = Path(cached.path).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", cached.mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Audio-Provider", cached.provider)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return

        super().do_GET()


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("=" * 58)
    print("  纯前端版本地预览（模拟 Netlify 静态部署）")
    print(f"  http://127.0.0.1:{port}")
    print("  /api/health -> 404（因此前端走 static 模式）")
    print("  /api/dict、/api/audio 由本脚本用 Python 模块顶替 Netlify 函数")
    print("=" * 58)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
