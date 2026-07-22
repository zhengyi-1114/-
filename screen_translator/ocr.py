"""OCR 文字识别。"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image


@lru_cache(maxsize=1)
def _get_engine() -> Any:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def image_to_numpy(image: Image.Image) -> np.ndarray:
    """将 PIL Image 转为 OCR 可用的 BGR/RGB numpy 数组。"""
    if image.mode != "RGB":
        image = image.convert("RGB")
    return np.array(image)


def recognize_text(image: Image.Image) -> str:
    """识别图片中的文字，按阅读顺序拼接为多行文本。"""
    engine = _get_engine()
    arr = image_to_numpy(image)
    result, _ = engine(arr)

    if not result:
        return ""

    lines: list[str] = []
    for item in result:
        # RapidOCR 返回: [box, text, score]
        if len(item) >= 2 and item[1]:
            lines.append(str(item[1]).strip())

    return "\n".join(line for line in lines if line)