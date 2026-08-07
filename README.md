# 屏幕 OCR 翻译

框选电脑屏幕区域，自动识别文字并翻译。也支持打开本地图片。

## 功能

- **框选翻译**：拖拽选择屏幕区域 → OCR → 翻译
- **打开图片**：对截图 / 照片做同样流程
- **韩文 / 日文**：源语言选「韩语」或「日语」时，自动切换专用 OCR 引擎
- **多语言**：源语言可自动检测，目标语言默认简体中文
- **快捷键**：`Ctrl+Shift+T` 开始框选（窗口内；部分系统也支持全局热键）
- **命令行**：无界面时也可对图片文件 OCR + 翻译

## Windows 一键版（.exe）

本仓库可用 PyInstaller 打成 Windows 程序（**文件夹分发**，内含 `ScreenOCRTranslator.exe`）。

### 下载

1. 打开 GitHub → **Actions** → **Build Windows EXE**
2. 选最新成功运行 → Artifacts → 下载 `ScreenOCRTranslator-windows.zip`
3. 解压后双击 `ScreenOCRTranslator.exe`（勿只拷单个 exe）

也可在 Windows 本机自行打包：

```bat
packaging\build_windows.bat
```

产物在 `dist\ScreenOCRTranslator\`。把 **整个文件夹** 拷到任意 Windows 电脑即可用。  
在 exe 同目录放 `.env` 配置豆包 Key（可参考 `.env.example` / `使用说明.txt`）。

> 说明：当前云端是 Linux，不能直接产出 `.exe`；需在 Windows 或用上面的 GitHub Actions 构建。体积较大（含 OCR 模型库）属正常。

## 翻译后端

**默认：本地深度学习 NMT**（`nmt`，不走 GPT/豆包大模型 API）。

```bash
TRANSLATOR_BACKEND=nmt
# NMT_ENGINE=auto          # auto / nllb / marian
# NMT_MODEL=facebook/nllb-200-distilled-600M
```

首次运行会从 Hugging Face 下载 NLLB 模型（约几百 MB），之后离线可用。  
若 NLLB 不可用，会自动回退 MarianMT（韩→英→中）。

可选其它后端：

```bash
# Google（免 Key，需联网）
TRANSLATOR_BACKEND=google

# GPT / 豆包（大模型 API，可选）
TRANSLATOR_BACKEND=openai
OPENAI_API_KEY=sk-...

TRANSLATOR_BACKEND=doubao
DOUBAO_API_KEY=你的方舟Key
DOUBAO_MODEL=doubao-seed-translation-250915
```

命令行：

```bash
python main.py page.png -s ko -t zh-CN --backend nmt --paired
```

### 如何微调（可选）

预训练 NLLB 已能用；若要对**网漫/武侠术语**更准，可用自己的韩中对照微调：

1. **准备数据**（至少几百句，越多越好）  
   - 用双语站对齐：`ref_localize` 产出的 `*-align.json` / `*-对照.txt`  
   - 或手工 JSONL：每行 `{"source":"韩文","target":"中文"}`

```bash
python -m screen_translator.finetune_nmt prepare \
  -i ep2-ref-align.json ep76-ko-zh-对照.txt \
  -o data/train.jsonl
```

2. **训练**（建议 GPU；CPU 很慢）

```bash
pip install datasets accelerate
python -m screen_translator.finetune_nmt train \
  --train data/train.jsonl \
  -o models/nllb-ko-zh-ft \
  --epochs 3 --batch-size 4 --fp16
```

从已有权重继续训（更小学习率）：

```bash
python -m screen_translator.finetune_nmt train \
  --train data/train.jsonl \
  -o models/nllb-ko-zh-ft \
  --resume --epochs 3 --lr 5e-6 --batch-size 2 --grad-accum 8
```

3. **启用微调模型**

```bash
export TRANSLATOR_BACKEND=nmt
export NMT_ENGINE=nllb
export NMT_MODEL=/绝对路径/models/nllb-ko-zh-ft
python main.py --web
```

数据质量比数量更重要：优先用咚漫等人工汉化对照，少用错 OCR + 乱机译。术语建议过采样；NLLB 词表对部分简体武侠用字可能是 `<unk>`，可改近义或繁体再训。

## 环境要求

- Python 3.10+
- Windows / macOS / Linux（需有图形界面才能框选屏幕）
- 联网（调用 Google 翻译；OCR 在本地完成）

## 安装

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt

# 可选：全局热键（Windows / macOS 一般可直接装；Linux 可能需要 python3-dev）
pip install pynput
```

首次运行 OCR 时会加载本地模型，稍等片刻即可。

## 使用

### 懒加载滚屏截图（可复用）

默认对 Naver 等网漫会 **下载全部切图再竖向拼接**，并生成：

- **同名 `.zip`（推荐）**：里面是普通 JPG 切图，下载到电脑解压即可用看图软件打开
- `*-cuts/` 原始切图文件夹
- `*-parts/` 分段 JPG
- 整张长图 PNG（很多查看器打不开，仅供程序处理）

可选：加 `--pdf` / `--html` 才额外生成 PDF/HTML。

### 双语站对照汉化

若同时有「未汉化」和「已汉化」同一话链接，可先各自抓切图，再对齐：

```bash
# 1) 两边都抓成 cuts（Naver / 咚漫均支持）
python capture_page.py "韩文URL" -o ep-ko.png --method images
python capture_page.py "汉化URL" -o ep-zh.png --method images

# 2) OCR + 机译桥接对齐汉化站文本
python -m screen_translator.ref_localize \
  --ko-cuts ep-ko-cuts --zh-cuts ep-zh-cuts -o ep-ref --method bridge
```

输出 `ep-ref-对照.txt`：每条含原文 / 汉化参考 / 机译对照。短语气气泡可能对不齐，长对白一般可用。

```bash
python capture_page.py "https://m.comic.naver.com/webtoon/detail?titleId=..." -o page.png

# 等价
python -m screen_translator.web_capture "https://..." -o page.png

# 强制模式：images=切图拼接（推荐网漫） / scroll=边滚边截 / screenshot=旧全页截图
python capture_page.py "URL" -o page.png --method images
```

常用参数：`--method auto --width 800 --wait-ms 350 --max-rounds 220`。  
库调用：

```python
from screen_translator.web_capture import capture_webpage, scroll_lazy_load
capture_webpage(url, out_path="page.png", method="auto")
```

### 网页界面（推荐）

```bash
python main.py --web
```

打开后可：
- **网页链接**：粘贴 Naver 等网漫 URL，自动滚动懒加载截长图并翻译
- **上传图片**：直接上传截图/长图

默认网漫气泡模式；翻译默认本地 NMT（深度学习），无需大模型 Key。

### 桌面图形界面（本机框选屏幕）

```bash
python main.py --gui
# 或
python main.py
```

1. 点击 **框选翻译**（或按 `Ctrl+Shift+T`）
2. 拖拽选中屏幕上的文字区域
3. 左侧显示原文，右侧显示译文
4. 可点 **复制译文**

> 云端 Agent 里的桌面窗口跑在远程 VNC 上，聊天面板里通常看不到；请用 `--web` 网页版，或在本机运行桌面版。

### 命令行（图片）

```bash
# OCR + 翻译成中文
python main.py path/to/image.png

# 只 OCR
python main.py path/to/image.png --ocr-only

# 指定语言：韩语 → 中文（支持超长网漫截图，自动切片）
python main.py page.png -s ko -t zh-CN

# 网漫模式：气泡定位聚合后再翻译（推荐）
python main.py page.png --webtoon -s ko -t zh-CN --backend doubao
```

超长全页截图（数万像素高）不要整张塞进网页上传，用上面的命令行更稳。

## 项目结构

```
screen_translator/
  app.py          # 桌面图形界面
  web.py          # 网页界面（Gradio：链接/图片）
  web_capture.py  # 懒加载滚屏截图（可复用）
  webtoon.py      # 网漫气泡定位翻译
  capture.py      # 截屏与区域框选
  ocr.py          # RapidOCR / EasyOCR 识别
  translate.py    # NMT / Google / GPT / 豆包翻译
  nmt.py          # 本地深度学习神经机器翻译
  __main__.py     # CLI / GUI / Web 入口
capture_page.py   # 懒加载截图命令行入口
main.py
requirements.txt
```

## 说明

- OCR 使用 `rapidocr-onnxruntime`（本地，支持中英等常见文字）
- 翻译使用 `deep-translator`（Google，无需 API Key）
- 若全局热键无效（常见于部分 Linux / 权限受限环境），请用窗口内按钮或窗口获得焦点后的快捷键
- 识别效果依赖清晰度与对比度；细小或艺术字体可能不准

## 许可证

MIT
