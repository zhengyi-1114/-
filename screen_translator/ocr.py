"""OCR 文字识别（多策略增强，提升截图识别率）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

ImageInput = Union[Image.Image, np.ndarray, str, Path]


@lru_cache(maxsize=1)
def _get_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR

    # 降低阈值，更容易检出小字 / 低对比度文字
    return RapidOCR(
        text_score=0.35,
        Det={"thresh": 0.2, "box_thresh": 0.35, "unclip_ratio": 1.8},
    )


def load_image(image: ImageInput) -> Image.Image:
    """把各种输入统一成 RGB PIL Image。"""
    if image is None:
        raise ValueError("未提供图片")

    if isinstance(image, Image.Image):
        img = image
    elif isinstance(image, (str, Path)):
        img = Image.open(image)
    elif isinstance(image, np.ndarray):
        arr = image
        if arr.dtype != np.uint8:
            max_v = float(arr.max()) if arr.size else 0.0
            if max_v <= 1.0:
                arr = (np.clip(arr, 0.0, 1.0) * 255.0).astype(np.uint8)
            else:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
        if arr.ndim == 2:
            img = Image.fromarray(arr, mode="L")
        elif arr.ndim == 3 and arr.shape[-1] == 4:
            img = Image.fromarray(arr, mode="RGBA")
        elif arr.ndim == 3 and arr.shape[-1] == 3:
            img = Image.fromarray(arr, mode="RGB")
        else:
            raise ValueError(f"无法解析的数组形状: {arr.shape}")
    else:
        raise TypeError(f"不支持的图片类型: {type(image)}")

    if img.mode in ("RGBA", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        alpha = img.split()[-1]
        background.paste(img.convert("RGBA"), mask=alpha)
        return background
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def image_to_numpy(image: Image.Image) -> np.ndarray:
    if image.mode != "RGB":
        image = image.convert("RGB")
    return np.array(image)


def _run_ocr(image: Image.Image) -> str:
    engine = _get_engine()
    result, _ = engine(image_to_numpy(image))
    if not result:
        return ""
    lines: list[str] = []
    for item in result:
        if len(item) >= 2 and item[1]:
            text = str(item[1]).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _is_mostly_dark(image: Image.Image) -> bool:
    gray = ImageOps.grayscale(image.resize((64, 64)))
    return float(np.mean(np.array(gray))) < 90


def _variants(image: Image.Image) -> list[Image.Image]:
    """生成多种增强版本，提高难例召回。"""
    w, h = image.size
    variants: list[Image.Image] = [image]

    # 小图放大
    long_side = max(w, h)
    if long_side < 900:
        scale = max(2, int(1200 / long_side) + 1)
        up = image.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
        variants.append(up)
    elif long_side < 1400:
        up = image.resize((int(w * 1.5), int(h * 1.5)), Image.Resampling.LANCZOS)
        variants.append(up)

    base = variants[-1]
    contrast = ImageEnhance.Contrast(base).enhance(1.8)
    variants.append(contrast)
    sharp = ImageEnhance.Sharpness(contrast).enhance(1.6)
    variants.append(sharp)

    # 深色界面：反色后再识别一次
    if _is_mostly_dark(image):
        inverted = ImageOps.invert(base.convert("RGB"))
        variants.append(inverted)
        variants.append(ImageEnhance.Contrast(inverted).enhance(1.5))

    # 灰度 + 自动对比度
    gray = ImageOps.autocontrast(ImageOps.grayscale(base)).convert("RGB")
    variants.append(gray)

    return variants


def recognize_text(image: ImageInput) -> str:
    """识别图片中的文字；自动尝试多种增强策略。"""
    img = load_image(image)
    best = ""
    best_score = -1.0

    for i, variant in enumerate(_variants(img)):
        text = _run_ocr(variant)
        if not text:
            continue
        # 分数：更长更好；有空格/换行的可读结果加分
        score = float(len(text)) + 0.35 * text.count(" ") + 0.5 * text.count("\n")
        if score > best_score:
            best = text
            best_score = score
        # 前两个变体（原图/放大）都试过且结果够长，可提前结束
        if i >= 1 and len(best) >= 40:
            break
    return best
