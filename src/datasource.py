# -*- coding: utf-8 -*-
"""
数据层：封装基金 / 行业板块 / ETF 的数据获取。

可用数据源（已在沙箱环境验证）:
  - 天天基金 fund.eastmoney.com / fundf10 / api.fund  : 基金净值、持仓、行业配置
  - 同花顺 10jqka (akshare)                           : 行业列表、板块实时行情与资金流、板块历史指数
  - 东方财富 push2his                                  : 指数 / ETF / 板块 K 线（需重试）
  - 新浪                                               : ETF 历史 K 线（备份源）

注意：东财 push2（实时行情）在部分网络环境被拦截，push2his 偶发断连，因此统一加重试。
"""
from __future__ import annotations

import io
import json
import re
import time
import warnings
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_TIMEOUT = 20
_RETRY = 6


def _session(referer: str = "https://quote.eastmoney.com/") -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": referer,
                      "Accept-Language": "zh-CN,zh;q=0.9"})
    return s


def _get(url: str, referer: str = "https://quote.eastmoney.com/",
         timeout: int = _TIMEOUT, retry: int = _RETRY, as_json: bool = False):
    """带重试的 GET。返回 text 或 json。"""
    s = _session(referer)
    last = None
    for i in range(retry):
        try:
            r = s.get(url, timeout=timeout)
            if r.status_code == 200:
                if as_json:
                    return r.json()
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:                      # noqa: BLE001
            last = f"{type(e).__name__}"
        time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"请求失败 {url[:90]} -> {last}")


# --------------------------------------------------------------------------
# 1. 通用 K 线（指数 / ETF / 板块）
# --------------------------------------------------------------------------
def kline_em(secid: str, beg: str = "20240101", end: str = "20500101") -> pd.DataFrame:
    """东方财富历史 K 线。secid 形如 1.510300(沪) / 0.159915(深) / 90.BK1036(板块)。"""
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&klt=101&fqt=1&beg={beg}&end={end}"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
    js = _get(url, as_json=True)
    data = (js or {}).get("data") or {}
    rows = []
    _name = data.get("name", "")
    for line in data.get("klines", []):
        p = line.split(",")
        rows.append({
            "date": p[0], "open": float(p[1]), "close": float(p[2]),
            "high": float(p[3]), "low": float(p[4]),
            "volume": float(p[5]), "amount": float(p[6]),
            "amplitude": float(p[7]) if len(p) > 7 and p[7] else None,
            "pct": float(p[8]) if len(p) > 8 and p[8] else None,
            "chg": float(p[9]) if len(p) > 9 and p[9] else None,
            "turnover": float(p[10]) if len(p) > 10 and p[10] else None,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df.attrs["name"] = _name
    return df


def _market_prefix(code: str) -> str:
    """根据代码推断东财市场前缀。"""
    if code.upper().startswith("BK"):
        return "90."
    return "1." if code[0] in "56" else "0."


def etf_kline(code: str, beg: str = "20240101", end: str = "20500101") -> pd.DataFrame:
    """ETF 历史 K 线（前复权）。失败时回退新浪源。"""
    try:
        df = kline_em(f"{_market_prefix(code)}{code}", beg, end)
        if not df.empty:
            return df
    except Exception:                                # noqa: BLE001
        pass
    import akshare as ak
    sina = "sh" if code[0] in "56" else "sz"
    df = ak.fund_etf_hist_sina(symbol=f"{sina}{code}")
    df = df.rename(columns={"date": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").loc[beg:end]
    df = df[["open", "high", "low", "close", "volume", "amount"]].astype(float)
    df.attrs["name"] = ""
    return df


# 常见场内 ETF 名称（东财实时行情接口不可用时用于兜底）
ETF_NAMES = {
    "510300": "沪深300ETF", "510500": "中证500ETF", "512480": "半导体ETF",
    "512690": "酒ETF", "588000": "科创50ETF", "159915": "创业板ETF",
    "510050": "上证50ETF", "512880": "证券ETF", "512800": "银行ETF",
    "512170": "医疗ETF", "515790": "光伏ETF", "516160": "新能源ETF",
    "159941": "纳指ETF", "513050": "中概互联网ETF", "518880": "黄金ETF",
    "512760": "芯片ETF", "515030": "新能源车ETF", "512660": "军工ETF",
}


def etf_name(code: str) -> str:
    """取 ETF 名称：K线接口 -> 内置表 -> 代码。"""
    try:
        df = kline_em(f"{_market_prefix(code)}{code}", "20260101")
        if df.attrs.get("name"):
            return df.attrs["name"]
    except Exception:                                # noqa: BLE001
        pass
    return ETF_NAMES.get(code, code)


# --------------------------------------------------------------------------
# 2. 公募基金
# --------------------------------------------------------------------------
@lru_cache(maxsize=64)
def fund_nav(code: str) -> pd.DataFrame:
    """基金单位净值走势（成立来）。列：净值日期 / 单位净值 / 日增长率"""
    import akshare as ak
    df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势", period="成立来")
    df["净值日期"] = pd.to_datetime(df["净值日期"])
    df["单位净值"] = pd.to_numeric(df["单位净值"], errors="coerce")
    df["日增长率"] = pd.to_numeric(df["日增长率"], errors="coerce")
    return df.dropna(subset=["单位净值"]).set_index("净值日期")


@lru_cache(maxsize=64)
def fund_basic(code: str) -> dict:
    """基金基本信息（雪球源）。"""
    import akshare as ak
    try:
        df = ak.fund_individual_basic_info_xq(symbol=code)
        return dict(zip(df["item"], df["value"]))
    except Exception:                                # noqa: BLE001
        return {}


def fund_holdings(code: str, year: str = "") -> tuple[pd.DataFrame, str]:
    """基金股票持仓明细。返回 (DataFrame, 报告期文本)。

    数据来源：天天基金 F10 页面（返回 JS 片段，需提取 content 后解析表格）。
    """
    url = (f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={code}"
           f"&topline=30&year={year}&month=&rt=0.1")
    txt = _get(url, referer="https://fundf10.eastmoney.com/")
    m = re.search(r'content:\s*"(.*?)",\s*arryear', txt, re.S)
    content = m.group(1) if m else txt
    # 仅还原 JS 转义，不要用 unicode_escape（会破坏 UTF-8 中文）
    content = content.replace("\\'", "'").replace('\\"', '"').replace("\\/", "/")
    period = ""
    pm = re.search(r"(\d{4})年(\d)季度", content[:2000])
    if pm:
        period = f"{pm.group(1)}年{pm.group(2)}季度"
    tables = pd.read_html(io.StringIO(content))
    if not tables:
        return pd.DataFrame(), period
    df = tables[0]
    df.columns = [str(c).replace("\n", "").replace(" ", "") for c in df.columns]
    rename = {}
    for c in df.columns:
        if "股票代码" in c:
            rename[c] = "代码"
        elif "股票名称" in c:
            rename[c] = "名称"
        elif "占净值" in c:
            rename[c] = "占净值比例"
        elif "持股数" in c:
            rename[c] = "持股数"
        elif "持仓市值" in c:
            rename[c] = "持仓市值"
    df = df.rename(columns=rename)
    keep = [c for c in ["代码", "名称", "占净值比例", "持股数", "持仓市值"] if c in df.columns]
    df = df[keep].copy()
    for c in keep:
        if c not in ("代码", "名称"):
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "").str.replace("%", ""),
                                  errors="coerce")
    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.extract(r"(\d+)")[0].fillna("")
        df = df[df["代码"] != ""]
        df["代码"] = df["代码"].str.zfill(6)
    return df.reset_index(drop=True), period


def fund_industry_allocation(code: str, year: str = "") -> pd.DataFrame:
    """基金行业配置（占净值比例）。"""
    import akshare as ak
    df = ak.fund_portfolio_industry_allocation_em(symbol=code, date=year or str(pd.Timestamp.now().year - 1))
    df["占净值比例"] = pd.to_numeric(df["占净值比例"], errors="coerce")
    return df


@lru_cache(maxsize=4)
def fund_universe() -> pd.DataFrame:
    """全市场开放式基金当日净值（2.4w+ 只），用于筛选与横向对比。"""
    import akshare as ak
    return ak.fund_open_fund_daily_em()


# --------------------------------------------------------------------------
# 3. 行业板块（同花顺）
# --------------------------------------------------------------------------
@lru_cache(maxsize=4)
def industry_list() -> pd.DataFrame:
    """同花顺行业板块列表。"""
    import akshare as ak
    return ak.stock_board_industry_name_ths()


def industry_snapshot() -> pd.DataFrame:
    """行业板块实时快照：涨跌幅 / 成交额 / 净流入 / 涨跌家数 / 领涨股。"""
    import akshare as ak
    df = ak.stock_board_industry_summary_ths()
    num_cols = ["涨跌幅", "总成交量", "总成交额", "净流入", "上涨家数", "下跌家数", "均价",
                "领涨股-最新价", "领涨股-涨跌幅"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@lru_cache(maxsize=128)
def industry_index(name: str, start: str = "20240101", end: str = "20500101") -> pd.DataFrame:
    """同花顺行业板块历史指数行情（OHLCV）。"""
    import akshare as ak
    df = ak.stock_board_industry_index_ths(symbol=name, start_date=start, end_date=end)
    df = df.rename(columns={"日期": "date", "开盘价": "open", "最高价": "high",
                            "最低价": "low", "收盘价": "close", "成交量": "volume",
                            "成交额": "amount"})
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[["open", "high", "low", "close", "volume", "amount"]].astype(float)


def industry_momentum(names: list[str], start: str = "20250101",
                      end: str = "20500101", windows=(5, 20, 60, 120)) -> pd.DataFrame:
    """批量计算行业板块多周期动量。"""
    rows = []
    for n in names:
        try:
            df = industry_index(n, start, end)
        except Exception:                            # noqa: BLE001
            continue
        if len(df) < 30:
            continue
        rec = {"行业": n, "最新点位": round(df["close"].iloc[-1], 2)}
        for w in windows:
            if len(df) > w:
                rec[f"{w}日涨幅%"] = round((df["close"].iloc[-1] / df["close"].iloc[-1 - w] - 1) * 100, 2)
        c = df["close"]
        rec["年内涨幅%"] = _ytd(c)
        rec["年化波动%"] = round(c.pct_change().std() * (250 ** 0.5) * 100, 2)
        rec["最大回撤%"] = round(_max_drawdown(c) * 100, 2)
        rows.append(rec)
    return pd.DataFrame(rows)


def _ytd(s: pd.Series) -> float:
    yr = s.index[-1].year
    base = s[s.index < f"{yr}-01-01"]
    b = base.iloc[-1] if len(base) else s.iloc[0]
    return round((s.iloc[-1] / b - 1) * 100, 2)


def _max_drawdown(s: pd.Series) -> float:
    """最大回撤，正数表示回撤幅度（与 metrics.max_drawdown 口径一致）。"""
    return float(-(s / s.cummax() - 1).min())


# --------------------------------------------------------------------------
# 3.5 个股：行业归属与 K 线
# --------------------------------------------------------------------------
@lru_cache(maxsize=512)
def stock_profile(code: str) -> dict:
    """个股所属板块（东财 F10）。返回 {"name": 公司名, "boards": [行业...]}。

    boards 已剔除题材/风格/地域。典型：{"name":"贵州茅台","boards":["食品饮料","白酒"]}
    """
    code = code.zfill(6)
    market = "SH" if code[0] in "69" else ("SZ" if code[0] in "023" else "BJ")
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax?code={market}{code}"
    try:
        js = _get(url, referer="https://emweb.securities.eastmoney.com/", retry=3, as_json=True)
    except Exception:                                # noqa: BLE001
        return {"name": "", "boards": []}
    raw = (js or {}).get("ssbk", [])
    # 板块列表顺序不稳定，公司名取所有项 SECURITY_NAME_ABBR 的众数
    names = [str(x.get("SECURITY_NAME_ABBR", "")).strip() for x in raw if x.get("SECURITY_NAME_ABBR")]
    name = max(set(names), key=names.count) if names else ""
    out = []
    # 题材/风格/地域类板块关键词，需剔除
    noise = ("板块", "风格", "指数", "龙头", "调研", "股", "价", "率", "标", "概念",
             "概念股", "今日", "昨日", "融资", "融券", "沪股", "深股", "转债", "次新",
             "破净", "预盈", "预增", "送转", "高送", "含可", "回购", "增持", "举牌",
             "独角兽", "专精特新", "MSCI", "富时", "标普", "成分", "成份", "样本")
    for x in raw[:8]:
        bname = str(x.get("BOARD_NAME", "")).strip()
        if not bname:
            continue
        # 去掉申万层级后缀：白酒Ⅱ / 白酒Ⅲ
        bname = re.sub(r"[ⅠⅡⅢⅣⅤ]+$", "", bname).strip()
        if any(k in bname for k in noise):
            continue
        if bname not in out:
            out.append(bname)
    return {"name": name, "boards": out[:3]}


@lru_cache(maxsize=512)
def stock_industry(code: str) -> list[str]:
    """兼容旧接口：仅返回行业列表。"""
    return stock_profile(code)["boards"]


def search_stock(name: str, count: int = 8) -> list[dict]:
    """东财模糊搜索：名称 -> 候选代码（含市场类型）。"""
    import urllib.parse
    url = ("https://searchapi.eastmoney.com/api/suggest/get"
           f"?input={urllib.parse.quote(name)}&type=14"
           "&token=D43BF722C8E33BDC906FB84D85E326E8&count={count}")
    try:
        js = _get(url, referer="https://quote.eastmoney.com/", retry=3, as_json=True)
    except Exception:                                # noqa: BLE001
        return []
    data = ((js or {}).get("QuotationCodeTable") or {}).get("Data") or []
    out = []
    for x in data:
        c = str(x.get("Code", ""))
        if not c or len(c) != 6 or not c.isdigit():
            continue                                  # 只要 6 位数字代码（A 股）
        out.append({
            "code": c,
            "name": str(x.get("Name", "")),
            "market": str(x.get("SecurityTypeName", "")),
            "mkt": x.get("MktNum"),
        })
    return out


def search_any(name: str, count: int = 8) -> list[dict]:
    """东财模糊搜索（含港股/美股），用于判断是否纯境外标的。"""
    import urllib.parse
    url = ("https://searchapi.eastmoney.com/api/suggest/get"
           f"?input={urllib.parse.quote(name)}&type=14"
           "&token=D43BF722C8E33BDC906FB84D85E326E8&count={count}")
    try:
        js = _get(url, referer="https://quote.eastmoney.com/", retry=3, as_json=True)
    except Exception:                                # noqa: BLE001
        return []
    data = ((js or {}).get("QuotationCodeTable") or {}).get("Data") or []
    return [{"code": str(x.get("Code", "")), "name": str(x.get("Name", "")),
             "market": str(x.get("SecurityTypeName", "")), "mkt": x.get("MktNum")}
            for x in data if x.get("Code")]


# 东财 K 线熔断：连续失败后一段时间内直接走备份源，避免每次空等重试
_EM_DISABLED_UNTIL = 0.0
_EM_FAILS = 0


def em_disabled() -> bool:
    return time.time() < _EM_DISABLED_UNTIL


def _em_ok() -> bool:
    global _EM_FAILS, _EM_DISABLED_UNTIL
    _EM_FAILS = 0
    return True


def _em_fail() -> None:
    """记录一次东财 K 线失败；连续 3 次则熔断 15 分钟。"""
    global _EM_FAILS, _EM_DISABLED_UNTIL
    _EM_FAILS += 1
    if _EM_FAILS >= 3:
        _EM_DISABLED_UNTIL = time.time() + 900
        _EM_FAILS = 0


def kline_em_safe(secid: str, beg: str = "20240101", end: str = "20500101") -> pd.DataFrame:
    """东财 K 线（带熔断）。熔断期内直接抛异常，由调用方回退备份源。"""
    if em_disabled():
        raise RuntimeError("东财 K 线已熔断，走备份源")
    try:
        df = kline_em(secid, beg, end)
    except Exception:                                # noqa: BLE001
        _em_fail()
        raise
    if df.empty:
        _em_fail()
        raise RuntimeError("空数据")
    _em_ok()
    return df


def _kline_tencent(symbol: str, beg: str = "20240101",
                   end: str = "20500101", n: int = 800) -> pd.DataFrame:
    """腾讯前复权日 K 线。symbol 形如 sh600519 / sz300750 / sh000300。"""
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={symbol},day,,,{n},qfq")
    js = _get(url, referer="https://gu.qq.com/", retry=3, as_json=True)
    node = ((js or {}).get("data") or {}).get(symbol) or {}
    arr = node.get("qfqday") or node.get("day") or []
    rows = [{"date": r[0], "open": float(r[1]), "close": float(r[2]),
             "high": float(r[3]), "low": float(r[4]),
             "volume": float(r[5]) if len(r) > 5 else np.nan,
             "amount": np.nan} for r in arr if len(r) >= 5]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index().loc[beg:end]
    df.attrs["name"] = ""
    return df


def stock_kline(code: str, beg: str = "20240101", end: str = "20500101") -> pd.DataFrame:
    """A 股个股前复权 K 线：东财优先，失败回退腾讯。"""
    code = code.zfill(6)
    try:
        return kline_em_safe(f"{'1.' if code[0] in '69' else '0.'}{code}", beg, end)
    except Exception:                                # noqa: BLE001
        pass
    sym = ("sh" if code[0] in "69" else "sz") + code
    return _kline_tencent(sym, beg, end)


# --------------------------------------------------------------------------
# 4. 基准与指数
# --------------------------------------------------------------------------
INDEX_SECID = {
    "沪深300": "1.000300",
    "中证500": "1.000905",
    "中证1000": "1.000852",
    "创业板指": "0.399006",
    "科创50": "1.000688",
    "上证指数": "1.000001",
    "中证800": "1.000906",
}


# 腾讯源指数代码（东财被限流时的回退）
INDEX_TENCENT = {
    "沪深300": "sh000300", "中证500": "sh000905", "中证1000": "sh000852",
    "创业板指": "sz399006", "科创50": "sh000688", "上证指数": "sh000001",
    "中证800": "sh000906",
}


def index_kline(name: str, beg: str = "20240101", end: str = "20500101") -> pd.DataFrame:
    if name not in INDEX_SECID:
        raise KeyError(f"未知指数 {name}，可选：{list(INDEX_SECID)}")
    try:
        return kline_em_safe(INDEX_SECID[name], beg, end)
    except Exception:                                # noqa: BLE001
        pass
    return _kline_tencent(INDEX_TENCENT[name], beg, end)


if __name__ == "__main__":
    print(kline_em("1.510300", "20250901", "20261012").tail(2))
    print(industry_snapshot().head(3))
