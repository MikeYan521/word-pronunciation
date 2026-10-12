# 英语单词书 · 词汇提取与英美双发音学习器

上传一本英语单词书 PDF（含扫描版），自动**按章节识别书中的词汇条目**，为每个词条提供
英式/美式音标、中文释义，以及**两个独立可点击的真实发音按钮**。

> 界面语言为中文，单词、音标等学习内容保留英文原文。

![pages](https://img.shields.io/badge/python-3.12-blue) ![tests](https://img.shields.io/badge/tests-91%20passed-brightgreen)

---

## 1. 快速开始

```powershell
# Windows（自动选择带 pdfplumber 的 Python）
.\run.ps1                 # 打开 http://127.0.0.1:8000
.\run.ps1 -Port 8080

# 或者手动指定解释器
python run.py
```

首次使用建议：

1. 打开首页 → 上传单词书 PDF；
2. 扫描版请勾选“尝试 OCR”（Windows 自带 OCR 引擎，无需额外安装）；
3. 解析完成后进入**章节校验**页，核对每章页码范围（✓ 已确认 / ! 待核对）；
4. 确认后生成词表 → 进入章节页面，点击 英 / 美 按钮即可听发音。

> 依赖：`pdfplumber`、`pypdf`（Python 3.10+）；可选 `pypdfium2`（渲染，已被 pdfplumber 依赖）、
> `winsdk`（Windows 内置 OCR，放在 `vendor/pylibs` 即可被自动加载）、`pytesseract`+Tesseract（备选 OCR）。
> `pip install pdfplumber pypdf`

---

## 2. 它如何提取“词条”而不是“所有英文单词”

单词书与普通课文 PDF 的排版不同：每个目标词都是一条**词条**（headword + 音标 + 词性 +
中文释义 + 例句 + 页脚汇总）。因此提取器不是简单地抓取英文单词，而是识别排版规律：

| 证据 | 说明 |
| --- | --- |
| 词条行 | 行首是单词，紧随 `/.../` 音标块：`mishap /lmshæp/`、`breeze [bri:z/`（OCR 常把一侧定界符识别成 `[`、`l`） |
| 词性 | 词条行后的 `n.` `v.` `adj.` 等 |
| 中文释义 | 词条附近的汉字（OCR 可能有误，**仅作为提示**） |
| 页脚词汇汇总 | 页面底部该页词条列表（无音标），用于**交叉校验**并补全 OCR 漏识的词条 |
| 例句 | 以 `【例】`（OCR 后常变成 `[VIJI`）开头或普通句子，**不计入词条** |

提取规则实现于 `app/entries.py`，并按下列原则工作：

* 只把“行首单词 + 音标”当作词条起始；
* 例句、正文、搭配说明中的英文单词不会被收录；
* 页脚汇总不会重复计入（它只用来校验召回率、补全漏识词条）；
* 保留**原书顺序**（`ordinal`）与**原书页码**（每个词条都能追溯到页）；
* 同一词条全书只建一条记录；若出现在多个章节，则保留各章节信息；
* 若整本书没有“词条 + 音标”的排版规律（普通课文），自动退化为**全词模式**（正文词汇 + 词形还原）。

自动判定：`app/vocab.py::detect_extraction_mode()`。实测《雅思词汇真经》（336 页扫描版）：
词条模式提取 **3,167** 条，而全词模式会得到 28,101 个“单词”。

---

## 3. 章节边界：来自整本书的结构，而不是单个关键词

`app/chapters.py` 依次使用以下证据，并在最后做一次统一校验：

1. **PDF 书签**（若书签是“每页一个”的页面标签，则自动忽略）；
2. **印刷目录页 + 印刷页码**：识别目录中的 `Chapter 1..N` 与对应起始页码，用页脚印刷页码与
   物理页的偏差（`page_offset`）换算成真实页；实测《词汇真经》offset = 11，得到 22 章 + 附录；
3. **页首 `Chapter N` 标识**：页首标识 = 章节起点证据；页脚标识 = 属于该章的运行时页脚，不会新开一章；
4. **目录页本身不参与正文词条**，也不作为章节起始证据；
5. **边界校验（唯一入口 `evaluate_chapters()`）**：所有生成路径（书签 / 目录 / 标题 /
   兜底划分 / 正文标识新增 / 用户确认后的重算）都必须经过该函数，章节拿不到置信度就
   不能进入 UI。新章节的初始状态是 `unknown`，**没有默认的 high**。

| 置信度 | 判定条件 | 界面 |
| --- | --- | --- |
| `high` | 该章**首页页首**存在匹配的 `Chapter N` 正文标识，且编号单调、范围正常 | ✓ |
| `medium` | 只能由目录页码推算、章节内**页脚**标识、或边界吸附到附近标识推断 | ! 待核对 |
| `low` | 无有效证据、证据互相冲突（如同一页多个章节标识）、范围异常 | ! 待核对 |
| `unknown` | 尚未评估（正常流程不会出现；UI 按待核对处理） | ! 待核对 |
| `manual` | 用户手动指定了起止页（这是最强证据） | ✓ |

补充规则：

* 目录页自身的 `Chapter N` **不作为**正文边界证据（只用于推算页码）；
* 目录推算与正文标识冲突时**以正文为准**，并在证据里记录相差多少页；
* 每章都会写入**可追溯证据**：目录记录（页 + 印刷页 + 换算）、正文标识页、页脚标识、
  起止页推断依据、冲突/OCR 异常、降级原因；
* 词条数为 0 **不会**机械地降低置信度，而是单独给出内容提醒（例如“附录/索引，或 OCR 未识别出内容”）；
* 只修改章节名称**不会**提升置信度，仅记录“用户修改了章节名称（不影响边界置信度）”。

实测《雅思词汇真经》：22 章中 5 章在首页找到正文标识（high），17 章由目录 + 页脚标识确认
（medium），最后的“附录/索引”因正文无标识判为 low —— 不再是“全部 high”。

### 章节校验页面

生成词表前先进入**章节校验**页（`web/js/pages/parse.js`）：

```
01  Chapter 1   [目录推算]                          ✓
    教材第 12–31 页 · 词条 221 个
    依据：p12 页首有 Chapter 1
```

* ✓ = 已确认；! = 待核对（可「仅看待核对」筛选）；
* ✎ 直接修改起止页 → **只重新处理该范围**（`POST /api/chapters/{id}/range`）；
* ✂ 拆分章节、🗑 删除、新增章节、全部合并；
* “未归属页面”会单独列出（前置内容/空隙），不会被计入词表；
* 重新识别章节（`/redetect-chapters`）会按现有 OCR 文本重算，不需要重新 OCR。

---

## 4. 英美双发音（真实录音 + 实测校验）

| 环节 | 实现 |
| --- | --- |
| 音标 | 本地 `ipa-dict`（MIT）：`data/ipa/en_UK.txt`（65,119 条）/ `en_US.txt`（125,927 条），离线可用 |
| 释义 | 在线词典（有道 JSON 接口，逐词查询并缓存到 SQLite）；失败时回退英文释义 |
| 英式发音 | 独立请求 `type=1`，独立缓存文件 |
| 美式发音 | 独立请求 `type=2`，独立缓存文件 |
| 音频校验 | HTTP 200 + 体积区间 + **真实容器识别**（MP3/WAV/OGG，按魔数判断而非信 Content-Type）+ 时长合理性（0.12–5s） |
| 缺失处理 | 该口音按钮显示“无音频”，**绝不用另一种口音替代**，点击可重试 |

发音接口封装在 `app/audio/`：

```
app/audio/base.py         # 提供者协议 + 容器/时长探测
app/audio/service.py      # 提供者链、校验、磁盘缓存（data/audio）、并发去重
app/audio/providers/youdao.py     # 有道词典发音（真实录音，type=1 英式 / type=2 美式）
app/audio/providers/freedict.py   # Free Dictionary API（dictionaryapi.dev，MIT 项目，维基媒体录音）
```

替换数据源只需改 `AudioService(providers=[...])` 的顺序或新增一个 provider。

实测（`python tools/verify_audio.py`，输出 `outputs/audio_verification.md`）：
40 个常用词中 **39 个同时取得英美两种音频，且两段音频 MD5 全部不同**（证明是真·两种口音），
唯一缺失的 “weather” 会被明确标记而不是静默替换。你可以在项目里重新运行该脚本复核。

### 关于被要求调研的 5 个开源项目

| 项目 | 许可证 | 结论 |
| --- | --- | --- |
| `open-dict-data/ipa-dict` | MIT | **直接复用数据**（en_UK / en_US），已随项目分发 |
| `ngocsangyem/freedictionaryapi` | MIT | 部署域名 `api.freedictionary.dev` 已无法解析（NXDOMAIN），自建需要 MongoDB + 抓取维基词典；**不接入**，其数据模型作为参考 |
| `yusufdinc974/LinguaReader` | **无 LICENSE** | 无许可证 = 保留所有权利，**代码不可复制**，仅参考交互设计 |
| `Ironship/WordHunter` | AGPL-3.0 | 传染性许可证，复制会要求整个应用 AGPL；**不复制代码**，仅参考 SRS/数据模型 |
| `analystsandeep/pdf-text-to-speech-reader` | MIT | 使用 Web Speech API（合成语音），与“真实英美口音”目标不符；仅参考逐词高亮思路 |

浏览器语音合成只作为**显式标注的备用方案**：`web/js/audio.js::ttsSupport()` 会先检查设备是否
真的存在 `en-GB` / `en-US` 语音，界面上始终标注为“合成语音”，不会冒充真实录音。

---

## 5. 学习进度与统计

* 每个词条可标记**收藏**与**已掌握**，词表支持搜索、按字母/原书顺序排序、筛选未掌握与收藏；
* 学习统计页展示：词条总数、已获取释义/音标数量、已掌握与收藏数量、已缓存的英美发音数量、
  各单词书的掌握进度，以及待掌握词条清单。

## 6. 跟读（预留）

单词详情页已支持**录音 / 回放 / 与标准音对比播放**（`MediaRecorder`）。发音评估接口预留为
`app/pronunciation.py`（未实现），未配置前不会给出任何评分——因为普通语音识别不等于发音评估。

---

## 7. 项目结构

```
app/
  server.py        HTTP 服务（标准库 http.server）+ JSON API + 静态前端
  db.py            SQLite 架构与迁移（升级不丢数据）
  library.py       单词书/章节/词条 CRUD、解析流水线、后台解析进度
  pdf_parser.py    PDF 文本层提取；扫描页并行渲染 + OCR；印刷页码识别
  ocr.py           OCR 引擎（Windows 内置 winsdk / Tesseract），自动探测
  chapters.py      章节识别：书签 / 目录+页码 / 页首标识 / 边界校验
  entries.py       词条识别（单词书排版规则）+ 页脚交叉校验
  vocab.py         词条模式 / 全词模式（词形还原、去重、页码）
  library.py       单词书/章节/词条 CRUD、解析流水线、学习统计
  ipa.py           ipa-dict 音标查询
  dictionary.py    释义/音标在线查询（可替换）
  enrichment.py    批量补齐音标/释义（并发 + 缓存）
  audio/           发音提供者链 + 校验 + 缓存
web/               无构建步骤的前端（hash 路由 + 响应式）
tests/             91 个单元/集成测试
tools/             命令行工具（见下）
data/              运行数据：app.sqlite3、pdfs/、parsed/、audio/、ipa/
```

## 8. 命令行工具

```powershell
python -m unittest discover -s tests -t .     # 运行全部测试（91 个）
python tools/make_sample_pdf.py               # 生成示例单词书 PDF
python tools/parse_local_pdf.py book.pdf --ocr            # 解析并打印章节/词条摘要
python tools/parse_local_pdf.py --textbook-id 5 --ocr     # 就地重新解析已有单词书
python tools/inspect_extraction.py --list                 # 查看已解析的单词书
python tools/inspect_extraction.py --textbook-id 5 --pages 66   # 查看某页识别出的词条
python tools/verify_audio.py --limit 40                   # 实测英美发音并生成报告
python tools/smoke_test.py                                # 端到端冒烟测试（含真实音频）
```

## 9. 存储与隐私

* 上传的 PDF、解析缓存、词库、音频缓存、学习记录全部保存在本机 `data/`（SQLite + 文件）。
* 只有**单个单词**会发送给在线词典接口（用于释义/音标兜底与发音），**整本 PDF 不会外发**。
* 无任何硬编码密钥；如需更换为需要密钥的服务，请通过服务端环境变量注入。

## 10. 已知限制

## 10.5 部署到 Netlify

仓库里已包含 Netlify 部署配置（`netlify.toml` + `netlify/functions/`）以及
**压缩包产物** `outputs/english-wordbook-netlify.zip`（约 1.8MB）。

* 前端会自动选择运行模式：探测到 `/api/health` 就用本地 Python 后端，否则走
  **浏览器内解析**（pdf.js + tesseract.js + IndexedDB）；
* Netlify 侧只需要两个无状态函数：`/api/dict`（释义/音标）与 `/api/audio`（真实发音 + 校验 + 缓存）；
* 部署步骤见 `DEPLOY.md`（支持拖拽 `web/` 快速上线，或用 Git/CLI 获得完整功能）。

* 扫描版质量差时，OCR 会把个别单词识别错（例如 `learn` → `leam`）。这类词条会标记为
  “未收录”，可在章节页面手动删除或修正；页脚汇总交叉校验可补回大部分漏识词条。
* 在线词典接口为公开但未文档化的接口：已做超时、失败回退与本地缓存，可替换为自己的数据源。
* 目录页码与正文不一致时以正文标识为准；若两者都无法确认，章节会被标记为“待核对”，
  由你手动指定页码范围后只重新处理该范围。

## 11. 许可与致谢

* 音标数据：[ipa-dict](https://github.com/open-dict-data/ipa-dict)（MIT），许可证见 `data/ipa/LICENSE-ipa-dict.txt`。
* 备用音频：Free Dictionary API（MIT 项目，音频来自 Wiktionary/维基媒体）。
* 本仓库未复制任何无许可证（LinguaReader）或 AGPL（WordHunter）项目的代码。
* 界面设计系统：取自 [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md)（MIT）
  中的 **Notion**  DESIGN.md —— `web/css/app.css` 顶部的令牌（颜色 / 字号 / 圆角 / 间距 / 阴影）与该文档一致：

| 类别 | 采用的值 |
| --- | --- |
| 画布 | `--canvas #ffffff`、`--surface #f6f5f4`、hairline `#e5e3df` |
| 文字 | ink `#1a1a1a`、charcoal `#37352f`、steel `#787671`、stone `#a4a097` |
| 主操作 | 紫色 `#5645d4`（按下 `#4534b3`）；链接蓝 `#0075de` |
| 语义色 | 成功 `#1aae39`、警告 `#dd5b00`、错误 `#e03131` |
| 圆角 | 按钮/输入 8px、卡片 12px、标签 6px、状态胶囊 9999px（Notion 是"矩形化"而非全胶囊） |
| 阴影 | 扁平优先：卡片只用 1px hairline；浮层用 `0 16px 48px -8px rgba(15,15,15,.16)` |
| 间距 | 4px 基数 / 8px 递增；卡片内边距 24px；页面最大宽 1280px + 32px 边距 |
| 特色组件 | 顶部导航用 pill-tab（激活态黑底白字）、上传区用 pastel feature card、章节复核卡用警示 tint |
| 发音按钮 | UK 用链接蓝、US 用品牌橙，均为 8px 圆角矩形 + 对应浅色 tint（同时有"英/美"文字标签，不依赖颜色区分） |
