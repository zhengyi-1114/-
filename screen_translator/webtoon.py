"""网漫气泡定位：检测文本框 → 聚合成气泡 → 带上下文翻译。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from screen_translator.ocr import (
    TILE_HEIGHT,
    TILE_OVERLAP,
    _get_easy_reader,
    image_to_numpy,
    load_image,
    normalize_ocr_lang,
)
from screen_translator.translate import translate_text

Image.MAX_IMAGE_PIXELS = None

HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
NOISE_RE = re.compile(r"^[@#\d\W_]+$", re.UNICODE)


@dataclass
class TextBox:
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(1.0, self.y1 - self.y0)


@dataclass
class Bubble:
    index: int
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float
    boxes: list[TextBox]
    translated: str = ""
    context_used: str = ""


def _box_from_easy(item: Any, y_offset: float = 0.0) -> Optional[TextBox]:
    box, text, conf = item[0], str(item[1]).strip(), float(item[2])
    if not text:
        return None
    xs = [float(p[0]) for p in box]
    ys = [float(p[1]) for p in box]
    return TextBox(
        text=text,
        conf=conf,
        x0=min(xs),
        y0=min(ys) + y_offset,
        x1=max(xs),
        y1=max(ys) + y_offset,
    )


def _iter_tiles(image: Image.Image) -> Iterable[tuple[int, Image.Image]]:
    w, h = image.size
    step = max(100, TILE_HEIGHT - TILE_OVERLAP)
    top = 0
    while top < h:
        bottom = min(top + TILE_HEIGHT, h)
        yield top, image.crop((0, top, w, bottom))
        if bottom >= h:
            break
        top += step


def detect_text_boxes(
    image: Image.Image,
    lang: str = "ko",
    min_conf: float = 0.28,
) -> list[TextBox]:
    """对整页（可超长）检测文本框，返回页面绝对坐标。"""
    from PIL import ImageEnhance

    ocr_lang = normalize_ocr_lang(lang)
    if ocr_lang == "ja":
        langs = ["ja", "en"]
    elif ocr_lang in {"ch", "en"}:
        langs = ["ch_sim", "en"]
    else:
        langs = ["ko", "en"]

    reader = _get_easy_reader(",".join(langs))
    boxes: list[TextBox] = []
    scale = 1.35
    tiles = list(_iter_tiles(image))

    for ti, (y_off, tile) in enumerate(tiles, 1):
        gray = np.array(ImageOps.grayscale(tile.resize((64, 64))))
        if float(np.std(gray)) < 8.0:
            if ti % 10 == 0 or ti == len(tiles):
                print(f"[detect] {ti}/{len(tiles)} boxes={len(boxes)} (skip blank)", flush=True)
            continue
        # 适度放大 + 对比度，提高气泡字识别率
        up = tile.resize(
            (int(tile.width * scale), int(tile.height * scale)),
            Image.Resampling.LANCZOS,
        )
        up = ImageEnhance.Contrast(up).enhance(1.45)
        up = ImageEnhance.Sharpness(up).enhance(1.25)
        result = reader.readtext(image_to_numpy(up), detail=1, paragraph=False)
        for item in result:
            tb = _box_from_easy(item, y_offset=0.0)
            if tb is None or tb.conf < min_conf:
                continue
            # 映射回原图坐标
            tb = TextBox(
                text=_cleanup_ocr_ko(tb.text),
                conf=tb.conf,
                x0=tb.x0 / scale,
                y0=tb.y0 / scale + float(y_off),
                x1=tb.x1 / scale,
                y1=tb.y1 / scale + float(y_off),
            )
            if NOISE_RE.match(tb.text) and len(tb.text) <= 3:
                continue
            boxes.append(tb)
        if ti % 5 == 0 or ti == len(tiles):
            print(f"[detect] {ti}/{len(tiles)} boxes={len(boxes)}", flush=True)
    boxes.sort(key=lambda b: (b.y0, b.x0))
    return boxes


def _cleanup_ocr_ko(text: str) -> str:
    """常见 OCR 错字轻量修正（武侠网漫）。"""
    reps = {
        "순{이": "순혈이",
        "기파는": "기파는",
        "초얼공": "초절공",
        "본교름": "본교를",
        "머리블": "머리를",
        "하시움고": "하시옵고",
        "바람올": "바람을",
        "쇠고": "쐬고",
        "느껴야해": "느껴야 해",
        "구결올": "구결을",
        "무공올": "무공을",
        "내력올": "내력을",
        "본단올": "본단을",
        "나석올": "나섰을",
        "주변올": "주변을",
        "희망울": "희망을",
        "품지논": "품지는",
        "내 젓": "내 것",
        "느미비": "네 몸이",
        "긋이라": "곳이라",
        "햇지": "했지",
        "햇된": "했던",
        "출정올": "출정을",
        "모습올": "모습을",
        "공력올": "공력을",
        "위치틀": "위치를",
        "외유틀": "외유를",
        "너느": "너는",
        "암전히": "얌전히",
        "실어야 켓군": "실어야겠군",
        "자짙이": "재질이",
        "넓없어": "넓어야",
        "돌아보야켓논데": "돌아봐야겠는데",
        "마라굉렬공": "마라굉렬공",
        "멸마청강수": "멸마청강수",
        "혈염교": "혈염교",
        "태사시여": "태사시여",
    }
    out = text
    for a, b in reps.items():
        out = out.replace(a, b)
    return out


def _can_merge(a: TextBox, b: TextBox) -> bool:
    """判断两个文本框是否同属一个气泡。"""
    gap_y = b.y0 - a.y1
    # 垂直重叠或间距不大
    if gap_y > max(28.0, 0.9 * a.h):
        return False
    # 水平需大致同列/同气泡区域
    overlap_x = min(a.x1, b.x1) - max(a.x0, b.x0)
    width = max(a.x1 - a.x0, b.x1 - b.x0)
    if overlap_x <= 0 and abs(a.cx - b.cx) > max(120.0, 0.55 * width):
        return False
    return True


def group_bubbles(boxes: list[TextBox]) -> list[Bubble]:
    if not boxes:
        return []

    clusters: list[list[TextBox]] = [[boxes[0]]]
    for box in boxes[1:]:
        last = clusters[-1]
        if _can_merge(last[-1], box) or any(_can_merge(prev, box) for prev in last[-2:]):
            last.append(box)
        else:
            clusters.append([box])

    bubbles: list[Bubble] = []
    for idx, cluster in enumerate(clusters, 1):
        cluster = sorted(cluster, key=lambda b: (b.y0, b.x0))
        # 同行合并用空格，换行用换行
        lines: list[str] = []
        cur: list[TextBox] = [cluster[0]]
        for b in cluster[1:]:
            prev = cur[-1]
            same_line = abs(b.cy - prev.cy) <= max(12.0, 0.45 * prev.h)
            if same_line:
                cur.append(b)
            else:
                lines.append(" ".join(x.text for x in cur))
                cur = [b]
        lines.append(" ".join(x.text for x in cur))
        text = "\n".join(lines).strip()
        # 过滤页脚推荐、纯标签
        if not text or (not HANGUL_RE.search(text) and len(text) < 4):
            # 保留含韩文或较长文本
            if not HANGUL_RE.search(text):
                continue
        bubbles.append(
            Bubble(
                index=idx,
                text=text,
                conf=float(sum(b.conf for b in cluster) / len(cluster)),
                x0=min(b.x0 for b in cluster),
                y0=min(b.y0 for b in cluster),
                x1=max(b.x1 for b in cluster),
                y1=max(b.y1 for b in cluster),
                boxes=cluster,
            )
        )
    # 重新编号
    for i, b in enumerate(bubbles, 1):
        b.index = i
    return bubbles


def _is_likely_ui_noise(text: str) -> bool:
    t = text.strip()
    if t.startswith("#") and HANGUL_RE.search(t) is None:
        return True
    if re.fullmatch(r"\d+화.*", t):
        return True
    if t in {"PSG", "더불다", "같이 장불래?"}:
        return True
    return False


def translate_bubbles(
    bubbles: list[Bubble],
    *,
    source: str = "ko",
    target: str = "zh-CN",
    backend: str = "doubao",
    context_window: int = 2,
    skip_noise: bool = True,
) -> list[Bubble]:
    """
    按气泡整段翻译（先聚合对白，再翻译，避免碎行乱译）。
    若后端是通用对话模型，会附带上文；seed-translation 则只译当前气泡全文。
    """
    import os

    model = os.getenv("DOUBAO_MODEL", "")
    use_seed = backend == "doubao" and "translation" in model
    recent: list[str] = []

    for bubble in bubbles:
        if skip_noise and _is_likely_ui_noise(bubble.text):
            bubble.translated = ""
            continue

        if use_seed or context_window <= 0 or not recent:
            prompt = bubble.text
            bubble.context_used = ""
        else:
            ctx = " / ".join(recent[-context_window:])
            prompt = (
                f"上文：{ctx}\n"
                f"请只翻译下面这句对白，不要翻译上文，不要解释：\n"
                f"{bubble.text}"
            )
            bubble.context_used = ctx

        try:
            raw = translate_text(prompt, source=source, target=target, backend=backend)
        except Exception as exc:
            bubble.translated = f"[翻译失败: {exc}]"
            continue

        translated = raw.strip()
        if not use_seed:
            for marker in ("请只翻译下面这句对白", "不要翻译上文", "当前：", "对白："):
                if marker in translated:
                    translated = translated.split(marker)[-1].strip(" ：:\n")
            if "\n" in translated:
                parts = [p.strip() for p in translated.splitlines() if p.strip()]
                cjk_parts = [p for p in parts if re.search(r"[\u4e00-\u9fff]", p)]
                translated = "\n".join(cjk_parts or parts)

        bubble.translated = translated
        if bubble.translated and not bubble.translated.startswith("[翻译失败"):
            recent.append(bubble.text.replace("\n", " "))
        time.sleep(0.12)
    return bubbles


def render_preview(
    image: Image.Image,
    bubbles: list[Bubble],
    out_path: Path,
    max_height: int = 4000,
) -> Path:
    """导出带气泡框和译文的预览条（页面顶部一段）。"""
    h = min(image.height, max_height)
    crop = image.crop((0, 0, image.width, h)).convert("RGBA")
    overlay = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 18
        )
    except OSError:
        font = ImageFont.load_default()

    for b in bubbles:
        if b.y0 > h:
            continue
        draw.rectangle(
            [b.x0, b.y0, b.x1, min(b.y1, h)],
            outline=(45, 212, 168, 220),
            width=2,
        )
        label = (b.translated or b.text).replace("\n", " ")
        if len(label) > 28:
            label = label[:28] + "…"
        ty = max(0, b.y0 - 22)
        draw.rectangle([b.x0, ty, min(b.x0 + 8 * len(label) + 12, crop.width), ty + 20], fill=(15, 23, 20, 180))
        draw.text((b.x0 + 4, ty + 1), label, fill=(231, 240, 235, 255), font=font)

    merged = Image.alpha_composite(crop, overlay).convert("RGB")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.save(out_path)
    return out_path


def bubbles_to_paired_text(bubbles: list[Bubble]) -> str:
    lines = ["网漫气泡对照（定位聚合 + 上下文翻译）", f"共 {len(bubbles)} 个气泡\n"]
    for b in bubbles:
        if not b.translated:
            continue
        lines.append(f"{b.index:03d}  @y={int(b.y0)}")
        lines.append(f"韩文：{b.text.replace(chr(10), ' / ')}")
        lines.append(f"中文：{b.translated.replace(chr(10), ' / ')}")
        if b.context_used:
            lines.append(f"上文：{b.context_used}")
        lines.append("")
    return "\n".join(lines)


def process_webtoon(
    image_path: str | Path,
    *,
    source: str = "ko",
    target: str = "zh-CN",
    backend: str = "doubao",
    out_dir: str | Path = "/opt/cursor/artifacts",
) -> dict[str, Any]:
    image_path = Path(image_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    img = load_image(image_path)
    print(f"[webtoon] image={img.size}", flush=True)
    boxes = detect_text_boxes(img, lang=source)
    print(f"[webtoon] boxes={len(boxes)}", flush=True)
    bubbles = group_bubbles(boxes)
    print(f"[webtoon] bubbles={len(bubbles)} translating...", flush=True)

    # 增量翻译并落盘
    paired_path = out_dir / "webtoon-bubbles-对照.txt"
    ws_paired = Path("/workspace/webtoon-bubbles-对照.txt")
    done: list[Bubble] = []
    for i, bubble in enumerate(bubbles, 1):
        translate_bubbles(
            [bubble],
            source=source,
            target=target,
            backend=backend,
            context_window=0 if "translation" in __import__("os").getenv("DOUBAO_MODEL", "") else 2,
        )
        done.append(bubble)
        if i % 5 == 0 or i == len(bubbles):
            paired = bubbles_to_paired_text(done)
            # 临时修正 index 显示为全局
            paired_path.write_text(paired, encoding="utf-8")
            ws_paired.write_text(paired, encoding="utf-8")
            print(
                f"[webtoon] {i}/{len(bubbles)} "
                f"ok={sum(1 for b in done if b.translated and not b.translated.startswith('[翻译失败'))}",
                flush=True,
            )

    bubbles = done
    paired = bubbles_to_paired_text(bubbles)
    paired_path.write_text(paired, encoding="utf-8")
    ws_paired.write_text(paired, encoding="utf-8")

    json_path = out_dir / "webtoon-bubbles.json"
    payload = [
        {
            **{k: v for k, v in asdict(b).items() if k != "boxes"},
            "box_count": len(b.boxes),
        }
        for b in bubbles
    ]
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    preview_path = out_dir / "webtoon-bubbles-preview.png"
    render_preview(img, bubbles, preview_path)

    return {
        "boxes": len(boxes),
        "bubbles": len(bubbles),
        "translated": sum(
            1
            for b in bubbles
            if b.translated and not b.translated.startswith("[翻译失败")
        ),
        "paired_path": str(paired_path),
        "json_path": str(json_path),
        "preview_path": str(preview_path),
    }
