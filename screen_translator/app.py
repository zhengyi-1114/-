"""图形界面：框选屏幕区域 → OCR → 翻译。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from PIL import Image

from screen_translator.capture import Region, RegionSelector, grab_region
from screen_translator.ocr import recognize_text
from screen_translator.translate import BACKENDS, LANGUAGES, translate_text

# 配色：深青墨色界面，避免紫/奶油风
COLORS = {
    "bg": "#0f1714",
    "panel": "#16211c",
    "panel_alt": "#1c2a24",
    "text": "#e7f0eb",
    "muted": "#8aa399",
    "accent": "#2dd4a8",
    "accent_dim": "#1f9f7e",
    "border": "#2a3d34",
    "danger": "#e07a6a",
}


class ScreenTranslatorApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("屏幕 OCR 翻译")
        self.root.geometry("720x560")
        self.root.minsize(560, 420)
        self.root.configure(bg=COLORS["bg"])

        self.source_var = tk.StringVar(value="auto")
        self.target_var = tk.StringVar(value="zh-CN")
        self.backend_var = tk.StringVar(value="google")
        self.status_var = tk.StringVar(
            value="就绪：韩文请选源语言「韩语」；AI 翻译需配置 API Key"
        )
        self.busy = False
        self._hotkey_listener = None

        self._build_style()
        self._build_ui()
        self._bind_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure(
            "TLabel",
            background=COLORS["bg"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Title.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["accent"],
            font=("Microsoft YaHei UI", 18, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Status.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#06241a",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
        )
        style.map(
            "Accent.TButton",
            background=[("active", COLORS["accent_dim"]), ("disabled", COLORS["border"])],
        )
        style.configure(
            "Ghost.TButton",
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 10),
            padding=(12, 8),
        )
        style.map(
            "Ghost.TButton",
            background=[("active", COLORS["border"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["panel_alt"],
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["text"],
        )

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="TFrame", padding=20)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text="屏幕 OCR 翻译", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="框选屏幕区域，自动识别文字并翻译。也可打开本地图片。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 16))

        toolbar = ttk.Frame(outer, style="TFrame")
        toolbar.pack(fill=tk.X, pady=(0, 12))

        ttk.Button(
            toolbar,
            text="框选翻译",
            style="Accent.TButton",
            command=self.start_region_capture,
        ).pack(side=tk.LEFT)

        ttk.Button(
            toolbar,
            text="打开图片",
            style="Ghost.TButton",
            command=self.open_image,
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Button(
            toolbar,
            text="复制译文",
            style="Ghost.TButton",
            command=self.copy_translation,
        ).pack(side=tk.LEFT, padx=(8, 0))

        lang_row = ttk.Frame(outer, style="TFrame")
        lang_row.pack(fill=tk.X, pady=(0, 12))

        lang_pairs = [(code, name) for code, name in LANGUAGES.items()]
        display_map = {f"{name} ({code})": code for code, name in lang_pairs}
        self._display_to_code = display_map
        self._code_to_display = {v: k for k, v in display_map.items()}

        ttk.Label(lang_row, text="源语言").pack(side=tk.LEFT)
        self.source_combo = ttk.Combobox(
            lang_row,
            state="readonly",
            width=18,
            values=list(display_map.keys()),
        )
        self.source_combo.set(self._code_to_display.get("auto", "自动检测 (auto)"))
        self.source_combo.pack(side=tk.LEFT, padx=(6, 16))
        self.source_combo.bind("<<ComboboxSelected>>", self._sync_langs)

        ttk.Label(lang_row, text="目标语言").pack(side=tk.LEFT)
        target_values = [k for k, v in display_map.items() if v != "auto"]
        self.target_combo = ttk.Combobox(
            lang_row,
            state="readonly",
            width=18,
            values=target_values,
        )
        self.target_combo.set(self._code_to_display.get("zh-CN", "中文（简体） (zh-CN)"))
        self.target_combo.pack(side=tk.LEFT, padx=(6, 16))
        self.target_combo.bind("<<ComboboxSelected>>", self._sync_langs)

        ttk.Label(lang_row, text="翻译后端").pack(side=tk.LEFT)
        backend_labels = [f"{name} ({code})" for code, name in BACKENDS.items()]
        self._backend_to_code = {f"{name} ({code})": code for code, name in BACKENDS.items()}
        self.backend_combo = ttk.Combobox(
            lang_row,
            state="readonly",
            width=28,
            values=backend_labels,
        )
        self.backend_combo.set("Google 翻译（免 Key） (google)")
        self.backend_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.backend_combo.bind("<<ComboboxSelected>>", self._sync_langs)

        # 原文 / 译文
        panes = ttk.Frame(outer, style="TFrame")
        panes.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(panes, style="TFrame")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        ttk.Label(left, text="原文").pack(anchor="w")
        self.src_text = self._make_text(left)

        right = ttk.Frame(panes, style="TFrame")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        ttk.Label(right, text="译文").pack(anchor="w")
        self.dst_text = self._make_text(right)

        status_bar = ttk.Frame(outer, style="Panel.TFrame", padding=(10, 8))
        status_bar.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(status_bar, textvariable=self.status_var, style="Status.TLabel").pack(
            anchor="w"
        )

    def _make_text(self, parent: ttk.Frame) -> tk.Text:
        wrap = tk.Frame(parent, bg=COLORS["border"], padx=1, pady=1)
        wrap.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        text = tk.Text(
            wrap,
            wrap=tk.WORD,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            insertbackground=COLORS["accent"],
            relief=tk.FLAT,
            font=("Microsoft YaHei UI", 11),
            padx=10,
            pady=10,
            highlightthickness=0,
        )
        text.pack(fill=tk.BOTH, expand=True)
        return text

    def _sync_langs(self, _event=None) -> None:
        src_label = self.source_combo.get()
        dst_label = self.target_combo.get()
        backend_label = self.backend_combo.get()
        self.source_var.set(self._display_to_code.get(src_label, "auto"))
        self.target_var.set(self._display_to_code.get(dst_label, "zh-CN"))
        self.backend_var.set(self._backend_to_code.get(backend_label, "google"))

    def _bind_hotkeys(self) -> None:
        # 窗口内快捷键
        self.root.bind("<Control-Shift-T>", lambda e: self.start_region_capture())
        self.root.bind("<Control-o>", lambda e: self.open_image())

        # 尝试注册全局热键（失败则静默忽略，仍可用窗口内快捷键）
        try:
            from pynput import keyboard

            def on_activate() -> None:
                self.root.after(0, self.start_region_capture)

            hotkey = keyboard.HotKey(
                keyboard.HotKey.parse("<ctrl>+<shift>+t"),
                on_activate,
            )

            def on_press(key) -> None:
                hotkey.press(self._hotkey_listener.canonical(key))

            def on_release(key) -> None:
                hotkey.release(self._hotkey_listener.canonical(key))

            self._hotkey_listener = keyboard.Listener(
                on_press=on_press,
                on_release=on_release,
            )
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
        except Exception:
            # Wayland / 权限不足等环境下全局热键可能不可用
            pass

    def start_region_capture(self) -> None:
        if self.busy:
            return
        self._sync_langs()
        self.status_var.set("请拖拽选择屏幕区域…")
        # 先最小化主窗口，避免挡到要识别的内容
        self.root.withdraw()
        self.root.after(180, self._show_selector)

    def _show_selector(self) -> None:
        selector = RegionSelector(on_selected=self._on_region_selected)
        selector.show()

    def _on_region_selected(self, region: Optional[Region]) -> None:
        self.root.deiconify()
        self.root.lift()
        if region is None:
            self.status_var.set("已取消框选。")
            return
        try:
            image = grab_region(region)
        except Exception as exc:
            self.status_var.set(f"截屏失败: {exc}")
            messagebox.showerror("截屏失败", str(exc))
            return
        self._process_image_async(image)

    def open_image(self) -> None:
        if self.busy:
            return
        path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[
                ("图片", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"),
                ("全部文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            image = Image.open(path)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))
            return
        self._process_image_async(image)

    def _process_image_async(self, image: Image.Image) -> None:
        self.busy = True
        self.status_var.set("正在识别文字…")
        self.src_text.delete("1.0", tk.END)
        self.dst_text.delete("1.0", tk.END)
        source = self.source_var.get()
        target = self.target_var.get()
        backend = self.backend_var.get()

        def worker() -> None:
            try:
                original = recognize_text(image, lang=source)
                if not original.strip():
                    self.root.after(0, lambda: self._finish_empty())
                    return
                translated = translate_text(
                    original, source=source, target=target, backend=backend
                )
                self.root.after(
                    0,
                    lambda: self._finish_ok(original, translated),
                )
            except Exception as exc:
                self.root.after(0, lambda: self._finish_error(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_empty(self) -> None:
        self.busy = False
        self.status_var.set("未识别到文字。韩文请把源语言选「韩语」后重试。")

    def _finish_ok(self, original: str, translated: str) -> None:
        self.busy = False
        self.src_text.insert("1.0", original)
        self.dst_text.insert("1.0", translated)
        self.status_var.set("完成。可复制译文，或再次框选。")

    def _finish_error(self, exc: Exception) -> None:
        self.busy = False
        self.status_var.set(f"处理失败: {exc}")
        messagebox.showerror("处理失败", str(exc))

    def copy_translation(self) -> None:
        text = self.dst_text.get("1.0", tk.END).strip()
        if not text:
            self.status_var.set("没有可复制的译文。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("译文已复制到剪贴板。")

    def _on_close(self) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_app() -> None:
    ScreenTranslatorApp().run()


if __name__ == "__main__":
    run_app()