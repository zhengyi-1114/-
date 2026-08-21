"""用已汉化站点对照未汉化站点：OCR → 顺序对齐 → 输出参考汉化对照。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from screen_translator.ocr import normalize_ocr_lang
from screen_translator.webtoon import Bubble, TextBox, detect_text_boxes, group_bubbles

Image.MAX_IMAGE_PIXELS = None

ProgressCb = Callable[[str], None]

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")

DISCLAIMER_RE = re.compile(
    r"纯属虚构|请勿代入|正确的价值观|切勿模仿|本作品|著作权|版权所有|咚漫|WEBTOON",
    re.I,
)


@dataclass
class LineItem:
    index: int
    text: str
    cut: str
    y0: float
    conf: float


@dataclass
class AlignedPair:
    index: int
    ko: str
    zh_ref: str
    zh_mt: str = ""
    source: str = "ref"  # ref | mt | mixed
    ko_cut: str = ""
    zh_cut: str = ""


@dataclass
class AlignResult:
    pairs: list[AlignedPair] = field(default_factory=list)
    ko_count: int = 0
    zh_count: int = 0
    paired_path: Optional[Path] = None
    json_path: Optional[Path] = None


def _log(cb: Optional[ProgressCb], msg: str) -> None:
    if cb:
        cb(msg)


def group_bubbles_zh(boxes: list[TextBox]) -> list[Bubble]:
    """中文气泡聚合（不过滤非韩文）。"""
    if not boxes:
        return []
    from screen_translator.webtoon import _can_merge

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
        lines: list[str] = []
        cur: list[TextBox] = [cluster[0]]
        for b in cluster[1:]:
            prev = cur[-1]
            same_line = abs(b.cy - prev.cy) <= max(12.0, 0.45 * prev.h)
            if same_line:
                cur.append(b)
            else:
                lines.append("".join(x.text for x in cur))
                cur = [b]
        lines.append("".join(x.text for x in cur))
        text = "\n".join(lines).strip()
        if not text:
            continue
        if not CJK_RE.search(text) and len(text) < 2:
            continue
        if DISCLAIMER_RE.search(text) and len(text) > 12:
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
    for i, b in enumerate(bubbles, 1):
        b.index = i
    return bubbles


def ocr_cuts_folder(
    folder: str | Path,
    *,
    lang: str,
    progress: Optional[ProgressCb] = None,
    cache_path: Optional[str | Path] = None,
) -> list[LineItem]:
    folder = Path(folder)
    cache_path = Path(cache_path) if cache_path else folder / f"ocr_{normalize_ocr_lang(lang)}.json"
    if cache_path.is_file():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        _log(progress, f"[ocr] cache hit {cache_path} items={len(data)}")
        return [LineItem(**x) for x in data]

    paths = sorted(folder.glob("*.jpg")) + sorted(folder.glob("*.png"))
    # 去重同名
    seen = set()
    uniq = []
    for p in paths:
        if p.name in seen:
            continue
        seen.add(p.name)
        uniq.append(p)

    items: list[LineItem] = []
    y_base = 0.0
    ocr_lang = normalize_ocr_lang(lang)
    for i, p in enumerate(uniq, 1):
        img = Image.open(p).convert("RGB")
        boxes = detect_text_boxes(img, lang=ocr_lang)
        if ocr_lang in {"ch", "zh", "zh-CN"}:
            bubbles = group_bubbles_zh(boxes)
        else:
            bubbles = group_bubbles(boxes)
        for b in bubbles:
            items.append(
                LineItem(
                    index=0,
                    text=b.text,
                    cut=p.name,
                    y0=y_base + b.y0,
                    conf=b.conf,
                )
            )
        y_base += float(img.height) + 40.0
        if i == 1 or i % 10 == 0 or i == len(uniq):
            _log(progress, f"[ocr:{ocr_lang}] {i}/{len(uniq)} bubbles={len(items)} file={p.name}")

    for i, it in enumerate(items, 1):
        it.index = i
    cache_path.write_text(
        json.dumps([asdict(x) for x in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log(progress, f"[ocr] saved cache {cache_path}")
    return items


def align_by_order(
    ko_items: list[LineItem],
    zh_items: list[LineItem],
) -> list[AlignedPair]:
    """按阅读顺序比例对齐（切图数量不同也能用）。"""
    if not ko_items:
        return []
    if not zh_items:
        return [
            AlignedPair(index=i, ko=k.text, zh_ref="", source="mt", ko_cut=k.cut)
            for i, k in enumerate(ko_items, 1)
        ]

    pairs: list[AlignedPair] = []
    n_ko, n_zh = len(ko_items), len(zh_items)
    for i, k in enumerate(ko_items):
        # 比例映射到中文序列
        j = int(round(i * (n_zh - 1) / max(1, n_ko - 1))) if n_ko > 1 else 0
        j = max(0, min(n_zh - 1, j))
        z = zh_items[j]
        pairs.append(
            AlignedPair(
                index=i + 1,
                ko=k.text,
                zh_ref=z.text,
                source="ref",
                ko_cut=k.cut,
                zh_cut=z.cut,
            )
        )
    return pairs


def align_by_mt_bridge(
    ko_items: list[LineItem],
    zh_items: list[LineItem],
    *,
    bridge_backend: str = "google",
    min_score: float = 0.28,
    window: int = 12,
    progress: Optional[ProgressCb] = None,
) -> list[AlignedPair]:
    """机译桥接：韩文→机译中文→模糊匹配汉化站 OCR。"""
    from difflib import SequenceMatcher

    from screen_translator.translate import translate_text

    zh_items = [
        z
        for z in zh_items
        if z.text.strip() and not re.search(r"未经授权|法律责任|版权所有", z.text)
    ]
    if not ko_items:
        return []
    if not zh_items:
        return align_by_order(ko_items, [])

    pairs: list[AlignedPair] = []
    used: set[int] = set()
    n_ko, n_zh = len(ko_items), len(zh_items)

    for i, k in enumerate(ko_items):
        try:
            mt = translate_text(
                k.text, source="ko", target="zh-CN", backend=bridge_backend
            )
        except Exception as exc:
            _log(progress, f"[bridge] mt fail #{i+1}: {exc}")
            mt = ""

        center = int(i * (n_zh - 1) / max(1, n_ko - 1)) if n_ko > 1 else 0
        best_j = None
        best_r = 0.0
        mt_flat = mt.replace("\n", "")
        for j in range(max(0, center - window), min(n_zh, center + window + 1)):
            if j in used and len(zh_items[j].text) > 6:
                continue
            zh_flat = zh_items[j].text.replace("\n", "")
            r = SequenceMatcher(None, mt_flat, zh_flat).ratio()
            a, b = set(mt_flat), set(zh_flat)
            if a and b:
                r = max(r, len(a & b) / max(1, len(a | b)) * 0.9)
            if r > best_r:
                best_r = r
                best_j = j

        if best_j is not None and best_r >= min_score:
            z = zh_items[best_j]
            used.add(best_j)
            pairs.append(
                AlignedPair(
                    index=i + 1,
                    ko=k.text,
                    zh_ref=z.text,
                    zh_mt=mt,
                    source="ref",
                    ko_cut=k.cut,
                    zh_cut=z.cut,
                )
            )
        else:
            z = zh_items[max(0, min(n_zh - 1, center))]
            pairs.append(
                AlignedPair(
                    index=i + 1,
                    ko=k.text,
                    zh_ref=z.text,
                    zh_mt=mt,
                    source="approx",
                    ko_cut=k.cut,
                    zh_cut=z.cut,
                )
            )

        if i == 0 or (i + 1) % 15 == 0 or i + 1 == n_ko:
            _log(progress, f"[bridge] {i+1}/{n_ko}")

    return pairs


def fill_mt(
    pairs: list[AlignedPair],
    *,
    backend: str = "doubao",
    progress: Optional[ProgressCb] = None,
    every: int = 1,
) -> list[AlignedPair]:
    """补充机译字段（若桥接阶段未写入）。"""
    from screen_translator.translate import translate_text

    for i, p in enumerate(pairs, 1):
        if not p.ko.strip():
            continue
        if p.zh_mt.strip():
            continue
        if every > 1 and (i % every) != 0 and p.zh_ref:
            continue
        try:
            mt = translate_text(p.ko, source="ko", target="zh-CN", backend=backend)
            p.zh_mt = mt
            if not p.zh_ref.strip():
                p.zh_ref = mt
                p.source = "mt"
            elif p.source == "ref":
                p.source = "mixed"
        except Exception as exc:
            _log(progress, f"[mt] fail #{i}: {exc}")
        if i == 1 or i % 10 == 0 or i == len(pairs):
            _log(progress, f"[mt] {i}/{len(pairs)}")
    return pairs

def write_outputs(
    pairs: list[AlignedPair],
    out_prefix: str | Path,
) -> AlignResult:
    out_prefix = Path(out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    paired = out_prefix.with_name(out_prefix.name + "-对照.txt")
    js = out_prefix.with_name(out_prefix.name + "-align.json")

    lines = [
        "双语对照汉化（参考站对齐）",
        "说明：zh_ref=汉化站 OCR；zh_mt=机译（豆包等）；优先采用 zh_ref 作正式汉化。",
        "",
    ]
    for p in pairs:
        lines.append(f"{p.index:03d}  [{p.source}]  ko:{p.ko_cut}  zh:{p.zh_cut}")
        lines.append(f"原文：{p.ko}")
        lines.append(f"汉化参考：{p.zh_ref}")
        if p.zh_mt and p.zh_mt != p.zh_ref:
            lines.append(f"机译对照：{p.zh_mt}")
        lines.append("")
    paired.write_text("\n".join(lines), encoding="utf-8")
    js.write_text(
        json.dumps([asdict(p) for p in pairs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return AlignResult(
        pairs=pairs,
        ko_count=len(pairs),
        zh_count=len({p.zh_cut for p in pairs if p.zh_cut}),
        paired_path=paired,
        json_path=js,
    )


def localize_with_reference(
    ko_cuts: str | Path,
    zh_cuts: str | Path,
    *,
    out_prefix: str | Path = "ep-ref",
    method: str = "bridge",
    do_mt: bool = False,
    backend: str = "doubao",
    bridge_backend: str = "google",
    progress: Optional[ProgressCb] = None,
) -> AlignResult:
    ko_items = ocr_cuts_folder(ko_cuts, lang="ko", progress=progress)
    zh_items = ocr_cuts_folder(zh_cuts, lang="ch", progress=progress)
    _log(progress, f"[align] ko_bubbles={len(ko_items)} zh_bubbles={len(zh_items)} method={method}")
    if method == "order":
        pairs = align_by_order(ko_items, zh_items)
        if do_mt:
            fill_mt(pairs, backend=backend, progress=progress)
    else:
        pairs = align_by_mt_bridge(
            ko_items,
            zh_items,
            bridge_backend=bridge_backend,
            progress=progress,
        )
        if do_mt:
            fill_mt(pairs, backend=backend, progress=progress)
    return write_outputs(pairs, out_prefix)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="未汉化站 + 已汉化站 对照对齐汉化")
    p.add_argument("--ko-cuts", required=True, help="韩文/未汉化切图目录")
    p.add_argument("--zh-cuts", required=True, help="中文/已汉化切图目录")
    p.add_argument("-o", "--output", default="ep-ref", help="输出前缀")
    p.add_argument(
        "--method",
        choices=["bridge", "order"],
        default="bridge",
        help="bridge=机译桥接匹配汉化站（推荐）；order=按顺序比例对齐",
    )
    p.add_argument("--no-mt", action="store_true", help="桥接时已含机译；此开关保留兼容")
    p.add_argument("--backend", default="doubao", help="额外机译后端（--method order 时）")
    p.add_argument("--bridge-backend", default="google", help="桥接用机译后端")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    progress = lambda m: print(m, file=sys.stderr)
    try:
        result = localize_with_reference(
            args.ko_cuts,
            args.zh_cuts,
            out_prefix=args.output,
            method=args.method,
            do_mt=False,
            backend=args.backend,
            bridge_backend=args.bridge_backend,
            progress=progress,
        )
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    print(result.paired_path)
    print(result.json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
