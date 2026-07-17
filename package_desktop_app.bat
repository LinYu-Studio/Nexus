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

REM 创建虚拟环境（可选）
REM echo 创建虚拟环境...
REM python -m venv venv
REM if %errorlevel% neq 0 (
echo 错误：创建虚拟环境失败。
pause
exit /b 1
REM )

REM 激活虚拟环境
REM call venv\Scripts\activate

REM 安装必要的依赖项
REM echo 安装依赖项...
REM pip install -r requirements_desktop.txt
REM if %errorlevel% neq 0 (
echo 错误：安装依赖项失败。
pause
exit /b 1
REM )

REM 开始打包过程
echo 开始打包Windows应用程序...

REM 使用PyInstaller打包应用
pyinstaller desktop_app.spec

REM 检查打包是否成功
if %errorlevel% neq 0 (
echo 错误：打包过程失败。请查看上面的错误信息。
pause
exit /b 1
)

REM 打包成功后的提示
echo.
echo Windows应用程序打包成功！
echo 可执行文件位置：dist\LVMO_GAME\LVMO_GAME.exe
echo.
echo 使用说明：
echo 1. 您可以将dist\LVMO_GAME文件夹中的所有文件复制到任何位置运行

echo 2. 首次运行时，程序会在用户目录下创建.LVMO_GAME文件夹存储数据

echo 3. 要创建独立的安装包，可以使用Inno Setup等工具将dist\LVMO_GAME文件夹打包

echo.
echo 注意：如需修改程序图标，请编辑desktop_app.spec文件中的icon参数

pause