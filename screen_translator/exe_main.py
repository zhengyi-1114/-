"""Windows .exe 启动入口：本地打开网页版（双击即可用）。"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def app_dir() -> Path:
    """冻结后以 exe 所在目录为准；开发时用仓库根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def ensure_workdir() -> Path:
    root = app_dir()
    os.chdir(root)
    # 让相对路径 .env / 输出文件落在 exe 旁
    os.environ.setdefault("SCREEN_TRANSLATOR_HOME", str(root))
    return root


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ[key] = value


def ensure_playwright_browser() -> None:
    """首次运行时尝试安装 Chromium（抓网页需要）。失败不阻断启动。"""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return
    except Exception:
        pass
    try:
        import subprocess

        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
            timeout=600,
        )
    except Exception:
        pass


def open_browser_later(url: str, delay: float = 2.2) -> None:
    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open, daemon=True).start()


def show_tray_window(url: str) -> None:
    """简单状态窗，避免纯控制台黑框；关闭窗口即结束进程。"""
    try:
        import tkinter as tk
    except Exception:
        # 无 tk 时阻塞在服务上
        return

    root = tk.Tk()
    root.title("屏幕/网漫 OCR 翻译")
    root.geometry("420x180")
    root.resizable(False, False)

    msg = (
        "程序已启动。\n\n"
        f"浏览器地址：\n{url}\n\n"
        "请勿关闭本窗口（关闭后服务会停止）。\n"
        "首次识别韩文时会下载 OCR 模型，请耐心等待。"
    )
    label = tk.Label(root, text=msg, justify="left", anchor="w", padx=16, pady=16)
    label.pack(fill="both", expand=True)

    def on_open() -> None:
        webbrowser.open(url)

    btn = tk.Button(root, text="打开网页界面", command=on_open)
    btn.pack(pady=(0, 12))

    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


def main() -> int:
    root = ensure_workdir()
    load_dotenv(root / ".env")
    # 也尝试用户目录下的配置
    load_dotenv(Path.home() / ".screen_translator.env")

    host = os.getenv("SCREEN_TRANSLATOR_HOST", "127.0.0.1")
    port = int(os.getenv("SCREEN_TRANSLATOR_PORT", "7860"))
    url = f"http://127.0.0.1:{port}"

    # 后台预检浏览器（不阻塞 UI 太久）
    threading.Thread(target=ensure_playwright_browser, daemon=True).start()

    open_browser_later(url)

    def run_server() -> None:
        from screen_translator.web import run_web

        # exe 内不走公网 share，只本机
        run_web(host=host, port=port, share=False)

    server = threading.Thread(target=run_server, daemon=True)
    server.start()

    # 等服务起来一点
    time.sleep(1.5)
    show_tray_window(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
