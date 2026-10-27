@echo off
setlocal enabledelayedexpansion

echo ============================================
echo   Building Fun Corners Executables
echo ============================================

REM Install build requirements explicitly before building; do not silently change the environment.
cd /d "%~dp0"
python -c "import PyInstaller, PIL, pystray" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Missing dependencies. Run: python -m pip install -r requirements-build.txt
    exit /b 1
)

echo.
echo [1/2] Building Fun Corners standalone (FunCorners.exe)...
python -m PyInstaller --clean -y FunCorners.spec
if errorlevel 1 (
    echo [ERROR] Failed to build FunCorners.exe
    exit /b 1
)

echo.
echo [2/2] Building Setup Wizard (FunCorners-Setup.exe)...
python -m PyInstaller --clean -y FunCorners-Setup.spec
if errorlevel 1 (
    echo [ERROR] Failed to build FunCorners-Setup.exe
    exit /b 1
)

echo.
echo ============================================
echo   Build Successful!
echo   Outputs:
echo     - dist\FunCorners.exe
echo     - dist\FunCorners-Setup.exe
echo ============================================
exit /b 0
