# -*- coding: utf-8 -*-
"""
单只基金深度诊断：行业现状 -> 趋势预测 -> 当日买卖建议。

用法：
    python3.11 diagnose.py --fund 005827 --bench 沪深300 --out diag.json
    python3.11 diagnose.py --fund 519674 --horizon 20,60
"""
from __future__ import annotations

import argparse
import json
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import datasource as ds          # noqa: E402
import industry_map as imap      # noqa: E402
import metrics as mx             # noqa: E402

RF = 0.015


# ==========================================================================
# 1. 技术面信号
# ==========================================================================
def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd_hist(s: pd.Series) -> pd.Series:
    fast = s.ewm(span=12, adjust=False).mean()
    slow = s.ewm(span=26, adjust=False).mean()
    return (fast - slow) - (fast - slow).ewm(span=9, adjust=False).mean()


def _clip01(x):
    return float(np.clip(x, -1, 1)) if x == x else 0.0


def signal_frame(nav: pd.Series) -> pd.DataFrame:
    """逐日计算技术面信号，返回 DataFrame（每行一个交易日）。"""
    df = pd.DataFrame({"nav": nav})
    df["mom20"] = nav / nav.shift(20) - 1
    df["mom60"] = nav / nav.shift(60) - 1
    df["ma20"] = nav.rolling(20).mean()
    df["ma60"] = nav.rolling(60).mean()
    df["ma120"] = nav.rolling(120).mean()
    # 均线排列：多头 +1 / 空头 -1
    df["ma_arr"] = ((nav > df["ma20"]).astype(int)
                    + (df["ma20"] > df["ma60"]).astype(int)
                    + (df["ma60"] > df["ma120"]).astype(int)) / 3 * 2 - 1
    df["rsi"] = _rsi(nav)
    df["macd"] = _macd_hist(nav)
    df["macd_n"] = df["macd"] / nav.rolling(60).std().replace(0, np.nan) * 10
    df["pos"] = nav / nav.rolling(252).max() - 1          # 距一年高点回撤
    v20 = nav.pct_change().rolling(20).std()
    v250 = nav.pct_change().rolling(250).std()
    df["vol_r"] = v20 / v250.replace(0, np.nan)
    return df


def _score_row(r: pd.Series) -> float:
    """把单日信号合成 [-1, +1] 的综合分。

    权重：中期动量 30% | 均线排列 25% | 短期动量 15% | RSI 10% | MACD 10% | 位置 10%
    """
    mom60 = _clip01(r["mom60"] / 0.15)                    # 60日 ±15% 视为满档
    mom20 = _clip01(r["mom20"] / 0.08)
    ma = _clip01(r["ma_arr"])
    rsi = _clip01((r["rsi"] - 50) / 25)
    macd = _clip01(r["macd_n"] / 1.0)
    pos = _clip01((r["pos"] + 0.10) / 0.10)               # 回撤 10% 内为中性偏上
    return float(0.30 * mom60 + 0.25 * ma + 0.15 * mom20
                 + 0.10 * rsi + 0.10 * macd + 0.10 * pos)


def signal_score(sig: pd.DataFrame) -> pd.Series:
    """向量化计算综合信号分。"""
    mom60 = (sig["mom60"] / 0.15).clip(-1, 1)
    mom20 = (sig["mom20"] / 0.08).clip(-1, 1)
    ma = sig["ma_arr"].clip(-1, 1)
    rsi = ((sig["rsi"] - 50) / 25).clip(-1, 1)
    macd = (sig["macd_n"]).clip(-1, 1)
    pos = ((sig["pos"] + 0.10) / 0.10).clip(-1, 1)
    return (0.30 * mom60 + 0.25 * ma + 0.15 * mom20
            + 0.10 * rsi + 0.10 * macd + 0.10 * pos)


# ==========================================================================
# 2. 历史类比：信号相似情形下的未来收益分布
# ==========================================================================
def analog_forecast(nav: pd.Series, horizon: int = 20, k: int = 100) -> dict:
    """
    统计类比预测：在本基金自身历史中找出「信号分最接近当前」的 k 个交易日，
    统计其后 horizon 个交易日的实际收益分布。

    注意：滚动窗口样本存在重叠与自相关，结果是经验分布而非独立同分布检验，
    仅作概率参考，不构成收益承诺。
    """
    sig = signal_frame(nav)
    sc = signal_score(sig).dropna()
    if len(sc) < horizon + 130:
        return {"error": "历史样本不足，无法做统计类比"}
    now = float(sc.iloc[-1])
    fwd = (nav.shift(-horizon) / nav - 1).reindex(sc.index).dropna()
    valid = sc.index.intersection(fwd.index)
    if len(valid) < 40:
        return {"error": "有效样本不足"}
    sc_v, fwd_v = sc.loc[valid], fwd.loc[valid]
    # 取信号距离最近的 k 个样本
    dist = (sc_v - now).abs().sort_values()
    pick = dist.head(min(k, len(dist))).index
    r = fwd_v.loc[pick].dropna()
    if len(r) < 20:
        return {"error": "相似样本不足"}
    base = fwd_v                                          # 全样本基准（无条件分布）
    def q(s, p):
        return float(np.percentile(s, p))
    return {
        "当前信号分": round(now, 3),
        "相似样本数": int(len(r)),
        "样本信号区间": [round(float(sc_v.loc[pick].min()), 3),
                         round(float(sc_v.loc[pick].max()), 3)],
        "上涨概率%": round(float((r > 0).mean()) * 100, 1),
        "基准上涨概率%": round(float((base > 0).mean()) * 100, 1),
        "中位数%": round(float(r.median()) * 100, 2),
        "均值%": round(float(r.mean()) * 100, 2),
        "基准中位数%": round(float(base.median()) * 100, 2),
        "乐观P95%": round(q(r, 95) * 100, 2),
        "悲观P5%": round(q(r, 5) * 100, 2),
        "最好%": round(float(r.max()) * 100, 2),
        "最差%": round(float(r.min()) * 100, 2),
        "收益分布": [round(float(x) * 100, 2) for x in r.values],
    }


# ==========================================================================
# 3. 持仓 -> 行业暴露 -> 行业现状
# ==========================================================================
def holding_industries(holdings: pd.DataFrame, max_n: int = 10) -> tuple[list[dict], dict]:
    """把持仓个股解析为同花顺行业。返回 (明细, 覆盖统计)。"""
    out = []
    for _, r in holdings.head(max_n).iterrows():
        code = str(r.get("代码", "")).zfill(6)
        hname = str(r.get("名称", ""))
        w = pd.to_numeric(pd.Series([r.get("占净值比例")]), errors="coerce").iloc[0]
        rec = {"代码": code, "名称": hname, "占净值比例": None if w != w else float(w)}
        if not code or code == "000000":
            rec.update({"行业": None, "来源": "无效代码"})
            out.append(rec)
            continue
        try:
            res = imap.resolve(code, hname)
            rec["行业"] = res["industry"]
            rec["来源"] = res["source"]
            if res.get("matched_name"):
                rec["匹配标的"] = res["matched_name"]
            if res.get("warn"):
                rec["提示"] = res["warn"]
        except Exception:                                 # noqa: BLE001
            rec.update({"行业": None, "来源": "解析失败"})
        # 个股自身近期表现（仅 A 股可获取）
        use_code = str(rec.get("代码", ""))
        if rec.get("来源", "").startswith(("直接命中", "别名表", "映射表", "模糊")) or "按名搜索" in str(rec.get("来源", "")):
            try:
                k = ds.stock_kline(use_code)
                if len(k) > 60:
                    c = k["close"]
                    rec["个股20日%"] = round((c.iloc[-1] / c.iloc[-21] - 1) * 100, 2)
                    rec["个股60日%"] = round((c.iloc[-1] / c.iloc[-61] - 1) * 100, 2)
                    rec["个股年内%"] = round(ds._ytd(c), 2)
            except Exception:                             # noqa: BLE001
                pass
        time.sleep(0.2)                                   # 缓解接口限流
        out.append(rec)

    tw = sum(x["占净值比例"] for x in out if x.get("占净值比例"))
    cw = sum(x["占净值比例"] for x in out if x.get("行业") and x.get("占净值比例"))
    cov = {"已识别权重": round(cw, 2), "总权重": round(tw, 2),
           "覆盖度%": round(cw / tw * 100, 1) if tw else 0}
    return out, cov


def industry_exposure(hi: list[dict]) -> pd.DataFrame:
    """按占净值比例聚合行业暴露。"""
    rows = [x for x in hi if x.get("行业") and x.get("占净值比例")]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    g = df.groupby("行业").agg(
        权重=("占净值比例", "sum"),
        个股数=("代码", "count"),
        成分=("名称", lambda s: "、".join(s)),
    ).reset_index().sort_values("权重", ascending=False)
    total = g["权重"].sum()
    g["权重%"] = (g["权重"] / total * 100).round(2)
    return g


def industry_status(expo: pd.DataFrame) -> pd.DataFrame:
    """给行业暴露表补充：动量 / 资金流 / 波动 / 回撤 / 景气分。"""
    if expo.empty:
        return expo
    names = expo["行业"].tolist()
    try:
        snap = ds.industry_snapshot()
    except Exception:                                     # noqa: BLE001
        snap = pd.DataFrame()
    rows = []
    for n in names:
        try:
            d = ds.industry_index(n, "20250101")
        except Exception:                                 # noqa: BLE001
            rows.append({"行业": n, "数据": "不可用"})
            continue
        if len(d) < 60:
            rows.append({"行业": n, "数据": "样本不足"})
            continue
        c = d["close"]
        rec = {"行业": n, "最新点位": round(float(c.iloc[-1]), 2)}
        for w in (5, 20, 60):
            if len(c) > w:
                rec[f"{w}日%"] = round((c.iloc[-1] / c.iloc[-1 - w] - 1) * 100, 2)
        rec["年内%"] = round(ds._ytd(c), 2)
        rec["年化波动%"] = round(float(c.pct_change().std() * np.sqrt(250) * 100), 2)
        rec["最大回撤%"] = round(float(ds._max_drawdown(c) * 100), 2)
        rec["距年内高点%"] = round((c.iloc[-1] / c.tail(250).max() - 1) * 100, 2)
        rows.append(rec)
    st = pd.DataFrame(rows)
    if not snap.empty and "板块" in snap.columns:
        f = snap[["板块", "净流入", "涨跌幅"]].copy() if "涨跌幅" in snap.columns else snap[["板块", "净流入"]].copy()
        f["板块"] = f["板块"].astype(str)
        fmap = dict(zip(f["板块"], pd.to_numeric(f["净流入"], errors="coerce")))
        st["今日净流入亿"] = st["行业"].map(fmap)
        if "涨跌幅" in f.columns:
            pmap = dict(zip(f["板块"], pd.to_numeric(f["涨跌幅"], errors="coerce")))
            st["今日涨跌%"] = st["行业"].map(pmap)
    out = expo.merge(st, on="行业", how="left")
    # 行业景气分：动量 50% + 资金流 25% + 风险 25%（相对自身历史，非横截面）
    def z(s):
        s = pd.to_numeric(s, errors="coerce")
        sd = s.std()
        return (s - s.mean()) / sd if sd and sd == sd else s * 0
    mcol = "60日%" if "60日%" in out.columns else None
    out["_m"] = z(out[mcol]) if mcol else 0
    out["_f"] = z(out.get("今日净流入亿", pd.Series(dtype=float))) if "今日净流入亿" in out else 0
    out["_r"] = -z(out["最大回撤%"]) if "最大回撤%" in out.columns else 0
    out["行业景气分"] = (out["_m"].fillna(0) * 0.5 + out["_f"].fillna(0) * 0.25
                      + out["_r"].fillna(0) * 0.25).round(3)
    # 动量数据缺失的行业：景气分不可信，标记出来
    if mcol:
        out.loc[out[mcol].isna(), "行业景气分"] = np.nan
    out = out.drop(columns=[c for c in ["_m", "_f", "_r"] if c in out.columns])
    # JSON 友好：NaN -> None
    return out.replace({np.nan: None})


def industry_health_score(st: pd.DataFrame) -> float:
    """按权重加权得到基金整体的行业景气分（-1..1）。"""
    if st.empty or "行业景气分" not in st.columns:
        return 0.0
    w = pd.to_numeric(st["权重%"], errors="coerce").fillna(0)
    s = pd.to_numeric(st["行业景气分"], errors="coerce")
    mask = s.notna()
    if mask.sum() == 0 or w[mask].sum() == 0:
        return 0.0
    # 仅在能算出景气分的行业内重新归一化权重
    return float((w[mask] * s[mask]).sum() / w[mask].sum())


# ==========================================================================
# 4. 综合建议
# ==========================================================================
def make_advice(sig_now: float, ind_score: float, perf: dict,
                f20: dict, f60: dict, nav: pd.Series) -> dict:
    """综合评分 -> 操作档位 -> 具体建议。"""
    # 位置分：距一年高点回撤越小越好；但过热（>0）也要扣分
    dd_now = float(nav.iloc[-1] / nav.tail(252).max() - 1)
    pos_score = float(np.clip((dd_now + 0.15) / 0.15, -1, 1))   # -15% -> -1, 0% -> +1
    # 风险分：波动与回撤控制
    vol = pd.to_numeric(pd.Series([perf.get("年化波动%")]), errors="coerce").iloc[0]
    mdd = pd.to_numeric(pd.Series([perf.get("最大回撤%")]), errors="coerce").iloc[0]
    risk_score = 0.0
    if vol == vol:
        risk_score += float(np.clip((28 - vol) / 18, -1, 1)) * 0.5
    if mdd == mdd:
        risk_score += float(np.clip((45 - mdd) / 30, -1, 1)) * 0.5

    total = (sig_now * 0.40 + ind_score * 0.25 + pos_score * 0.20 + risk_score * 0.15)
    score = int(round((total + 1) / 2 * 100))                    # 映射到 0-100
    score = int(np.clip(score, 0, 100))

    # 档位
    if score >= 72:
        level, act, color = "买入", "积极买入 / 加仓", "buy"
    elif score >= 62:
        level, act, color = "偏多", "可分批买入，控制单次仓位", "add"
    elif score >= 48:
        level, act, color = "持有", "持有观望，不加不减", "hold"
    elif score >= 38:
        level, act, color = "减仓", "逢反弹减仓，降低暴露", "reduce"
    else:
        level, act, color = "卖出", "规避 / 择机清仓", "sell"

    # 建议仓位：波动越高仓位越低
    base_pos = {"buy": 0.75, "add": 0.55, "hold": 0.40, "reduce": 0.20, "sell": 0.05}[color]
    vol_adj = 1.0 if not vol == vol else float(np.clip(22 / max(vol, 8), 0.6, 1.3))
    pos_pct = int(round(base_pos * vol_adj * 100 / 5) * 5)
    pos_pct = int(np.clip(pos_pct, 0, 90))

    # 止损 / 止盈参考（按年化波动折算）
    v = vol / 100 if vol == vol else 0.22
    stop = round(float(nav.iloc[-1]) * (1 - 1.5 * v * np.sqrt(20 / 250)), 4)
    take = round(float(nav.iloc[-1]) * (1 + 2.0 * v * np.sqrt(60 / 250)), 4)

    up20 = f20.get("上涨概率%") if "上涨概率%" in f20 else None
    med20 = f20.get("中位数%") if "中位数%" in f20 else None
    p5_20 = f20.get("悲观P5%") if "悲观P5%" in f20 else None
    up60 = f60.get("上涨概率%") if "上涨概率%" in f60 else None

    reasons = []
    reasons.append(f"技术面信号分 {sig_now:+.2f}（{'偏强' if sig_now > 0.2 else '偏弱' if sig_now < -0.2 else '中性'}）")
    reasons.append(f"持仓行业景气分 {ind_score:+.2f}")
    reasons.append(f"距一年高点 {dd_now * 100:.1f}%")
    if up20 is not None:
        reasons.append(f"历史类比：未来20日上涨概率 {up20}%，中位 {med20}%，悲观情形 {p5_20}%")
    if up60 is not None:
        reasons.append(f"未来60日上涨概率 {up60}%")

    invalid = []
    if sig_now > 0.3:
        invalid.append("净值跌破 60 日均线且连续 3 日未收回")
    else:
        invalid.append("净值站上 60 日均线并放量")
    invalid.append("持仓行业出现单日净流出超 5% 的极端情况")

    return {
        "评分": score, "档位": level, "操作": act, "色标": color,
        "建议仓位%": pos_pct,
        "止损参考": stop, "止盈参考": take,
        "当前净值": round(float(nav.iloc[-1]), 4),
        "距一年高点%": round(dd_now * 100, 2),
        "分项": {
            "技术面信号": round(sig_now, 3),
            "行业景气": round(ind_score, 3),
            "位置": round(pos_score, 3),
            "风险控制": round(risk_score, 3),
        },
        "依据": reasons,
        "失效条件": invalid,
    }


# ==========================================================================
# 5. 主流程
# ==========================================================================
def diagnose(code: str, bench: str = "沪深300", horizons=(20, 60),
             start: str = "20230101") -> dict:
    nav = ds.fund_nav(code)
    if start:
        nav = nav.loc[start:]
    if nav.empty or len(nav) < 60:
        return {"error": f"基金 {code} 净值数据不足"}
    nav_s = nav["单位净值"]

    basic = ds.fund_basic(code)
    name = basic.get("基金名称") or basic.get("基金简称") or code

    bench_s = None
    try:
        bk = ds.index_kline(bench, start)
        bench_s = bk["close"]
    except Exception:                                     # noqa: BLE001
        pass

    perf = mx.performance(nav_s, bench_s)
    out = {
        "meta": {"代码": code, "名称": name, "基准": bench,
                 "净值截止": str(nav_s.index[-1].date()),
                 "样本起点": str(nav_s.index[0].date()),
                 "生成时间": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")},
        "profile": {k: basic.get(k) for k in
                    ["基金公司", "基金经理", "基金类型", "成立时间", "资产规模",
                     "业绩比较基准", "管理费率", "托管费率"] if basic.get(k)},
        "perf": perf,
    }

    # 净值曲线（周采样）
    s = nav_s.resample("W").last().dropna()
    out["nav_curve"] = [[str(d.date()), round(float(v), 4)] for d, v in s.items()]
    if bench_s is not None:
        bb, aa = mx.align(bench_s, nav_s)
        bs = (bb / bb.iloc[0]).resample("W").last()
        as_ = (aa / aa.iloc[0]).resample("W").last()
        out["cmp_curve"] = [[str(d.date()),
                             round(float(as_.get(d, float("nan"))), 4) if d in as_.index else None,
                             round(float(v), 4)] for d, v in bs.items()]

    # 年度收益
    try:
        yr = mx.yearly_returns(nav_s, bench_s)
        out["yearly"] = yr.to_dict("records")
    except Exception:                                     # noqa: BLE001
        pass

    # ---- 持仓 -> 行业 ----
    holdings, period = ds.fund_holdings(code)
    out["holdings_period"] = period
    if not holdings.empty:
        hi, cov = holding_industries(holdings)
        out["holdings"] = hi
        out["industry_coverage"] = cov
        expo = industry_exposure(hi)
        if not expo.empty:
            st = industry_status(expo)
            out["industry_exposure"] = st.to_dict("records")
            out["industry_health"] = round(industry_health_score(st), 3)
            top10 = pd.to_numeric(holdings["占净值比例"], errors="coerce").head(10).sum()
            out["top10_weight"] = round(float(top10), 2)
        else:
            out["industry_exposure"] = []
            out["industry_health"] = 0.0
    else:
        out["holdings"] = []
        out["industry_exposure"] = []
        out["industry_health"] = 0.0
        out["industry_coverage"] = {"已识别权重": 0, "总权重": 0, "覆盖度%": 0}

    # ---- 趋势信号 ----
    sig = signal_frame(nav_s)
    sc = signal_score(sig)
    last = sig.iloc[-1]
    out["trend"] = {
        "当前信号分": round(float(sc.iloc[-1]), 3),
        "信号分近60日": [round(float(x), 3) for x in sc.tail(60).values],
        "20日涨幅%": round(float(last["mom20"]) * 100, 2) if last["mom20"] == last["mom20"] else None,
        "60日涨幅%": round(float(last["mom60"]) * 100, 2) if last["mom60"] == last["mom60"] else None,
        "RSI14": round(float(last["rsi"]), 1) if last["rsi"] == last["rsi"] else None,
        "MA20": round(float(last["ma20"]), 4) if last["ma20"] == last["ma20"] else None,
        "MA60": round(float(last["ma60"]), 4) if last["ma60"] == last["ma60"] else None,
        "MA120": round(float(last["ma120"]), 4) if last["ma120"] == last["ma120"] else None,
        "排列": ("多头" if last["ma20"] > last["ma60"] > last["ma120"]
                else "空头" if last["ma20"] < last["ma60"] < last["ma120"] else "纠缠"),
        "波动率比": round(float(last["vol_r"]), 2) if last["vol_r"] == last["vol_r"] else None,
    }
    # 分项明细
    det = {
        "中期动量": round(float((last["mom60"] / 0.15).clip(-1, 1)), 3),
        "均线排列": round(float(last["ma_arr"]), 3),
        "短期动量": round(float((last["mom20"] / 0.08).clip(-1, 1)), 3),
        "RSI": round(float(((last["rsi"] - 50) / 25).clip(-1, 1)), 3),
        "MACD": round(float(last["macd_n"]), 3) if last["macd_n"] == last["macd_n"] else None,
        "位置": round(float(((last["pos"] + 0.10) / 0.10).clip(-1, 1)), 3),
    }
    out["trend"]["明细"] = det
    out["trend"]["距一年高点%"] = round(float(last["pos"]) * 100, 2)

    # ---- 概率预测 ----
    out["forecast"] = {}
    for h in horizons:
        out["forecast"][f"{h}日"] = analog_forecast(nav_s, h)

    # ---- 建议 ----
    f20 = out["forecast"].get("20日", {})
    f60 = out["forecast"].get("60日", {})
    out["advice"] = make_advice(float(sc.iloc[-1]), out["industry_health"],
                                perf, f20, f60, nav_s)
    return out


def main():
    ap = argparse.ArgumentParser(description="单只基金深度诊断：行业现状 + 趋势预测 + 买卖建议")
    ap.add_argument("--fund", required=True, help="基金代码，如 005827")
    ap.add_argument("--bench", default="沪深300")
    ap.add_argument("--horizon", default="20,60", help="预测周期（交易日）")
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    hs = [int(x) for x in a.horizon.split(",") if x.strip()]
    d = diagnose(a.fund, a.bench, tuple(hs), a.start)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1, default=str)
        print(f"已写入 {a.out}")
    else:
        print(json.dumps(d, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
