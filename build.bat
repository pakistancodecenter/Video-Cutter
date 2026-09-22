@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Video Cutter - Windows Build
echo ============================================

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :error

python -m pip install --upgrade pip
if errorlevel 1 goto :error

pip install -r requirements-dev.txt
if errorlevel 1 goto :error

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

pyinstaller --noconfirm --clean video_cutter.spec
if errorlevel 1 goto :error

echo.
echo Build completed successfully.
echo Output: dist\VideoCutter
pause
exit /b 0

:error
echo.
echo Build failed. Review the error messages above.
pause
exit /b 1
