@echo off
chcp 65001 >nul
echo ========================================
echo   CC Agent 打包工具
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

REM 创建虚拟环境（如果不存在）
if not exist ".venv" (
    echo [1/6] 创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)

REM 激活虚拟环境
echo [2/6] 激活虚拟环境...
call .venv\Scripts\activate.bat

REM 安装依赖
echo [3/6] 安装依赖...
pip install --upgrade pip
pip install pyinstaller PySide6 pyyaml pydantic httpx selenium pywinauto pywin32 pillow

REM 清理旧构建
echo [4/6] 清理旧构建...
if exist "build" rmdir /s /q build
if exist "dist" rmdir /s /q dist

REM 执行打包
echo [5/6] 执行打包（使用 spec 文件）...
pyinstaller CCAgent.spec --noconfirm

echo.
echo ========================================
echo   打包完成！
echo   输出目录: dist\CCAgent\CCAgent.exe
echo ========================================
echo.
echo 是否立即运行测试？(Y/N)
set /p choice=
if /i "%choice%"=="Y" goto run
if /i "%choice%"=="y" goto run
exit /b 0

:run
echo 启动程序...
dist\CCAgent\CCAgent.exe
