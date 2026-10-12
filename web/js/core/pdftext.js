// 浏览器端 PDF 文本提取（pdf.js）。扫描版可选用 tesseract.js OCR（按需从 CDN 加载）。

import * as pdfjs from "../../vendor/pdfjs/pdf.min.mjs";

pdfjs.GlobalWorkerOptions.workerSrc =
  new URL("../../vendor/pdfjs/pdf.worker.min.mjs", import.meta.url).href;

const SCANNED_PAGE_CHAR_THRESHOLD = 25;
const SCANNED_PDF_RATIO = 0.6;
const OCR_DPI = 300;
let tesseractModule = null;

async function loadTesseract() {
  if (tesseractModule) return tesseractModule;
  // 语言包较大（约 15MB），仅在用户勾选 OCR 时加载
  tesseractModule = await import("https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.esm.min.js");
  return tesseractModule;
}

export async function ocrAvailability() {
  try {
    await loadTesseract();
    return { available: true, engine: "tesseract.js", reason: "" };
  } catch (err) {
    return {
      available: false, engine: null,
      reason: `无法加载 tesseract.js（${err.message}）。扫描版需要联网加载 OCR 引擎；`
        + "也可以改用带文本层的 PDF，或在本地版（Python）里用系统 OCR。",
    };
  }
}

function groupIntoLines(items) {
  const usable = items
    .filter((item) => item.str && item.str.trim())
    .map((item) => ({
      text: item.str,
      x: item.transform[4],
      y: item.transform[5],
      size: Math.abs(item.transform[3]) || item.height || 0,
      eol: Boolean(item.hasEOL),
    }))
    .sort((a, b) => (Math.abs(b.y - a.y) > 2 ? b.y - a.y : a.x - b.x));
  const lines = [];
  let current = null;
  for (const item of usable) {
    if (current && Math.abs(item.y - current.y) <= Math.max(2, item.size * 0.5)) {
      current.parts.push(item.text);
      current.sizes.push(item.size);
      current.right = item.x;
      if (item.eol) { lines.push(current); current = null; }
      continue;
    }
    if (current) lines.push(current);
    current = { y: item.y, parts: [item.text], sizes: [item.size], right: item.x };
  }
  if (current) lines.push(current);
  return lines.map((line) => {
    const sizes = line.sizes.filter((size) => size > 0).sort((a, b) => a - b);
    return {
      text: line.parts.join(" ").replace(/\s{2,}/g, " ").trim(),
      size: sizes.length ? sizes[Math.floor(sizes.length / 2)] : 0,
      top: line.y,
    };
  }).filter((line) => line.text);
}

function joinLines(lines) {
  let text = "";
  for (const line of lines) {
    if (text.endsWith("-") && /^[a-z]/.test(line.text)) text = text.slice(0, -1) + line.text;
    else text += (text ? "\n" : "") + line.text;
  }
  return text;
}

function detectPrintedPage(lines) {
  const candidates = [...lines.slice(0, 3), ...lines.slice(-3)].map((line) => line.text.trim());
  for (const text of candidates) {
    if (text.length > 6) continue;
    const match = /^[-–—\s]*(\d{1,4})[-–—\s]*$/.exec(text);
    if (match) {
      const value = Number(match[1]);
      if (value >= 1 && value <= 2000) return value;
    }
  }
  return null;
}

async function readOutline(doc) {
  try {
    const raw = await doc.getOutline();
    if (!raw) return [];
    const out = [];
    const walk = async (items, depth) => {
      for (const item of items) {
        const destination = item.dest;
        let page = null;
        try {
          const ref = Array.isArray(destination)
            ? (typeof destination[0] === "string" ? destination[0] : destination[0])
            : destination;
          page = (await doc.getPageIndex(ref)) + 1;
        } catch { page = null; }
        if (item.title) out.push({ title: item.title.trim(), page, depth });
        if (item.items) await walk(item.items, depth + 1);
      }
    };
    await walk(raw, 0);
    return out.filter((entry) => entry.title);
  } catch {
    return [];
  }
}

async function ocrPage(page, warnings, dpi = 0) {
  try {
    const { createWorker } = await loadTesseract();
    const viewport = page.getViewport({ scale: (dpi || OCR_DPI) / 72 });
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    const context = canvas.getContext("2d");
    await page.render({ canvasContext: context, viewport }).promise;
    const worker = await createWorker("eng", 1, { logger: () => {} });
    const { data } = await worker.recognize(canvas);
    await worker.terminate();
    const text = (data.text || "").trim();
    if (!text) warnings.push(`第 ${page.pageNumber} 页 OCR 未识别出文字`);
    return text;
  } catch (err) {
    warnings.push(`第 ${page.pageNumber} 页 OCR 失败：${err.message}`);
    return "";
  }
}

/** @returns {Promise<Object>} ParsedPdf 结构（与 Python 版字段一致） */
export async function extractPdf(file, { onProgress, useOcr = false, ocrMaxPages = 20, ocrDpi = 0 } = {}) {
  const data = await file.arrayBuffer();
  const doc = await pdfjs.getDocument({ data }).promise;
  const warnings = [];
  const outline = await readOutline(doc);
  const pages = [];
  const ocrInfo = useOcr ? await ocrAvailability() : { available: false, reason: "" };
  if (useOcr && !ocrInfo.available) warnings.push(ocrInfo.reason);
  let ocrBudget = Number(ocrMaxPages) > 0 ? Number(ocrMaxPages) : Number.POSITIVE_INFINITY;
  let ocrSkippedForBudget = 0;

  for (let index = 1; index <= doc.numPages; index += 1) {
    const page = await doc.getPage(index);
    const content = await page.getTextContent();
    let lines = groupIntoLines(content.items);
    let text = joinLines(lines);
    let ocrUsed = false;
    if (text.length < SCANNED_PAGE_CHAR_THRESHOLD && useOcr && ocrInfo.available) {
      if (ocrBudget <= 0) {
        ocrSkippedForBudget += 1;
      } else {
        ocrBudget -= 1;
        const ocrText = await ocrPage(page, warnings, ocrDpi);
        if (ocrText) {
          ocrUsed = true;
          lines = ocrText.split(/\r?\n/).map((line) => ({ text: line.trim(), size: 0, top: 0 }))
            .filter((line) => line.text);
          text = ocrText;
        }
      }
    }
    const sizes = lines.map((line) => line.size).filter((size) => size > 0).sort((a, b) => a - b);
    pages.push({
      number: index,
      lines,
      text,
      char_count: text.length,
      body_size: sizes.length ? sizes[Math.floor(sizes.length / 2)] : 0,
      has_text: text.length >= SCANNED_PAGE_CHAR_THRESHOLD,
      ocr_used: ocrUsed,
      printed_page: detectPrintedPage(lines),
    });
    if (onProgress) onProgress(index, doc.numPages, ocrUsed ? "OCR 识别中" : "提取文本层");
  }

  const preOcrTextPages = pages.filter((page) => page.char_count >= SCANNED_PAGE_CHAR_THRESHOLD).length;
  const textPages = pages.filter((page) => page.has_text).length;
  const preOcrRatio = pages.length ? preOcrTextPages / pages.length : 0;
  const textRatio = pages.length ? textPages / pages.length : 0;
  const scanned = pages.length > 0 && preOcrRatio < (1 - SCANNED_PDF_RATIO);
  if (scanned && !useOcr) warnings.push("检测到扫描版 PDF（缺少文本层），可勾选“尝试 OCR”后重新解析。");
  if (scanned && useOcr && !ocrInfo.available) warnings.push(ocrInfo.reason);
  if (ocrSkippedForBudget > 0) {
    warnings.push(`已按设置只 OCR 了前 ${ocrMaxPages} 页，还有 ${ocrSkippedForBudget} 页未识别；`
      + "确认效果后再解析整本（或改用本地版，速度快得多）。");
  }

  return {
    page_count: pages.length,
    pages,
    outline,
    scanned,
    text_ratio: Number(textRatio.toFixed(3)),
    pre_ocr_text_ratio: Number(preOcrRatio.toFixed(3)),
    ocr_page_count: pages.filter((page) => page.ocr_used).length,
    page_offset: 0,
    warnings,
    ocr: { available: ocrInfo.available, engine: ocrInfo.engine || null, reason: ocrInfo.reason || "" },
  };
}
