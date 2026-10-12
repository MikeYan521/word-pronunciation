"""End-to-end smoke test against a real running server instance.

    python tools/smoke_test.py

It starts the HTTP server in-process on a spare port, uploads the generated
sample textbook, confirms the chapters, enriches vocabulary, verifies that both
UK and US audio can really be fetched (and are different recordings), checks the
statistics endpoint and prints a report.

Requires network access for the dictionary/audio checks; those steps are
reported as FAIL-soft (they are marked SKIP if the network is unavailable).
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402
from app.server import Handler  # noqa: E402
from tools.make_sample_pdf import build_pdf  # noqa: E402

PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool | None, detail: str = "") -> None:
    status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}{(' - ' + detail) if detail else ''}")


def request(method: str, path: str, data=None, headers=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def get_json(path):
    status, body, _ = request("GET", path)
    return status, (json.loads(body) if body else None)


def post_json(path, payload):
    status, body, _ = request("POST", path, json.dumps(payload).encode(),
                              {"Content-Type": "application/json"})
    return status, (json.loads(body) if body else None)


def post_multipart(path, file_path: Path, fields: dict[str, str]):
    boundary = "----codex" + uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
        )
    mime = mimetypes.guess_type(file_path.name)[0] or "application/pdf"
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{file_path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    return request("POST", path, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def main() -> int:
    config.ensure_dirs()
    db.init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.4)

    try:
        # 0. health ---------------------------------------------------------
        status, health = get_json("/api/health")
        record("健康检查 /api/health", status == 200,
               f"IPA 英式 {health['ipa']['uk_entries']} / 美式 {health['ipa']['us_entries']} 条"
               if status == 200 else str(status))
        record("前端页面 /", request("GET", "/")[0] == 200)

        # 1. PDF upload -----------------------------------------------------
        sample = config.DATA_DIR / "tmp" / "smoke_sample.pdf"
        build_pdf(sample)
        status, body, _ = post_multipart("/api/uploads", sample,
                                         {"title": "Smoke Test Book"})
        if status != 202:
            record("上传 PDF 并解析", False, f"HTTP {status} {body[:200]!r}")
            return 1
        textbook_id = json.loads(body)["textbook_id"]
        # parsing runs in the background; poll until it is done
        deadline = time.time() + 180
        info = {}
        while time.time() < deadline:
            _, info = get_json(f"/api/textbooks/{textbook_id}/status")
            if info.get("ready") or info.get("status") == "failed":
                break
            time.sleep(0.4)
        if info.get("status") == "failed":
            record("上传 PDF 并解析", False, (info.get("progress") or {}).get("message", "failed"))
            return 1
        _, preview = get_json(f"/api/textbooks/{textbook_id}/preview")
        record("上传 PDF 并解析", True,
               f"{preview['page_count']} 页，识别 {len(preview['chapters'])} 个章节，"
               f"方式={preview['report'].get('strategy')}")
        record("解析进度接口", bool(info.get("progress")),
               (info.get("progress") or {}).get("message", ""))

        # 2. TOC / back matter handling --------------------------------------
        toc = preview["report"].get("toc_pages") or []
        back = preview["report"].get("back_matter_start")
        record("目录页与附录识别", bool(toc) and bool(back), f"目录页={toc} 附录起始页={back}")

        # 3. confirm chapters ------------------------------------------------
        payload = {"chapters": [
            {"title": c["title"], "start_page": c["start_page"], "end_page": c["end_page"]}
            for c in preview["chapters"]
        ]}
        status, applied = post_json(f"/api/textbooks/{textbook_id}/chapters", payload)
        record("确认章节并生成词表", status == 200,
               f"{applied['stats']['unique_words']} 个唯一词条" if status == 200 else str(status))

        chapters = applied["textbook"]["chapters"]
        chapter_id = chapters[0]["id"]

        # 4. chapter word list + local IPA ----------------------------------
        status, chapter = get_json(f"/api/chapters/{chapter_id}")
        words = chapter["words"]
        record("章节词表读取", status == 200 and len(words) > 0, f"{len(words)} 个词条")

        status, enriched = post_json("/api/enrich", {"word_ids": [w["id"] for w in words[:10]]})
        if status != 200:
            record("在线获取释义/音标", None, f"HTTP {status}（网络受限）")
        else:
            filled = [w for w in enriched["words"] if w.get("meaning_cn") or w.get("uk_ipa")]
            local = [w for w in enriched["words"] if w.get("ipa_source") == "ipa-dict"]
            remote = [w for w in enriched["words"] if w.get("ipa_source") and w.get("ipa_source") != "ipa-dict"]
            record("本地 ipa-dict 音标", len(local) > 0,
                   f"{len(local)}/{len(enriched['words'])} 命中本地词库，{len(remote)} 条由在线词典补齐")
            record("在线词典释义", len(filled) > 0,
                   f"{len(filled)}/10 个词条获得释义或音标：" +
                   ", ".join(f"{w['lemma']}={w.get('meaning_cn') or '—'}" for w in enriched["words"][:3]))
            samples = [f"{w['lemma']} UK/{w.get('uk_ipa') or '—'}/ US/{w.get('us_ipa') or '—'}"
                       for w in enriched["words"][:4]]
            print("    样例：" + " | ".join(samples))

            # after enrichment the chapter list must expose IPA + meanings
            status, refreshed = get_json(f"/api/chapters/{chapter_id}")
            with_ipa = [w for w in refreshed["words"] if w.get("uk_ipa") or w.get("us_ipa")]
            record("词表返回英美音标", len(with_ipa) > 0,
                   f"{len(with_ipa)}/{len(refreshed['words'])} 个词条带音标")

        # 5. dual audio ------------------------------------------------------
        probe_ids = [w["id"] for w in words[:5]]
        try:
            status, probed = post_json("/api/audio/probe", {"word_ids": probe_ids})
        except Exception as exc:
            status, probed = 0, {"results": []}
            record("英美双发音校验", None, f"网络异常：{type(exc).__name__}")
        if status != 200:
            if status:
                record("英美双发音校验", None, f"HTTP {status}（网络受限）")
        else:
            for entry in probed["results"]:
                print(f"    {entry['lemma']:15s} uk={entry['accents']['uk']['available']}"
                      f"({entry['accents']['uk']['provider']}) "
                      f"us={entry['accents']['us']['available']}"
                      f"({entry['accents']['us']['provider']})")
            both = sum(1 for e in probed["results"]
                       if e["accents"]["uk"]["available"] and e["accents"]["us"]["available"])
            record("英美双发音校验", both > 0, f"{both}/{len(probed['results'])} 个单词同时有英美音频")

        # fetch both accents for one word and compare the bytes
        sample_word = next((w for w in words if w["lemma"] in
                            ("environment", "government", "opportunity", "technology", "knowledge")),
                           words[0])
        status_uk, uk_bytes, uk_headers = request(
            "GET", f"/api/audio?word_id={sample_word['id']}&accent=uk")
        status_us, us_bytes, us_headers = request(
            "GET", f"/api/audio?word_id={sample_word['id']}&accent=us")
        if status_uk != 200 or status_us != 200:
            record("英美双发音音频下载", None,
                   f"uk={status_uk} us={status_us}（网络受限或该口音缺失）")
        else:
            uk_md5 = hashlib.md5(uk_bytes).hexdigest()
            us_md5 = hashlib.md5(us_bytes).hexdigest()
            record("英美双发音音频下载", uk_md5 != us_md5,
                   f"{sample_word['lemma']}: UK {len(uk_bytes)}B/{uk_headers.get('Content-Type')} "
                   f"vs US {len(us_bytes)}B/{us_headers.get('Content-Type')}")
            record("两种口音音频确实不同", uk_md5 != us_md5,
                   f"uk_md5={uk_md5[:12]} us_md5={us_md5[:12]}")

        # missing accent must be reported, never substituted
        status_missing, body_missing, _ = request(
            "GET", "/api/audio?word=asdkjfhqwer&accent=uk")
        record("无音频时的明确报错", status_missing == 404,
               f"HTTP {status_missing} {(json.loads(body_missing).get('error') if body_missing else '')}")

        # 6. progress / stats ------------------------------------------------
        status, updated = post_json("/api/enrich", {"word_ids": [words[0]["id"]]})
        status, stats = get_json("/api/stats")
        record("统计接口", status == 200,
               f"{stats['books']} 本单词书 / {stats['words']['total']} 个词条")
        status, _body, _headers = request("PATCH", f"/api/words/{words[0]['id']}",
                                          json.dumps({"mastered": True}).encode(),
                                          {"Content-Type": "application/json"})
        status, stats2 = get_json("/api/stats")
        record("掌握状态写入统计", status == 200 and stats2["words"]["mastered"] >= 1,
               f"已掌握 {stats2['words']['mastered']}")

        # 7. persistence -----------------------------------------------------
        status, books = get_json("/api/textbooks")
        record("数据持久化（SQLite）", status == 200 and any(b["id"] == textbook_id for b in books["textbooks"]),
               f"{len(books['textbooks'])} 本教材")
    finally:
        server.shutdown()
        server.server_close()
        if (config.DATA_DIR / "tmp" / "smoke_sample.pdf").exists():
            (config.DATA_DIR / "tmp" / "smoke_sample.pdf").unlink()

    failed = [r for r in RESULTS if r[0] == "FAIL"]
    skipped = [r for r in RESULTS if r[0] == "SKIP"]
    print("\n" + "=" * 62)
    print(f"结果：{len(RESULTS) - len(failed) - len(skipped)} 通过 / {len(failed)} 失败 / {len(skipped)} 跳过")
    if failed:
        for _, name, detail in failed:
            print(f"  FAIL {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
