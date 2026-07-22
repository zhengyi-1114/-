# 屏幕 OCR 翻译

框选电脑屏幕区域，自动识别文字并翻译。也支持打开本地图片。

## 功能

- **框选翻译**：拖拽选择屏幕区域 → OCR → 翻译
- **打开图片**：对截图 / 照片做同样流程
- **多语言**：源语言可自动检测，目标语言默认简体中文
- **快捷键**：`Ctrl+Shift+T` 开始框选（窗口内；部分系统也支持全局热键）
- **命令行**：无界面时也可对图片文件 OCR + 翻译

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

### 网页界面（云端 / 浏览器推荐）

```bash
python main.py --web
```

浏览器打开提示的地址（默认 `http://127.0.0.1:7860`），上传截图即可识别并翻译。

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

# 指定语言：英语 → 日语
python main.py path/to/image.png -s en -t ja
```

## 项目结构

```
screen_translator/
  app.py          # 桌面图形界面
  web.py          # 网页界面（Gradio）
  capture.py      # 截屏与区域框选
  ocr.py          # RapidOCR 识别
  translate.py    # Google 翻译
  __main__.py     # CLI / GUI / Web 入口
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
