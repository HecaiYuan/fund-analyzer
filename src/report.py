# -*- coding: utf-8 -*-
"""
报告渲染：把 analyze.py 产出的 JSON 渲染为单文件 HTML 分析报告。

用法:
  python3.11 report.py --data result.json --out 基金行业分析报告.html
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

UP = "#c0392b"      # 涨-红
DOWN = "#1e8e3e"    # 跌-绿
FLAT = "#6b7280"
ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"
def _locate_echarts() -> str:
    """按 环境变量 > assets/ > .cache/ 顺序定位 echarts.min.js（便于项目迁移到任意目录）。"""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    for cand in (os.environ.get("FUND_ECHARTS"),
                 os.path.join(root, "assets", "echarts.min.js"),
                 os.path.join(root, ".cache", "echarts.min.js")):
        if cand and os.path.exists(cand):
            return cand
    return os.path.join(root, "assets", "echarts.min.js")   # 不存在时由 echarts_tag 下载


_ECHARTS_CACHE = _locate_echarts()


def echarts_tag(offline: bool = True) -> str:
    """返回 ECharts 引入标签。默认内联（离线可用），失败则回退 CDN。"""
    if not offline:
        return f'<script src="{ECHARTS_CDN}"></script>'
    import os
    path = os.path.abspath(_ECHARTS_CACHE)
    if not os.path.exists(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            import urllib.request
            urllib.request.urlretrieve(ECHARTS_CDN, path)
        except Exception:                                # noqa: BLE001
            return f'<script src="{ECHARTS_CDN}"></script>'
    try:
        with open(path, encoding="utf-8") as f:
            return "<script>" + f.read().replace("</", "<\\/") + "</script>"
    except Exception:                                    # noqa: BLE001
        return f'<script src="{ECHARTS_CDN}"></script>'


def color(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return FLAT
    return UP if v > 0 else (DOWN if v < 0 else FLAT)


def fmt(v, suffix="", nd=2) -> str:
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:+.{nd}f}{suffix}" if suffix in ("%",) else f"{f:.{nd}f}{suffix}"


# ------------------------------ 洞察生成 ------------------------------
def build_insights(d: dict) -> list[str]:
    out = []
    funds = [f for f in d.get("funds", []) if "perf" in f]
    etfs = [e for e in d.get("etfs", []) if "perf" in e]
    ind = d.get("industries", {})

    if funds:
        best = max(funds, key=lambda x: (x["perf"].get("夏普比率") or -99))
        worst = min(funds, key=lambda x: (x["perf"].get("夏普比率") if x["perf"].get("夏普比率") is not None else 99))
        out.append(f"风险调整后收益最高的是 <b>{best['name']}</b>（夏普 {best['perf'].get('夏普比率')}），"
                   f"最低的是 <b>{worst['name']}</b>（夏普 {worst['perf'].get('夏普比率')}）。")
        for f in funds:
            p = f["perf"]
            alpha, mdd = p.get("年化Alpha"), p.get("最大回撤%")
            if alpha is not None and alpha < -5:
                out.append(f"<b>{f['name']}</b> 年化 Alpha {alpha}%，相对{d.get('meta',{}).get('bench','基准')}持续跑输，"
                           f"最大回撤 {mdd}%，需警惕风格逆风。")
            elif alpha is not None and alpha > 5:
                out.append(f"<b>{f['name']}</b> 年化 Alpha {alpha}%，超额收益显著，最大回撤 {mdd}%。")

    mom = ind.get("momentum") or []
    if mom:
        top = mom[0]
        bottom = mom[-1]
        out.append(f"行业动量榜首 <b>{top['行业']}</b>（60日 {top.get('60日涨幅%')}%，综合得分 {top.get('综合得分')}），"
                   f"垫底 <b>{bottom['行业']}</b>（60日 {bottom.get('60日涨幅%')}%）。")
        volatile = sorted(mom, key=lambda x: -(x.get("年化波动%") or 0))[:1]
        if volatile:
            out.append(f"波动最剧烈的行业是 <b>{volatile[0]['行业']}</b>（年化波动 {volatile[0].get('年化波动%')}%），"
                       f"仓位管理需匹配其风险特征。")
    fi = ind.get("flow_in") or []
    fo = ind.get("flow_out") or []
    if fi and fo:
        out.append(f"当日资金净流入前列：{('、'.join(x['板块'] for x in fi[:3]))}；"
                   f"净流出前列：{('、'.join(x['板块'] for x in fo[:3]))}。")

    if etfs:
        ranked = sorted(etfs, key=lambda x: -(x["perf"].get("近3月%") or -999))
        if ranked:
            out.append(f"ETF 近3月动量最强的是 <b>{ranked[0]['name']}</b>（{ranked[0]['perf'].get('近3月%')}%），"
                       f"最弱的是 <b>{ranked[-1]['name']}</b>（{ranked[-1]['perf'].get('近3月%')}%）。")
    return out


# ------------------------------ 表格渲染 ------------------------------
def perf_table(rows: list[dict], keys: list[str], name_key: str = "name") -> str:
    head = "".join(f"<th>{k}</th>" for k in ["名称", "代码"] + keys)
    body = ""
    for r in rows:
        p = r.get("perf", {})
        tds = f"<td class='nm'>{r.get(name_key, '')}</td><td class='code'>{r.get('code','')}</td>"
        for k in keys:
            v = p.get(k)
            if k in ("最大回撤%",) and v is not None:
                tds += f"<td style='color:{DOWN}'>{v}</td>"
            elif isinstance(v, (int, float)):
                tds += f"<td style='color:{color(v) if k not in ('年化波动%','跟踪误差%','最大回撤%','回撤修复天数') else '#374151'}'>{v}</td>"
            else:
                tds += f"<td>{v if v is not None else '—'}</td>"
        body += f"<tr>{tds}</tr>"
    return f"<table class='grid'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def simple_table(records: list[dict], cols: list[str], color_cols: set | None = None) -> str:
    color_cols = color_cols or set()
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = ""
    for r in records:
        tds = ""
        for c in cols:
            v = r.get(c)
            if c in color_cols:
                tds += f"<td style='color:{color(v)};font-weight:600'>{fmt(v,'%')}</td>"
            elif isinstance(v, float):
                tds += f"<td>{v:.2f}</td>"
            else:
                tds += f"<td>{v if v is not None else '—'}</td>"
        body += f"<tr>{tds}</tr>"
    return f"<table class='grid'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


# ------------------------------ HTML ------------------------------
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
background:#f4f6f9;color:#1f2937;line-height:1.65;padding:0 0 60px}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px}
header{background:linear-gradient(120deg,#1e3a5f 0%,#2d5a8c 60%,#3a7ca5 100%);color:#fff;padding:44px 0 38px;margin-bottom:26px}
header h1{font-size:30px;font-weight:700;letter-spacing:.5px}
header .sub{margin-top:10px;font-size:14px;opacity:.9}
.tags{margin-top:16px;display:flex;gap:8px;flex-wrap:wrap}
.tag{background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);padding:4px 12px;border-radius:20px;font-size:12.5px}
h2{font-size:20px;margin:34px 0 14px;padding-left:12px;border-left:4px solid #2d5a8c;color:#1e3a5f}
h3{font-size:15.5px;margin:20px 0 10px;color:#374151}
.card{background:#fff;border-radius:12px;padding:20px 22px;box-shadow:0 1px 3px rgba(16,24,40,.08);margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 3px rgba(16,24,40,.08)}
.kpi .lab{font-size:12px;color:#6b7280}
.kpi .val{font-size:22px;font-weight:700;margin-top:4px}
table.grid{width:100%;border-collapse:collapse;font-size:13.5px}
table.grid th{background:#eef2f7;color:#374151;font-weight:600;text-align:right;padding:9px 10px;white-space:nowrap;border-bottom:1px solid #dbe3ec}
table.grid th:first-child,table.grid th:nth-child(2){text-align:left}
table.grid td{padding:9px 10px;text-align:right;border-bottom:1px solid #f0f3f7;white-space:nowrap}
table.grid td.nm{text-align:left;font-weight:600}
table.grid td.code{text-align:left;color:#9ca3af;font-size:12px}
table.grid tbody tr:hover{background:#fafbfc}
.chart{width:100%;height:340px}
.chart.tall{height:420px}
.insights{background:#fffaf0;border-left:4px solid #e0a458;padding:16px 20px;border-radius:8px}
.insights li{margin:8px 0 8px 18px;font-size:14px}
.note{font-size:12.5px;color:#6b7280;margin-top:10px}
.pill{display:inline-block;background:#eef2f7;border-radius:6px;padding:2px 8px;font-size:12px;margin:2px 4px 2px 0;color:#374151}
.fund-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
footer{text-align:center;color:#9ca3af;font-size:12.5px;margin-top:36px;line-height:1.9}
.warn{background:#fef2f2;border-left:4px solid #c0392b;padding:12px 16px;border-radius:8px;font-size:13px;color:#7f1d1d}
"""

JS = """
const D = window.DATA;
const CU = '__UP__', CD = '__DOWN__';
function mk(id, opt){const el=document.getElementById(id); if(!el) return; echarts.init(el).setOption(opt);}
const baseGrid = {left:60,right:24,top:36,bottom:36};
const axisStyle = {axisLine:{lineStyle:{color:'#cbd5e1'}},axisLabel:{color:'#64748b',fontSize:11},
  splitLine:{lineStyle:{color:'#f1f5f9'}}};

// 1) 行业综合得分
(function(){
  const m = (D.industries&&D.industries.momentum)||[];
  if(!m.length) return;
  const s = m.slice().sort((a,b)=>a['综合得分']-b['综合得分']);
  const show = s.slice(-18);
  mk('c_ind_score',{grid:{...baseGrid,left:80},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
    xAxis:{type:'value',...axisStyle},
    yAxis:{type:'category',data:show.map(x=>x['行业']),...axisStyle,splitLine:{show:false}},
    series:[{type:'bar',data:show.map(x=>({value:x['综合得分'],
      itemStyle:{color:x['综合得分']>=0?CU:CD,borderRadius:[0,3,3,0]}})),
      label:{show:true,position:'right',fontSize:10,color:'#64748b',formatter:p=>p.value}}]});
})();

// 2) 行业今日涨跌
(function(){
  const l=(D.industries&&D.industries.leaders)||[];
  if(!l.length) return;
  const arr=l.slice().sort((a,b)=>a['涨跌幅']-b['涨跌幅']);
  mk('c_ind_today',{grid:{...baseGrid,left:80},tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
    xAxis:{type:'value',...axisStyle,axisLabel:{formatter:'{value}%',color:'#64748b',fontSize:11}},
    yAxis:{type:'category',data:arr.map(x=>x['板块']),...axisStyle,splitLine:{show:false}},
    series:[{type:'bar',data:arr.map(x=>({value:x['涨跌幅'],itemStyle:{color:x['涨跌幅']>=0?CU:CD,borderRadius:[0,3,3,0]}})),
      label:{show:true,position:'right',fontSize:10,color:'#64748b',formatter:p=>p.value+'%'}}]});
})();

// 3) 基金净值走势（归一化）
(function(){
  const f=(D.funds||[]).filter(x=>x.cmp_curve&&x.cmp_curve.length);
  if(!f.length) return;
  const pal=['#2d5a8c','#c0392b','#1e8e3e','#e0a458','#7c3aed','#0891b2'];
  const series=f.map((x,i)=>({name:x.name,type:'line',showSymbol:false,smooth:true,
    lineStyle:{width:2,color:pal[i%pal.length]},itemStyle:{color:pal[i%pal.length]},
    data:x.cmp_curve.map(r=>[r[0],r[1]===null?null:+(r[1]*100).toFixed(2)])}));
  // 基准取第一只基金的第三列
  series.push({name:D.meta.bench,type:'line',showSymbol:false,smooth:true,
    lineStyle:{width:2,type:'dashed',color:'#94a3b8'},itemStyle:{color:'#94a3b8'},
    data:f[0].cmp_curve.map(r=>[r[0],+(r[2]*100).toFixed(2)])});
  mk('c_fund_nav',{grid:baseGrid,tooltip:{trigger:'axis',valueFormatter:v=>v===null?'-':v+'%'},
    legend:{top:4,textStyle:{fontSize:11,color:'#475569'}},
    xAxis:{type:'category',data:f[0].cmp_curve.map(r=>r[0]),...axisStyle},
    yAxis:{type:'value',...axisStyle,axisLabel:{formatter:'{value}%',color:'#64748b',fontSize:11}},
    series:series});
})();

// 4) 基金年度收益
(function(){
  const f=(D.funds||[]).filter(x=>x.yearly&&x.yearly.length);
  if(!f.length) return;
  const years=f[0].yearly.map(r=>r['年份']);
  const pal=['#2d5a8c','#c0392b','#1e8e3e','#e0a458'];
  mk('c_fund_year',{grid:baseGrid,tooltip:{trigger:'axis',valueFormatter:v=>v+'%'},
    legend:{top:4,textStyle:{fontSize:11,color:'#475569'}},
    xAxis:{type:'category',data:years,...axisStyle},
    yAxis:{type:'value',...axisStyle,axisLabel:{formatter:'{value}%',color:'#64748b',fontSize:11}},
    series:f.map((x,i)=>({name:x.name,type:'bar',data:x.yearly.map(r=>r['标的%']),
      itemStyle:{color:pal[i%pal.length],borderRadius:[3,3,0,0]}}))});
})();

// 5) ETF 走势
(function(){
  const e=(D.etfs||[]).filter(x=>x.price_curve&&x.price_curve.length);
  if(!e.length) return;
  const pal=['#2d5a8c','#c0392b','#1e8e3e','#e0a458','#7c3aed'];
  const byDate={};
  e.forEach(x=>x.price_curve.forEach(r=>{byDate[r[0]]=1;}));
  const dates=Object.keys(byDate).sort();
  const series=e.map((x,i)=>{
    const m=new Map(x.price_curve.map(r=>[r[0],r[1]]));
    return {name:`${x.name}(${x.code})`,type:'line',showSymbol:false,smooth:true,
      lineStyle:{width:2,color:pal[i%pal.length]},itemStyle:{color:pal[i%pal.length]},
      data:dates.map(d=>[d,m.has(d)?m.get(d):null])};
  });
  mk('c_etf_nav',{grid:baseGrid,tooltip:{trigger:'axis'},legend:{top:4,textStyle:{fontSize:11,color:'#475569'}},
    xAxis:{type:'category',data:dates,...axisStyle},
    yAxis:{type:'value',scale:true,...axisStyle},
    series:series});
})();

// 6) 基金持仓
(function(){
  const f=(D.funds||[]).filter(x=>x.holdings&&x.holdings.length);
  if(!f.length) return;
  const h=f[0].holdings.slice(0,10).slice().reverse();
  mk('c_hold',{grid:{...baseGrid,left:70},tooltip:{trigger:'axis',axisPointer:{type:'shadow'},valueFormatter:v=>v+'%'},
    xAxis:{type:'value',...axisStyle},
    yAxis:{type:'category',data:h.map(x=>x['名称']),...axisStyle,splitLine:{show:false}},
    series:[{type:'bar',data:h.map(x=>x['占净值比例']),itemStyle:{color:'#2d5a8c',borderRadius:[0,3,3,0]},
      label:{show:true,position:'right',fontSize:10,color:'#64748b',formatter:'{c}%'}}]});
})();

// 7) 行业配置（基金）
(function(){
  const f=(D.funds||[]).filter(x=>x.industry_alloc&&x.industry_alloc.length);
  if(!f.length) return;
  const d=f[0].industry_alloc.map(x=>({name:x['行业类别'],value:x['占净值比例']}));
  mk('c_alloc',{tooltip:{trigger:'item',formatter:'{b}: {c}%'},
    legend:{bottom:0,textStyle:{fontSize:11}},
    series:[{type:'pie',radius:['38%','66%'],center:['50%','44%'],data:d,
      label:{formatter:'{b} {c}%',fontSize:11,color:'#475569'},
      itemStyle:{borderColor:'#fff',borderWidth:2}}]});
})();
"""


def build_html(d: dict, offline: bool = True) -> str:
    meta = d.get("meta", {})
    funds = d.get("funds", [])
    etfs = d.get("etfs", [])
    ind = d.get("industries", {})
    insights = build_insights(d)
    gen = meta.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))

    parts = []
    parts.append(f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>基金 · 行业 · ETF 分析报告</title>
{echarts_tag(offline)}
<style>{CSS}</style></head><body>
<header><div class="wrap">
<h1>基金 · 行业 · ETF 分析报告</h1>
<div class="sub">生成时间 {gen} ｜ 对比基准 {meta.get('bench','—')} ｜ 数据区间 {meta.get('start','—')} 起</div>
<div class="tags">
<span class="tag">基金 {len(funds)} 只</span>
<span class="tag">ETF {len(etfs)} 只</span>
<span class="tag">行业板块 {ind.get('momentum_count', 0)} 个</span>
<span class="tag">数据源：天天基金 / 东方财富 / 同花顺</span>
</div></div></header><div class="wrap">""")

    # ---- 核心结论 ----
    if insights:
        lis = "".join(f"<li>{x}</li>" for x in insights)
        parts.append(f"<h2>核心结论</h2><div class='card insights'><ul>{lis}</ul></div>")

    # ---- 市场概览 ----
    bench_pts = meta.get("bench_points") or []
    if bench_pts:
        first, last = bench_pts[0], bench_pts[-1]
        chg = (last[1] / first[1] - 1) * 100 if first[1] else 0
        recent = [p for p in bench_pts if p[0] >= bench_pts[max(0, len(bench_pts) - 13)][0]]
        r_chg = (recent[-1][1] / recent[0][1] - 1) * 100 if recent[0][1] else 0
        parts.append(f"""<h2>市场基准</h2><div class="kpis">
<div class="kpi"><div class="lab">{meta.get('bench')} 最新点位</div><div class="val">{last[1]:.2f}</div></div>
<div class="kpi"><div class="lab">区间涨跌（{first[0]} 起）</div>
<div class="val" style="color:{color(chg)}">{chg:+.2f}%</div></div>
<div class="kpi"><div class="lab">近3月涨跌</div>
<div class="val" style="color:{color(r_chg)}">{r_chg:+.2f}%</div></div>
<div class="kpi"><div class="lab">数据截止</div><div class="val" style="font-size:17px">{last[0]}</div></div>
</div>""")

    # ---- 行业 ----
    if ind and not ind.get("error"):
        parts.append("<h2>行业板块</h2>")
        if ind.get("leaders"):
            parts.append("<div class='card'><h3>今日领涨板块（前12）</h3>" +
                         simple_table(ind["leaders"],
                                      ["板块", "涨跌幅", "总成交额", "净流入", "上涨家数", "下跌家数", "领涨股", "领涨股-涨跌幅"],
                                      {"涨跌幅", "领涨股-涨跌幅"}) +
                         "<div class='note'>成交额单位：亿元；净流入单位：亿元</div></div>")
        if ind.get("laggards"):
            parts.append("<div class='card'><h3>今日领跌板块（后8）</h3>" +
                         simple_table(ind["laggards"],
                                      ["板块", "涨跌幅", "总成交额", "净流入", "上涨家数", "下跌家数", "领涨股"],
                                      {"涨跌幅"}) + "</div>")
        if ind.get("flow_in") or ind.get("flow_out"):
            parts.append("<div class='fund-grid'>")
            if ind.get("flow_in"):
                parts.append("<div class='card'><h3>资金净流入 TOP10</h3>" +
                             simple_table(ind["flow_in"], ["板块", "净流入", "涨跌幅"], {"涨跌幅"}) + "</div>")
            if ind.get("flow_out"):
                parts.append("<div class='card'><h3>资金净流出 TOP10</h3>" +
                             simple_table(ind["flow_out"], ["板块", "净流入", "涨跌幅"], {"涨跌幅"}) + "</div>")
            parts.append("</div>")
        if ind.get("momentum"):
            cols = ["名称", "5日涨幅%", "20日涨幅%", "60日涨幅%", "120日涨幅%", "年内涨幅%", "年化波动%", "最大回撤%", "综合得分"]
            recs = ind["momentum"]
            cols = [c for c in cols if any(c in r for r in recs)]
            head = "".join(f"<th>{c}</th>" for c in cols)
            body = ""
            for r in recs:
                tds = f"<td class='nm'>{r.get('行业','')}</td>"
                for c in cols[1:]:
                    v = r.get(c)
                    if c == "最大回撤%":
                        tds += f"<td style='color:{DOWN}'>{v}</td>"
                    elif c == "综合得分":
                        tds += f"<td style='color:{color(v)};font-weight:700'>{v}</td>"
                    elif c == "年化波动%":
                        tds += f"<td>{v}</td>"
                    else:
                        tds += f"<td style='color:{color(v)}'>{v}</td>"
                body += f"<tr>{tds}</tr>"
            parts.append("<div class='card'><h3>行业动量与综合得分（按得分排序）</h3>"
                         f"<table class='grid'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
                         "<div class='note'>综合得分 = 60日动量(50%) + 当日资金流(25%) + 回撤控制(25%)，标准化后加权</div></div>")
            parts.append("<div class='card'><h3>行业综合得分（前18）</h3><div id='c_ind_score' class='chart tall'></div></div>")
            parts.append("<div class='card'><h3>今日板块涨跌幅</h3><div id='c_ind_today' class='chart'></div></div>")

    # ---- 基金 ----
    if funds:
        parts.append("<h2>公募基金</h2>")
        cards = ""
        for f in funds:
            b = f.get("basic", {})
            if b:
                pills = "".join(f"<span class='pill'>{k}：{v}</span>" for k, v in b.items()
                                if k not in ("投资策略", "投资目标"))
                cards += f"<div class='card'><h3>{f.get('name','')}（{f.get('code','')}）</h3><div>{pills}</div></div>"
        if cards:
            parts.append("<div class='fund-grid'>" + cards + "</div>")

        keys = ["累计收益%", "年化收益%", "近1年%", "近3月%", "年化波动%", "最大回撤%",
                "夏普比率", "卡玛比率", "月度胜率%", "年化Alpha", "Beta", "区间超额收益%"]
        keys = [k for k in keys if any(k in (f.get("perf") or {}) for f in funds)]
        parts.append("<div class='card'><h3>绩效对比（基准：" + str(meta.get("bench", "")) + "）</h3>" +
                     perf_table([f for f in funds if "perf" in f], keys) +
                     "<div class='note'>最大回撤为正数表示净值从高点回落幅度；夏普/卡玛为风险调整后收益指标</div></div>")
        if any(f.get("cmp_curve") for f in funds):
            parts.append("<div class='card'><h3>净值走势（起点归一化 = 100）</h3><div id='c_fund_nav' class='chart tall'></div></div>")
        if any(f.get("yearly") for f in funds):
            parts.append("<div class='card'><h3>年度收益对比</h3><div id='c_fund_year' class='chart'></div></div>")
        # 持仓与行业配置
        hf = next((f for f in funds if f.get("holdings")), None)
        if hf:
            parts.append(f"<div class='card'><h3>{hf.get('name','')} 前十大持仓（{hf.get('holdings_period','最新')}）</h3>"
                         + simple_table(hf["holdings"], ["代码", "名称", "占净值比例", "持股数", "持仓市值"])
                         + f"<div class='note'>前十大持仓集中度 {hf.get('holdings_top10_weight','—')}%｜持股数单位：万股；市值单位：万元</div></div>")
            parts.append("<div class='card'><h3>前十大持仓占比</h3><div id='c_hold' class='chart'></div></div>")
        af = next((f for f in funds if f.get("industry_alloc")), None)
        if af:
            parts.append(f"<div class='card'><h3>{af.get('name','')} 行业配置</h3><div id='c_alloc' class='chart'></div></div>")

    # ---- ETF ----
    if etfs:
        parts.append("<h2>ETF</h2>")
        keys = ["累计收益%", "年化收益%", "近1年%", "近3月%", "近1月%", "年化波动%",
                "最大回撤%", "夏普比率", "卡玛比率", "年化Alpha", "Beta"]
        keys = [k for k in keys if any(k in (e.get("perf") or {}) for e in etfs)]
        parts.append("<div class='card'><h3>ETF 绩效对比</h3>" +
                     perf_table([e for e in etfs if "perf" in e], keys) +
                     "<div class='note'>价格采用前复权；近5日均成交额单位：亿元</div></div>")
        if any(e.get("price_curve") for e in etfs):
            parts.append("<div class='card'><h3>ETF 价格走势</h3><div id='c_etf_nav' class='chart tall'></div></div>")

    # ---- 风险提示 ----
    parts.append("""<h2>风险提示</h2><div class="warn">
本报告基于天天基金、东方财富、同花顺公开数据自动生成，所有指标为历史统计结果，不构成任何投资建议。
场外基金净值披露存在滞后（通常 T+1），与场内 ETF 的统计截止日可能不同；行业板块采用同花顺分类，
与申万/中信分类口径存在差异。过往业绩不代表未来表现，投资须谨慎。
</div>""")

    data_js = json.dumps(d, ensure_ascii=False, default=str).replace("</", "<\\/")
    js_code = JS.replace("__UP__", UP).replace("__DOWN__", DOWN)
    parts.append(f"""<footer>数据来源：天天基金（fund.eastmoney.com）、东方财富（push2his）、同花顺（10jqka）｜
生成时间 {gen}<br>指标口径：年化按 365 天折算，无风险利率取 1.5%，波动率按 250 交易日年化</footer></div>
<script>window.DATA = {data_js};</script>
<script>{js_code}</script></body></html>""")
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="基金行业分析报告.html")
    ap.add_argument("--online", action="store_true", help="改用 CDN 引入 ECharts（文件更小，但需联网）")
    a = ap.parse_args()
    with open(a.data, encoding="utf-8") as f:
        d = json.load(f)
    html = build_html(d, offline=not a.online)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"saved -> {a.out} ({len(html)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
