"""网页懒加载滚屏截图（可库调用，也可命令行单独使用）。"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence
from urllib.parse import urlparse

ProgressCb = Callable[[str], None]

DEFAULT_DISMISS = ("확인", "동의", "닫기", "Close", "OK", "Agree", "Accept")


@dataclass
class CaptureResult:
    path: Path
    url: str
    width: int
    height: int
    scroll_rounds: int
    final_page_height: int
    file_size: int


def _log(cb: Optional[ProgressCb], msg: str) -> None:
    if cb:
        cb(msg)


def _validate_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入有效的 http/https 链接")
    return url


def dismiss_popups(page, texts: Sequence[str] = DEFAULT_DISMISS) -> None:
    """尽量关掉常见同意/关闭弹窗。"""
    for text in texts:
        try:
            btn = page.get_by_role("button", name=text)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=800)
        except Exception:
            pass


def wait_visible_images(page, per_image_timeout_ms: int = 2500) -> None:
    """等待当前视口内图片加载完成。"""
    page.evaluate(
        f"""
        () => Promise.all(
          Array.from(document.images)
            .filter(img => {{
              const r = img.getBoundingClientRect();
              return r.bottom > 0 && r.top < window.innerHeight;
            }})
            .map(img => img.complete ? Promise.resolve() :
              new Promise(res => {{
                img.onload = img.onerror = () => res();
                setTimeout(res, {int(per_image_timeout_ms)});
              }}))
        )
        """
    )


def wait_all_images(page, per_image_timeout_ms: int = 4000) -> None:
    """等待页面已插入 DOM 的图片加载完成。"""
    page.evaluate(
        f"""
        () => Promise.all(
          Array.from(document.images).map(img => img.complete ? Promise.resolve() :
            new Promise(res => {{
              img.onload = img.onerror = () => res();
              setTimeout(res, {int(per_image_timeout_ms)});
            }}))
        )
        """
    )


def scroll_lazy_load(
    page,
    *,
    max_rounds: int = 220,
    wait_ms: int = 350,
    scroll_ratio: float = 0.85,
    stable_rounds: int = 3,
    progress: Optional[ProgressCb] = None,
) -> tuple[int, int]:
    """
    逐步向下滚动，触发懒加载，直到页面高度稳定。

    返回: (实际滚动轮数, 最终页面高度)
    """
    last_height = 0
    stable = 0
    rounds = 0
    for i in range(1, max_rounds + 1):
        rounds = i
        page.evaluate(
            f"window.scrollBy(0, Math.floor(window.innerHeight * {float(scroll_ratio)}))"
        )
        page.wait_for_timeout(wait_ms)
        wait_visible_images(page)

        height_now = int(page.evaluate("document.body.scrollHeight") or 0)
        scrolled = int(page.evaluate("window.scrollY + window.innerHeight") or 0)
        _log(progress, f"[scroll] round={i} height={height_now} scrolled_to={scrolled}")

        if height_now <= last_height and scrolled >= height_now - 8:
            stable += 1
        else:
            stable = 0
        last_height = height_now
        if stable >= stable_rounds:
            break

    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(400)
    wait_all_images(page)
    return rounds, last_height


def capture_webpage(
    url: str,
    *,
    out_path: str | Path,
    width: int = 800,
    height: int = 1200,
    max_rounds: int = 220,
    wait_ms: int = 350,
    scroll_ratio: float = 0.85,
    stable_rounds: int = 3,
    headless: bool = True,
    timeout_ms: int = 90000,
    dismiss: Sequence[str] = DEFAULT_DISMISS,
    progress: Optional[ProgressCb] = None,
) -> CaptureResult:
    """
    打开网页 → 懒加载滚屏 → 全页截图。

    可被其他模块直接调用；也可通过本文件 CLI 使用。
    """
    from playwright.sync_api import sync_playwright

    url = _validate_url(url)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _log(progress, f"[capture] open {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        dismiss_popups(page, dismiss)

        rounds, page_height = scroll_lazy_load(
            page,
            max_rounds=max_rounds,
            wait_ms=wait_ms,
            scroll_ratio=scroll_ratio,
            stable_rounds=stable_rounds,
            progress=progress,
        )

        page.screenshot(path=str(out_path), full_page=True)
        browser.close()

    if not out_path.is_file() or out_path.stat().st_size < 1000:
        raise RuntimeError("截图失败或文件过小，请检查链接是否可访问")

    # 用文件实际尺寸回填
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with Image.open(out_path) as im:
            img_w, img_h = im.size
    except Exception:
        img_w, img_h = width, page_height

    result = CaptureResult(
        path=out_path,
        url=url,
        width=img_w,
        height=img_h,
        scroll_rounds=rounds,
        final_page_height=page_height,
        file_size=out_path.stat().st_size,
    )
    _log(
        progress,
        f"[capture] saved {out_path} size={result.file_size} "
        f"px={result.width}x{result.height} rounds={rounds}",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="懒加载滚屏截取网页长图（可复用脚本）",
    )
    p.add_argument("url", help="网页 URL，如 https://m.comic.naver.com/...")
    p.add_argument(
        "-o",
        "--output",
        default="page.png",
        help="输出图片路径，默认 page.png",
    )
    p.add_argument("--width", type=int, default=800, help="视口宽度，默认 800")
    p.add_argument("--height", type=int, default=1200, help="视口高度，默认 1200")
    p.add_argument("--max-rounds", type=int, default=220, help="最大滚动轮数")
    p.add_argument("--wait-ms", type=int, default=350, help="每次滚动后等待毫秒")
    p.add_argument(
        "--scroll-ratio",
        type=float,
        default=0.85,
        help="每次滚动视口高度比例，默认 0.85",
    )
    p.add_argument(
        "--stable-rounds",
        type=int,
        default=3,
        help="页面高度连续稳定多少轮后停止，默认 3",
    )
    p.add_argument("--headed", action="store_true", help="有界面模式（调试用）")
    p.add_argument("--quiet", action="store_true", help="少打印进度")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    progress = None if args.quiet else (lambda m: print(m, file=sys.stderr))
    try:
        result = capture_webpage(
            args.url,
            out_path=args.output,
            width=args.width,
            height=args.height,
            max_rounds=args.max_rounds,
            wait_ms=args.wait_ms,
            scroll_ratio=args.scroll_ratio,
            stable_rounds=args.stable_rounds,
            headless=not args.headed,
            progress=progress,
        )
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    print(result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
