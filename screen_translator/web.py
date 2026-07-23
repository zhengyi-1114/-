"""网页界面：上传图片 → OCR → 翻译（云端可访问）。"""

from __future__ import annotations

from typing import Any

import gradio as gr
from PIL import Image, ImageDraw, ImageFont

from screen_translator.ocr import recognize_text
from screen_translator.translate import LANGUAGES, translate_text

LANG_CHOICES = [f"{name} ({code})" for code, name in LANGUAGES.items()]
CODE_OF = {f"{name} ({code})": code for code, name in LANGUAGES.items()}
TARGET_CHOICES = [c for c in LANG_CHOICES if not c.endswith("(auto)")]


def _code(label: str, default: str) -> str:
    return CODE_OF.get(label, default)


def _font(path: str, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _make_sample_zh() -> Image.Image:
    img = Image.new("RGB", (900, 280), "#ffffff")
    draw = ImageDraw.Draw(img)
    font = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    cfont = _font("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 36)
    draw.text((40, 50), "Hello, world! Screen OCR test.", fill="#111111", font=font)
    draw.text((40, 130), "这是一段用于测试识别的中文。", fill="#111111", font=cfont)
    draw.text((40, 200), "Upload a clear screenshot for best results.", fill="#333333", font=font)
    return img


def _make_sample_ko() -> Image.Image:
    img = Image.new("RGB", (900, 300), "#ffffff")
    draw = ImageDraw.Draw(img)
    # NanumGothic 对韩文支持更好
    kfont = _font("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 40)
    draw.text((40, 40), "안녕하세요. 반갑습니다.", fill="#111111", font=kfont)
    draw.text((40, 120), "오늘 날씨가 정말 좋습니다.", fill="#111111", font=kfont)
    draw.text((40, 200), "한국어 OCR 번역 테스트", fill="#111111", font=kfont)
    return img


def process(
    image: Any,
    source_label: str,
    target_label: str,
) -> tuple[str, str, str]:
    if image is None:
        return "", "", "请先上传或粘贴一张截图。"

    source = _code(source_label, "auto")
    target = _code(target_label, "zh-CN")

    try:
        # 源语言选韩语/日语时，强制走对应 OCR 引擎
        original = recognize_text(image, lang=source)
    except Exception as exc:
        return "", "", f"OCR 失败: {type(exc).__name__}: {exc}"

    if not original.strip():
        return (
            "",
            "",
            "未识别到文字。韩文请把源语言选「韩语」，并使用清晰截图。",
        )

    try:
        translated = translate_text(original, source=source, target=target)
    except Exception as exc:
        return original, "", f"翻译失败: {type(exc).__name__}: {exc}"

    return (
        original,
        translated or "",
        f"完成。源语言={source} → {target}，识别 {len(original)} 字符。",
    )


CUSTOM_CSS = """
.gradio-container {
  max-width: 980px !important;
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
footer { display: none !important; }
"""


def build_demo() -> gr.Blocks:
    sample_zh = _make_sample_zh()
    sample_ko = _make_sample_ko()
    with gr.Blocks(title="屏幕 OCR 翻译") as demo:
        gr.Markdown(
            """
            # 屏幕 OCR 翻译
            **翻译韩文**：源语言请选「韩语 (ko)」，再上传截图并点「识别并翻译」。  
            **网漫长图**：支持自动切片识别，但网页上传有大小限制；超长截图更推荐命令行：  
            `python main.py page.png -s ko -t zh-CN`  
            首次识别韩文会下载模型，可能需要等待几十秒。
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
                    sample_zh_btn = gr.Button("中英示例")
                    sample_ko_btn = gr.Button("韩文示例")
                    btn = gr.Button("识别并翻译", variant="primary")
                source = gr.Dropdown(
                    choices=LANG_CHOICES,
                    value="韩语 (ko)",
                    label="源语言（韩文必选韩语）",
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

        def load_zh():
            return sample_zh, "自动检测 (auto)"

        def load_ko():
            return sample_ko, "韩语 (ko)"

        sample_zh_btn.click(fn=load_zh, outputs=[image, source])
        sample_ko_btn.click(fn=load_ko, outputs=[image, source])
        btn.click(
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
