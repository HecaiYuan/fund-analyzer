#!/usr/bin/env bash
# macOS / Linux 一键运行：安装依赖 → 按清单跑每日追踪
set -e
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python)
echo "使用解释器：$PY"
"$PY" -m pip install -r requirements.txt

"$PY" fund.py watch list
"$PY" fund.py track
