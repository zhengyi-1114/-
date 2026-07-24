"""网页懒加载滚屏截图（可库调用，也可命令行单独使用）。

对 Naver 等网漫站点，默认改为「收集切图 URL → 下载 → 竖向拼接」，
避免 full_page 截图时懒加载卸图造成中间空白、整页不全。
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional, Sequence
from urllib.parse import urlparse

ProgressCb = Callable[[str], None]

DEFAULT_DISMISS = ("확인", "동의", "닫기", "Close", "OK", "Agree", "Accept")

# JPEG 单边像素上限；超长图改存 PNG / PDF / 分段
JPEG_MAX_DIM = 65000


@dataclass
class CaptureResult:
    path: Path
    url: str
    width: int
    height: int
    scroll_rounds: int
    final_page_height: int
    file_size: int
    method: str = "screenshot"
    parts: Optional[list[Path]] = None
    preview_path: Optional[Path] = None
    pdf_path: Optional[Path] = None
    html_path: Optional[Path] = None
    zip_path: Optional[Path] = None
    cut_count: int = 0
    cut_paths: list[Path] = field(default_factory=list)


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


def collect_comic_image_urls(page) -> list[str]:
    """从页面收集网漫切图 URL（优先 data-src，过滤占位透明图）。"""
    urls = page.evaluate(
        """
        () => {
          const nodes = Array.from(
            document.querySelectorAll('img.toon_image, img[id^=toon_], .wt_viewer img, #comic_view img')
          );
          const fallback = nodes.length
            ? nodes
            : Array.from(document.images).filter(img => {
                const u = img.getAttribute('data-src') || img.currentSrc || img.src || '';
                return /image-comic|mobilewebimg|webtoon|comic/i.test(u);
              });
          const out = [];
          const seen = new Set();
          for (const img of fallback) {
            const u = (img.getAttribute('data-src')
              || img.getAttribute('data-original')
              || img.currentSrc
              || img.src
              || '').trim();
            if (!u) continue;
            if (/bg_transparency|spacer|blank|data:image\\/gif/i.test(u)) continue;
            if (seen.has(u)) continue;
            seen.add(u);
            out.push(u);
          }
          return out;
        }
        """
    )
    return list(urls or [])


def download_image_bytes(
    url: str,
    *,
    referer: str,
    user_agent: str,
    timeout: int = 60,
) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Referer": referer,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def stitch_images(images: Sequence["Image.Image"]) -> "Image.Image":
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    if not images:
        raise ValueError("没有可拼接的图片")
    width = max(im.width for im in images)
    scaled: list[Image.Image] = []
    for im in images:
        rgb = im.convert("RGB")
        if rgb.width != width:
            h = max(1, int(rgb.height * width / rgb.width))
            rgb = rgb.resize((width, h), Image.Resampling.LANCZOS)
        scaled.append(rgb)
    total_h = sum(im.height for im in scaled)
    canvas = Image.new("RGB", (width, total_h), (255, 255, 255))
    y = 0
    for im in scaled:
        canvas.paste(im, (0, y))
        y += im.height
    return canvas


def split_tall_image(
    image_path: str | Path,
    *,
    part_height: int = 2500,
    parts_dir: Optional[str | Path] = None,
    preview_height: int = 1800,
    quality: int = 88,
    progress: Optional[ProgressCb] = None,
) -> tuple[list[Path], Path]:
    """把超长图切成多段 JPG，并另存顶部预览，方便普通查看器打开。"""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    src = Path(image_path)
    out_dir = Path(parts_dir) if parts_dir else src.with_name(f"{src.stem}-parts")
    out_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as im:
        rgb = im.convert("RGB")
        w, h = rgb.size
        parts: list[Path] = []
        idx = 1
        for y in range(0, h, part_height):
            crop = rgb.crop((0, y, w, min(y + part_height, h)))
            part_path = out_dir / f"part-{idx:02d}.jpg"
            crop.save(part_path, quality=quality, optimize=True)
            parts.append(part_path)
            idx += 1

        preview = rgb.crop((0, 0, w, min(preview_height, h)))
        preview_path = src.with_name(f"{src.stem}-top.jpg")
        preview.save(preview_path, quality=90, optimize=True)

    _log(progress, f"[capture] split {len(parts)} parts -> {out_dir}")
    _log(progress, f"[capture] preview -> {preview_path}")
    return parts, preview_path


def write_zip_from_files(
    image_files: Sequence[str | Path],
    zip_path: str | Path,
    *,
    arcname_prefix: str = "cuts",
    progress: Optional[ProgressCb] = None,
) -> Path:
    """把图片打成 ZIP，方便下载到本机解压成普通 JPG/PNG。"""
    import zipfile

    zip_path = Path(zip_path)
    files = [Path(p) for p in image_files]
    if not files:
        raise ValueError("没有可打包的图片")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, arcname=f"{arcname_prefix}/{p.name}")
    _log(progress, f"[capture] zip ({len(files)} files) -> {zip_path}")
    return zip_path


def write_pdf_from_files(
    image_files: Sequence[str | Path],
    pdf_path: str | Path,
    *,
    progress: Optional[ProgressCb] = None,
) -> Path:
    """把多张图片写成「一页一张」的 PDF（嵌入原图，避免乱码/糊图）。"""
    pdf_path = Path(pdf_path)
    files = [str(Path(p)) for p in image_files]
    if not files:
        raise ValueError("没有可用于 PDF 的图片")
    try:
        import img2pdf

        pdf_path.write_bytes(img2pdf.convert(files))
    except Exception:
        # 回退：Pillow 一页一张（可能更大）
        from PIL import Image

        pages = [Image.open(p).convert("RGB") for p in files]
        pages[0].save(pdf_path, save_all=True, append_images=pages[1:])
        for im in pages:
            im.close()
    _log(progress, f"[capture] pdf ({len(files)} pages) -> {pdf_path}")
    return pdf_path


def write_html_viewer(
    image_files: Sequence[str | Path],
    html_path: str | Path,
    *,
    title: str = "网漫整话预览",
    progress: Optional[ProgressCb] = None,
) -> Path:
    """生成可滚动 HTML，用浏览器打开即可看完整话。"""
    html_path = Path(html_path)
    html_dir = html_path.parent.resolve()
    tags: list[str] = []
    for p in image_files:
        p = Path(p).resolve()
        try:
            rel = p.relative_to(html_dir).as_posix()
        except ValueError:
            rel = p.name
        tags.append(f'  <img src="{rel}" alt="{p.stem}" loading="lazy" />')
    html_path.write_text(
        f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
  body {{ margin:0; background:#111; }}
  .wrap {{ max-width:720px; margin:0 auto; padding:8px 0 40px; }}
  h1 {{ color:#eee; font:600 15px/1.4 sans-serif; padding:12px 14px; margin:0; }}
  img {{ display:block; width:100%; height:auto; background:#fff; }}
</style>
</head>
<body>
<div class="wrap">
<h1>{title} · 共 {len(image_files)} 张 · 请用浏览器打开本文件</h1>
{chr(10).join(tags)}
</div>
</body>
</html>
""",
        encoding="utf-8",
    )
    _log(progress, f"[capture] html -> {html_path}")
    return html_path


def save_long_image(
    image: "Image.Image",
    out_path: Path,
    *,
    also_pdf: bool = True,
    also_html: bool = True,
    split_when_taller_than: int = 4000,
    part_height: int = 2500,
    progress: Optional[ProgressCb] = None,
    skip_giant_raster: bool = False,
) -> tuple[Path, Optional[Path], Optional[list[Path]], Optional[Path], Optional[Path]]:
    """保存超长图：分段 JPG + PDF/HTML；超高单图很多查看器打不开。"""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rgb = image.convert("RGB")

    parts: Optional[list[Path]] = None
    preview_path: Optional[Path] = None
    # 先切段：这是最稳妥的查看方式之一
    if split_when_taller_than > 0 and rgb.height > split_when_taller_than:
        # 临时落盘再切，避免重复逻辑
        tmp = out_path.with_name(f".{out_path.stem}.tmp.png")
        rgb.save(tmp)
        parts, preview_path = split_tall_image(
            tmp,
            part_height=part_height,
            parts_dir=out_path.with_name(f"{out_path.stem}-parts"),
            progress=progress,
        )
        # 预览文件名对齐到正式 stem
        desired_preview = out_path.with_name(f"{out_path.stem}-top.jpg")
        if preview_path != desired_preview and preview_path.is_file():
            preview_path.replace(desired_preview)
            preview_path = desired_preview
        tmp.unlink(missing_ok=True)

    written_raster = False
    if not skip_giant_raster:
        if rgb.height >= JPEG_MAX_DIM or out_path.suffix.lower() in {".png", ""}:
            if out_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                out_path = out_path.with_suffix(".png")
            if rgb.height >= JPEG_MAX_DIM and out_path.suffix.lower() in {".jpg", ".jpeg"}:
                out_path = out_path.with_suffix(".png")
                _log(progress, f"[capture] height={rgb.height} > JPEG limit, save as PNG")
            if out_path.suffix.lower() == ".png":
                rgb.save(out_path, optimize=True)
            else:
                rgb.save(out_path, quality=92, optimize=True)
            written_raster = True
        else:
            rgb.save(out_path, quality=92, optimize=True)
            written_raster = True
    else:
        _log(progress, "[capture] skip giant single image (use PDF/HTML/parts instead)")
        # 若未写长图，用第一段作为 path 占位不合适；仍写 PNG 供程序读取，但标注很大
        # 程序侧仍需要整图时再写
        if out_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            out_path = out_path.with_suffix(".png")
        rgb.save(out_path, optimize=True)
        written_raster = True

    pdf_path: Optional[Path] = None
    html_path: Optional[Path] = None
    view_files: list[Path] = list(parts or [])
    if not view_files:
        # 无分段时用整图
        if written_raster:
            view_files = [out_path]

    if also_pdf and view_files:
        pdf_path = write_pdf_from_files(
            view_files, out_path.with_suffix(".pdf"), progress=progress
        )
    if also_html and view_files:
        html_path = write_html_viewer(
            view_files,
            out_path.with_suffix(".html"),
            title=out_path.stem,
            progress=progress,
        )

    return out_path, pdf_path, parts, preview_path, html_path


def capture_by_comic_images(
    page,
    *,
    page_url: str,
    out_path: Path,
    user_agent: str,
    progress: Optional[ProgressCb] = None,
    split_when_taller_than: int = 4000,
    part_height: int = 2500,
    also_pdf: bool = False,
    also_html: bool = False,
    save_cuts: bool = True,
    also_zip: bool = True,
) -> CaptureResult:
    """下载页面内网漫切图并竖向拼接（最完整、无空白）。"""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    urls = collect_comic_image_urls(page)
    if len(urls) < 2:
        raise RuntimeError("未检测到足够的网漫切图，请改用 screenshot/scroll 模式")

    parsed = urlparse(page_url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"
    _log(progress, f"[capture] comic cuts={len(urls)}, downloading...")

    cuts_dir = out_path.with_name(f"{out_path.stem}-cuts")
    if save_cuts:
        if cuts_dir.exists():
            for old in cuts_dir.glob("*"):
                old.unlink()
        cuts_dir.mkdir(parents=True, exist_ok=True)

    images: list[Image.Image] = []
    cut_paths: list[Path] = []
    for i, u in enumerate(urls, 1):
        data = download_image_bytes(u, referer=referer, user_agent=user_agent)
        im = Image.open(BytesIO(data)).convert("RGB")
        images.append(im)
        if save_cuts:
            ext = ".jpg"
            if ".png" in u.lower():
                ext = ".png"
            cut_path = cuts_dir / f"{i:03d}{ext}"
            if ext == ".jpg":
                im.save(cut_path, quality=92, optimize=True)
            else:
                im.save(cut_path)
            cut_paths.append(cut_path)
        if i == 1 or i == len(urls) or i % 10 == 0:
            _log(progress, f"[capture] downloaded {i}/{len(urls)} {im.size}")

    canvas = stitch_images(images)

    # 本机保存优先：ZIP（普通 JPG 切图），PDF/HTML 可选
    zip_path: Optional[Path] = None
    pdf_path: Optional[Path] = None
    html_path: Optional[Path] = None
    if also_zip and cut_paths:
        zip_path = write_zip_from_files(
            cut_paths,
            out_path.with_suffix(".zip"),
            arcname_prefix=f"{out_path.stem}-cuts",
            progress=progress,
        )
    if also_pdf and cut_paths:
        pdf_path = write_pdf_from_files(
            cut_paths, out_path.with_suffix(".pdf"), progress=progress
        )
    if also_html and cut_paths:
        html_path = write_html_viewer(
            cut_paths,
            out_path.with_suffix(".html"),
            title=f"{out_path.stem}（{len(cut_paths)} 切）",
            progress=progress,
        )

    out_path, _pdf2, parts, preview_path, _html2 = save_long_image(
        canvas,
        out_path,
        also_pdf=False,
        also_html=False,
        split_when_taller_than=split_when_taller_than,
        part_height=part_height,
        progress=progress,
    )
    if pdf_path is None:
        pdf_path = _pdf2
    if html_path is None:
        html_path = _html2
    if zip_path is None and parts:
        zip_path = write_zip_from_files(
            parts,
            Path(out_path).with_suffix(".zip"),
            arcname_prefix=f"{Path(out_path).stem}-parts",
            progress=progress,
        )

    return CaptureResult(
        path=out_path,
        url=page_url,
        width=canvas.width,
        height=canvas.height,
        scroll_rounds=0,
        final_page_height=canvas.height,
        file_size=out_path.stat().st_size,
        method="images",
        parts=parts,
        preview_path=preview_path,
        pdf_path=pdf_path,
        html_path=html_path,
        zip_path=zip_path,
        cut_count=len(urls),
        cut_paths=cut_paths,
    )


def capture_by_scroll_stitch(
    page,
    *,
    page_url: str,
    out_path: Path,
    max_rounds: int = 400,
    wait_ms: int = 280,
    overlap: int = 80,
    progress: Optional[ProgressCb] = None,
    split_when_taller_than: int = 4000,
    part_height: int = 2500,
    also_pdf: bool = False,
    also_html: bool = False,
    also_zip: bool = True,
) -> CaptureResult:
    """边滚边截视口，再竖向去重叠拼接（通用站点，比 full_page 更完整）。"""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)

    strips: list[Image.Image] = []
    last_y = -1
    rounds = 0
    for i in range(1, max_rounds + 1):
        rounds = i
        wait_visible_images(page)
        png = page.screenshot(type="png")
        strip = Image.open(BytesIO(png)).convert("RGB")
        y = int(page.evaluate("window.scrollY") or 0)
        page_h = int(page.evaluate("document.body.scrollHeight") or 0)
        view_h = strip.height

        if not strips:
            strips.append(strip)
        else:
            # 去掉与上一帧重叠区域
            dy = y - last_y
            if dy <= 0:
                # 高度可能刚增长，再等一轮
                pass
            else:
                crop_top = max(0, view_h - dy)
                # 保守：保留 overlap 像素对齐容错
                crop_top = max(0, min(view_h - 1, crop_top - 0))
                if crop_top < view_h:
                    strips.append(strip.crop((0, crop_top, strip.width, view_h)))

        last_y = y
        _log(progress, f"[stitch] round={i} y={y} page_h={page_h} strips={len(strips)}")

        at_bottom = y + view_h >= page_h - 2
        if at_bottom:
            # 再等懒加载增高
            page.wait_for_timeout(wait_ms + 200)
            new_h = int(page.evaluate("document.body.scrollHeight") or 0)
            if new_h <= page_h and at_bottom:
                # 确认到底
                stable_bottom = True
                for _ in range(2):
                    page.wait_for_timeout(wait_ms)
                    wait_visible_images(page)
                    if int(page.evaluate("document.body.scrollHeight") or 0) > page_h:
                        stable_bottom = False
                        break
                if stable_bottom:
                    break

        page.evaluate(
            f"window.scrollBy(0, Math.max(1, window.innerHeight - {int(overlap)}))"
        )
        page.wait_for_timeout(wait_ms)

    canvas = stitch_images(strips)
    out_path, pdf_path, parts, preview_path, html_path = save_long_image(
        canvas,
        out_path,
        also_pdf=also_pdf,
        also_html=also_html,
        split_when_taller_than=split_when_taller_than,
        part_height=part_height,
        progress=progress,
    )
    zip_path = None
    if also_zip and parts:
        zip_path = write_zip_from_files(
            parts,
            Path(out_path).with_suffix(".zip"),
            arcname_prefix=f"{Path(out_path).stem}-parts",
            progress=progress,
        )
    return CaptureResult(
        path=out_path,
        url=page_url,
        width=canvas.width,
        height=canvas.height,
        scroll_rounds=rounds,
        final_page_height=int(page.evaluate("document.body.scrollHeight") or canvas.height),
        file_size=out_path.stat().st_size,
        method="scroll",
        parts=parts,
        preview_path=preview_path,
        pdf_path=pdf_path,
        html_path=html_path,
        zip_path=zip_path,
    )


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
    split_when_taller_than: int = 4000,
    part_height: int = 2500,
    method: str = "auto",
    also_pdf: bool = False,
    also_html: bool = False,
    also_zip: bool = True,
) -> CaptureResult:
    """
    打开网页并保存长图。

    method:
      - auto: 检测到网漫切图则下载拼接；否则边滚边截拼接
      - images: 强制下载切图拼接（Naver 网漫推荐）
      - scroll: 边滚边截视口拼接
      - screenshot: 旧逻辑 full_page（可能有空白）
    """
    from playwright.sync_api import sync_playwright

    url = _validate_url(url)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    method = (method or "auto").lower().strip()
    if method not in {"auto", "images", "scroll", "screenshot"}:
        raise ValueError("method 必须是 auto/images/scroll/screenshot")

    user_agent = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    )

    _log(progress, f"[capture] open {url} method={method}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(
            viewport={"width": width, "height": height},
            user_agent=user_agent,
        )
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        dismiss_popups(page, dismiss)
        page.wait_for_timeout(800)

        # 网漫切图的 data-src 通常一进页就齐全，可先探测，避免无意义长滚
        early_urls = collect_comic_image_urls(page)
        need_scroll = method in {"scroll", "screenshot"} or (
            method == "auto" and len(early_urls) < 2
        ) or (method == "images" and len(early_urls) < 2)

        rounds, page_height = 0, 0
        if need_scroll or method in {"auto", "images"}:
            # images 模式仍轻量滚几轮，触发可能的延迟插入
            scroll_rounds = max_rounds if need_scroll else min(30, max_rounds)
            rounds, page_height = scroll_lazy_load(
                page,
                max_rounds=scroll_rounds,
                wait_ms=wait_ms,
                scroll_ratio=scroll_ratio,
                stable_rounds=stable_rounds if need_scroll else 2,
                progress=progress,
            )

        comic_urls = collect_comic_image_urls(page)
        if len(comic_urls) < len(early_urls):
            comic_urls = early_urls
        _log(progress, f"[capture] detected comic cuts={len(comic_urls)}")

        chosen = method
        if method == "auto":
            chosen = "images" if len(comic_urls) >= 2 else "scroll"

        try:
            if chosen == "images":
                # 切图模式不依赖当前滚动位置；但先滚过一遍可让 data-src 齐全
                result = capture_by_comic_images(
                    page,
                    page_url=url,
                    out_path=out_path,
                    user_agent=user_agent,
                    progress=progress,
                    split_when_taller_than=split_when_taller_than,
                    part_height=part_height,
                    also_pdf=also_pdf,
                    also_html=also_html,
                    also_zip=also_zip,
                )
                result.scroll_rounds = rounds
                result.final_page_height = page_height
            elif chosen == "scroll":
                result = capture_by_scroll_stitch(
                    page,
                    page_url=url,
                    out_path=out_path,
                    max_rounds=max(max_rounds * 2, 400),
                    wait_ms=wait_ms,
                    progress=progress,
                    split_when_taller_than=split_when_taller_than,
                    part_height=part_height,
                    also_pdf=also_pdf,
                    also_html=also_html,
                    also_zip=also_zip,
                )
            else:
                # 旧逻辑：全页截图（可能中间空白）
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(400)
                # 尽量把 data-src 灌进 src，减少空白
                page.evaluate(
                    """
                    () => {
                      for (const img of document.querySelectorAll('img[data-src]')) {
                        const u = img.getAttribute('data-src');
                        if (u && (!img.src || /bg_transparency/i.test(img.src))) {
                          img.src = u;
                        }
                      }
                    }
                    """
                )
                wait_all_images(page, per_image_timeout_ms=8000)
                # 再滚一遍确保加载
                scroll_lazy_load(
                    page,
                    max_rounds=max_rounds,
                    wait_ms=wait_ms,
                    scroll_ratio=scroll_ratio,
                    stable_rounds=stable_rounds,
                    progress=progress,
                )
                page.screenshot(path=str(out_path), full_page=True)
                from PIL import Image

                Image.MAX_IMAGE_PIXELS = None
                with Image.open(out_path) as im:
                    img_w, img_h = im.size
                parts = None
                preview_path = None
                pdf_path = None
                html_path = None
                if split_when_taller_than > 0 and img_h > split_when_taller_than:
                    # 转存为更稳妥的长图产物
                    with Image.open(out_path) as im:
                        out_path2, pdf_path, parts, preview_path, html_path = save_long_image(
                            im,
                            out_path,
                            also_pdf=also_pdf,
                            split_when_taller_than=split_when_taller_than,
                            part_height=part_height,
                            progress=progress,
                        )
                        out_path = out_path2
                        img_w, img_h = im.size
                result = CaptureResult(
                    path=out_path,
                    url=url,
                    width=img_w,
                    height=img_h,
                    scroll_rounds=rounds,
                    final_page_height=page_height,
                    file_size=out_path.stat().st_size,
                    method="screenshot",
                    parts=parts,
                    preview_path=preview_path,
                    pdf_path=pdf_path,
                    html_path=html_path,
                )
        finally:
            browser.close()

    if not result.path.is_file() or result.file_size < 1000:
        raise RuntimeError("截图失败或文件过小，请检查链接是否可访问")

    _log(
        progress,
        f"[capture] saved {result.path} method={result.method} "
        f"size={result.file_size} px={result.width}x{result.height} "
        f"cuts={result.cut_count} rounds={result.scroll_rounds}",
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="懒加载滚屏截取网页长图（网漫默认下载切图拼接，保证整话完整）",
    )
    p.add_argument("url", help="网页 URL，如 https://m.comic.naver.com/...")
    p.add_argument(
        "-o",
        "--output",
        default="page.png",
        help="输出图片路径，默认 page.png",
    )
    p.add_argument(
        "--method",
        choices=["auto", "images", "scroll", "screenshot"],
        default="auto",
        help="auto=网漫切图优先；images=下载切图；scroll=边滚边截；screenshot=旧全页截图",
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
    p.add_argument(
        "--no-split",
        action="store_true",
        help="关闭超长图自动切段（默认高度>4000 会切成 JPG 分段）",
    )
    p.add_argument(
        "--pdf",
        action="store_true",
        help="额外生成 PDF（默认不生成；推荐直接下 ZIP 到本机）",
    )
    p.add_argument(
        "--html",
        action="store_true",
        help="额外生成 HTML 预览（默认不生成）",
    )
    p.add_argument(
        "--no-zip",
        action="store_true",
        help="不打包 ZIP（默认会生成同名 .zip，内含普通 JPG 切图）",
    )
    p.add_argument(
        "--part-height",
        type=int,
        default=2500,
        help="切段高度（像素），默认 2500",
    )
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
            split_when_taller_than=0 if args.no_split else 4000,
            part_height=args.part_height,
            method=args.method,
            also_pdf=args.pdf,
            also_html=args.html,
            also_zip=not args.no_zip,
        )
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    # 优先打印本机可下载的 ZIP
    if result.zip_path:
        print(result.zip_path)
    if result.cut_paths:
        print(result.cut_paths[0].parent)
    if result.parts:
        print(result.parts[0].parent)
    print(result.path)
    if result.preview_path:
        print(result.preview_path)
    if result.pdf_path:
        print(result.pdf_path)
    if result.html_path:
        print(result.html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
