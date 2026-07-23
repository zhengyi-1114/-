"""网页应用：支持网站链接 / 上传图片 → OCR/气泡翻译。"""

from __future__ import annotations

import tempfile
import traceback
from pathlib import Path
from typing import Any, Optional

import gradio as gr
from PIL import Image, ImageDraw, ImageFont

from screen_translator.ocr import recognize_text
from screen_translator.translate import (
    BACKENDS,
    LANGUAGES,
    resolve_backend,
    translate_lines,
)
from screen_translator.web_capture import capture_webpage
from screen_translator.webtoon import process_webtoon

LANG_CHOICES = [f"{name} ({code})" for code, name in LANGUAGES.items()]
CODE_OF = {f"{name} ({code})": code for code, name in LANGUAGES.items()}
TARGET_CHOICES = [c for c in LANG_CHOICES if not c.endswith("(auto)")]
BACKEND_CHOICES = [f"{label} ({key})" for key, label in BACKENDS.items()]
BACKEND_CODE = {f"{label} ({key})": key for key, label in BACKENDS.items()}
MODE_CHOICES = ["网漫气泡模式（推荐）", "普通整图 OCR"]


def _code(label: str, default: str) -> str:
    return CODE_OF.get(label, default)


def _backend(label: str) -> str:
    try:
        return BACKEND_CODE.get(label, resolve_backend(None))
    except Exception:
        return "google"


def _font(path: str, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _make_sample_ko() -> Image.Image:
    img = Image.new("RGB", (900, 300), "#ffffff")
    draw = ImageDraw.Draw(img)
    kfont = _font("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 40)
    draw.text((40, 40), "안녕하세요. 반갑습니다.", fill="#111111", font=kfont)
    draw.text((40, 120), "오늘 날씨가 정말 좋습니다.", fill="#111111", font=kfont)
    draw.text((40, 200), "한국어 OCR 번역 테스트", fill="#111111", font=kfont)
    return img


def _save_upload(image: Any) -> Path:
    if image is None:
        raise ValueError("请先上传图片")
    if isinstance(image, Image.Image):
        path = Path(tempfile.mkstemp(prefix="ocr_", suffix=".png")[1])
        image.convert("RGB").save(path)
        return path
    if isinstance(image, str):
        return Path(image)
    raise TypeError(f"不支持的图片类型: {type(image)}")


def _maybe_trim(image_path: Path, full_page: bool, max_height: int = 20000) -> tuple[Path, str]:
    """网页端默认只处理前一段，避免超长网漫卡死。"""
    from PIL import Image as PILImage

    PILImage.MAX_IMAGE_PIXELS = None
    img = PILImage.open(image_path)
    note = f"原图 {img.size[0]}x{img.size[1]}"
    if full_page or img.height <= max_height:
        return image_path, note
    trimmed = Path(tempfile.mkstemp(prefix="trim_", suffix=".png")[1])
    img.crop((0, 0, img.width, max_height)).save(trimmed)
    return trimmed, f"{note}，网页端已裁切前 {max_height}px（勾选整页可全量，会很慢）"


def _run_pipeline(
    image_path: Path,
    *,
    source: str,
    target: str,
    backend: str,
    mode: str,
    full_page: bool = False,
) -> tuple[str, Optional[Image.Image], str]:
    image_path, trim_note = _maybe_trim(image_path, full_page=full_page)

    if mode.startswith("网漫"):
        result = process_webtoon(
            image_path,
            source=source if source != "auto" else "ko",
            target=target,
            backend=backend,
            out_dir="/opt/cursor/artifacts",
        )
        paired = Path(result["paired_path"]).read_text(encoding="utf-8")
        preview = None
        preview_path = Path(result["preview_path"])
        if preview_path.is_file():
            preview = Image.open(preview_path)
        status = (
            f"完成（气泡模式）。{trim_note}。文本框 {result['boxes']} → 气泡 {result['bubbles']}，"
            f"译出 {result['translated']}。后端={backend}"
        )
        return paired, preview, status

    text = recognize_text(str(image_path), lang=source)
    if not text.strip():
        return "", None, f"未识别到文字。{trim_note}"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    zh = translate_lines(lines, source=source, target=target, backend=backend)
    blocks = ["普通 OCR 对照\n"]
    for i, (a, b) in enumerate(zip(lines, zh), 1):
        blocks.append(f"{i:03d}")
        blocks.append(f"原文：{a}")
        blocks.append(f"译文：{b}")
        blocks.append("")
    return (
        "\n".join(blocks),
        Image.open(image_path),
        f"完成（普通模式）。{trim_note}。{len(lines)} 行，后端={backend}",
    )


def process_image(
    image: Any,
    source_label: str,
    target_label: str,
    backend_label: str,
    mode: str,
    full_page: bool,
) -> tuple[str, Optional[Image.Image], str]:
    try:
        source = _code(source_label, "ko")
        target = _code(target_label, "zh-CN")
        backend = _backend(backend_label)
        path = _save_upload(image)
        return _run_pipeline(
            path,
            source=source,
            target=target,
            backend=backend,
            mode=mode,
            full_page=full_page,
        )
    except Exception as exc:
        return "", None, f"失败: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}"


def process_url(
    url: str,
    source_label: str,
    target_label: str,
    backend_label: str,
    mode: str,
    full_page: bool,
) -> tuple[Optional[Image.Image], str, Optional[Image.Image], str]:
    try:
        if not url or not url.strip():
            return None, "", None, "请输入网页链接。"
        source = _code(source_label, "ko")
        target = _code(target_label, "zh-CN")
        backend = _backend(backend_label)

        shot = Path(tempfile.mkstemp(prefix="web_", suffix=".png")[1])
        capture_webpage(url.strip(), out_path=shot)
        full = Image.open(shot)
        preview_src = full if full.height <= 3000 else full.crop((0, 0, full.width, 3000))

        paired, preview, status = _run_pipeline(
            shot,
            source=source,
            target=target,
            backend=backend,
            mode=mode,
            full_page=full_page,
        )
        status = f"已截取网页 {full.size[0]}x{full.size[1]}。{status}"
        return preview_src, paired, preview, status
    except Exception as exc:
        return None, "", None, f"失败: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-500:]}"


CUSTOM_CSS = """
.gradio-container { max-width: 1100px !important; }
footer { display: none !important; }
"""


def build_demo() -> gr.Blocks:
    sample = _make_sample_ko()
    try:
        default_backend = resolve_backend(None)
    except Exception:
        default_backend = "doubao"
    default_backend_label = next(
        (c for c in BACKEND_CHOICES if c.endswith(f"({default_backend})")),
        BACKEND_CHOICES[-1],
    )

    with gr.Blocks(title="屏幕/网漫 OCR 翻译") as demo:
        gr.Markdown(
            """
            # 屏幕 / 网漫 OCR 翻译
            支持两种输入：
            1. **网页链接**（自动滚动懒加载并截长图，适合 Naver 网漫）
            2. **直接上传图片**
            
            推荐开启「网漫气泡模式」+ 豆包翻译。
            """
        )

        with gr.Row():
            source = gr.Dropdown(LANG_CHOICES, value="韩语 (ko)", label="源语言")
            target = gr.Dropdown(
                TARGET_CHOICES, value="中文（简体） (zh-CN)", label="目标语言"
            )
            backend = gr.Dropdown(
                BACKEND_CHOICES, value=default_backend_label, label="翻译后端"
            )
            mode = gr.Dropdown(MODE_CHOICES, value=MODE_CHOICES[0], label="识别模式")
            full_page = gr.Checkbox(
                label="处理整页（很慢，适合命令行；网页默认只处理前 20000px）",
                value=False,
            )

        with gr.Tab("网页链接"):
            url = gr.Textbox(
                label="网页 URL",
                placeholder="https://m.comic.naver.com/webtoon/detail?titleId=...",
                lines=2,
            )
            url_btn = gr.Button("抓取并翻译", variant="primary")
            with gr.Row():
                page_preview = gr.Image(label="网页截图预览（顶部）", height=420)
                bubble_preview = gr.Image(label="气泡定位预览", height=420)
            url_out = gr.Textbox(label="韩中对照", lines=22)
            url_status = gr.Textbox(label="状态", interactive=False)

        with gr.Tab("上传图片"):
            image = gr.Image(
                type="pil",
                label="截图 / 网漫长图",
                sources=["upload", "clipboard"],
                height=360,
            )
            with gr.Row():
                sample_btn = gr.Button("填入韩文示例")
                img_btn = gr.Button("识别并翻译", variant="primary")
            img_preview = gr.Image(label="预览", height=420)
            img_out = gr.Textbox(label="韩中对照", lines=22)
            img_status = gr.Textbox(label="状态", interactive=False)

        sample_btn.click(fn=lambda: sample, outputs=image)
        img_btn.click(
            fn=process_image,
            inputs=[image, source, target, backend, mode, full_page],
            outputs=[img_out, img_preview, img_status],
        )
        url_btn.click(
            fn=process_url,
            inputs=[url, source, target, backend, mode, full_page],
            outputs=[page_preview, url_out, bubble_preview, url_status],
        )

    return demo


def run_web(host: str = "0.0.0.0", port: int = 7860, share: bool = True) -> None:
    # 加载 .env
    from screen_translator.__main__ import _load_dotenv

    _load_dotenv(override=True)
    demo = build_demo()
    demo.launch(
        server_name=host,
        server_port=port,
        share=share,
        show_error=True,
        theme=gr.themes.Soft(
            primary_hue="teal",
            secondary_hue="stone",
            neutral_hue="stone",
        ),
        css=CUSTOM_CSS,
    )


if __name__ == "__main__":
    run_web()
