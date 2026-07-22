"""命令行入口：可对图片文件做 OCR + 翻译，便于无 GUI 环境测试。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from screen_translator.ocr import recognize_text
from screen_translator.translate import LANGUAGES, translate_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="屏幕/图片 OCR 翻译工具",
    )
    parser.add_argument(
        "image",
        nargs="?",
        help="图片路径；省略则启动图形界面",
    )
    parser.add_argument(
        "-s",
        "--source",
        default="auto",
        help=f"源语言代码，默认 auto。可选: {', '.join(LANGUAGES)}",
    )
    parser.add_argument(
        "-t",
        "--target",
        default="zh-CN",
        help="目标语言代码，默认 zh-CN",
    )
    parser.add_argument(
        "--ocr-only",
        action="store_true",
        help="只做 OCR，不翻译",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="强制启动桌面图形界面",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="启动网页界面（云端推荐，浏览器可访问）",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="网页服务监听地址，默认 0.0.0.0",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7860,
        help="网页服务端口，默认 7860",
    )
    return parser


def run_on_image(path: Path, source: str, target: str, ocr_only: bool) -> int:
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1

    image = Image.open(path)
    print("正在识别文字…", file=sys.stderr)
    text = recognize_text(image, lang=source)
    if not text:
        print("未识别到文字。", file=sys.stderr)
        return 2

    print("—— 原文 ——")
    print(text)

    if ocr_only:
        return 0

    print("\n正在翻译…", file=sys.stderr)
    translated = translate_text(text, source=source, target=target)
    print("—— 译文 ——")
    print(translated)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.web:
        from screen_translator.web import run_web

        run_web(host=args.host, port=args.port)
        return 0

    if args.gui or not args.image:
        from screen_translator.app import run_app

        run_app()
        return 0

    return run_on_image(
        Path(args.image),
        source=args.source,
        target=args.target,
        ocr_only=args.ocr_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())