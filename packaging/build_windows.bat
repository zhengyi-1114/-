@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo [1/4] 创建虚拟环境...
if not exist .venv-win (
  py -3.11 -m venv .venv-win 2>nul || py -3.12 -m venv .venv-win || python -m venv .venv-win
)
call .venv-win\Scripts\activate.bat

echo [2/4] 安装依赖（较慢，含 torch/easyocr）...
python -m pip install -U pip wheel
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m playwright install chromium

echo [3/4] PyInstaller 打包...
if exist dist\ScreenOCRTranslator rmdir /s /q dist\ScreenOCRTranslator
if exist build rmdir /s /q build
pyinstaller --noconfirm packaging\screen_translator.spec

echo [4/4] 复制说明与示例配置...
copy /Y .env.example dist\ScreenOCRTranslator\.env.example >nul 2>nul
copy /Y packaging\使用说明.txt dist\ScreenOCRTranslator\使用说明.txt >nul 2>nul

echo.
echo 完成：dist\ScreenOCRTranslator\ScreenOCRTranslator.exe
echo 把整个 ScreenOCRTranslator 文件夹拷到 Windows 电脑即可双击运行。
pause
