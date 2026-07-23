"""OCR 文字识别：中英用 RapidOCR，韩/日用 EasyOCR。"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

# 允许网页漫画这类超长截图
Image.MAX_IMAGE_PIXELS = None

ImageInput = Union[Image.Image, np.ndarray, str, Path]

# 源语言 / OCR 语言 → 引擎
OCR_LANG_ALIASES = {
    "auto": "auto",
    "zh": "ch",
    "zh-CN": "ch",
    "zh-TW": "ch",
    "en": "ch",  # RapidOCR 中英模型足够
    "ch": "ch",
    "ko": "ko",
    "ja": "ja",
    "jp": "ja",
}

# 超过该高度则切片识别（网页漫画全页截图）
TALL_IMAGE_THRESHOLD = 2500
TILE_HEIGHT = 1800
TILE_OVERLAP = 200

HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def normalize_ocr_lang(lang: Optional[str]) -> str:
    if not lang:
        return "auto"
    return OCR_LANG_ALIASES.get(lang, lang)


@lru_cache(maxsize=1)
def _get_rapid_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR(
        text_score=0.35,
        Det={"thresh": 0.2, "box_thresh": 0.35, "unclip_ratio": 1.8},
    )


@lru_cache(maxsize=4)
def _get_easy_reader(langs_key: str) -> Any:
    import easyocr

    langs = langs_key.split(",")
    # 首次会下载模型，稍慢
    return easyocr.Reader(langs, gpu=False, verbose=False)


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


def _is_mostly_dark(image: Image.Image) -> bool:
    gray = ImageOps.grayscale(image.resize((64, 64)))
    return float(np.mean(np.array(gray))) < 90


def _variants(image: Image.Image) -> list[Image.Image]:
    w, h = image.size
    variants: list[Image.Image] = [image]
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

    if _is_mostly_dark(image):
        inverted = ImageOps.invert(base.convert("RGB"))
        variants.append(inverted)
        variants.append(ImageEnhance.Contrast(inverted).enhance(1.5))

    gray = ImageOps.autocontrast(ImageOps.grayscale(base)).convert("RGB")
    variants.append(gray)
    return variants


def _run_rapid(image: Image.Image) -> str:
    engine = _get_rapid_engine()
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


def _run_easy(image: Image.Image, langs: list[str]) -> str:
    reader = _get_easy_reader(",".join(langs))
    result = reader.readtext(image_to_numpy(image), detail=1, paragraph=False)
    if not result:
        return ""

    def sort_key(item: Any) -> tuple[float, float]:
        box = item[0]
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        return (float(min(ys)), float(min(xs)))

    lines: list[str] = []
    for item in sorted(result, key=sort_key):
        text = str(item[1]).strip()
        conf = float(item[2]) if len(item) > 2 else 1.0
        if text and conf >= 0.25:
            lines.append(text)
    return "\n".join(lines)


def _score_text(text: str, prefer: str = "auto") -> float:
    if not text:
        return -1.0
    score = float(len(text)) + 0.35 * text.count(" ") + 0.5 * text.count("\n")
    hangul = len(HANGUL_RE.findall(text))
    kana = len(KANA_RE.findall(text))
    cjk = len(CJK_RE.findall(text))
    if prefer == "ko":
        score += hangul * 3.0
    elif prefer == "ja":
        score += (kana + cjk) * 2.0
    elif prefer == "ch":
        score += cjk * 2.0
    else:
        score += hangul * 2.0 + kana * 1.5 + cjk * 1.0
    return score


def _ocr_once(image: Image.Image, engine: str) -> str:
    """单次识别（给超长图切片用，避免每种增强都跑一遍）。"""
    if engine == "ko":
        return _run_easy(image, ["ko", "en"])
    if engine == "ja":
        return _run_easy(image, ["ja", "en"])
    return _run_rapid(image)


def _recognize_with_engine(image: Image.Image, engine: str) -> str:
    best = ""
    best_score = -1.0
    prefer = engine if engine in {"ko", "ja", "ch"} else "auto"

    for i, variant in enumerate(_variants(image)):
        text = _ocr_once(variant, engine)
        score = _score_text(text, prefer=prefer)
        if score > best_score:
            best = text
            best_score = score
        if i >= 1 and len(best) >= 40 and best_score > 20:
            break
    return best


def _iter_tiles(image: Image.Image) -> list[Image.Image]:
    w, h = image.size
    if h <= TALL_IMAGE_THRESHOLD:
        return [image]

    step = max(100, TILE_HEIGHT - TILE_OVERLAP)
    tiles: list[Image.Image] = []
    top = 0
    while top < h:
        bottom = min(top + TILE_HEIGHT, h)
        tiles.append(image.crop((0, top, w, bottom)))
        if bottom >= h:
            break
        top += step
    return tiles


def _merge_tile_texts(parts: list[str]) -> str:
    """合并切片结果，去掉重叠区可能重复的末尾/开头行。"""
    merged: list[str] = []
    for part in parts:
        lines = [ln.strip() for ln in part.splitlines() if ln.strip()]
        if not lines:
            continue
        if not merged:
            merged.extend(lines)
            continue
        max_check = min(6, len(lines), len(merged))
        overlap = 0
        for k in range(max_check, 0, -1):
            if merged[-k:] == lines[:k]:
                overlap = k
                break
        merged.extend(lines[overlap:])
    return "\n".join(merged)


def _recognize_tall(image: Image.Image, engine: str) -> str:
    parts: list[str] = []
    for tile in _iter_tiles(image):
        gray = np.array(ImageOps.grayscale(tile.resize((64, 64))))
        if float(np.std(gray)) < 8.0:
            continue
        text = _ocr_once(tile, engine)
        if text.strip():
            parts.append(text.strip())
    return _merge_tile_texts(parts)


def detect_script(text: str) -> str:
    """根据已识别文本猜测主要文字体系。"""
    hangul = len(HANGUL_RE.findall(text))
    kana = len(KANA_RE.findall(text))
    cjk = len(CJK_RE.findall(text))
    if hangul >= max(3, kana, cjk):
        return "ko"
    if kana >= 3:
        return "ja"
    if cjk >= 3:
        return "zh-CN"
    return "en"


def recognize_text(image: ImageInput, lang: str = "auto") -> str:
    """
    识别图片文字。超长图（如网漫全页）会自动切片后识别再合并。

    lang:
      - auto: 先中英 RapidOCR，若几乎无有效汉字且像乱码，再尝试韩/日
      - ko / ja / ch / zh-CN / en: 指定引擎
    """
    img = load_image(image)
    ocr_lang = normalize_ocr_lang(lang)
    tall = img.height > TALL_IMAGE_THRESHOLD

    def run(engine: str) -> str:
        if tall:
            return _recognize_tall(img, engine)
        return _recognize_with_engine(img, engine)

    if ocr_lang == "ko":
        return run("ko")
    if ocr_lang == "ja":
        return run("ja")
    if ocr_lang == "ch":
        return run("ch")

    primary = run("ch")
    hangul = len(HANGUL_RE.findall(primary))
    cjk = len(CJK_RE.findall(primary))
    if hangul < 2 and cjk < 8 and len(primary) < 80 and not tall:
        korean = run("ko")
        if _score_text(korean, "ko") > _score_text(primary, "ch"):
            return korean
    return primary
