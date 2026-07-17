@echo off

REM 设置中文显示
chcp 65001

REM 检查Python是否已安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
echo 错误：未找到Python。请先安装Python并添加到系统PATH中。
pause
exit /b 1
)

REM 检查pip是否可用
pip --version >nul 2>&1
if %errorlevel% neq 0 (
echo 错误：未找到pip。请确保Python安装包含pip。
pause
exit /b 1
)

REM 创建临时目录用于安装（解决某些路径问题）
set TEMP_INSTALL_DIR=%TEMP%\lvmo_game_install
echo 创建临时安装目录：%TEMP_INSTALL_DIR%
md "%TEMP_INSTALL_DIR%" 2>nul

REM 定义requirements文件路径
set REQUIREMENTS_FILE=requirements_desktop.txt

REM 打印安装说明
echo.
echo =======================================================
echo          LVMO_GAME 桌面应用程序依赖项安装
echo =======================================================
echo.
echo 正在安装以下依赖项：
echo - pywebview>=4.0.0 （桌面窗口框架）
echo - pyinstaller>=6.0.0 （应用打包工具）
echo.
echo 注意：如果遇到权限或路径错误，请尝试以管理员身份运行此脚本。
echo.

REM 尝试安装依赖项，使用--no-cache-dir参数避免缓存问题
echo 开始安装依赖项...
echo.
pip install --no-cache-dir -r "%REQUIREMENTS_FILE%"

REM 检查安装是否成功
if %errorlevel% neq 0 (
echo.
echo =======================================================
echo 安装失败！请尝试以下解决方案：
echo =======================================================
echo 1. 以管理员身份运行此批处理文件

echo 2. 手动安装依赖项：
echo    pip install --user pywebview>=4.0.0 pyinstaller>=6.0.0

echo 3. 或者使用以下命令（强制更新pip并安装）：
echo    python -m pip install --upgrade pip

echo    pip install --upgrade setuptools wheel

echo    pip install pywebview>=4.0.0 pyinstaller>=6.0.0

echo 4. 如果问题依然存在，可能需要检查Python环境或重新安装Python

echo.
echo 按任意键退出...
pause
exit /b 1
)

REM 安装成功后，检查PATH环境变量并提供建议
set PYTHON_SCRIPTS_PATH=
echo 检查Python脚本目录是否在PATH中...
for /f "tokens=2 delims= " %%i in ('pip --version') do (
    for %%j in (%%i) do (
        set PYTHON_SCRIPTS_PATH=%%~dpjScripts
    )
)

REM 验证脚本路径是否存在
if exist "%PYTHON_SCRIPTS_PATH%" (
echo Python脚本目录：%PYTHON_SCRIPTS_PATH%

REM 检查PATH是否包含Python脚本目录
echo %PATH% | findstr /i "%PYTHON_SCRIPTS_PATH%" >nul
if %errorlevel% neq 0 (
echo.
echo =======================================================
echo              重要提示：PATH环境变量
echo =======================================================
echo Python脚本目录不在PATH环境变量中。这可能会导致无法直接运行pyinstaller等命令。
echo.
echo 建议将以下路径添加到系统PATH环境变量中：
echo %PYTHON_SCRIPTS_PATH%
echo.
echo 或者，在运行package_desktop_app.bat之前，先运行以下命令：
echo set PATH=%%PATH%%;%PYTHON_SCRIPTS_PATH%
echo.
echo =======================================================
)
)

REM 清理临时目录
rd /s /q "%TEMP_INSTALL_DIR%" 2>nul

REM 安装成功提示
echo.
echo =======================================================
echo              依赖项安装成功！
echo =======================================================
echo.
echo 现在您可以运行package_desktop_app.bat来打包Windows应用程序了。
echo.
echo 注意事项：
echo 1. 如果打包过程中遇到"pyinstaller不是内部或外部命令"的错误，请确保Python脚本目录在PATH中
echo 2. 如果问题依然存在，可以尝试手动运行：
echo    python -m PyInstaller desktop_app.spec
echo.
echo 按任意键退出...
pause
exit /b 0