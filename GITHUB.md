# 推到 GitHub

> **最快方式**：直接用 `outputs/english-wordbook-github.zip` —— 它已经是一个
> **初始化好、完成首次提交、分支为 `main`** 的完整仓库（含 `.git`）。
> 解压后只需要两条命令：
>
> ```bash
> cd english-wordbook-github
> git remote add origin https://github.com/<你的用户名>/<仓库名>.git
> git push -u origin main
> ```
>
> 下面的步骤是"从零自己建仓库"的版本，二者选一即可。

本目录已经是一个**初始化好并完成首次提交**的 Git 仓库（分支 `main`），
只差把它推到你的 GitHub 账号。

## 1. 在 GitHub 建一个空仓库

打开 <https://github.com/new>：

* Repository name 随便取，例如 `english-wordbook-reader`
* **不要**勾选 Add a README / .gitignore / license（本地已经有了）
* Public / Private 都行 —— 建议 **Private**（见文末版权提醒）

## 2. 关联并推送

在本目录打开 PowerShell（或 Git Bash）执行：

```bash
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```

第一次推送会让你登录 GitHub（浏览器授权或 Personal Access Token 都可以）。
如果之前已经加过 origin，就改成：

```bash
git remote set-url origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

## 3. 让 Netlify 从这个仓库自动部署

1. Netlify → **Add new site → Import an existing project** → 选 GitHub → 选这个仓库
2. 构建配置会自动读取仓库根目录的 `netlify.toml`（`publish = "web"`，`functions = "netlify/functions"`）
3. 点 **Deploy**。之后每次 `git push` 都会自动重新部署

## 仓库里有什么、没有什么

**包含**（推送后即可用）：

```
app/                本地 Python 版（SQLite、Windows 内置 OCR）
web/                前端 + 浏览器内解析内核（pdf.js 已内置）
  data/ipa/         ipa-dict 音标数据（MIT，4.6MB，必须提交）
  vendor/pdfjs/     pdf.js（Apache-2.0，必须提交）
  sample/           自己生成的 Demo 词表（可安全公开）
netlify/            Netlify 函数（释义/发音代理）
tools/              命令行工具
tests/              91 个测试
netlify.toml package.json README.md DEPLOY.md THIRD_PARTY.md LICENSE
```

**不包含**（`.gitignore` 已排除）：

| 路径 | 原因 |
| --- | --- |
| `data/` | 你上传的 PDF、解析缓存、音频缓存、SQLite 数据库（本机私有） |
| `outputs/` | 导出的词表、截图等交付物 |
| `web/sample/词汇真经.wordbook.json` | 教材词表属于第三方版权内容 |
| `work/` | 开发过程临时文件 |

> 想让站点自带《词汇真经》词表（自己用、私有仓库）：把导出的 JSON 放到
> `web/sample/词汇真经.wordbook.json` 再提交即可 —— 首页「载入内置词表」会优先用它。
> 公开仓库建议不要提交。

## 常用命令

```bash
git status                 # 看当前改动
git add -A && git commit -m "更新"   # 提交
git push                   # 推送并触发 Netlify 重新部署
```

## 版权提醒

音标数据（ipa-dict，MIT）与 pdf.js（Apache-2.0）可以随仓库公开分发，
许可证文件都在对应目录里（`THIRD_PARTY.md` 有汇总）。
但**教材的词表、释义、例句属于出版社的版权内容** —— 自己学习没问题，
推到公开仓库等于再分发，建议：仓库设为 Private，或不要把教材词表放进仓库。
