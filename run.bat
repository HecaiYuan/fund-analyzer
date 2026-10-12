@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === 1/3 安装依赖 ===
python -m pip install -r requirements.txt
if errorlevel 1 goto :err

echo === 2/3 查看追踪清单 ===
python fund.py watch list

echo === 3/3 跑每日追踪 ===
python fund.py track
if errorlevel 1 goto :err

echo.
echo 完成，产物在 output 目录
pause
exit /b 0

:err
echo 运行出错，请截图上面的报错信息
pause
exit /b 1
