# 在 Windows PowerShell 中运行：
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\packaging\build_windows.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "[1/4] 创建虚拟环境..."
if (-not (Test-Path ".venv-win")) {
  try { py -3.11 -m venv .venv-win } catch {
    try { py -3.12 -m venv .venv-win } catch { python -m venv .venv-win }
  }
}
& .\.venv-win\Scripts\Activate.ps1

Write-Host "[2/4] 安装依赖..."
python -m pip install -U pip wheel
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m playwright install chromium

Write-Host "[3/4] PyInstaller 打包..."
if (Test-Path "dist\ScreenOCRTranslator") { Remove-Item -Recurse -Force "dist\ScreenOCRTranslator" }
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
pyinstaller --noconfirm packaging\screen_translator.spec

Write-Host "[4/4] 复制说明..."
if (Test-Path ".env.example") { Copy-Item ".env.example" "dist\ScreenOCRTranslator\.env.example" -Force }
Copy-Item "packaging\使用说明.txt" "dist\ScreenOCRTranslator\使用说明.txt" -Force

# 可选：打 zip
$zip = "dist\ScreenOCRTranslator-windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "dist\ScreenOCRTranslator\*" -DestinationPath $zip
Write-Host "完成: dist\ScreenOCRTranslator\ScreenOCRTranslator.exe"
Write-Host "压缩包: $zip"
