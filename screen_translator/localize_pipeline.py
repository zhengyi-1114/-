"""URL / 切图目录 → OCR+翻译+擦嵌 → 本地按序保存汉化图。"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from screen_translator.ocr import load_image
from screen_translator.render_localize import localize_image_pixels
from screen_translator.webtoon import (
    Bubble,
    detect_text_boxes,
    group_bubbles,
    translate_bubbles,
)

Image.MAX_IMAGE_PIXELS = None

ProgressCb = Callable[[str], None]
HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")


@dataclass
class LocalizeResult:
    out_dir: Path
    saved: list[Path]
    copied_as_is: int
    localized: int
    cuts_total: int


def _log(cb: Optional[ProgressCb], msg: str) -> None:
    if cb:
        cb(msg)
    else:
        print(msg, file=sys.stderr, flush=True)


def _has_korean_bubbles(bubbles: list[Bubble]) -> bool:
    for b in bubbles:
        if HANGUL_RE.search(b.text or ""):
            return True
    return False


def localize_one_image(
    image: Image.Image,
    *,
    source: str = "ko",
    target: str = "zh-CN",
    backend: str = "nmt",
    font_path: Optional[str] = None,
) -> tuple[Image.Image, list[Bubble], bool]:
    """
    单张图汉化。
    返回 (结果图, bubbles, did_localize)。
    无韩文气泡时原样返回（I1）。
    """
    boxes = detect_text_boxes(image, lang=source if source != "auto" else "ko")
    bubbles = group_bubbles(boxes)
    if not _has_korean_bubbles(bubbles):
        return image.convert("RGB"), bubbles, False

    translate_bubbles(
        bubbles,
        source=source if source != "auto" else "ko",
        target=target,
        backend=backend or "nmt",
        context_window=0,
    )
    # 去掉译失败且无译文的
    usable = [
        b
        for b in bubbles
        if (b.translated or "").strip() and not b.translated.startswith("[翻译失败")
    ]
    if not usable:
        return image.convert("RGB"), bubbles, False

    out = localize_image_pixels(image, usable, font_path=font_path)
    return out, bubbles, True


def _list_cut_images(cuts_dir: Path) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files = [p for p in cuts_dir.iterdir() if p.is_file() and p.suffix.lower() in exts]
    # 优先按数字序号排
    def key(p: Path):
        m = re.match(r"^(\d+)", p.stem)
        return (0, int(m.group(1))) if m else (1, p.name.lower())

    return sorted(files, key=key)


def localize_cuts_dir(
    cuts_dir: Path,
    out_dir: Path,
    *,
    source: str = "ko",
    target: str = "zh-CN",
    backend: str = "nmt",
    font_path: Optional[str] = None,
    progress: Optional[ProgressCb] = None,
) -> LocalizeResult:
    """对切图目录逐张汉化，保存为 001.jpg, 002.jpg…（H1 串行）。"""
    cuts_dir = Path(cuts_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cuts = _list_cut_images(cuts_dir)
    if not cuts:
        raise FileNotFoundError(f"切图目录无图片: {cuts_dir}")

    saved: list[Path] = []
    copied = 0
    localized = 0
    for i, cut in enumerate(cuts, 1):
        _log(progress, f"[localize] {i}/{len(cuts)} {cut.name}")
        img = load_image(cut)
        try:
            result_img, _bubbles, did = localize_one_image(
                img,
                source=source,
                target=target,
                backend=backend,
                font_path=font_path,
            )
        except Exception as exc:
            _log(progress, f"[localize] 失败，原样保存 {cut.name}: {exc}")
            result_img = img.convert("RGB")
            did = False

        out_path = out_dir / f"{i:03d}.jpg"
        result_img.save(out_path, quality=92, optimize=True)
        saved.append(out_path)
        if did:
            localized += 1
        else:
            copied += 1
            _log(progress, f"[localize] 无韩文/未译出 → 原样 {out_path.name}")

    _log(
        progress,
        f"[localize] done total={len(cuts)} localized={localized} copy={copied} → {out_dir}",
    )
    return LocalizeResult(
        out_dir=out_dir,
        saved=saved,
        copied_as_is=copied,
        localized=localized,
        cuts_total=len(cuts),
    )


def localize_url(
    url: str,
    out_dir: Path,
    *,
    source: str = "ko",
    target: str = "zh-CN",
    backend: str = "nmt",
    font_path: Optional[str] = None,
    work_dir: Optional[Path] = None,
    method: str = "images",
    progress: Optional[ProgressCb] = None,
) -> LocalizeResult:
    """
    B3：抓取网漫切图后逐张汉化到 out_dir。
    切图缓存到 work_dir（默认 out_dir 同级临时目录）。
    """
    from screen_translator.web_capture import capture_webpage

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir) if work_dir else out_dir.parent / f"{out_dir.name}-work"
    work.mkdir(parents=True, exist_ok=True)
    capture_out = work / "page.png"

    _log(progress, f"[localize-url] capture {url}")
    result = capture_webpage(
        url,
        out_path=capture_out,
        method=method,
        also_zip=False,
        also_pdf=False,
        also_html=False,
        progress=progress,
    )
    cuts = list(result.cut_paths or [])
    if not cuts:
        # 回退：用 parts 或整图当单张
        if result.parts:
            cuts = list(result.parts)
        elif result.path.is_file():
            cuts = [result.path]
        else:
            raise RuntimeError("未获得任何切图，无法汉化")

    cuts_dir = work / "cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)
    # 若 cut_paths 已在 *-cuts 下则直接用；否则复制编号
    src_dir = cuts[0].parent if cuts else cuts_dir
    if (result.cut_paths and len(result.cut_paths) >= 1) and src_dir.exists():
        return localize_cuts_dir(
            src_dir,
            out_dir,
            source=source,
            target=target,
            backend=backend,
            font_path=font_path,
            progress=progress,
        )

    # 规范化到 cuts_dir
    for i, p in enumerate(cuts, 1):
        img = load_image(p)
        dest = cuts_dir / f"{i:03d}.jpg"
        img.save(dest, quality=92, optimize=True)
    return localize_cuts_dir(
        cuts_dir,
        out_dir,
        source=source,
        target=target,
        backend=backend,
        font_path=font_path,
        progress=progress,
    )
