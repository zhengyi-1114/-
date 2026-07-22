"""网页界面：上传图片 → OCR → 翻译（云端可访问）。"""

from __future__ import annotations

from typing import Any, Optional

import gradio as gr
from PIL import Image, ImageDraw, ImageFont

from screen_translator.ocr import recognize_text
from screen_translator.translate import LANGUAGES, translate_text

LANG_CHOICES = [f"{name} ({code})" for code, name in LANGUAGES.items()]
CODE_OF = {f"{name} ({code})": code for code, name in LANGUAGES.items()}
TARGET_CHOICES = [c for c in LANG_CHOICES if not c.endswith("(auto)")]


def _code(label: str, default: str) -> str:
    return CODE_OF.get(label, default)


def _make_sample() -> Image.Image:
    img = Image.new("RGB", (900, 280), "#ffffff")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36
        )
        cfont = ImageFont.truetype(
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 36
        )
    except OSError:
        font = ImageFont.load_default()
        cfont = font
    draw.text((40, 50), "Hello, world! Screen OCR test.", fill="#111111", font=font)
    draw.text((40, 130), "这是一段用于测试识别的中文。", fill="#111111", font=cfont)
    draw.text((40, 200), "Upload a clear screenshot for best results.", fill="#333333", font=font)
    return img


def process(
    image: Any,
    source_label: str,
    target_label: str,
) -> tuple[str, str, str]:
    if image is None:
        return "", "", "请先上传或粘贴一张截图。"

    try:
        original = recognize_text(image)
    except Exception as exc:
        return "", "", f"OCR 失败: {type(exc).__name__}: {exc}"

    if not original.strip():
        return (
            "",
            "",
            "未识别到文字。请换更清晰、对比度更高的截图，或先点击下方示例图试一次。",
        )

    try:
        translated = translate_text(
            original,
            source=_code(source_label, "auto"),
            target=_code(target_label, "zh-CN"),
        )
    except Exception as exc:
        return original, "", f"翻译失败: {type(exc).__name__}: {exc}"

    return original, translated or "", f"完成。识别到 {len(original)} 个字符。"


CUSTOM_CSS = """
.gradio-container {
  max-width: 980px !important;
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
footer { display: none !important; }
"""


def build_demo() -> gr.Blocks:
    sample = _make_sample()
    with gr.Blocks(title="屏幕 OCR 翻译") as demo:
        gr.Markdown(
            """
            # 屏幕 OCR 翻译
            上传**清晰截图**（文字尽量大、对比明显），点「识别并翻译」。  
            若一直失败，先点「填入示例图」确认服务正常。
            """
        )
        with gr.Row():
            with gr.Column(scale=1):
                image = gr.Image(
                    type="pil",
                    label="截图 / 图片",
                    sources=["upload", "clipboard"],
                    height=360,
                )
                with gr.Row():
                    sample_btn = gr.Button("填入示例图")
                    btn = gr.Button("识别并翻译", variant="primary")
                source = gr.Dropdown(
                    choices=LANG_CHOICES,
                    value="自动检测 (auto)",
                    label="源语言",
                )
                target = gr.Dropdown(
                    choices=TARGET_CHOICES,
                    value="中文（简体） (zh-CN)",
                    label="目标语言",
                )
                status = gr.Textbox(label="状态", interactive=False)
            with gr.Column(scale=1):
                original = gr.Textbox(label="原文", lines=12)
                translated = gr.Textbox(label="译文", lines=12)

        sample_btn.click(fn=lambda: sample, outputs=image)
        btn.click(
            fn=process,
            inputs=[image, source, target],
            outputs=[original, translated, status],
        )
        # 不再自动 image.change，避免上传未完成就识别导致空结果
    return demo


def run_web(host: str = "0.0.0.0", port: int = 7860, share: bool = True) -> None:
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
