"""Optional OCR for scanned PDFs.

Two engines are supported, both detected at runtime:

``windows``
    The OCR engine built into Windows 10/11 (``Windows.Media.Ocr``) through the
    ``winsdk`` package.  Needs **no external program**, only an OCR language
    pack (English is present on almost every Windows install).
``tesseract``
    The classic Tesseract engine via ``pytesseract`` + the Tesseract binary.

Nothing here is required for the app to run: PDFs that already contain a text
layer never touch this module, and when no engine is available the UI tells the
user what to install instead of silently producing an empty word list.

If a copy of ``winsdk`` exists under ``vendor/pylibs`` it is used automatically,
so the app can do OCR without touching the system Python installation.
"""

from __future__ import annotations

import asyncio
import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config


def _ensure_vendor_path() -> None:
    vendor = config.PROJECT_ROOT / "vendor" / "pylibs"
    if vendor.exists() and str(vendor) not in sys.path:
        sys.path.append(str(vendor))


_ensure_vendor_path()


@dataclass
class OcrEngineInfo:
    name: str
    available: bool
    languages: list[str]
    reason: str = ""
    note: str = ""


def _windows_languages() -> list[str]:
    try:
        from winsdk.windows.media.ocr import OcrEngine
    except Exception:
        return []
    try:
        return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


def _tesseract_status() -> tuple[bool, str]:
    try:
        import pytesseract  # type: ignore
    except Exception:
        return False, "未安装 pytesseract"
    try:
        version = pytesseract.get_tesseract_version()
        return True, str(version)
    except Exception as exc:
        return False, f"未找到 Tesseract 可执行文件（{exc.__class__.__name__}）"


def engines() -> list[OcrEngineInfo]:
    """All known engines with availability, best first."""
    out: list[OcrEngineInfo] = []

    ok, detail = _tesseract_status()
    out.append(OcrEngineInfo(
        "tesseract", ok, ["eng"], "" if ok else detail,
        "pytesseract + Tesseract-OCR（识别英文通常最准）",
    ))

    languages = _windows_languages()
    english = [lang for lang in languages if lang.lower().startswith("en")]
    out.append(OcrEngineInfo(
        "windows", bool(languages), languages,
        "" if languages else "未找到 winsdk（pip install winsdk）或系统未安装 OCR 语言包",
        "Windows 内置 OCR，无需额外程序" + (f"，可用语言：{', '.join(languages)}" if languages else ""),
    ))
    available = [engine for engine in out if engine.available]
    return available + [engine for engine in out if not engine.available]


def best_engine(prefer: str | None = None) -> OcrEngineInfo | None:
    found = engines()
    if prefer:
        for engine in found:
            if engine.name == prefer and engine.available:
                return engine
    for engine in found:
        if engine.available:
            return engine
    return None


def status() -> dict[str, Any]:
    found = engines()
    best = best_engine()
    return {
        "available": bool(best),
        "engine": best.name if best else None,
        "languages": best.languages if best else [],
        "reason": "" if best else (
            "未找到可用的 OCR 引擎。可选方案："
            "(1) 安装 Windows OCR 支持：pip install winsdk；"
            "(2) 安装 Tesseract：winget install UB-Mannheim.TesseractOCR 并 pip install pytesseract pillow。"
        ),
        "engines": [engine.__dict__ for engine in found],
    }


# --------------------------------------------------------------------------
# Engine implementations
# --------------------------------------------------------------------------

def _pick_language(engine: OcrEngineInfo, language: str | None) -> str | None:
    if engine.name == "tesseract":
        return "eng"
    wanted = (language or "").lower()
    if wanted:
        for tag in engine.languages:
            if tag.lower() == wanted or tag.lower().startswith(wanted[:2]):
                return tag
    for tag in engine.languages:
        if tag.lower().startswith("en"):
            return tag
    return engine.languages[0] if engine.languages else None


async def _windows_recognize(png_bytes: bytes, language: str | None) -> str:
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png_bytes)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)

    ocr_engine = None
    if language:
        try:
            ocr_engine = OcrEngine.try_create_from_language(Language(language))
        except Exception:
            ocr_engine = None
    if ocr_engine is None:
        ocr_engine = OcrEngine.try_create_from_user_profile_languages()
    if ocr_engine is None:
        raise RuntimeError("Windows OCR 没有可用的语言包")

    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await ocr_engine.recognize_async(bitmap)
    # NOTE: ``result.text`` joins every paragraph into one line; the per-line
    # structure lives in ``result.lines`` and that is what the chapter detector
    # needs, so rebuild the text from the lines.
    lines = [line.text for line in (result.lines or []) if line.text and line.text.strip()]
    if lines:
        return "\n".join(lines)
    return result.text or ""


async def _windows_recognize_structured(png_bytes: bytes, language: str | None) -> dict[str, Any]:
    """Like :func:`_windows_recognize`, but keeps word bounding boxes.

    The boxes are what a word-level read-aloud highlight would need, so the
    structure is available even though the current UI does not use it yet.
    """
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png_bytes)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    engine = None
    if language:
        try:
            engine = OcrEngine.try_create_from_language(Language(language))
        except Exception:
            engine = None
    engine = engine or OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows OCR 没有可用的语言包")
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines or []:
        words = []
        for word in line.words or []:
            rect = word.bounding_rect
            words.append({"text": word.text, "x": rect.x, "y": rect.y,
                          "width": rect.width, "height": rect.height})
        lines.append({"text": line.text, "words": words})
    return {"text": "\n".join(line["text"] for line in lines), "lines": lines}


def _tesseract_recognize(png_bytes: bytes, language: str | None) -> str:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore

    image = Image.open(io.BytesIO(png_bytes))
    return pytesseract.image_to_string(image, lang=language or "eng")


def recognize(png_bytes: bytes, language: str | None = None, engine_name: str | None = None) -> str:
    """OCR a PNG image; returns text (empty string when nothing was found)."""
    engine = best_engine(engine_name)
    if engine is None:
        raise RuntimeError(status()["reason"])
    if engine.name == "tesseract":
        return _tesseract_recognize(png_bytes, _pick_language(engine, language))
    return asyncio.run(_windows_recognize(png_bytes, _pick_language(engine, language)))


def recognize_structured(png_bytes: bytes, language: str | None = None) -> dict[str, Any]:
    """OCR with word boxes (Windows engine); plain text for other engines."""
    engine = best_engine()
    if engine is not None and engine.name == "windows":
        return asyncio.run(_windows_recognize_structured(png_bytes, _pick_language(engine, language)))
    text = recognize(png_bytes, language)
    return {"text": text, "lines": [{"text": line, "words": []} for line in text.splitlines()]}


def render_page_png(page: Any, dpi: int = 200) -> bytes:
    """Render a pdfplumber page (or any object with ``to_image``) to PNG bytes."""
    image = page.to_image(resolution=dpi).original
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def ocr_status_for_api() -> dict[str, Any]:
    """Status payload shown to the user (kept in pdf_parser for compatibility)."""
    info = status()
    if not info["available"]:
        info["reason"] = (
            "未检测到 OCR 引擎。扫描版 PDF 需要 OCR，可选："
            "① Windows 内置 OCR：pip install winsdk（推荐，无需额外程序）；"
            "② Tesseract：winget install UB-Mannheim.TesseractOCR 后 pip install pytesseract pillow。"
        )
    return info
