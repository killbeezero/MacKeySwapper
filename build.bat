@echo off
echo ================================
echo  MacKeySwapper Build Script
echo ================================
echo.

pyinstaller --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller not found. Run: pip install pyinstaller
    pause
    exit /b 1
)

if exist dist\MacKeySwapper rmdir /s /q dist\MacKeySwapper
if exist build rmdir /s /q build

echo [1/3] Generating icon...
python generate_icon.py
if %errorlevel% neq 0 (
    echo [WARN] Icon generation failed, building without icon...
)

echo [2/3] Building...

pyinstaller MacKeySwapper.spec

if %errorlevel% neq 0 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo [3/3] Copying assets...
if exist assets xcopy /E /I /Y assets dist\MacKeySwapper\_internal\assets >nul
if exist icon.png copy /Y icon.png dist\MacKeySwapper\_internal\icon.png >nul
if exist icon.png copy /Y icon.png dist\MacKeySwapper\icon.png >nul

echo.
echo ================================
echo  Build complete!
echo  Output: dist\MacKeySwapper\
echo  Run:    dist\MacKeySwapper\MacKeySwapper.exe
echo ================================
pause
