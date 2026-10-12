# 部署到 Netlify

这个项目有两种运行方式，**同一套前端代码**会自动选择：

| 运行方式 | 说明 |
| --- | --- |
| 本地（`python run.py`） | Python 后端：SQLite 存储、系统 OCR（Windows 内置 / Tesseract） |
| Netlify（本压缩包） | **全在浏览器里跑**：pdf.js 解析 PDF、tesseract.js OCR、IndexedDB 存储；Netlify 只托管静态文件 + 两个无状态函数 |

部署包结构：

```
netlify.toml                # publish=web, functions=netlify/functions
netlify/functions/
  dict.mjs                  # /api/dict  → 中文释义 + 英美音标（服务端代理，绕开跨域）
  audio.mjs                 # /api/audio → 经过校验的真实英美录音（英/美分别获取）
  _shared/dict.mjs
  _shared/audio.mjs
web/                        # 静态前端（部署后就是网站本体）
  index.html  css/  js/      js/core/ 是浏览器内解析流水线
  data/ipa/                 # ipa-dict 音标数据（MIT，4.6MB）
  vendor/pdfjs/             # pdf.js（已内置，不依赖 CDN）
tools/netlify-prepare.mjs   # 构建命令（检查 vendor/音标数据是否齐全）
package.json
```

---

## 方式 A：拖拽部署（最快，30 秒）

1. 打开 <https://app.netlify.com/drop>
2. 把 `web/` 文件夹**直接拖进去**

这样部署的是纯静态站，**可用**，但有两处降级：

* 中文释义改为英文释义（浏览器直连 dictionaryapi.dev，允许跨域；有道接口不允许跨域）
* 发音改为浏览器直接播放词典录音地址（**仍是词典真实录音**，但无法在服务端预先校验，也没有统一 CDN 缓存；
  个别浏览器可能因为自动播放策略需要你多点一次）

如果这两点能接受，方式 A 就够了。

> 想让中文释义和"发音校验 / 缓存"都生效，用下面的方式 B（Git 部署），
> 那样会启用 `netlify/functions/` 里的两个函数。

## 方式 B：Git 部署（推荐，功能完整）

1. 把本压缩包解压后**上传到一个 GitHub 仓库**（公开/私有都行）
2. Netlify → **Add new site → Import an existing project** → 选该仓库
3. 构建配置会自动读取 `netlify.toml`（publish `web`，functions `netlify/functions`），直接 Deploy

部署完成后：

* `/api/dict` → 中文释义 + 英美音标（服务端调用，无需跨域）
* `/api/audio` → 真实英美录音，服务端逐条校验（HTTP 状态 / 体积 / 容器魔数 / 时长），并带 CDN 长缓存
* 页面里的发音按钮点击即播，缺失的口音明确显示"无音频"，不会用另一种口音替代

## 方式 C：Netlify CLI

```bash
npm install -g netlify-cli
netlify login
netlify deploy --build          # 预览
netlify deploy --build --prod   # 正式
```

---

## 使用说明（部署后）

1. 打开首页 → 上传单词书 PDF
2. 扫描版请勾选「尝试 OCR」（首次会从 CDN 加载 tesseract.js 语言包，约 15MB，之后走缓存）
3. 解析完成后进入**章节校验**页：`✓` 已确认 / `!` 待核对（可点 ✎ 手动改页码范围，只重算该范围）
4. 点「确认并生成词表」→ 进入章节页面查看词条、英美音标与发音按钮
5. 数据保存在浏览器 IndexedDB（换浏览器/清缓存会清空；不会把 PDF 上传到任何服务器）

---

## 手机怎么用（重点）

**扫描版 PDF 不要指望在手机上解析。** 《词汇真经》这类 300+ 页扫描本，
手机浏览器既要逐页渲染又要跑 OCR，内存和耗时都不现实（容易被系统杀掉进程），
而且 OCR 语言包很大。

推荐流程（一次操作，之后手机随时学习）：

1. **在电脑上**打开本项目（本地 `python run.py` 最快，或这个 Netlify 站点）
2. 上传同一本 PDF → 勾选「尝试 OCR」→ 等待解析完成 → 确认章节 → 生成词表
3. 进入这本书的页面，点 **「导出词表」** → 得到 `书名.wordbook.json`（约 1 MB，含音标/释义/章节/学习进度）
4. 把这个 JSON 发到手机上（微信/网盘/AirDrop 都行）
5. 手机浏览器打开本站 → 首页点 **「导入词表 JSON」** → 选择该文件
6. 手机上就能看到全部章节与词条：英美音标、中文释义、两个发音按钮，学习进度也存在手机本地

导出/导入格式（`wordbook-export/1`）在 Python 版与静态版之间**互通**，
也可以用来在两台设备之间同步，或在重装浏览器前备份。

如果只是想小范围试一下 OCR：上传时把「OCR 页数上限」设成 20（默认），
只识别前 20 页，先确认效果再决定是否整本跑。

## 常见问题

**扫描版识别效果不如本地版？**
Netlify 版用 tesseract.js（浏览器 WASM），本地 Python 版优先用 Windows 内置 OCR，准确率更高。追求扫描版精度建议用本地版。

**必须联网吗？**
解析 PDF、章节识别、发音按钮音频需要联网（音频来自在线词典）；音标数据已内置离线。释义/发音首次获取后由 CDN 缓存。

**能放多大 PDF？**
浏览器内解析，PDF 本身不上传，因此只受本机内存限制；30MB 以内的 PDF 会额外保留一份原件用于"重新 OCR"。

**为什么扫描版上传后提示"这是扫描版 PDF"？**
说明该 PDF 没有文字层（整页是图片）。这是浏览器版的硬限制，不是解析失败；
按上面的"手机怎么用"流程在电脑上解析一次即可。

**许可证**
音标数据：ipa-dict（MIT，见 `web/data/ipa/LICENSE-ipa-dict.txt`）。
前端设计系统：Notion DESIGN.md（来自 MIT 许可的 VoltAgent/awesome-design-md）。
PDF 解析：pdf.js（Apache-2.0）。OCR 可选：tesseract.js（Apache-2.0）。
