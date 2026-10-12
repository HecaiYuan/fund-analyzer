# -*- coding: utf-8 -*-
"""
主分析流程：基金 / 行业 / ETF 三合一分析，输出 JSON。

用法:
  python3.11 analyze.py --funds 005827,003096 --etfs 510300,512480 \
      --industries 半导体,白酒 --top-industries 40 --bench 沪深300 \
      --start 20230101 --out result.json
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime

import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, __file__.rsplit("/", 1)[0])

import datasource as ds            # noqa: E402
import metrics as mx               # noqa: E402


# ------------------------------ 基金 ------------------------------
def weekly(s: pd.Series) -> pd.Series:
    """按周采样，保留该周最后一个真实交易日日期（避免生成未来日期标签）。"""
    tmp = s.dropna().reset_index()
    tmp.columns = ["dt", "v"]
    tmp["wk"] = tmp["dt"].dt.to_period("W")
    out = tmp.groupby("wk").last().set_index("dt")["v"]
    return out


def analyze_fund(code: str, bench_series: pd.Series | None, start: str | None) -> dict:
    nav = ds.fund_nav(code)
    if start:
        nav = nav.loc[start:]
    basic = ds.fund_basic(code)
    name = basic.get("基金名称") or basic.get("基金简称") or code
    item = {"code": code, "name": name, "basic": {
        k: str(basic.get(k)) for k in ["基金公司", "基金经理", "基金类型", "成立时间",
                                       "最新规模", "基金评级", "业绩比较基准"]
        if basic.get(k) and str(basic.get(k)) not in ("<NA>", "nan")
    }}
    item["perf"] = mx.performance(nav["单位净值"], bench_series)
    if bench_series is not None:
        item["yearly"] = mx.yearly_returns(nav["单位净值"], bench_series).to_dict("records")
    # 净值曲线（周采样，控制体积）
    item["nav_curve"] = [[str(d.date()), round(float(v), 4)] for d, v in weekly(nav["单位净值"]).items()]
    if bench_series is not None:
        b, a = mx.align(bench_series, nav["单位净值"])
        bs = weekly(b / b.iloc[0])
        as_ = weekly(a / a.iloc[0])
        amap = {d: v for d, v in as_.items()}
        item["cmp_curve"] = [[str(d.date()),
                              round(float(amap[d]), 4) if d in amap else None,
                              round(float(v), 4)] for d, v in bs.items()]
    # 持仓与行业配置
    try:
        h, period = ds.fund_holdings(code)
        item["holdings"] = h.head(10).to_dict("records") if not h.empty else []
        item["holdings_period"] = period
        item["holdings_top10_weight"] = round(float(h["占净值比例"].head(10).sum()), 2) if not h.empty else None
    except Exception as e:                                  # noqa: BLE001
        item["holdings_error"] = str(e)[:80]
    try:
        ia = ds.fund_industry_allocation(code)
        item["industry_alloc"] = ia.head(8).to_dict("records") if not ia.empty else []
    except Exception as e:                                  # noqa: BLE001
        item["industry_alloc_error"] = str(e)[:80]
    return item


# ------------------------------ 行业 ------------------------------
def analyze_industries(names: list[str], top_n: int) -> dict:
    snap = ds.industry_snapshot()
    out = {"snapshot_date": str(datetime.now().date())}
    cols = ["板块", "涨跌幅", "总成交额", "净流入", "上涨家数", "下跌家数", "领涨股", "领涨股-涨跌幅"]
    cols = [c for c in cols if c in snap.columns]
    out["leaders"] = snap.sort_values("涨跌幅", ascending=False)[cols].head(12).to_dict("records")
    out["laggards"] = snap.sort_values("涨跌幅")[cols].head(8).to_dict("records")
    if "净流入" in snap.columns:
        out["flow_in"] = snap.sort_values("净流入", ascending=False)[["板块", "净流入", "涨跌幅"]].head(10).to_dict("records")
        out["flow_out"] = snap.sort_values("净流入")[["板块", "净流入", "涨跌幅"]].head(10).to_dict("records")
    # 动量：未指定则取成交额前列行业
    if not names:
        names = snap.sort_values("总成交额", ascending=False)["板块"].head(top_n).tolist()
    mom = ds.industry_momentum(names)
    scored = mx.industry_score(mom, snap)
    out["momentum"] = scored.to_dict("records")
    out["momentum_count"] = len(scored)
    return out


# ------------------------------ ETF ------------------------------
def analyze_etf(code: str, bench_series: pd.Series | None, start: str | None) -> dict:
    df = ds.etf_kline(code, start or "20230101")
    if df.empty:
        return {"code": code, "error": "无数据"}
    close = df["close"]
    item = {"code": code, "name": df.attrs.get("name") or ds.etf_name(code)}
    item["perf"] = mx.performance(close, bench_series)
    item["last_price"] = round(float(close.iloc[-1]), 4)
    item["last_date"] = str(close.index[-1].date())
    item["price_curve"] = [[str(d.date()), round(float(v), 4)] for d, v in weekly(close).items()]
    item["volume_recent"] = round(float(df["amount"].tail(5).mean() / 1e8), 2)   # 近5日均成交额(亿)
    return item


# ------------------------------ 主流程 ------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--funds", default="", help="基金代码，逗号分隔")
    ap.add_argument("--etfs", default="", help="ETF 代码，逗号分隔")
    ap.add_argument("--industries", default="", help="行业名称，逗号分隔；留空则按成交额取前列")
    ap.add_argument("--top-industries", type=int, default=40)
    ap.add_argument("--bench", default="沪深300")
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--out", default="result.json")
    a = ap.parse_args()

    result = {"meta": {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "bench": a.bench, "start": a.start}}

    bench_series = None
    if a.bench:
        try:
            bk = ds.index_kline(a.bench, a.start)
            bench_series = bk["close"]
            result["meta"]["bench_points"] = [[str(d.date()), round(float(v), 2)]
                                              for d, v in weekly(bench_series).items()]
        except Exception as e:                                # noqa: BLE001
            result["meta"]["bench_error"] = str(e)[:100]

    result["funds"] = []
    for c in [x.strip() for x in a.funds.split(",") if x.strip()]:
        try:
            result["funds"].append(analyze_fund(c, bench_series, a.start))
            print(f"[基金] {c} done", flush=True)
        except Exception as e:                                # noqa: BLE001
            result["funds"].append({"code": c, "error": str(e)[:120]})
            print(f"[基金] {c} FAIL {e}", flush=True)

    result["etfs"] = []
    for c in [x.strip() for x in a.etfs.split(",") if x.strip()]:
        try:
            result["etfs"].append(analyze_etf(c, bench_series, a.start))
            print(f"[ETF] {c} done", flush=True)
        except Exception as e:                                # noqa: BLE001
            result["etfs"].append({"code": c, "error": str(e)[:120]})
            print(f"[ETF] {c} FAIL {e}", flush=True)

    try:
        names = [x.strip() for x in a.industries.split(",") if x.strip()]
        result["industries"] = analyze_industries(names, a.top_industries)
        print(f"[行业] {result['industries'].get('momentum_count', 0)} 个板块 done", flush=True)
    except Exception as e:                                    # noqa: BLE001
        result["industries"] = {"error": str(e)[:120]}
        print(f"[行业] FAIL {e}", flush=True)

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)
    print("saved ->", a.out)


if __name__ == "__main__":
    main()
