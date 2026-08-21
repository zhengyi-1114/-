# EasyOCR / torch 隐藏导入补充
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("easyocr") + collect_submodules("torch")
