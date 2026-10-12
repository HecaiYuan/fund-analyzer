# -*- coding: utf-8 -*-
"""
单只基金诊断报告渲染（离线自包含 HTML，ECharts 内联）。

    python3.11 diagnose_report.py --data diag.json --out ../output/诊断报告.html
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime

UP = "#c0392b"        # 涨-红
DOWN = "#1e8e3e"      # 跌-绿
FLAT = "#6b7280"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(_ROOT, "assets")          # 项目内自带，离线可用
ECHARTS = os.environ.get("FUND_ECHARTS") or os.path.join(CACHE, "echarts.min.js")
if not os.path.exists(ECHARTS):                # 兼容旧布局
    _legacy = os.path.join(_ROOT, ".cache", "echarts.min.js")
    if os.path.exists(_legacy):
        CACHE, ECHARTS = os.path.join(_ROOT, ".cache"), _legacy

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;
background:#f5f6f8;color:#1f2329;line-height:1.6;padding:24px 16px}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:25px;font-weight:700;letter-spacing:-.3px}
.sub{color:#6b7280;font-size:13px;margin-top:6px}
.card{background:#fff;border-radius:12px;padding:20px 22px;margin:16px 0;
box-shadow:0 1px 3px rgba(0,0,0,.06)}
h2{font-size:17px;font-weight:650;margin-bottom:14px;display:flex;align-items:center;gap:8px}
h2::before{content:'';width:3px;height:15px;background:#2563eb;border-radius:2px}
h3{font-size:14px;font-weight:620;margin:16px 0 8px;color:#374151}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f8fafc;text-align:left;padding:9px 10px;font-weight:600;color:#475569;
border-bottom:1px solid #e5e7eb;white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid #f1f3f5}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.up{color:%s;font-weight:600}
.down{color:%s;font-weight:600}
.flat{color:%s}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
@media(max-width:900px){.grid2,.grid3{grid-template-columns:1fr}}
.kv{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px dashed #eef0f2;font-size:13px}
.kv:last-child{border:none}
.kv span:first-child{color:#6b7280}
.kv span:last-child{font-weight:600}
/* 建议卡片 */
.verdict{border-radius:14px;padding:22px 26px;color:#fff;margin:16px 0;
display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:20px}
.v-buy{background:linear-gradient(135deg,#c0392b,#e74c3c)}
.v-add{background:linear-gradient(135deg,#d35400,#e67e22)}
.v-hold{background:linear-gradient(135deg,#5b6b7c,#7f8c8d)}
.v-reduce{background:linear-gradient(135deg,#1e8e3e,#27ae60)}
.v-sell{background:linear-gradient(135deg,#145a32,#1e8e3e)}
.v-left{flex:1;min-width:250px}
.v-tag{font-size:34px;font-weight:800;letter-spacing:1px}
.v-desc{font-size:15px;opacity:.95;margin-top:4px}
.v-right{text-align:right;min-width:150px}
.v-score{font-size:44px;font-weight:800;line-height:1}
.v-score small{font-size:15px;font-weight:500;opacity:.85}
.v-pos{font-size:13px;opacity:.92;margin-top:8px}
.gauge{height:7px;background:rgba(255,255,255,.28);border-radius:4px;margin-top:10px;overflow:hidden}
.gauge i{display:block;height:100%;background:#fff;border-radius:4px}
.chart{width:100%;height:320px}
.chart-sm{width:100%;height:260px}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;
background:#eef2f7;color:#475569;margin-right:5px}
.tag-ok{background:#e8f5e9;color:#1e8e3e}
.tag-warn{background:#fff3e0;color:#e65100}
.tag-bad{background:#ffebee;color:#c62828}
.reason{padding:9px 0 9px 22px;position:relative;font-size:13.5px;border-bottom:1px dashed #eef0f2}
.reason:last-child{border:none}
.reason::before{content:'▸';position:absolute;left:4px;color:#2563eb;font-weight:700}
.warn-box{background:#fff8e1;border-left:3px solid #f57c00;padding:12px 16px;
border-radius:0 8px 8px 0;font-size:12.5px;color:#6d4c41;margin-top:12px}
.warn-box b{color:#e65100}
.foot{text-align:center;color:#9ca3af;font-size:11.5px;padding:22px 0}
.bar{height:5px;background:#eef0f2;border-radius:3px;overflow:hidden;min-width:52px;display:inline-block;vertical-align:middle;width:52px}
.bar i{display:block;height:100%}
.src{font-size:11px;color:#9ca3af}
"""

JS = r"""
const D = window.DATA;
const CU = '__UP__', CD = '__DOWN__', CF = '__FLAT__';
const COL = {buy:'#c0392b', add:'#e67e22', hold:'#7f8c8d', reduce:'#27ae60', sell:'#1e8e3e'};
function col(v){ return v>0?CU:(v<0?CD:CF); }
function fmt(v, d=2){ return v===null||v===undefined||isNaN(v)?'—':Number(v).toFixed(d); }
function pct(v, d=2){ if(v===null||v===undefined||isNaN(v)) return '—';
  const s = Number(v).toFixed(d)+'%'; return `<span class="${v>0?'up':(v<0?'down':'flat')}">${v>0?'+':''}${s}</span>`; }
function mk(id, opt){ const el=document.getElementById(id); if(!el) return;
  const c = echarts.init(el); c.setOption(opt); window.addEventListener('resize',()=>c.resize()); }

// 1) 净值 vs 基准
if(D.cmp_curve && D.cmp_curve.length){
  mk('c_nav', {tooltip:{trigger:'axis'}, legend:{data:['本基金','基准'],top:0,textStyle:{fontSize:11}},
    grid:{left:52,right:18,top:34,bottom:30},
    xAxis:{type:'category',data:D.cmp_curve.map(r=>r[0]),axisLabel:{fontSize:10}},
    yAxis:{type:'value',scale:true,axisLabel:{fontSize:10,formatter:v=>v.toFixed(2)}},
    series:[{name:'基准',type:'line',data:D.cmp_curve.map(r=>r[2]),showSymbol:false,lineStyle:{width:1.4,color:'#94a3b8'}},
            {name:'本基金',type:'line',data:D.cmp_curve.map(r=>r[1]),showSymbol:false,lineStyle:{width:2,color:'#2563eb'},
             areaStyle:{opacity:.10,color:'#2563eb'}}]});
}

// 2) 行业暴露饼图
const ind = D.industry_exposure||[];
if(ind.length){
  mk('c_pie',{tooltip:{trigger:'item',formatter:p=>`${p.name}<br/>权重 ${p.value}%`},
    legend:{type:'scroll',orient:'vertical',right:0,top:10,textStyle:{fontSize:11}},
    series:[{type:'pie',radius:['40%','66%'],center:['38%','52%'],
      data:ind.map(x=>({name:x['行业'],value:x['权重%']})),
      label:{fontSize:10,formatter:'{b}\n{d}%'},
      itemStyle:{borderColor:'#fff',borderWidth:1.5}}]});
  // 行业动量条形
  const names = ind.map(x=>x['行业']);
  mk('c_ind',{tooltip:{trigger:'axis'},grid:{left:60,right:20,top:16,bottom:26},
    xAxis:{type:'value',axisLabel:{fontSize:10,formatter:'{value}%'}},
    yAxis:{type:'category',data:names,axisLabel:{fontSize:10}},
    series:[{name:'60日涨幅',type:'bar',data:ind.map(x=>({value:x['60日%']??0,
      itemStyle:{color:(x['60日%']??0)>=0?CU:CD}})),barWidth:'58%',
      label:{show:true,position:'right',fontSize:9,formatter:p=>fmt(p.value,1)+'%'}}]});
}

// 3) 信号分走势
if(D.trend && D.trend['信号分近60日']){
  const s = D.trend['信号分近60日'];
  mk('c_sig',{tooltip:{trigger:'axis'},grid:{left:44,right:16,top:16,bottom:26},
    xAxis:{type:'category',data:s.map((_,i)=>i+1),axisLabel:{fontSize:10}},
    yAxis:{type:'value',min:-1,max:1,axisLabel:{fontSize:10}},
    series:[{type:'line',data:s,showSymbol:false,lineStyle:{width:1.8,color:'#2563eb'},
      areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[
        {offset:0,color:'rgba(37,99,235,.28)'},{offset:1,color:'rgba(37,99,235,.02)'}])},
      markLine:{silent:true,symbol:'none',data:[
        {yAxis:0,lineStyle:{color:'#9ca3af',type:'dashed'}},
        {yAxis:0.3,lineStyle:{color:CU,type:'dotted',width:1}},
        {yAxis:-0.3,lineStyle:{color:CD,type:'dotted',width:1}}]}}]});
}

// 4) 预测收益分布直方图
function histo(id, arr){
  if(!arr||!arr.length) return;
  const lo = Math.min(...arr), hi = Math.max(...arr);
  const n = 14, w = (hi-lo)/n || 1;
  const bins = new Array(n).fill(0);
  arr.forEach(v=>{ let i=Math.floor((v-lo)/w); if(i>=n)i=n-1; if(i<0)i=0; bins[i]++; });
  const labels = bins.map((_,i)=>(lo+i*w).toFixed(1)+'%');
  mk(id,{tooltip:{trigger:'axis',formatter:p=>`区间 ${p[0].axisValue}<br/>样本 ${p[0].value} 次`},
    grid:{left:44,right:16,top:18,bottom:34},
    xAxis:{type:'category',data:labels,axisLabel:{fontSize:9,rotate:38}},
    yAxis:{type:'value',axisLabel:{fontSize:10}},
    series:[{type:'bar',data:bins.map((v,i)=>({value:v,
      itemStyle:{color:(lo+i*w+w/2)>=0?CU:CD}})),barWidth:'82%'}]});
}
if(D.forecast){ const f=D.forecast['20日']; if(f&&f['收益分布']) histo('c_f20', f['收益分布']);
  const g=D.forecast['60日']; if(g&&g['收益分布']) histo('c_f60', g['收益分布']); }
"""


def _pct(v, d=2):
    if v is None:
        return '<span class="flat">—</span>'
    try:
        v = float(v)
    except (TypeError, ValueError):
        return '<span class="flat">—</span>'
    cls = "up" if v > 0 else ("down" if v < 0 else "flat")
    return f'<span class="{cls}">{v:+.{d}f}%</span>'


def _num(v, d=2):
    return "—" if v is None else f"{float(v):.{d}f}"


def _bar(v, lo=-1, hi=1):
    """把 -1..1 的分值画成迷你条。"""
    if v is None:
        return '<span class="flat">—</span>'
    r = max(0.0, min(1.0, (float(v) - lo) / (hi - lo))) * 100
    c = UP if v > 0 else (DOWN if v < 0 else FLAT)
    return f'<span class="bar"><i style="width:{r:.0f}%;background:{c}"></i></span> {float(v):+.2f}'


def build_html(d: dict) -> str:
    meta = d.get("meta", {})
    adv = d.get("advice", {})
    trend = d.get("trend", {})
    perf = d.get("perf", {})
    inds = d.get("industry_exposure", []) or []
    holds = d.get("holdings", []) or []
    cov = d.get("industry_coverage", {})
    fc = d.get("forecast", {})
    gen = meta.get("生成时间") or datetime.now().strftime("%Y-%m-%d %H:%M")

    color = adv.get("色标", "hold")
    parts = []
    parts.append(f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{meta.get('名称', '')} 基金诊断报告</title>
<style>{CSS.replace('%s', UP, 1).replace('%s', DOWN, 1).replace('%s', FLAT, 1)}</style></head><body><div class="wrap">""")

    # ---------- 头部 ----------
    parts.append(f"""<div><h1>{meta.get('名称','')} <span style="color:#9ca3af;font-weight:400">({meta.get('代码','')})</span></h1>
<div class="sub">{meta.get('基金类型','') or ''} ｜ 基准 {meta.get('基准','')} ｜ 净值截止 {meta.get('净值截止','')} ｜ 生成 {gen}</div></div>""")

    # ---------- 核心结论 ----------
    score = adv.get("评分", 50)
    parts.append(f"""<div class="verdict v-{color}">
  <div class="v-left"><div class="v-tag">{adv.get('档位','')}</div>
    <div class="v-desc">{adv.get('操作','')}</div>
    <div class="gauge"><i style="width:{score}%"></i></div></div>
  <div class="v-right"><div class="v-score">{score}<small>/100</small></div>
    <div class="v-pos">建议仓位 <b>{adv.get('建议仓位%','—')}%</b></div>
    <div class="v-pos">止损 {adv.get('止损参考','—')} ｜ 止盈 {adv.get('止盈参考','—')}</div></div>
</div>""")

    # 依据
    rs = "".join(f"<div class='reason'>{r}</div>" for r in adv.get("依据", []))
    iv = "、".join(adv.get("失效条件", []))
    parts.append(f"""<div class="card"><h2>判断依据与失效条件</h2>
<div class="grid2"><div>{rs}</div>
<div><h3 style="margin-top:0">建议失效条件（触发即需重新评估）</h3>
<div style="font-size:13px;color:#b71c1c">{iv}</div>
<h3>分项打分</h3>
<div class="kv"><span>技术面信号</span><span>{_bar(adv.get('分项',{}).get('技术面信号'))}</span></div>
<div class="kv"><span>行业景气</span><span>{_bar(adv.get('分项',{}).get('行业景气'))}</span></div>
<div class="kv"><span>位置（距一年高点）</span><span>{_bar(adv.get('分项',{}).get('位置'))}</span></div>
<div class="kv"><span>风险控制</span><span>{_bar(adv.get('分项',{}).get('风险控制'))}</span></div>
</div></div>
<div class="warn-box"><b>口径说明：</b>评分 = 技术面信号 40% + 行业景气 25% + 位置 20% + 风险控制 15%。
「建议仓位」指该基金在权益类资产中的占比上限参考，已按年化波动做反比调整。
止损/止盈位为按近 1 年波动率折算的技术参考位，<b>不是承诺价格</b>。</div></div>""")

    # ---------- 绩效 ----------
    kv = [("累计收益", _pct(perf.get("累计收益%"))), ("年化收益", _pct(perf.get("年化收益%"))),
          ("最大回撤", f"<span class='down'>-{_num(perf.get('最大回撤%'))}%</span>"),
          ("年化波动", _num(perf.get("年化波动%")) + "%"),
          ("夏普比率", _num(perf.get("夏普比率"))), ("卡玛比率", _num(perf.get("卡玛比率"))),
          ("月度胜率", _num(perf.get("月度胜率%"), 1) + "%"),
          ("近1年", _pct(perf.get("近1年%"))), ("近6月", _pct(perf.get("近6月%"))),
          ("近3月", _pct(perf.get("近3月%")))]
    if perf.get("基准累计%") is not None:
        kv += [("基准累计", _pct(perf.get("基准累计%"))),
               ("区间超额", _pct(perf.get("区间超额收益%"))),
               ("年化Alpha", _pct(perf.get("年化Alpha"))),
               ("Beta", _num(perf.get("Beta"))),
               ("信息比率", _num(perf.get("信息比率")))]
    rows = "".join(f"<div class='kv'><span>{k}</span><span>{v}</span></div>" for k, v in kv)
    prof = d.get("profile", {})
    pr = "".join(f"<div class='kv'><span>{k}</span><span>{v}</span></div>" for k, v in prof.items())
    parts.append(f"""<div class="card"><h2>基金画像与历史表现</h2>
<div class="grid2">
<div><h3 style="margin-top:0">基本信息</h3>{pr or '<div class="sub">暂无</div>'}</div>
<div><h3 style="margin-top:0">风险收益（{perf.get('样本起点','')} 起）</h3>{rows}</div></div>
<div id='c_nav' class='chart'></div></div>""")

    # ---------- 行业现状 ----------
    if inds:
        cov_txt = cov.get("覆盖度%", 0)
        cov_cls = "tag-ok" if cov_txt >= 80 else ("tag-warn" if cov_txt >= 50 else "tag-bad")
        rows = []
        for x in inds:
            rows.append(f"""<tr><td><b>{x.get('行业')}</b></td>
<td class="num">{_num(x.get('权重%'))}%</td>
<td class="num">{_pct(x.get('5日%'))}</td>
<td class="num">{_pct(x.get('20日%'))}</td>
<td class="num">{_pct(x.get('60日%'))}</td>
<td class="num">{_pct(x.get('年内%'))}</td>
<td class="num">{_pct(x.get('今日涨跌%'))}</td>
<td class="num">{_num(x.get('今日净流入亿'), 1)}</td>
<td class="num">{_num(x.get('年化波动%'), 1)}</td>
<td class="num">{_bar(x.get('行业景气分'))}</td></tr>""")
        tbl = f"""<table><thead><tr><th>行业</th><th class="num">权重</th><th class="num">5日</th>
<th class="num">20日</th><th class="num">60日</th><th class="num">年内</th>
<th class="num">今日</th><th class="num">净流入(亿)</th><th class="num">波动</th><th class="num">景气分</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>"""

        hr = []
        for h in holds:
            hr.append(f"""<tr><td>{h.get('名称')}</td><td>{h.get('行业') or '—'}</td>
<td class="num">{_num(h.get('占净值比例'))}%</td>
<td class="num">{_pct(h.get('个股20日%'))}</td>
<td class="num">{_pct(h.get('个股60日%'))}</td>
<td class="num">{_pct(h.get('个股年内%'))}</td>
<td class="src">{h.get('来源','')}</td></tr>""")
        htbl = f"""<table><thead><tr><th>持股</th><th>归属行业</th><th class="num">占净值</th>
<th class="num">20日</th><th class="num">60日</th><th class="num">年内</th><th>归因方式</th></tr></thead>
<tbody>{''.join(hr)}</tbody></table>"""

        parts.append(f"""<div class="card"><h2>行业现状（持仓穿透）
<span class="tag {cov_cls}">覆盖度 {cov_txt}%</span>
<span class="tag">报告期 {d.get('holdings_period','')}</span>
<span class="tag">前十集中度 {d.get('top10_weight','—')}%</span></h2>
<div class="grid2"><div id='c_pie' class='chart-sm'></div><div id='c_ind' class='chart-sm'></div></div>
<h3>行业暴露与动量</h3>{tbl}
<h3>前十大持仓（含个股近期表现）</h3>{htbl}
<div class="warn-box"><b>归因说明：</b>行业由持仓个股穿透聚合而来（东财 F10 板块 → 同花顺行业）。
港股/中概标的无 A 股行情，仅按内置映射表归类，<b>个股近期涨跌幅显示为「—」</b>。
权重在已识别行业内归一化，覆盖度低于 80% 时行业结论需谨慎。</div></div>""")

    # ---------- 趋势预测 ----------
    t_rows = [("当前信号分", _num(trend.get("当前信号分"), 3)),
              ("均线排列", trend.get("排列", "—")),
              ("20日涨幅", _pct(trend.get("20日涨幅%"))),
              ("60日涨幅", _pct(trend.get("60日涨幅%"))),
              ("RSI(14)", _num(trend.get("RSI14"), 1)),
              ("MA20", _num(trend.get("MA20"), 4)),
              ("MA60", _num(trend.get("MA60"), 4)),
              ("MA120", _num(trend.get("MA120"), 4)),
              ("距一年高点", _pct(trend.get("距一年高点%"))),
              ("波动率比(20/250)", _num(trend.get("波动率比")))]
    tkv = "".join(f"<div class='kv'><span>{k}</span><span>{v}</span></div>" for k, v in t_rows)
    det = trend.get("明细", {})
    dkv = "".join(f"<div class='kv'><span>{k}</span><span>{_bar(v)}</span></div>" for k, v in det.items())

    frows = []
    for k in ("20日", "60日"):
        f = fc.get(k) or {}
        if not f or f.get("error"):
            continue
        frows.append(f"""<tr><td><b>{k}</b></td><td class="num">{f.get('相似样本数')}</td>
<td class="num">{_num(f.get('上涨概率%'),1)}%</td>
<td class="num">{_num(f.get('基准上涨概率%'),1)}%</td>
<td class="num">{_pct(f.get('中位数%'))}</td>
<td class="num">{_pct(f.get('乐观P95%'))}</td>
<td class="num">{_pct(f.get('悲观P5%'))}</td>
<td class="num">{_pct(f.get('最差%'))}</td></tr>""")
    ftbl = f"""<table><thead><tr><th>周期</th><th class="num">相似样本</th><th class="num">上涨概率</th>
<th class="num">基准概率</th><th class="num">中位数</th><th class="num">乐观P95</th>
<th class="num">悲观P5</th><th class="num">历史最差</th></tr></thead>
<tbody>{''.join(frows)}</tbody></table>""" if frows else '<div class="sub">样本不足，未生成概率预测</div>'

    parts.append(f"""<div class="card"><h2>趋势信号与涨跌概率</h2>
<div class="grid2"><div><h3 style="margin-top:0">当前技术状态</h3>{tkv}</div>
<div><h3 style="margin-top:0">信号分项（-1 弱 ← → +1 强）</h3>{dkv}</div></div>
<div id='c_sig' class='chart-sm'></div>
<h3>历史类比预测</h3>{ftbl}
<div class="grid2"><div id='c_f20' class='chart-sm'></div><div id='c_f60' class='chart-sm'></div></div>
<div class="warn-box"><b>预测方法（必读）：</b>在本基金<b>自身历史</b>中，找出「技术信号分与当前最接近」的
100 个交易日，统计它们之后 N 个交易日的<b>实际</b>收益分布，得出上涨概率与区间。
这是<b>经验类比，不是因果预测</b>：滚动窗口样本存在重叠与自相关，且历史规律会在风格切换时失效。
<b>请把它当作赔率参考，而非收益承诺。</b></div></div>""")

    # ---------- 年度收益 ----------
    yr = d.get("yearly") or []
    if yr:
        rr = "".join(
            f"<tr><td>{y.get('年份')}</td><td class='num'>{_pct(y.get('标的%'))}</td>"
            f"<td class='num'>{_pct(y.get('基准%'))}</td><td class='num'>{_pct(y.get('超额%'))}</td></tr>"
            for y in yr)
        parts.append(f"""<div class="card"><h2>分年度收益</h2>
<table><thead><tr><th>年份</th><th class="num">本基金</th><th class="num">基准</th>
<th class="num">超额</th></tr></thead><tbody>{rr}</tbody></table></div>""")

    payload = json.dumps(d, ensure_ascii=False, default=str).replace("</", "<\\/")
    script = JS.replace("__UP__", UP).replace("__DOWN__", DOWN).replace("__FLAT__", FLAT)
    tail = (
        '<div class="foot">数据来源：天天基金（fund.eastmoney.com）、东方财富、同花顺（10jqka）'
        f'｜生成时间 {gen}<br>本报告由量化模型自动生成，仅供研究参考，'
        '<b>不构成任何投资建议</b>。基金投资有风险，过往业绩不代表未来表现。</div>'
        f'</div><script>window.DATA = {payload};</script>'
        f"<script>{script}</script></body></html>"
    )
    parts.append(tail)
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="基金诊断报告.html")
    a = ap.parse_args()
    with open(a.data, encoding="utf-8") as f:
        d = json.load(f)
    html = build_html(d)
    # 内联 ECharts（若本地缓存存在则离线自包含）
    if os.path.exists(ECHARTS):
        with open(ECHARTS, encoding="utf-8") as f:
            lib = f.read()
        html = html.replace("<script>window.DATA",
                            "<script>" + lib + "</script>\n<script>window.DATA", 1)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成 {a.out}（{len(html) / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
