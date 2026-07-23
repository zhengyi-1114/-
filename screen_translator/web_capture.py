"""网页长图抓取：支持懒加载滚动后全页截图。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


def capture_webpage(
    url: str,
    *,
    out_path: str | Path,
    width: int = 800,
    height: int = 1200,
    max_rounds: int = 220,
    wait_ms: int = 350,
) -> Path:
    """
    打开网页，滚动触发懒加载，截取整页长图。
    适合 Naver 网漫等移动端长页面。
    """
    from playwright.sync_api import sync_playwright

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入有效的 http/https 链接")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="domcontentloaded", timeout=90000)

        # 尝试关掉常见弹窗
        for text in ["확인", "동의", "닫기", "Close", "OK", "Agree"]:
            try:
                btn = page.get_by_role("button", name=text)
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=800)
            except Exception:
                pass

        last_height = 0
        stable = 0
        for _ in range(max_rounds):
            page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85))")
            page.wait_for_timeout(wait_ms)
            page.evaluate(
                """
                () => Promise.all(
                  Array.from(document.images)
                    .filter(img => {
                      const r = img.getBoundingClientRect();
                      return r.bottom > 0 && r.top < window.innerHeight;
                    })
                    .map(img => img.complete ? Promise.resolve() :
                      new Promise(res => {
                        img.onload = img.onerror = () => res();
                        setTimeout(res, 2500);
                      }))
                )
                """
            )
            height_now = page.evaluate("document.body.scrollHeight")
            scrolled = page.evaluate("window.scrollY + window.innerHeight")
            if height_now <= last_height and scrolled >= height_now - 8:
                stable += 1
            else:
                stable = 0
            last_height = height_now
            if stable >= 3:
                break

        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        page.evaluate(
            """
            () => Promise.all(
              Array.from(document.images).map(img => img.complete ? Promise.resolve() :
                new Promise(res => {
                  img.onload = img.onerror = () => res();
                  setTimeout(res, 4000);
                }))
            )
            """
        )
        page.screenshot(path=str(out_path), full_page=True)
        browser.close()

    if not out_path.is_file() or out_path.stat().st_size < 1000:
        raise RuntimeError("截图失败或文件过小，请检查链接是否可访问")
    return out_path
