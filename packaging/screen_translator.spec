# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格：Windows 一目录分发（比单文件更稳，适合 torch/easyocr）。"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None
root = Path(SPECPATH).resolve().parent

datas = []
binaries = []
hiddenimports = [
    "screen_translator",
    "screen_translator.exe_main",
    "screen_translator.web",
    "screen_translator.web_capture",
    "screen_translator.webtoon",
    "screen_translator.ocr",
    "screen_translator.translate",
    "screen_translator.ref_localize",
    "screen_translator.app",
    "screen_translator.capture",
    "gradio",
    "easyocr",
    "torch",
    "cv2",
    "PIL",
    "numpy",
    "onnxruntime",
    "rapidocr_onnxruntime",
    "playwright",
    "deep_translator",
    "img2pdf",
    "requests",
]

for pkg in (
    "gradio",
    "gradio_client",
    "safehttpx",
    "groovy",
    "easyocr",
    "torch",
    "torchvision",
    "skimage",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "playwright",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

try:
    datas += collect_data_files("easyocr")
except Exception:
    pass

# 附带示例 env
env_example = root / ".env.example"
if env_example.is_file():
    datas.append((str(env_example), "."))

a = Analysis(
    [str(root / "screen_translator" / "exe_main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(root / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[str(root / "packaging" / "rthook_path.py")],
    excludes=[
        "matplotlib",
        "tkinter.test",
        "unittest",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScreenOCRTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 无黑框；日志可写文件
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "packaging" / "app.ico") if (root / "packaging" / "app.ico").is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ScreenOCRTranslator",
)
