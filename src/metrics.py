# -*- coding: utf-8 -*-
"""
指标层：基金 / 行业 / ETF 的风险收益与相对强弱指标。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 250
RF = 0.015          # 无风险利率（年化），用于夏普比率


# ------------------------- 基础统计 -------------------------
def max_drawdown(s: pd.Series) -> float:
    """最大回撤（正数表示回撤幅度比例）。"""
    if s.empty:
        return np.nan
    return float(1 - (s / s.cummax()).min())


def drawdown_series(s: pd.Series) -> pd.Series:
    return s / s.cummax() - 1


def annual_return(s: pd.Series) -> float:
    """年化收益率（按自然日折算）。"""
    if len(s) < 2:
        return np.nan
    days = (s.index[-1] - s.index[0]).days
    if days <= 0:
        return np.nan
    return float((s.iloc[-1] / s.iloc[0]) ** (365 / days) - 1)


def annual_vol(s: pd.Series) -> float:
    return float(s.pct_change().std() * np.sqrt(TRADING_DAYS))


def sharpe(s: pd.Series, rf: float = RF) -> float:
    v = annual_vol(s)
    if not v or np.isnan(v):
        return np.nan
    return float((annual_return(s) - rf) / v)


def sortino(s: pd.Series, rf: float = RF) -> float:
    r = s.pct_change().dropna()
    down = r[r < 0].std() * np.sqrt(TRADING_DAYS)
    if not down or np.isnan(down):
        return np.nan
    return float((annual_return(s) - rf) / down)


def calmar(s: pd.Series) -> float:
    mdd = max_drawdown(s)
    if not mdd:
        return np.nan
    return float(annual_return(s) / mdd)


def win_rate(s: pd.Series, freq: str = "ME") -> float:
    """月度 / 周度胜率。"""
    p = s.resample(freq).last().pct_change().dropna()
    return float((p > 0).mean()) if len(p) else np.nan


def recovery_days(s: pd.Series) -> int | None:
    """从历史最大回撤低点修复到前高所需交易日数（未修复返回 None）。"""
    if s.empty:
        return None
    dd = drawdown_series(s)
    trough = dd.idxmin()
    peak_before = s.loc[:trough].idxmax()
    peak_val = s.loc[peak_before]
    after = s.loc[trough:]
    hit = after[after >= peak_val]
    if len(hit):
        return int(len(s.loc[trough:hit.index[0]]))
    return None


# ------------------------- 相对基准 -------------------------
def align(a: pd.Series, b: pd.Series) -> tuple[pd.Series, pd.Series]:
    """按日期内连接对齐两个序列。"""
    df = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    return df["a"], df["b"]


def relative_metrics(nav: pd.Series, bench: pd.Series, rf: float = RF) -> dict:
    """相对基准的 Alpha / Beta / 跟踪误差 / 信息比率 / 超额收益。"""
    a, b = align(nav, bench)
    if len(a) < 20:
        return {}
    ra, rb = a.pct_change().dropna(), b.pct_change().dropna()
    idx = ra.index.intersection(rb.index)
    ra, rb = ra.loc[idx], rb.loc[idx]
    beta = float(np.cov(ra, rb)[0, 1] / np.var(rb)) if np.var(rb) else np.nan
    alpha = float((ra.mean() - beta * rb.mean()) * TRADING_DAYS)
    active = a / a.iloc[0] - b / b.iloc[0]          # 累计超额（净值口径）
    te = float((ra - rb).std() * np.sqrt(TRADING_DAYS))
    ir = float((ra.mean() - rb.mean()) * TRADING_DAYS / te) if te else np.nan
    return {
        "Beta": round(beta, 2) if beta == beta else None,
        "年化Alpha": round(alpha * 100, 2),
        "跟踪误差%": round(te * 100, 2),
        "信息比率": round(ir, 2) if ir == ir else None,
        "区间超额收益%": round(float(active.iloc[-1]) * 100, 2),
        "相关性": round(float(np.corrcoef(ra, rb)[0, 1]), 2),
    }


# ------------------------- 汇总 -------------------------
def performance(nav: pd.Series, bench: pd.Series | None = None,
                rf: float = RF) -> dict:
    """单资产绩效汇总（nav 为净值或价格序列）。"""
    if nav is None or len(nav) < 10:
        return {}
    nav = nav.dropna()
    out = {
        "样本起点": str(nav.index[0].date()),
        "样本终点": str(nav.index[-1].date()),
        "累计收益%": round((nav.iloc[-1] / nav.iloc[0] - 1) * 100, 2),
        "年化收益%": round(annual_return(nav) * 100, 2),
        "年化波动%": round(annual_vol(nav) * 100, 2),
        "最大回撤%": round(max_drawdown(nav) * 100, 2),
        "夏普比率": round(sharpe(nav, rf), 2),
        "索提诺比率": round(sortino(nav, rf), 2),
        "卡玛比率": round(calmar(nav), 2),
        "月度胜率%": round(win_rate(nav, "ME") * 100, 2) if win_rate(nav, "ME") == win_rate(nav, "ME") else None,
    }
    rd = recovery_days(nav)
    out["回撤修复天数"] = rd if rd is not None else "未修复"
    # 近端表现
    for label, days in [("近1月", 21), ("近3月", 63), ("近6月", 126), ("近1年", 250)]:
        if len(nav) > days:
            out[f"{label}%"] = round((nav.iloc[-1] / nav.iloc[-1 - days] - 1) * 100, 2)
    if bench is not None and len(bench) > 10:
        out.update(relative_metrics(nav, bench, rf))
        out["基准累计%"] = round((bench.iloc[-1] / bench.iloc[0] - 1) * 100, 2)
    return out


def yearly_returns(nav: pd.Series, bench: pd.Series | None = None) -> pd.DataFrame:
    """年度收益对比表。"""
    y = nav.resample("YE").last()
    y = pd.concat([nav.iloc[:1], y]).sort_index()
    ret = (y.pct_change().dropna() * 100).round(2)
    df = pd.DataFrame({"年份": ret.index.year, "标的%": ret.values})
    if bench is not None and len(bench) > 10:
        yb = bench.resample("YE").last()
        yb = pd.concat([bench.iloc[:1], yb]).sort_index()
        rb = (yb.pct_change().dropna() * 100).round(2)
        m = dict(zip(rb.index.year, rb.values))
        df["基准%"] = df["年份"].map(m)
        df["超额%"] = (df["标的%"] - df["基准%"]).round(2)
    return df


def rolling_excess(nav: pd.Series, bench: pd.Series, window: int = 250) -> pd.Series:
    """滚动窗口超额收益（用于判断超额稳定性）。"""
    a, b = align(nav, bench)
    if len(a) < window + 5:
        return pd.Series(dtype=float)
    return (a / a.shift(window) - b / b.shift(window)).dropna()


def industry_score(mom: pd.DataFrame, flow: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    行业综合打分：动量(50%) + 资金流(25%) + 风险调整(25%)。
    mom   : industry_momentum 输出
    flow  : industry_snapshot 输出（含 净流入 / 涨跌幅）
    """
    df = mom.copy()
    if df.empty:
        return df

    def z(s):
        s = pd.to_numeric(s, errors="coerce")
        std = s.std()
        return (s - s.mean()) / std if std and std == std else s * 0

    mom_col = "60日涨幅%" if "60日涨幅%" in df.columns else df.columns[-1]
    df["_mom_z"] = z(df[mom_col])
    df["_risk_z"] = z(-df["最大回撤%"]) if "最大回撤%" in df.columns else 0
    df["_flow_z"] = 0.0
    if flow is not None and not flow.empty and "净流入" in flow.columns:
        f = flow[["板块", "净流入"]].copy()
        f["净流入"] = pd.to_numeric(f["净流入"], errors="coerce")
        fmap = dict(zip(f["板块"], z(f["净流入"])))
        df["_flow_z"] = df["行业"].map(fmap).fillna(0)
    df["综合得分"] = (df["_mom_z"] * 0.5 + df["_flow_z"] * 0.25 + df["_risk_z"] * 0.25).round(3)
    return df.sort_values("综合得分", ascending=False).drop(
        columns=[c for c in ["_mom_z", "_risk_z", "_flow_z"] if c in df.columns])
