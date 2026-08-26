@echo off
chcp 65001 >nul
echo ========================================
echo   Meng_CSVEditor 打包脚本
echo ========================================
echo.

cd /d "%~dp0"

echo [1/2] 检查 PyInstaller ...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo 正在安装 PyInstaller ...
    pip install pyinstaller
)

echo [2/2] 打包为单文件 EXE ...
python -m PyInstaller --onefile --windowed ^
    --name Meng_CSVEditor ^
    --hidden-import csv_model ^
    --hidden-import csv_commands ^
    csv_editor.py

if errorlevel 1 (
    echo.
    echo [ERROR] 打包失败！
    pause
    exit /b 1
)

echo.
echo ========================================
echo   打包完成！
echo   输出: dist\Meng_CSVEditor.exe
echo ========================================
pause
