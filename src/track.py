#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""多基金持续追踪：批量诊断 + 历史留痕 + 当日决策面板。

设计前提（重要）：
    运行时间应在 **交易日 14:30 左右**（收盘前 30 分钟），而不是开盘前。
    原因见 market_window() 的 deadline 逻辑：
      - 场外基金 15:00 前提交申赎，按 **当日** 净值确认；15:00 后顺延到下一交易日
      - 14:30 时当日走势已基本定型，且仍留有下单窗口
      - 行业板块资金流 / 个股价格在这个时点是当日实时的，开盘前跑只能拿到昨日数据

用法：
    python3.11 track.py --funds 005827,519674,161725 --dir ../output
    python3.11 track.py --watchlist watchlist.json
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import warnings

warnings.filterwarnings("ignore")

import diagnose as dg  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
_EC_CANDIDATES = [Path(os.environ["FUND_ECHARTS"]) if os.environ.get("FUND_ECHARTS") else None,
                  BASE / "assets" / "echarts.min.js",
                  BASE / ".cache" / "echarts.min.js"]
EC_CACHE = next((p for p in _EC_CANDIDATES if p and p.exists()),
                BASE / "assets" / "echarts.min.js")

UP = "#c0392b"      # 涨-红
DOWN = "#1e8e3e"    # 跌-绿
FLAT = "#6b7280"

GEAR_ORDER = ["买入", "偏多", "持有", "减仓", "卖出"]
GEAR_COLOR = {"买入": UP, "偏多": "#e07b39", "持有": "#4b5563",
              "减仓": "#8a6d3b", "卖出": DOWN}


# --------------------------------------------------------------------------
# 交易时段与决策窗口
# --------------------------------------------------------------------------
def market_window(now: dt.datetime | None = None) -> dict:
    """判断当前时点相对 A 股交易时段的位置，给出决策窗口提示。"""
    now = now or dt.datetime.now()
    wd = now.weekday()                       # 0=周一
    is_weekday = wd < 5
    hm = now.hour * 60 + now.minute
    open_am, close_am = 9 * 60 + 30, 11 * 60 + 30
    open_pm, close_pm = 13 * 60, 15 * 60

    if not is_weekday:
        phase, trade = "非交易日（周末）", False
    elif hm < open_am:
        phase, trade = "开盘前", False
    elif hm <= close_am:
        phase, trade = "早盘交易中", True
    elif hm < open_pm:
        phase, trade = "午间休市", False
    elif hm <= close_pm:
        phase, trade = "尾盘交易中", True
    else:
        phase, trade = "已收盘", False

    deadline = now.replace(hour=15, minute=0, second=0, microsecond=0)
    mins_to_close = (deadline - now).total_seconds() / 60

    if trade and 0 < mins_to_close <= 60:
        urgency = "紧迫"
        hint = (f"距 15:00 申赎截止还有 {int(mins_to_close)} 分钟——"
                f"现在下单按 <b>今日净值</b> 确认，超时顺延下一交易日。")
    elif trade and mins_to_close > 60:
        urgency = "从容"
        hint = (f"距 15:00 申赎截止还有 {int(mins_to_close)} 分钟。"
                f"盘中数据为当日实时，但尾盘可能反转，建议临近 14:30 再复跑一次。")
    elif phase == "已收盘":
        urgency = "已过期"
        hint = "今日申赎窗口已关闭，本次结果仅作复盘参考，实际操作顺延至下一交易日。"
    elif not is_weekday:
        urgency = "休市"
        hint = "非交易日，无当日行情更新，结果基于最近一个交易日收盘数据。"
    else:
        urgency = "待开盘"
        hint = "尚未开盘，当前拿到的是上一交易日收盘数据，不能反映今日走势。"
    return {"phase": phase, "trading": trade, "urgency": urgency,
            "mins_to_close": round(mins_to_close, 1), "hint": hint,
            "now": now.strftime("%Y-%m-%d %H:%M:%S")}


# --------------------------------------------------------------------------
# 历史留痕
# --------------------------------------------------------------------------
HIST_FIELDS = ["日期", "时间", "代码", "名称", "评分", "档位", "操作", "建议仓位%",
               "止损位", "止盈位", "净值日期", "单位净值", "近20日%", "近60日%",
               "行业健康分", "20日上涨概率%", "20日中位%", "首要依据"]


def append_history(csv_path: Path, row: dict) -> None:
    new = csv_path.exists() is False or csv_path.stat().st_size == 0
    with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HIST_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in HIST_FIELDS})


def load_history(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with open(csv_path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _flatten(d: dict, when: dt.datetime) -> dict:
    """从诊断结果抽取历史留痕所需字段。"""
    adv = d.get("advice", {}) or {}
    perf = d.get("perf", {}) or {}
    tr = d.get("trend", {}) or {}
    fc20 = (d.get("forecast", {}) or {}).get("20日", {}) or {}
    reasons = adv.get("依据") or []
    return {
        "日期": when.strftime("%Y-%m-%d"),
        "时间": when.strftime("%H:%M"),
        "代码": d.get("code"),
        "名称": d.get("name"),
        "评分": adv.get("评分"),
        "档位": adv.get("档位"),
        "操作": adv.get("操作"),
        "建议仓位%": adv.get("建议仓位%"),
        "止损位": adv.get("止损参考"),
        "止盈位": adv.get("止盈参考"),
        "净值日期": d.get("nav_date"),
        "单位净值": adv.get("当前净值"),
        "近20日%": tr.get("20日涨幅%"),
        "近60日%": tr.get("60日涨幅%"),
        "行业健康分": d.get("industry_health"),
        "20日上涨概率%": fc20.get("上涨概率%"),
        "20日中位%": fc20.get("中位数%"),
        "首要依据": reasons[0] if reasons else "",
    }


# --------------------------------------------------------------------------
# 渲染：当日决策面板
# --------------------------------------------------------------------------
CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f4f5f7;color:#1f2328;
 font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:23px;margin:0 0 6px;font-weight:700;letter-spacing:.5px}
h2{font-size:17px;margin:30px 0 12px;padding-left:10px;border-left:4px solid #2c3e50;font-weight:650}
.sub{color:#6b7280;font-size:13px;margin-bottom:16px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:18px 20px;margin-bottom:14px;
 box-shadow:0 1px 3px rgba(0,0,0,.04)}
.banner{border-radius:10px;padding:14px 18px;margin-bottom:18px;font-size:13.5px;line-height:1.7}
.banner.u-紧迫{background:#fdecea;border-left:5px solid #c0392b;color:#7f2318}
.banner.u-从容{background:#eef4fb;border-left:5px solid #3b6ea5;color:#1f3d5c}
.banner.u-已过期,.banner.u-休市,.banner.u-待开盘{background:#f3f4f6;border-left:5px solid #6b7280;color:#4b5563}
.stat{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}
.stat div{flex:1;min-width:110px;background:#fff;border:1px solid #e5e7eb;border-radius:9px;padding:12px 14px;text-align:center}
.stat b{display:block;font-size:22px;line-height:1.25;font-weight:700}
.stat span{font-size:12px;color:#6b7280}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(370px,1fr));gap:14px}
.fc{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:16px 18px;position:relative;overflow:hidden}
.fc::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:__ACCENT__}
.fc h3{margin:0 0 2px;font-size:15.5px;font-weight:650}
.fc .code{font-size:12px;color:#9ca3af;font-family:ui-monospace,Menlo,monospace}
.gear{margin:8px 0 10px;font-size:15px;font-weight:700}
.gear small{font-weight:400;color:#6b7280;font-size:12px;margin-left:8px}
.kv{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:#4b5563;margin-bottom:8px}
.kv i{font-style:normal;color:#9ca3af}
.why{margin:8px 0 0;padding-left:16px;font-size:12.5px;color:#4b5563}
.why li{margin-bottom:2px}
.inv{margin-top:8px;font-size:12px;color:#92400e;background:#fffbeb;border-radius:6px;padding:6px 9px}
table.grid{width:100%;border-collapse:collapse;background:#fff;font-size:13px;
 border:1px solid #e5e7eb;border-radius:9px;overflow:hidden}
table.grid th{background:#f8f9fb;padding:9px 10px;text-align:left;font-weight:600;
 font-size:12.5px;color:#4b5563;border-bottom:1px solid #e5e7eb;white-space:nowrap}
table.grid td{padding:8px 10px;border-bottom:1px solid #f1f2f4}
table.grid tr:last-child td{border-bottom:none}
.up{color:__UP__;font-weight:600}
.down{color:__DOWN__;font-weight:600}
.flat{color:__FLAT__}
.chart{width:100%;height:300px}
.foot{margin-top:34px;padding-top:14px;border-top:1px solid #e5e7eb;color:#9ca3af;font-size:12px;text-align:center}
.err{background:#fdecea;border:1px solid #f5c6c2;border-radius:8px;padding:10px 14px;color:#7f2318;font-size:13px}
"""

JS = """
function mk(id, opt){var el=document.getElementById(id); if(!el) return;
  var c=echarts.init(el); c.setOption(opt); window.addEventListener('resize',function(){c.resize();});}
function cls(v){return v>0?'up':(v<0?'down':'flat');}
var D = window.DATA, R = D.results || [];
// 评分对比
mk('c_score', {
  grid:{left:70,right:40,top:24,bottom:56},
  tooltip:{trigger:'axis'},
  xAxis:{type:'category',data:R.map(function(r){return r.name;}),
    axisLabel:{interval:0,rotate:R.length>6?28:0,fontSize:11}},
  yAxis:{type:'value',max:100,min:0,splitLine:{lineStyle:{color:'#eef0f2'}}},
  series:[{type:'bar',barMaxWidth:38,data:R.map(function(r){
      var s=r.advice?r.advice['评分']:0;
      return {value:s,itemStyle:{color:s>=65?'__UP__':(s>=50?'#e07b39':(s>=35?'#9aa0a6':'__DOWN__'))}};}),
    label:{show:true,position:'top',fontSize:11,fontWeight:600}}]
});
// 历史评分轨迹
var H = D.history || [];
if(H.length>0){
  var dates=[...new Set(H.map(function(h){return h['日期']+ ' ' + h['时间']}))];
  var codes=[...new Set(H.map(function(h){return h['代码']}))];
  var ser=codes.map(function(c,i){
    return {type:'line',name:c,smooth:true,symbolSize:5,
      data:dates.map(function(d){
        var hit=H.find(function(h){return h['代码']===c && (h['日期']+' '+h['时间'])===d;});
        return hit?Number(hit['评分']):null;})};});
  mk('c_hist',{tooltip:{trigger:'axis'},legend:{top:0,textStyle:{fontSize:11}},
    grid:{left:44,right:20,top:40,bottom:56},
    xAxis:{type:'category',data:dates,axisLabel:{fontSize:10,rotate:dates.length>6?25:0}},
    yAxis:{type:'value',min:0,max:100,splitLine:{lineStyle:{color:'#eef0f2'}}},
    series:ser});
}
"""


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _num(v, nd=2, suffix="%"):
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return _esc(v)
    c = "up" if f > 0 else ("down" if f < 0 else "flat")
    return f'<span class="{c}">{f:+.{nd}f}{suffix}</span>'


def build_html(payload: dict) -> str:
    mw = payload["window"]
    results = payload["results"]
    hist = payload.get("history", [])
    p = [f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>基金追踪面板 · {payload['date']}</title>
<script>{payload['echarts']}</script>
<style>{CSS.replace('__UP__', UP).replace('__DOWN__', DOWN)
        .replace('__FLAT__', FLAT).replace('__ACCENT__', '#2c3e50')}</style>
</head><body><div class="wrap">
<h1>基金追踪 · 当日决策面板</h1>
<div class="sub">共 {len(results)} 只标的 · 基准 {payload.get('bench', '沪深300')} ·
生成于 {mw['now']} · 行情时点：{mw['phase']}</div>
<div class="banner u-{mw['urgency']}"><b>决策窗口：{mw['urgency']}</b>（{mw['phase']}）<br>{mw['hint']}</div>
"""]

    # 档位统计
    cnt = {g: 0 for g in GEAR_ORDER}
    for r in results:
        g = (r.get("advice") or {}).get("档位")
        if g in cnt:
            cnt[g] += 1
    p.append('<div class="stat">')
    for g in GEAR_ORDER:
        p.append(f'<div><b style="color:{GEAR_COLOR[g]}">{cnt[g]}</b><span>{g}</span></div>')
    p.append("</div>")

    # 决策卡片
    p.append("<h2>逐只决策</h2><div class='grid'>")
    for r in results:
        adv = r.get("advice") or {}
        tr = r.get("trend") or {}
        g = adv.get("档位", "—")
        col = GEAR_COLOR.get(g, FLAT)
        p.append(f"""<div class="fc">
<div style="position:absolute;left:0;top:0;bottom:0;width:4px;background:{col}"></div>
<h3>{_esc(r.get('name') or r.get('code'))} <span class="code">{r.get('code')}</span></h3>
<div class="gear" style="color:{col}">{g} · {adv.get('评分', '—')} 分
<small>{_esc(adv.get('操作', ''))} · 仓位 {adv.get('建议仓位%', '—')}%</small></div>
<div class="kv">
<span><i>净值日</i> {_esc(r.get('nav_date') or '—')}</span>
<span><i>近20日</i> {_num(tr.get('20日涨幅%'))}</span>
<span><i>近60日</i> {_num(tr.get('60日涨幅%'))}</span>
<span><i>RSI</i> {_esc(tr.get('RSI14'))}</span>
<span><i>行业健康</i> {_esc(r.get('industry_health'))}</span>
</div>""")
        key = (r.get("forecast") or {}).get("20日") or {}
        if key:
            p.append(f"""<div class="kv"><span><i>20日类比</i> 上涨概率 {_esc(key.get('上涨概率%'))}% ·
中位 {_num(key.get('中位数%'))} · 悲观 {_num(key.get('悲观P5%'))} / 乐观 {_num(key.get('乐观P95%'))}</span></div>""")
        sl, tp = adv.get("止损参考"), adv.get("止盈参考")
        if sl or tp:
            p.append(f"<div class='kv'><span><i>止损</i> {_esc(sl or '—')}</span>"
                     f"<span><i>止盈</i> {_esc(tp or '—')}</span></div>")
        reasons = adv.get("依据") or []
        if reasons:
            p.append("<ul class='why'>" + "".join(f"<li>{_esc(x)}</li>" for x in reasons[:4]) + "</ul>")
        if adv.get("失效条件"):
            p.append(f"<div class='inv'><b>失效条件：</b>{_esc(adv['失效条件'])}</div>")
        if r.get("error"):
            p.append(f"<div class='inv' style='background:#fdecea;color:#7f2318'>诊断失败：{_esc(r['error'])}</div>")
        p.append("</div>")
    p.append("</div>")

    # 评分对比图
    p.append("<h2>评分对比</h2><div class='card'><div id='c_score' class='chart'></div></div>")

    # 汇总表
    p.append("""<h2>明细表</h2><table class="grid"><thead><tr>
<th>名称</th><th>代码</th><th>评分</th><th>档位</th><th>建议仓位</th>
<th>近20日</th><th>近60日</th><th>20日上涨概率</th><th>行业健康分</th><th>核心行业</th>
</tr></thead><tbody>""")
    for r in sorted(results, key=lambda x: -(float((x.get("advice") or {}).get("评分") or 0))):
        adv = r.get("advice") or {}
        tr = r.get("trend") or {}
        fc = (r.get("forecast") or {}).get("20日") or {}
        tops = ", ".join(f"{x.get('行业')}{x.get('权重%')}%" for x in (r.get("industry_exposure") or [])[:3])
        p.append(f"""<tr><td>{_esc(r.get('name'))}</td><td>{r.get('code')}</td>
<td><b>{adv.get('评分', '—')}</b></td>
<td style="color:{GEAR_COLOR.get(adv.get('档位'), FLAT)};font-weight:650">{adv.get('档位', '—')}</td>
<td>{adv.get('建议仓位%', '—')}%</td>
<td>{_num(tr.get('20日涨幅%'))}</td><td>{_num(tr.get('60日涨幅%'))}</td>
<td>{_esc(fc.get('上涨概率%', '—'))}%</td><td>{_esc(r.get('industry_health'))}</td>
<td style="font-size:12px">{_esc(tops) or '—'}</td></tr>""")
    p.append("</tbody></table>")

    # 历史轨迹
    if len({h.get("日期") for h in hist}) >= 2:
        p.append("<h2>评分轨迹（历史留痕）</h2><div class='card'><div id='c_hist' class='chart'></div></div>")
    elif hist:
        p.append(f"<h2>历史留痕</h2><div class='card' style='font-size:13px;color:#6b7280'>"
                 f"已累计 {len(hist)} 条记录（{_esc(hist[0].get('日期'))} 起）。"
                 f"跨 2 个交易日以上后自动绘制评分轨迹图。</div>")

    data_js = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
    js_js = JS.replace("__UP__", UP).replace("__DOWN__", DOWN)
    p.append(
        '<div class="foot">数据来源：天天基金 / 东方财富 / 同花顺 · 生成时间 '
        + mw["now"]
        + '<br>本报告由量化模型自动生成，仅供研究参考，<b>不构成任何投资建议</b>。'
          '基金投资有风险，过往业绩不代表未来表现。</div></div>'
        + "<script>window.DATA = " + data_js + ";</script>"
        + "<script>" + js_js + "</script></body></html>"
    )
    return "".join(p)


def load_echarts() -> str:
    if EC_CACHE.exists():
        return EC_CACHE.read_text(encoding="utf-8", errors="ignore")
    return ""


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run(codes: list[str], bench: str, outdir: Path, horizon=(20, 60),
        start: str = "20230101") -> dict:
    now = dt.datetime.now()
    outdir.mkdir(parents=True, exist_ok=True)
    day_dir = outdir / "daily" / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    hist_csv = outdir / "history.csv"

    results, rows = [], []
    for i, c in enumerate(codes, 1):
        print(f"[{i}/{len(codes)}] 诊断 {c} ...", flush=True)
        t0 = time.time()
        try:
            d = dg.diagnose(c, bench, tuple(horizon), start)
            meta = d.get("meta", {}) or {}
            d["code"] = meta.get("代码", c)
            d["name"] = meta.get("名称", c)
            d["nav_date"] = meta.get("净值截止")
            with open(day_dir / f"{c}.json", "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1, default=str)
            row = _flatten(d, now)
            rows.append(row)
            append_history(hist_csv, row)
            results.append(d)
            print(f"    → {row['名称']} {row['档位']} {row['评分']}分 "
                  f"({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:                        # noqa: BLE001
            results.append({"code": c, "name": c, "error": f"{type(e).__name__}: {e}"[:160]})
            print(f"    → 失败 {type(e).__name__}: {str(e)[:80]}", flush=True)

    payload = {
        "date": now.strftime("%Y-%m-%d"),
        "generated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "bench": bench,
        "window": market_window(now),
        "results": results,
        "history": load_history(hist_csv)[-400:],
    }
    # 最近同类记录（用于轨迹图）
    html = build_html({**payload, "echarts": load_echarts()})
    panel = outdir / f"决策面板_{now.strftime('%Y%m%d_%H%M')}.html"
    panel.write_text(html, encoding="utf-8")
    latest = outdir / "最新决策面板.html"
    latest.write_text(html, encoding="utf-8")
    payload["panel"] = str(panel)

    # 控制台速览
    print("\n" + "=" * 62)
    print(f"{'名称':<16}{'代码':<9}{'评分':>5}  {'档位':<5}{'仓位':>5}  {'20日上涨':>8}")
    print("-" * 62)
    for r in sorted(results, key=lambda x: -(float((x.get("advice") or {}).get("评分") or 0))):
        adv = r.get("advice") or {}
        fc = (r.get("forecast") or {}).get("20日") or {}
        print(f"{str(r.get('name'))[:15]:<16}{r.get('code'):<9}"
              f"{str(adv.get('评分', '-')):>5}  {str(adv.get('档位', '-')):<5}"
              f"{str(adv.get('建议仓位%', '-')):>5}%  {str(fc.get('上涨概率%', '-')):>7}%")
    print("=" * 62)
    print(f"面板：{panel}")
    return payload


def main():
    ap = argparse.ArgumentParser(description="多基金持续追踪：批量诊断 + 历史留痕 + 当日决策面板")
    ap.add_argument("--funds", help="逗号分隔的基金代码，如 005827,519674")
    ap.add_argument("--watchlist", help="JSON 文件：[\"005827\", ...] 或 {\"funds\":[...],\"bench\":\"沪深300\"}")
    ap.add_argument("--bench", default="沪深300")
    ap.add_argument("--dir", default=os.environ.get("FUND_OUT", "output"),
                    help="输出目录（默认 ./output）")
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--panel-only", action="store_true",
                    help="不重新抓取，仅用当日已保存的 JSON 重新渲染面板（刷新时点提示）")
    a = ap.parse_args()

    codes, bench = [], a.bench
    if a.watchlist:
        obj = json.loads(Path(a.watchlist).read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            codes = obj.get("funds", [])
            bench = obj.get("bench", bench)
        else:
            codes = obj
    elif a.funds:
        codes = [x.strip() for x in a.funds.split(",") if x.strip()]

    mw = market_window()
    print(f"时点：{mw['now']} | {mw['phase']} | 决策窗口 {mw['urgency']}")
    print(mw["hint"].replace("<b>", "").replace("</b>", ""))

    outdir = Path(a.dir)
    if a.panel_only:
        day_dir = outdir / "daily" / dt.datetime.now().strftime("%Y-%m-%d")
        files = sorted(day_dir.glob("*.json")) if day_dir.exists() else []
        if not files:
            ap.error(f"{day_dir} 下没有已保存的诊断结果，请去掉 --panel-only")
        results = []
        for fp in files:
            d = json.loads(fp.read_text(encoding="utf-8"))
            meta = d.get("meta", {}) or {}
            d["code"] = meta.get("代码", fp.stem)
            d["name"] = meta.get("名称", fp.stem)
            d["nav_date"] = meta.get("净值截止")
            results.append(d)
        payload = {"date": dt.datetime.now().strftime("%Y-%m-%d"),
                   "generated": mw["now"], "bench": bench, "window": mw,
                   "results": results,
                   "history": load_history(outdir / "history.csv")[-400:]}
        html = build_html({**payload, "echarts": load_echarts()})
        now = dt.datetime.now()
        panel = outdir / f"决策面板_{now.strftime('%Y%m%d_%H%M')}.html"
        panel.write_text(html, encoding="utf-8")
        (outdir / "最新决策面板.html").write_text(html, encoding="utf-8")
        print(f"\n已渲染 {len(results)} 只 → {panel}")
        return
    if not codes:
        ap.error("需要 --funds 或 --watchlist")
    run(codes, bench, outdir)


if __name__ == "__main__":
    main()
