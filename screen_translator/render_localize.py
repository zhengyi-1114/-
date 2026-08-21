"""擦除原文 + 中文嵌字（成片渲染）。

擦字策略（C3）：
1) 文字框膨胀后采样边缘主色；近白/均匀则填充
2) 否则用 OpenCV inpaint
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Protocol, Sequence, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FontLike = Union[ImageFont.ImageFont, ImageFont.FreeTypeFont]


class BubbleLike(Protocol):
    x0: float
    y0: float
    x1: float
    y1: float
    translated: str


# 常见中文字体候选（Windows / Linux）
_FONT_CANDIDATES = [
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    # Linux
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]


def find_chinese_font(prefer: Optional[str] = None) -> Optional[str]:
    """返回可用中文字体路径。"""
    if prefer:
        p = Path(prefer)
        if p.is_file():
            return str(p)
    env = os.getenv("LOCALIZE_FONT", "").strip()
    if env and Path(env).is_file():
        return env
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def load_font(size: int, font_path: Optional[str] = None) -> FontLike:
    path = find_chinese_font(font_path)
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _clamp_box(
    x0: float, y0: float, x1: float, y1: float, w: int, h: int, pad: int = 0
) -> tuple[int, int, int, int]:
    a = max(0, int(x0) - pad)
    b = max(0, int(y0) - pad)
    c = min(w, int(x1) + pad)
    d = min(h, int(y1) + pad)
    if c <= a:
        c = min(w, a + 1)
    if d <= b:
        d = min(h, b + 1)
    return a, b, c, d


def _dominant_border_color(rgb: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """取框边缘像素的近似主色。"""
    x0, y0, x1, y1 = box
    h, w = rgb.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return (255, 255, 255)
    ring: list[np.ndarray] = []
    ring.append(rgb[y0, x0:x1])
    ring.append(rgb[y1 - 1, x0:x1])
    ring.append(rgb[y0:y1, x0])
    ring.append(rgb[y0:y1, x1 - 1])
    pixels = np.concatenate([p.reshape(-1, 3) for p in ring if p.size], axis=0)
    if pixels.size == 0:
        return (255, 255, 255)
    # 量化到 32 档再找众数，抗噪
    q = (pixels.astype(np.int32) // 32) * 32 + 16
    # 打包成单 int 做 bincount 替代
    keys, counts = np.unique(q, axis=0, return_counts=True)
    color = keys[int(np.argmax(counts))]
    return (int(color[0]), int(color[1]), int(color[2]))


def _is_simple_fill_color(color: tuple[int, int, int], rgb: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    """近白或框内颜色方差低 → 可用纯色填充。"""
    r, g, b = color
    if r >= 230 and g >= 230 and b >= 230:
        return True
    x0, y0, x1, y1 = box
    patch = rgb[y0:y1, x0:x1]
    if patch.size == 0:
        return True
    std = float(np.std(patch.astype(np.float32)))
    return std < 28.0


def erase_bubbles(
    image: Image.Image,
    bubbles: Sequence[BubbleLike],
    *,
    pad: int = 4,
) -> Image.Image:
    """C3：能填色则填色，否则 inpaint。"""
    img = image.convert("RGB")
    rgb = np.array(img)
    h, w = rgb.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    fill_regions: list[tuple[tuple[int, int, int, int], tuple[int, int, int]]] = []

    for bub in bubbles:
        if not (bub.translated or "").strip():
            continue
        box = _clamp_box(bub.x0, bub.y0, bub.x1, bub.y1, w, h, pad=pad)
        color = _dominant_border_color(rgb, box)
        if _is_simple_fill_color(color, rgb, box):
            fill_regions.append((box, color))
        else:
            x0, y0, x1, y1 = box
            mask[y0:y1, x0:x1] = 255

    out = rgb.copy()
    for (x0, y0, x1, y1), color in fill_regions:
        out[y0:y1, x0:x1] = color

    if int(mask.sum()) > 0:
        try:
            import cv2

            out = cv2.inpaint(out, mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
        except Exception:
            # inpaint 失败则退回主色填充
            ys, xs = np.where(mask > 0)
            if len(xs):
                # 粗略：用全局近白
                out[mask > 0] = (255, 255, 255)

    return Image.fromarray(out)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: FontLike) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap_text_to_width(
    text: str,
    font: FontLike,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """按像素宽度对中文/混排自动换行。"""
    text = (text or "").replace("\r", "").strip()
    if not text:
        return []
    # 先按原有换行切段，再对每段按宽度折行
    paragraphs = text.split("\n")
    lines: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        buf = ""
        for ch in para:
            trial = buf + ch
            tw, _ = _text_size(draw, trial, font)
            if tw <= max_width or not buf:
                buf = trial
            else:
                lines.append(buf)
                buf = ch
        if buf:
            lines.append(buf)
    return lines or [text]


def fit_font_and_lines(
    text: str,
    box_w: int,
    box_h: int,
    *,
    font_path: Optional[str] = None,
    min_size: int = 12,
    max_size: int = 48,
    line_spacing: float = 1.15,
) -> tuple[FontLike, list[str], int]:
    """在框内找最大可用字号，返回 (font, lines, line_height)。"""
    # 用临时图测尺寸
    probe = Image.new("RGB", (max(8, box_w), max(8, box_h)), (255, 255, 255))
    draw = ImageDraw.Draw(probe)
    inner_w = max(8, box_w - 8)
    inner_h = max(8, box_h - 8)

    best_font = load_font(min_size, font_path)
    best_lines = wrap_text_to_width(text, best_font, inner_w, draw)
    best_lh = min_size

    for size in range(max_size, min_size - 1, -1):
        font = load_font(size, font_path)
        lines = wrap_text_to_width(text, font, inner_w, draw)
        if not lines:
            continue
        # 行高
        _, th = _text_size(draw, "国漢Hg", font)
        lh = max(th, int(size * line_spacing))
        total_h = lh * len(lines)
        max_line_w = 0
        for ln in lines:
            tw, _ = _text_size(draw, ln, font)
            max_line_w = max(max_line_w, tw)
        if total_h <= inner_h and max_line_w <= inner_w:
            return font, lines, lh
        best_font, best_lines, best_lh = font, lines, lh
    return best_font, best_lines, best_lh


def draw_bubbles_text(
    image: Image.Image,
    bubbles: Sequence[BubbleLike],
    *,
    font_path: Optional[str] = None,
    fill: tuple[int, int, int] = (20, 20, 20),
    stroke_fill: tuple[int, int, int] = (255, 255, 255),
    stroke_width: int = 2,
) -> Image.Image:
    """在擦除后的图上嵌中文（框内居中）。"""
    img = image.convert("RGB")
    draw = ImageDraw.Draw(img)
    for bub in bubbles:
        text = (bub.translated or "").strip()
        if not text or text.startswith("[翻译失败"):
            continue
        x0, y0, x1, y1 = _clamp_box(bub.x0, bub.y0, bub.x1, bub.y1, img.width, img.height, pad=0)
        bw, bh = max(8, x1 - x0), max(8, y1 - y0)
        font, lines, lh = fit_font_and_lines(text, bw, bh, font_path=font_path)
        if not lines:
            continue
        total_h = lh * len(lines)
        ty = y0 + max(0, (bh - total_h) // 2)
        for i, ln in enumerate(lines):
            tw, _ = _text_size(draw, ln, font)
            tx = x0 + max(0, (bw - tw) // 2)
            draw.text(
                (tx, ty + i * lh),
                ln,
                font=font,
                fill=fill,
                stroke_width=stroke_width if stroke_width > 0 else 0,
                stroke_fill=stroke_fill,
            )
    return img


def localize_image_pixels(
    image: Image.Image,
    bubbles: Sequence[BubbleLike],
    *,
    font_path: Optional[str] = None,
    erase_pad: int = 4,
) -> Image.Image:
    """擦字 + 嵌字一站式。"""
    active = [b for b in bubbles if (b.translated or "").strip() and not b.translated.startswith("[翻译失败")]
    if not active:
        return image.convert("RGB")
    erased = erase_bubbles(image, active, pad=erase_pad)
    return draw_bubbles_text(erased, active, font_path=font_path)
