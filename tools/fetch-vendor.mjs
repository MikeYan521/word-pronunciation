// 下载前端运行库到 web/vendor/（部署包自带，不依赖 CDN）。
//
//   node tools/fetch-vendor.mjs
//
// 目前只取 pdf.js：浏览器里解析 PDF 必需。tesseract.js（扫描版 OCR）体积很大
// （语言包约 15MB），因此运行时按需从 CDN 加载，见 web/js/core/pdftext.js。

import { gunzipSync } from "node:zlib";
import { mkdirSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";

const PDFJS_VERSION = "4.6.82";
const TARBALL = `https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-${PDFJS_VERSION}.tgz`;
const WANTED = [
  ["package/build/pdf.min.mjs", "web/vendor/pdfjs/pdf.min.mjs"],
  ["package/build/pdf.worker.min.mjs", "web/vendor/pdfjs/pdf.worker.min.mjs"],
  ["package/LICENSE", "web/vendor/pdfjs/LICENSE"],   // Apache-2.0，随依赖一起带上
];

/** 极简 tar 解析（只取需要的文件）。 */
function extractTar(buffer) {
  const files = new Map();
  let offset = 0;
  while (offset + 512 <= buffer.length) {
    const header = buffer.subarray(offset, offset + 512);
    if (header.every((byte) => byte === 0)) break;
    const name = header.subarray(0, 100).toString("utf8").replace(/\0.*$/, "");
    const sizeOctal = header.subarray(124, 136).toString("utf8").replace(/\0.*$/, "").trim();
    const size = parseInt(sizeOctal || "0", 8);
    const dataStart = offset + 512;
    if (size > 0) files.set(name, buffer.subarray(dataStart, dataStart + size));
    offset = dataStart + Math.ceil(size / 512) * 512;
  }
  return files;
}

async function main() {
  console.log(`downloading pdfjs-dist@${PDFJS_VERSION} ...`);
  const res = await fetch(TARBALL);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const tarball = Buffer.from(await res.arrayBuffer());
  const files = extractTar(gunzipSync(tarball));
  for (const [source, target] of WANTED) {
    const data = files.get(source);
    if (!data) {
      console.error(`缺少 ${source}`);
      process.exitCode = 1;
      continue;
    }
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, data);
    console.log(`  ${target}  ${(data.length / 1024).toFixed(0)}KB${existsSync(target) ? "" : " (失败)"}`);
  }
}

main().catch((err) => {
  console.error("vendor 下载失败：", err.message);
  console.error("可手动把 pdfjs-dist 的 build/pdf.min.mjs 与 build/pdf.worker.min.mjs 放到 web/vendor/pdfjs/");
  process.exitCode = 1;
});
