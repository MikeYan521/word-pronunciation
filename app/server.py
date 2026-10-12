"""HTTP server: static frontend + JSON API.

Standard library only (``http.server``) so the app has no web-framework
dependency and starts with a single command.  Run with::

    python -m app.server            # or: python run.py
"""

from __future__ import annotations

import email
import json
import mimetypes
import re
import threading
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import config, db, dictionary, enrichment, ipa, library, pdf_parser
from .audio import service as audio_service


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def error(handler: BaseHTTPRequestHandler, message: str, status: int = 400, **extra: Any) -> None:
    json_response(handler, {"error": message, **extra}, status)


class Router:
    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern[str], Callable[..., Any]]] = []

    def add(self, method: str, pattern: str, func: Callable[..., Any]) -> None:
        self.routes.append((method.upper(), re.compile(f"^{pattern}$"), func))

    def match(self, method: str, path: str) -> tuple[Callable[..., Any] | None, dict[str, str]]:
        for route_method, pattern, func in self.routes:
            if route_method != method.upper():
                continue
            match = pattern.match(path)
            if match:
                return func, match.groupdict()
        return None, {}


router = Router()


def route(method: str, pattern: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        router.add(method, pattern, func)
        return func
    return decorator


# --------------------------------------------------------------------------
# API: health / meta
# --------------------------------------------------------------------------

@route("GET", r"/api/health")
def api_health(handler, **_) -> None:
    json_response(handler, {
        "status": "ok",
        "ipa": ipa.stats(),
        "ocr": pdf_parser.ocr_status(),
        "audio_providers": [
            {"name": p.name, "note": p.note} for p in audio_service.get_service().providers
        ],
        "tts_fallback": audio_service.tts_voices(),
        "data_dir": str(config.DATA_DIR),
    })


# --------------------------------------------------------------------------
# API: textbooks
# --------------------------------------------------------------------------

@route("GET", r"/api/textbooks")
def api_textbooks(handler, **_) -> None:
    json_response(handler, {"textbooks": library.list_textbooks()})


@route("GET", r"/api/textbooks/(?P<textbook_id>\d+)")
def api_textbook(handler, textbook_id: str) -> None:
    try:
        json_response(handler, library.get_textbook(int(textbook_id)))
    except KeyError:
        error(handler, "单词书不存在", 404)


@route("GET", r"/api/textbooks/(?P<textbook_id>\d+)/preview")
def api_preview(handler, textbook_id: str) -> None:
    try:
        json_response(handler, library.preview(int(textbook_id)))
    except KeyError as exc:
        error(handler, f"没有解析结果：{exc}", 404)


@route("PATCH", r"/api/textbooks/(?P<textbook_id>\d+)")
def api_textbook_patch(handler, textbook_id: str) -> None:
    body = handler.read_json()
    library.update_textbook(int(textbook_id), title=body.get("title"), status=body.get("status"))
    json_response(handler, library.get_textbook(int(textbook_id)))


@route("DELETE", r"/api/textbooks/(?P<textbook_id>\d+)")
def api_textbook_delete(handler, textbook_id: str) -> None:
    library.delete_textbook(int(textbook_id))
    json_response(handler, {"deleted": int(textbook_id)})


@route("GET", r"/api/textbooks/(?P<textbook_id>\d+)/export")
def api_textbook_export(handler, textbook_id: str) -> None:
    """导出词表 JSON（可在手机端导入，免去在手机上重新解析/OCR）。"""
    try:
        payload = library.export_textbook(int(textbook_id))
    except KeyError:
        error(handler, "单词书不存在", 404)
        return
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    filename = f"{payload['book']['title']}.wordbook.json"
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Content-Disposition",
                        "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
    handler.end_headers()
    handler.wfile.write(body)


@route("POST", r"/api/textbooks/import")
def api_textbook_import(handler, **_) -> None:
    payload = handler.read_json()
    try:
        result = library.import_textbook(payload)
    except ValueError as exc:
        error(handler, str(exc))
        return
    json_response(handler, result, 201)


@route("POST", r"/api/textbooks/(?P<textbook_id>\d+)/chapters")
def api_apply_chapters(handler, textbook_id: str) -> None:
    body = handler.read_json()
    chapters = body.get("chapters") or []
    if not chapters:
        error(handler, "至少需要一个章节")
        return
    result = library.apply_chapters(int(textbook_id), chapters)
    json_response(handler, result)


@route("POST", r"/api/textbooks/(?P<textbook_id>\d+)/reparse")
def api_reparse(handler, textbook_id: str) -> None:
    body = handler.read_json()
    library.parse_textbook_async(int(textbook_id), use_ocr=bool(body.get("use_ocr")))
    json_response(handler, {"textbook_id": int(textbook_id), "status": "parsing"}, 202)


@route("POST", r"/api/textbooks/(?P<textbook_id>\d+)/redetect-chapters")
def api_redetect(handler, textbook_id: str) -> None:
    """Re-detect chapters and re-extract vocabulary from the cached text."""
    try:
        result = library.redetect_chapters(int(textbook_id))
    except KeyError as exc:
        error(handler, f"没有可用的解析缓存：{exc}", 404)
        return
    json_response(handler, result)


@route("POST", r"/api/uploads")
def api_upload(handler, **_) -> None:
    fields, files = handler.read_multipart()
    upload = files.get("file") or (list(files.values())[0] if files else None)
    if upload is None and handler.raw_body:
        upload = {
            "filename": handler.headers.get("X-Filename") or "textbook.pdf",
            "data": handler.raw_body,
        }
    if not upload:
        error(handler, "没有收到 PDF 文件")
        return
    data = upload["data"]
    if not data.startswith(b"%PDF"):
        error(handler, "上传的文件不是 PDF（缺少 %PDF 头）")
        return

    title = (fields.get("title") or Path(upload["filename"]).stem).strip()
    tmp_dir = config.DATA_DIR / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"upload_{db.now().replace(':', '').replace(' ', '_')}.pdf"
    tmp_path.write_bytes(data)

    textbook_id = library.create_textbook(title, upload["filename"], tmp_path)
    try:
        # Parsing runs in the background so a 300-page scan cannot time out the
        # browser; the client polls /status and then opens the preview.
        library.parse_textbook_async(textbook_id, use_ocr=bool(fields.get("use_ocr")))
        json_response(handler, {
            "textbook_id": textbook_id,
            "status": "parsing",
            "message": "已开始解析，请稍候…",
        }, 202)
        return
    except Exception as exc:
        traceback.print_exc()
        library.update_textbook(textbook_id, status="failed")
        error(handler, f"解析失败：{exc}", 500)
        return
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@route("GET", r"/api/textbooks/(?P<textbook_id>\d+)/status")
def api_textbook_status(handler, textbook_id: str) -> None:
    tid = int(textbook_id)
    with db.session() as conn:
        row = conn.execute(
            "SELECT id, title, status, page_count FROM textbooks WHERE id = ?", (tid,)
        ).fetchone()
    if row is None:
        error(handler, "单词书不存在", 404)
        return
    payload = dict(row)
    payload["progress"] = library.get_progress(tid) or {}
    payload["ready"] = row["status"] in ("needs_review", "parsed")
    json_response(handler, payload)


# --------------------------------------------------------------------------
# API: chapters & words
# --------------------------------------------------------------------------

@route("GET", r"/api/chapters/(?P<chapter_id>\d+)")
def api_chapter(handler, chapter_id: str) -> None:
    try:
        json_response(handler, library.get_chapter(int(chapter_id)))
    except KeyError:
        error(handler, "章节不存在", 404)


@route("POST", r"/api/chapters/(?P<chapter_id>\d+)/range")
def api_chapter_range(handler, chapter_id: str) -> None:
    """手动指定章节起止页，并重新处理该范围（章节校验页使用）。"""
    body = handler.read_json()
    if body.get("start_page") is None or body.get("end_page") is None:
        error(handler, "缺少 start_page / end_page")
        return
    try:
        result = library.update_chapter_range(
            int(chapter_id), int(body["start_page"]), int(body["end_page"]),
            (body.get("title") or "").strip() or None,
        )
    except KeyError as exc:
        error(handler, str(exc), 404)
        return
    chapter = library.get_chapter(int(chapter_id))
    json_response(handler, {"chapter": chapter, "detail": result})


@route("POST", r"/api/chapters/(?P<chapter_id>\d+)/words")
def api_chapter_add_word(handler, chapter_id: str) -> None:
    body = handler.read_json()
    word = (body.get("word") or "").strip()
    if not word:
        error(handler, "请输入单词")
        return
    chapter = library.get_chapter(int(chapter_id))
    created = library.add_word(chapter["textbook_id"], int(chapter_id), word)
    json_response(handler, created, 201)


@route("DELETE", r"/api/chapters/(?P<chapter_id>\d+)/words/(?P<word_id>\d+)")
def api_chapter_remove_word(handler, chapter_id: str, word_id: str) -> None:
    library.remove_chapter_word(int(chapter_id), int(word_id))
    json_response(handler, {"removed": int(word_id)})


@route("GET", r"/api/textbooks/(?P<textbook_id>\d+)/words")
def api_words(handler, textbook_id: str) -> None:
    params = handler.query
    favorite = params.get("favorite")
    mastered = params.get("mastered")
    words = library.list_words(
        int(textbook_id),
        favorite=True if favorite == "1" else None,
        mastered=None if mastered in (None, "") else mastered == "1",
        search=params.get("q") or None,
    )
    json_response(handler, {"words": words})


@route("GET", r"/api/words/(?P<word_id>\d+)")
def api_word(handler, word_id: str) -> None:
    try:
        json_response(handler, library.get_word(int(word_id)))
    except KeyError:
        error(handler, "单词不存在", 404)


@route("PATCH", r"/api/words/(?P<word_id>\d+)")
def api_word_patch(handler, word_id: str) -> None:
    body = handler.read_json()
    word = library.update_word(
        int(word_id),
        favorite=body.get("favorite"),
        mastered=body.get("mastered"),
        meaning_cn=body.get("meaning_cn"),
    )
    json_response(handler, word)


@route("POST", r"/api/enrich")
def api_enrich(handler, **_) -> None:
    body = handler.read_json()
    ids = body.get("word_ids") or []
    if not ids:
        error(handler, "缺少 word_ids")
        return
    with db.session() as conn:
        words = enrichment.enrich_batch(conn, ids, force=bool(body.get("force")))
    json_response(handler, {"words": words})


# --------------------------------------------------------------------------
# API: audio
# --------------------------------------------------------------------------

@route("GET", r"/api/audio")
def api_audio(handler, **_) -> None:
    params = handler.query
    word_id = params.get("word_id")
    accent = (params.get("accent") or "").lower()
    if accent not in ("uk", "us"):
        error(handler, "accent 必须是 uk 或 us")
        return
    if word_id:
        with db.session() as conn:
            row = conn.execute("SELECT lemma FROM words WHERE id = ?", (int(word_id),)).fetchone()
        if row is None:
            error(handler, "单词不存在", 404)
            return
        word = row["lemma"]
    else:
        word = (params.get("word") or "").strip().lower()
        if not word:
            error(handler, "缺少 word 或 word_id")
            return
    force = params.get("refresh") == "1"
    cached, result = audio_service.get_service().resolve(word, accent, force=force)
    if cached is None:
        if word_id and not result.retryable:
            # definitive: the providers really have no recording for this accent
            library.mark_audio_missing(int(word_id), accent, True)
        error(handler, "没有可用的发音音频", 404, accent=accent, word=word,
              detail=result.error, retryable=bool(result.retryable))
        return
    if word_id:
        library.mark_audio(int(word_id), accent, cached)
    handler.send_audio(cached.path, cached.mime)


@route("POST", r"/api/audio/probe")
def api_audio_probe(handler, **_) -> None:
    body = handler.read_json()
    ids = [int(i) for i in (body.get("word_ids") or [])]
    accents = body.get("accents") or ["uk", "us"]
    accents = [a for a in accents if a in ("uk", "us")] or ["uk", "us"]
    if not ids:
        error(handler, "缺少 word_ids")
        return

    with db.session() as conn:
        rows = conn.execute(
            f"SELECT id, lemma FROM words WHERE id IN ({','.join('?' for _ in ids)})", ids
        ).fetchall()
    words = [dict(r) for r in rows]

    service = audio_service.get_service()
    results: list[dict[str, Any]] = []

    def probe_one(word: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"word_id": word["id"], "lemma": word["lemma"], "accents": {}}
        for accent in accents:
            cached, result = service.resolve(word["lemma"], accent)
            entry["accents"][accent] = {
                "available": cached is not None,
                "provider": cached.provider if cached else None,
                "source_note": cached.note if cached else None,
                "duration": round(cached.duration, 2) if cached and cached.duration else None,
                "error": None if cached else result.error,
                "retryable": bool(result.retryable) if not cached else False,
            }
            if cached:
                library.mark_audio(word["id"], accent, cached)
            elif not result.retryable:
                # definitive: remember it so the UI can disable just this button
                library.mark_audio_missing(word["id"], accent, True)
        return entry

    pool: list[dict[str, Any]] = []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as executor:
        for item in executor.map(probe_one, words):
            pool.append(item)
    with db.session() as conn:
        conn.execute(
            f"UPDATE words SET audio_checked_at = ? WHERE id IN ({','.join('?' for _ in ids)})",
            [db.now(), *ids],
        )
        conn.commit()
    json_response(handler, {"results": pool})


@route("GET", r"/api/audio/sources")
def api_audio_sources(handler, **_) -> None:
    json_response(handler, {
        "providers": [{"name": p.name, "note": p.note} for p in audio_service.get_service().providers],
        "policy": "英式(type=1/-uk)与美式(type=2/-us)分别获取并缓存；缺失时明确标记，不互相替代。",
        "cache_dir": str(config.AUDIO_CACHE_DIR),
    })


# --------------------------------------------------------------------------
# API: statistics
# --------------------------------------------------------------------------

@route("GET", r"/api/stats")
def api_stats(handler, **_) -> None:
    textbook_id = handler.query.get("textbook_id")
    json_response(handler, library.stats(int(textbook_id) if textbook_id else None))


@route("GET", r"/api/dictionary/(?P<word>[A-Za-z'\-]+)")
def api_dictionary(handler, word: str) -> None:
    """Ad-hoc lookup used by tests/tools (no DB write)."""
    info = dictionary.lookup(word)
    json_response(handler, {
        "word": info.word,
        "meaning_cn": info.meaning_cn,
        "pos": info.pos,
        "uk_ipa": info.uk_ipa or ipa.primary(word, "uk"),
        "us_ipa": info.us_ipa or ipa.primary(word, "us"),
        "definitions_en": info.definitions_en,
        "sources": {"meaning": info.meaning_source, "ipa": info.ipa_source},
        "error": info.error,
    })


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "VocabTrainer/0.1"
    protocol_version = "HTTP/1.1"

    # -- helpers ------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
        if "/api/" in (self.path or ""):
            return
        super().log_message(fmt, *args)

    @property
    def query(self) -> dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    @property
    def path_only(self) -> str:
        return urllib.parse.urlparse(self.path).path

    def read_body(self) -> bytes:
        if hasattr(self, "raw_body"):
            return self.raw_body
        length = int(self.headers.get("Content-Length") or 0)
        self.raw_body = self.rfile.read(length) if length else b""
        return self.raw_body

    def read_json(self) -> dict[str, Any]:
        body = self.read_body()
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
            return data if isinstance(data, dict) else {"data": data}
        except ValueError:
            return {}

    def read_multipart(self) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
        content_type = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in content_type:
            return {}, {}
        body = self.read_body()
        message = email.message_from_bytes(
            b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
        )
        fields: dict[str, str] = {}
        files: dict[str, dict[str, Any]] = {}
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename:
                files[name] = {"filename": filename, "data": payload}
            else:
                fields[name] = payload.decode("utf-8", "ignore")
        return fields, files

    def send_audio(self, path: Path, mime: str) -> None:
        data = path.read_bytes()
        total = len(data)
        range_header = self.headers.get("Range")
        start, end = 0, total - 1
        status = HTTPStatus.OK
        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = int(match.group(2))
                end = min(end, total - 1)
                if start > end:
                    start = 0
                status = HTTPStatus.PARTIAL_CONTENT
        chunk = data[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "public, max-age=86400")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        self.wfile.write(chunk)

    def _dispatch(self, method: str) -> None:
        path = self.path_only
        if path.startswith("/api/"):
            func, params = router.match(method, path)
            if func is None:
                error(self, f"未知接口 {method} {path}", 404)
                return
            try:
                func(self, **params)
            except BrokenPipeError:
                pass
            except Exception as exc:  # pragma: no cover - defensive
                traceback.print_exc()
                error(self, f"服务器错误：{exc}", 500)
            return
        if method == "GET":
            self.serve_static(path)
        else:
            error(self, "不支持的请求", 405)

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (config.WEB_DIR / relative).resolve()
        if not str(target).startswith(str(config.WEB_DIR.resolve())):
            error(self, "非法路径", 403)
            return
        if not target.exists() or target.is_dir():
            # SPA fallback
            target = config.WEB_DIR / "index.html"
        if not target.exists():
            error(self, "前端未构建（缺少 web/index.html）", 404)
            return
        mime, _ = mimetypes.guess_type(str(target))
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:      # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:     # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:    # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:   # noqa: N802
        self._dispatch("DELETE")


def run(host: str | None = None, port: int | None = None) -> None:
    config.ensure_dirs()
    db.init_db()
    host = host or config.HOST
    port = port or config.PORT
    server = ThreadingHTTPServer((host, port), Handler)
    stats = ipa.stats()
    print("=" * 62)
    print("  英语单词书 · 词汇提取与英美发音学习器")
    print(f"  地址:      http://{host}:{port}")
    print(f"  数据目录:  {config.DATA_DIR}")
    print(f"  IPA 词库:  英式 {stats['uk_entries']} / 美式 {stats['us_entries']} 条")
    ocr = pdf_parser.ocr_status()
    print(f"  OCR:       {'可用' if ocr['available'] else '不可用 - ' + ocr['reason']}")
    print("  按 Ctrl+C 停止")
    print("=" * 62)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
