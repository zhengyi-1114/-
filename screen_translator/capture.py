"""屏幕截取与区域框选。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import mss
from PIL import Image


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    def as_mss(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def grab_region(region: Optional[Region] = None) -> Image.Image:
    """截取指定区域；region 为 None 时截取全部虚拟屏幕。"""
    try:
        with mss.mss() as sct:
            if region is None:
                monitor = sct.monitors[0]
                shot = sct.grab(monitor)
            else:
                shot = sct.grab(region.as_mss())
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:
        # 部分远程桌面 / 特殊 visual 下 mss 会失败，回退到 ImageGrab
        from PIL import ImageGrab

        if region is None:
            shot = ImageGrab.grab()
        else:
            bbox = (
                region.left,
                region.top,
                region.left + region.width,
                region.top + region.height,
            )
            shot = ImageGrab.grab(bbox=bbox)
        return shot.convert("RGB")


def grab_monitor(monitor_index: int = 1) -> Image.Image:
    """按监视器序号截取（1 为主屏）。"""
    with mss.mss() as sct:
        monitors = sct.monitors
        if monitor_index < 0 or monitor_index >= len(monitors):
            raise IndexError(f"监视器索引超出范围: {monitor_index}")
        shot = sct.grab(monitors[monitor_index])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


class RegionSelector:
    """全屏半透明遮罩，拖拽框选屏幕区域。"""

    def __init__(self, on_selected: Callable[[Optional[Region]], None]):
        self.on_selected = on_selected
        self._start: Optional[tuple[int, int]] = None
        self._rect_id: Optional[int] = None
        self._root = None
        self._canvas = None

    def show(self) -> None:
        import tkinter as tk

        root = tk.Toplevel()
        self._root = root
        root.attributes("-fullscreen", True)
        try:
            root.attributes("-alpha", 0.3)
        except tk.TclError:
            pass
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        root.configure(bg="black", cursor="crosshair")
        root.focus_force()

        canvas = tk.Canvas(root, cursor="crosshair", bg="black", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas = canvas

        hint = canvas.create_text(
            root.winfo_screenwidth() // 2,
            40,
            text="拖拽选择识别区域 · Esc 取消",
            fill="white",
            font=("Microsoft YaHei UI", 16),
        )
        self._hint_id = hint

        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        root.bind("<Escape>", self._on_cancel)
        root.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_press(self, event) -> None:
        self._start = (event.x_root, event.y_root)
        if self._canvas and self._rect_id:
            self._canvas.delete(self._rect_id)
            self._rect_id = None

    def _on_drag(self, event) -> None:
        if not self._start or not self._canvas or not self._root:
            return
        x0, y0 = self._start
        # Canvas 坐标相对窗口；全屏时与屏幕大致一致
        x1, y1 = event.x, event.y
        # 将 root 坐标转为 canvas 本地
        local_x0 = x0 - self._root.winfo_rootx()
        local_y0 = y0 - self._root.winfo_rooty()
        if self._rect_id:
            self._canvas.delete(self._rect_id)
        self._rect_id = self._canvas.create_rectangle(
            local_x0,
            local_y0,
            x1,
            y1,
            outline="#00d4aa",
            width=2,
        )

    def _on_release(self, event) -> None:
        if not self._start:
            return
        x0, y0 = self._start
        x1, y1 = event.x_root, event.y_root
        left = min(x0, x1)
        top = min(y0, y1)
        width = abs(x1 - x0)
        height = abs(y1 - y0)
        self._close()
        if width < 5 or height < 5:
            self.on_selected(None)
            return
        self.on_selected(Region(left=left, top=top, width=width, height=height))

    def _on_cancel(self, _event=None) -> None:
        self._close()
        self.on_selected(None)

    def _close(self) -> None:
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None
            self._canvas = None
            self._rect_id = None
            self._start = None