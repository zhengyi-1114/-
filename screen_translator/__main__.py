"""命令行入口：可对图片文件做 OCR + 翻译，便于无 GUI 环境测试。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from screen_translator.ocr import recognize_text
from screen_translator.translate import BACKENDS, LANGUAGES, translate_lines, translate_text


def _load_dotenv(override: bool = True) -> None:
    env_path = Path(".env")
    if not env_path.is_file():
        return
    environ = __import__("os").environ
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and (override or key not in environ):
            environ[key] = value


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
        "--backend",
        default=None,
        choices=list(BACKENDS),
        help="翻译后端: google / openai / doubao（默认读 TRANSLATOR_BACKEND）",
    )
    parser.add_argument(
        "--paired",
        action="store_true",
        help="按行输出韩/中（或源/目标）一一对照",
    )
    parser.add_argument(
        "--webtoon",
        action="store_true",
        help="网漫模式：气泡定位聚合后再翻译（推荐长截图）",
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


def run_on_image(
    path: Path,
    source: str,
    target: str,
    ocr_only: bool,
    backend: str | None,
    paired: bool,
    webtoon: bool = False,
) -> int:
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1

    if webtoon:
        from screen_translator.webtoon import process_webtoon

        print("网漫模式：气泡定位中…", file=sys.stderr)
        result = process_webtoon(
            path,
            source=source if source != "auto" else "ko",
            target=target,
            backend=backend or "doubao",
        )
        print(
            f"文本框 {result['boxes']} → 气泡 {result['bubbles']}，"
            f"译出 {result['translated']}",
            file=sys.stderr,
        )
        print(f"对照: {result['paired_path']}")
        print(f"预览: {result['preview_path']}")
        print(Path(result["paired_path"]).read_text(encoding="utf-8")[:3000])
        return 0

    image = Image.open(path)
    print("正在识别文字…", file=sys.stderr)
    text = recognize_text(image, lang=source)
    if not text:
        print("未识别到文字。", file=sys.stderr)
        return 2

    if ocr_only:
        print("—— 原文 ——")
        print(text)
        return 0

    lines = [ln for ln in text.splitlines() if ln.strip()]
    print("\n正在翻译…", file=sys.stderr)
    zh_lines = translate_lines(lines, source=source, target=target, backend=backend)

    if paired:
        print("—— 对照 ——")
        for i, (src_ln, dst_ln) in enumerate(zip(lines, zh_lines), 1):
            print(f"{i:03d}")
            print(f"原文：{src_ln}")
            print(f"译文：{dst_ln}")
            print()
    else:
        print("—— 原文 ——")
        print("\n".join(lines))
        print("—— 译文 ——")
        print("\n".join(zh_lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(override=True)
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
        backend=args.backend,
        paired=args.paired,
        webtoon=args.webtoon,
    )


if __name__ == "__main__":
    raise SystemExit(main())
