#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund-analyzer 统一入口

    python fund.py report                 行业 / ETF 全景报告（较慢，5~10 分钟）
    python fund.py diag 016371            单只基金诊断（约 2~3 分钟）
    python fund.py diag 016371,024593     多只批量诊断
    python fund.py track                  按 config/watchlist.json 每日追踪
    python fund.py panel                  只重渲染决策面板（秒级，刷新时点提示）
    python fund.py watch list|add|rm      追踪清单管理

所有产物默认写入 ./output，可用 --dir 指定。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:                                     # Windows 控制台中文乱码
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                        # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
DEFAULT_OUT = ROOT / "output"
WATCH = ROOT / "config" / "watchlist.json"

ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def sh(args: list[str]) -> int:
    """在项目 src/ 下执行子脚本。"""
    return subprocess.run([sys.executable, *args], cwd=str(SRC), env=ENV).returncode


def ensure_out(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_watch() -> dict:
    if WATCH.exists():
        return json.loads(WATCH.read_text(encoding="utf-8"))
    return {"bench": "沪深300", "funds": []}


def write_watch(obj: dict) -> None:
    WATCH.parent.mkdir(parents=True, exist_ok=True)
    WATCH.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_report(a) -> int:
    out = ensure_out(Path(a.dir) if a.dir else DEFAULT_OUT)
    data = out / "行业ETF数据.json"
    print("[1/2] 抓取与分析（行业较多时会慢，请耐心）...", flush=True)
    rc = sh([str(SRC / "analyze.py"),
             "--funds", a.funds, "--etfs", a.etfs,
             "--top-industries", str(a.top), "--bench", a.bench,
             "--start", a.start, "--out", str(data)])
    if rc != 0 or not data.exists():
        print("数据阶段失败，未生成报告"); return rc or 1
    print("[2/2] 渲染 HTML ...", flush=True)
    html = out / "基金行业ETF分析报告.html"
    rc = sh([str(SRC / "report.py"), "--data", str(data), "--out", str(html)])
    if rc == 0:
        print(f"\n完成 → {html}")
    return rc


def cmd_diag(a) -> int:
    out = ensure_out(Path(a.dir) if a.dir else DEFAULT_OUT)
    codes = [c.strip() for c in a.funds.split(",") if c.strip()]
    if not codes:
        print("请给出基金代码，例如：python fund.py diag 016371"); return 1
    rc_all = 0
    for code in codes:
        print(f"\n===== 诊断 {code} =====", flush=True)
        j = out / f"diag_{code}.json"
        rc = sh([str(SRC / "diagnose.py"), "--fund", code, "--bench", a.bench,
                 "--horizon", a.horizon, "--start", a.start, "--out", str(j)])
        if rc != 0 or not j.exists():
            print(f"  {code} 诊断失败"); rc_all = rc_all or rc or 1; continue
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
            name = (d.get("meta") or {}).get("名称") or code
        except Exception:                                # noqa: BLE001
            name = code
        html = out / f"基金诊断报告_{name}{code}.html"
        rc = sh([str(SRC / "diagnose_report.py"), "--data", str(j), "--out", str(html)])
        if rc == 0:
            adv = (d.get("advice") or {}) if isinstance(d, dict) else {}
            print(f"  {name}｜评分 {adv.get('评分')}｜档位 {adv.get('档位')}｜建议仓位 {adv.get('建议仓位%')}%")
            print(f"  报告 → {html}")
        rc_all = rc_all or rc
    return rc_all


def cmd_track(a) -> int:
    out = ensure_out(Path(a.dir) if a.dir else DEFAULT_OUT)
    args = [str(SRC / "track.py"), "--bench", a.bench, "--dir", str(out), "--start", a.start]
    if a.funds:
        args += ["--funds", a.funds]
    else:
        if not WATCH.exists():
            print(f"未找到清单 {WATCH}，请先：python fund.py watch add 005827")
            return 1
        args += ["--watchlist", str(WATCH)]
    return sh(args)


def cmd_panel(a) -> int:
    out = ensure_out(Path(a.dir) if a.dir else DEFAULT_OUT)
    if not WATCH.exists():
        print(f"未找到清单 {WATCH}"); return 1
    return sh([str(SRC / "track.py"), "--panel-only", "--watchlist", str(WATCH), "--dir", str(out)])


def cmd_watch(a) -> int:
    obj = read_watch()
    codes = obj.get("funds", [])
    if a.watch_cmd in (None, "list"):
        print(f"基准：{obj.get('bench','沪深300')}   共 {len(codes)} 只")
        for i, c in enumerate(codes, 1):
            print(f"  {i}. {c}")
        return 0
    if a.watch_cmd == "add":
        for c in [x.strip() for x in a.codes.split(",") if x.strip()]:
            if c not in codes:
                codes.append(c); print(f"+ 已加入 {c}")
            else:
                print(f"= {c} 已在清单中")
    elif a.watch_cmd == "rm":
        for c in [x.strip() for x in a.codes.split(",") if x.strip()]:
            if c in codes:
                codes.remove(c); print(f"- 已移出 {c}")
            else:
                print(f"! {c} 不在清单中")
    else:
        print(f"未知子命令 {a.watch_cmd}"); return 1
    obj["funds"] = codes
    write_watch(obj)
    print(f"\n清单已保存 → {WATCH}")
    return 0


def check_env() -> None:
    try:
        import pandas  # noqa: F401
        import requests  # noqa: F401
        import akshare  # noqa: F401
    except ImportError as e:
        print(f"缺少依赖：{e.name}\n请先执行：  pip install -r requirements.txt")
        sys.exit(1)
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 及以上版本（代码使用 3.10+ 语法）")
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="fund.py", description="基金 / 行业分析工具箱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python fund.py diag 016371                诊断单只基金并生成 HTML
  python fund.py watch add 011453           加入每日追踪清单
  python fund.py track                      按清单跑追踪（15:00 前最佳）
  python fund.py panel                      仅刷新面板（秒级）
""")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("report", help="行业 / ETF 全景报告")
    p.add_argument("--funds", default=""); p.add_argument("--etfs", default="")
    p.add_argument("--top", type=int, default=40, help="纳入的行业数量（越大越慢）")
    p.add_argument("--bench", default="沪深300"); p.add_argument("--start", default="20230101")
    p.add_argument("--dir", default=""); p.set_defaults(func=cmd_report)

    p = sub.add_parser("diag", help="基金诊断（行业现状 + 趋势预测 + 买卖建议）")
    p.add_argument("funds", help="基金代码，逗号分隔")
    p.add_argument("--bench", default="沪深300"); p.add_argument("--horizon", default="20,60")
    p.add_argument("--start", default="20230101"); p.add_argument("--dir", default="")
    p.set_defaults(func=cmd_diag)

    p = sub.add_parser("track", help="每日追踪：批量诊断 + 历史留痕 + 决策面板")
    p.add_argument("--funds", default="", help="临时指定代码，留空则用清单")
    p.add_argument("--bench", default="沪深300"); p.add_argument("--start", default="20230101")
    p.add_argument("--dir", default=""); p.set_defaults(func=cmd_track)

    p = sub.add_parser("panel", help="仅用已保存结果重渲染决策面板")
    p.add_argument("--dir", default=""); p.set_defaults(func=cmd_panel)

    p = sub.add_parser("watch", help="追踪清单管理")
    p.add_argument("watch_cmd", nargs="?", default="list", choices=["list", "add", "rm"])
    p.add_argument("codes", nargs="?", default="", help="基金代码，逗号分隔")
    p.set_defaults(func=cmd_watch)

    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help(); return
    check_env()
    sys.exit(a.func(a) or 0)


if __name__ == "__main__":
    main()
