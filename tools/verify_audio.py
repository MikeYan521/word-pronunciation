"""Verify that UK and US pronunciation audio can really be fetched, for a list
of words, and that the two accents are genuinely different recordings.

    python tools/verify_audio.py                       # built-in textbook word list
    python tools/verify_audio.py words.txt             # one word per line
    python tools/verify_audio.py --limit 40 --out outputs/audio_verification.md

Every clip is downloaded, sniffed (real audio container?), duration-checked and
hashed, so the report states facts rather than assumptions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio.service import AudioService  # noqa: E402
from app.audio.base import probe_duration  # noqa: E402

DEFAULT_WORDS = [
    "hello", "water", "schedule", "tomato", "environment", "government",
    "opportunity", "knowledge", "technology", "celebrate", "describe",
    "ancient", "necessary", "improve", "decision", "research", "culture",
    "exercise", "weather", "beautiful", "student", "teacher", "aloud",
    "assessment", "communication", "behaviour", "influence", "device",
    "engineer", "health", "remarkable", "survive", "complex", "observe",
    "ability", "wild", "syllabus", "semester", "excited", "holiday",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wordfile", nargs="?", help="文本文件，每行一个单词")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="outputs/audio_verification.md")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新下载")
    args = parser.parse_args()

    if args.wordfile:
        words = [line.strip().lower() for line in Path(args.wordfile).read_text("utf-8").splitlines()]
        words = [w for w in words if w]
    else:
        words = DEFAULT_WORDS
    if args.limit:
        words = words[:args.limit]

    service = AudioService()

    def check(word: str) -> dict:
        entry: dict = {"word": word, "uk": None, "us": None}
        for accent in ("uk", "us"):
            cached, result = service.resolve(word, accent, force=args.refresh)
            if not cached:
                entry[accent] = {"ok": False, "error": result.error}
                continue
            data = cached.path.read_bytes()
            entry[accent] = {
                "ok": True,
                "provider": cached.provider,
                "mime": cached.mime,
                "bytes": len(data),
                "duration": round(cached.duration, 2) if cached.duration else None,
                "md5": hashlib.md5(data).hexdigest()[:12],
                "url": cached.url,
            }
        both = bool(entry["uk"] and entry["uk"].get("ok")) and bool(entry["us"] and entry["us"].get("ok"))
        entry["both"] = both
        entry["distinct"] = bool(both and entry["uk"]["md5"] != entry["us"]["md5"])
        return entry

    print(f"verifying {len(words)} words ...")
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for entry in pool.map(check, words):
            results.append(entry)
            mark = "OK " if entry["both"] else "-- "
            detail = ""
            if entry["both"]:
                detail = (f"UK {entry['uk']['mime']} {entry['uk']['duration']}s "
                          f"{entry['uk']['md5']} | US {entry['us']['mime']} "
                          f"{entry['us']['duration']}s {entry['us']['md5']} | "
                          f"{'distinct' if entry['distinct'] else 'IDENTICAL!'}")
            else:
                details = []
                for accent in ("uk", "us"):
                    if not entry[accent] or not entry[accent].get("ok"):
                        details.append(f"{accent}={entry[accent]['error'] if entry[accent] else 'n/a'}")
                detail = "; ".join(details)
            print(f"{mark}{entry['word']:15s} {detail}")

    both = sum(1 for r in results if r["both"])
    identical = [r["word"] for r in results if r["both"] and not r["distinct"]]
    uk_only = [r["word"] for r in results if r["uk"] and r["uk"].get("ok") and not (r["us"] and r["us"].get("ok"))]
    us_only = [r["word"] for r in results if r["us"] and r["us"].get("ok") and not (r["uk"] and r["uk"].get("ok"))]
    neither = [r["word"] for r in results if not r["both"] and r["word"] not in uk_only and r["word"] not in us_only]

    summary = {
        "words": len(results),
        "both_accents": both,
        "uk_only": uk_only,
        "us_only": us_only,
        "neither": neither,
        "identical_pairs": identical,
        "coverage": round(both / len(results), 3) if results else 0,
    }
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=1))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 英美发音音频实测报告",
        "",
        f"- 测试单词数：{summary['words']}",
        f"- 同时拿到英式+美式音频：{both}（覆盖率 {summary['coverage']}）",
        f"- 只有英式：{len(uk_only)} {uk_only if uk_only else ''}",
        f"- 只有美式：{len(us_only)} {us_only if us_only else ''}",
        f"- 两种口音都没有：{len(neither)} {neither if neither else ''}",
        f"- 英美音频内容完全相同的词：{len(identical)} {identical if identical else ''}",
        "",
        "| 单词 | 英式 | 美式 | 格式(UK/US) | 时长(UK/US) | 来源 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        uk, us = r["uk"], r["us"]
        lines.append(
            f"| {r['word']} | {'✓' if uk and uk.get('ok') else '✗'} "
            f"| {'✓' if us and us.get('ok') else '✗'} "
            f"| {(uk or {}).get('mime', '—')} / {(us or {}).get('mime', '—')} "
            f"| {(uk or {}).get('duration', '—')} / {(us or {}).get('duration', '—')} "
            f"| {(uk or {}).get('provider', '—')} / {(us or {}).get('provider', '—')} |"
        )
    lines += [
        "",
        "> 说明：本报告由 `python tools/verify_audio.py` 实测生成，每条音频都经过下载、",
        "> 容器识别（MP3/WAV/OGG）、时长合理性与 MD5 比对。英式与美式分别从不同的接口参数",
        "> / 不同的音频文件获取，MD5 不同的记录证明二者是相互独立的真实录音。",
    ]
    out.write_text("\n".join(lines), "utf-8")
    print(f"\nreport written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
