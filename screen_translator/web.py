"""网页界面：上传图片 → OCR → 翻译（云端可访问）。"""

from __future__ import annotations

from typing import Optional

import gradio as gr
from PIL import Image

from screen_translator.ocr import recognize_text
from screen_translator.translate import LANGUAGES, translate_text

LANG_CHOICES = [f"{name} ({code})" for code, name in LANGUAGES.items()]
CODE_OF = {f"{name} ({code})": code for code, name in LANGUAGES.items()}
TARGET_CHOICES = [c for c in LANG_CHOICES if not c.endswith("(auto)")]


def _code(label: str, default: str) -> str:
    return CODE_OF.get(label, default)


def process(
    image: Optional[Image.Image],
    source_label: str,
    target_label: str,
) -> tuple[str, str, str]:
    if image is None:
        return "", "", "请先上传或粘贴一张截图。"

    try:
        original = recognize_text(image)
    except Exception as exc:
        return "", "", f"OCR 失败: {exc}"

    if not original.strip():
        return "", "", "未识别到文字，请换一张更清晰的图。"

    try:
        translated = translate_text(
            original,
            source=_code(source_label, "auto"),
            target=_code(target_label, "zh-CN"),
        )
    except Exception as exc:
        return original, "", f"翻译失败: {exc}"

    return original, translated, "完成。"


CUSTOM_CSS = """
.gradio-container {
  max-width: 980px !important;
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
footer { display: none !important; }
"""


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="屏幕 OCR 翻译") as demo:
        gr.Markdown(
            """
            # 屏幕 OCR 翻译
            上传截图或照片，自动识别文字并翻译。  
            （云端环境无法框选你的本机屏幕；本机请运行 `python main.py` 使用桌面框选。）
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
                btn = gr.Button("识别并翻译", variant="primary")
                status = gr.Textbox(label="状态", interactive=False)
            with gr.Column(scale=1):
                original = gr.Textbox(label="原文", lines=12)
                translated = gr.Textbox(label="译文", lines=12)

        btn.click(
            fn=process,
            inputs=[image, source, target],
            outputs=[original, translated, status],
        )
        image.change(
            fn=process,
            inputs=[image, source, target],
            outputs=[original, translated, status],
        )
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
