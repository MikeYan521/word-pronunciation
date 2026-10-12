// Netlify 构建命令（netlify.toml 里配置为 `node tools/netlify-prepare.mjs`）。
//
// 部署包里已经带了 pdf.js 与音标数据，这里只是"缺了就补"的保险：
//   * web/vendor/pdfjs/  ← pdf.js（浏览器解析 PDF）
//   * web/data/ipa/      ← ipa-dict 音标数据（MIT）

import { existsSync, mkdirSync, copyFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

function need(paths) {
  return paths.some((path) => !existsSync(path));
}

const vendorMissing = need(["web/vendor/pdfjs/pdf.min.mjs", "web/vendor/pdfjs/pdf.worker.min.mjs"]);
if (vendorMissing) {
  console.log("pdf.js 缺失，尝试下载 ...");
  try {
    execFileSync(process.execPath, ["tools/fetch-vendor.mjs"], { stdio: "inherit" });
  } catch {
    console.error("下载 pdf.js 失败：请把 pdf.min.mjs / pdf.worker.min.mjs 放到 web/vendor/pdfjs/");
  }
}

const ipaTargets = [["en_UK.txt", "web/data/ipa/en_UK.txt"], ["en_US.txt", "web/data/ipa/en_US.txt"],
                    ["LICENSE-ipa-dict.txt", "web/data/ipa/LICENSE-ipa-dict.txt"]];
for (const [source, target] of ipaTargets) {
  if (existsSync(target)) continue;
  const origin = `data/ipa/${source}`;
  if (!existsSync(origin)) { console.error(`缺少音标数据：${origin}`); continue; }
  mkdirSync("web/data/ipa", { recursive: true });
  copyFileSync(origin, target);
  console.log(`copied ${origin} → ${target}`);
}

console.log("Netlify 构建准备完成：publish=web, functions=netlify/functions");
