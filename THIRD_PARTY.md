# 第三方组件与数据

本项目自身的代码以 MIT 许可发布（见 `LICENSE`）。以下第三方内容随项目分发，
各自保留原有许可证：

| 内容 | 位置 | 许可证 | 说明 |
| --- | --- | --- | --- |
| ipa-dict 音标数据 | `web/data/ipa/en_UK.txt`、`en_US.txt` | **MIT** | 英式 65,119 条 / 美式 125,927 条。许可证全文见 `web/data/ipa/LICENSE-ipa-dict.txt`。来源：<https://github.com/open-dict-data/ipa-dict> |
| pdf.js | `web/vendor/pdfjs/` | **Apache-2.0** | 浏览器内解析 PDF。许可证全文见 `web/vendor/pdfjs/LICENSE`。 |
| tesseract.js（可选） | 不在仓库内，运行时按需从 CDN 加载 | Apache-2.0 | 仅扫描版 OCR 时加载 |
| Free Dictionary API（可选） | 服务端函数调用 <https://dictionaryapi.dev> | MIT 项目 | 音频来自 Wiktionary / 维基媒体 |

## 界面设计系统

UI 令牌（颜色、字号、圆角、间距、阴影、组件规则）取自
[VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md)（MIT）
中的 **Notion** DESIGN.md，仅使用其设计规范数值，未复制其它项目代码。

## 未采用的开源项目（调研结论）

* `yusufdinc974/LinguaReader` —— **无 LICENSE**（保留所有权利），未使用其代码。
* `Ironship/WordHunter` —— AGPL-3.0，会带来传染性许可要求，未使用其代码。
* `analystsandeep/pdf-text-to-speech-reader` —— 使用浏览器合成语音，与本项目
  "真实英美录音"的目标不符，未接入。

## 关于用户上传的教材

本项目不包含任何教材原文。`data/`、`outputs/`、`web/sample/` 已被 `.gitignore`
排除——它们可能包含你导入的教材词条，属于第三方版权内容，请勿随仓库公开。
