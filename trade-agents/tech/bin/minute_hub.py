#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分钟级多级数据获取层 (A股指数/个股/板块/期指).

层级策略 (逐级回退, 命中即停):
  L1 腾讯 mkline  ifzq.gtimg.cn  (指数/个股/板块 15m/5m/1m, 速度最快)
  L2 新浪 K线     money.finance.sina.com.cn getKLineData (15m/5m, 320+根, 独立于akshare)
  L3 百度股市通   finance.pae.baidu.com (1min分时; 可聚合为任意粒度, 独立厂商)
  L4 新浪 akshare stock_zh_a_minute (1970根)
期指 15min K线用新浪 futures_zh_minute_sina (含 hold=OI) 为主, 1min分时兜底。

缓存: .cache/minute/<YYYYMMDD>/*.csv
强制 IPv4 (容器 IPv6 不通)。
"""
import socket
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import os, re, json, time
from datetime import datetime
import numpy as np
import pandas as pd
import requests

CACHE = os.path.join(os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache/minute")
H_TX = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
H_SINA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
H_BD = {"User-Agent": "Mozilla/5.0", "Referer": "https://gushitong.baidu.com/"}
TRIED = {}   # 记录各接口可用性


def _log(msg):
    print(f"[minute_hub] {msg}", flush=True)


# ---------------- L1 腾讯 mkline ----------------
def tx_kline(code, freq="15", n=320):
    """腾讯分钟K线. freq: 1/5/15/30/60. code: sh000001 / sz399006 / sz300308."""
    m = {"1": "m1", "5": "m5", "15": "m15", "30": "m30", "60": "m60"}.get(str(freq))
    if not m:
        return None
    try:
        url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},{m},,{n}"
        r = requests.get(url, headers=H_TX, timeout=12)
        d = r.json()
        node = d.get("data", {}).get(code, {})
        arr = node.get(m)
        if not arr:
            return None
        rows = []
        for it in arr:
            t = str(it[0])
            rows.append([t, float(it[1]), float(it[2]), float(it[3]), float(it[4]),
                         float(it[5]), float(it[7]) if len(it) > 7 and it[7] not in ({}, "") else np.nan])
        df = pd.DataFrame(rows, columns=["t", "open", "close", "high", "low", "volume", "amount"])
        df["t"] = pd.to_datetime(df["t"], format="%Y%m%d%H%M")
        # 腾讯分钟bar时间戳即「结束时间」(与新浪一致), 无需偏移
        df["day"] = df["t"]
        return df.sort_values("t").reset_index(drop=True)
    except Exception as e:
        TRIED[f"tx_kline_{code}_{freq}"] = str(e)[:60]
        return None


# ---------------- L2 新浪 akshare ----------------
def sina_kline(code, freq="15"):
    """akshare stock_zh_a_minute. code: sh000001 / sz399006 / sz300308."""
    try:
        import akshare as ak
        d = ak.stock_zh_a_minute(symbol=code, period=str(freq))
        if d is None or len(d) == 0:
            return None
        d = d.rename(columns={"day": "t"})
        d["t"] = pd.to_datetime(d["t"])
        d["close"] = pd.to_numeric(d["close"], errors="coerce")
        for c in ["open", "high", "low", "volume", "amount"]:
            if c in d.columns:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        return d.sort_values("t").reset_index(drop=True)
    except Exception as e:
        TRIED[f"sina_kline_{code}_{freq}"] = str(e)[:60]
        return None


# ---------------- L2 新浪 K线 (money.finance.sina.com.cn, 独立于 akshare) ----------------
def sina_kline_v2(code, freq="15", datalen=320):
    """新浪经典 K线接口. 支持 5/15/30/60min (1min 返回 null)."""
    if str(freq) == "1":
        return None
    try:
        url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={code}&scale={freq}&ma=5&datalen={datalen}")
        r = requests.get(url, headers=H_SINA, timeout=12)
        arr = r.json()
        if not isinstance(arr, list) or not arr:
            return None
        df = pd.DataFrame(arr)
        df = df.rename(columns={"day": "t"})
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df["t"] = pd.to_datetime(df["t"])
        return df[["t", "open", "high", "low", "close", "volume"]].sort_values("t").reset_index(drop=True)
    except Exception as e:
        TRIED[f"sina_v2_{code}_{freq}"] = str(e)[:60]
        return None


# ---------------- L3 百度股市通 (独立厂商, 1min 分时) ----------------
_BD_MAP = {"sh000001": "000001", "sz399001": "399001", "sz399006": "399006",
           "sh000905": "000905", "sh000852": "000852", "sh000300": "000300",
           "sh000016": "000016", "sh000928": "000928", "sh000929": "000929",
           "sh000930": "000930", "sh000931": "000931", "sh000932": "000932",
           "sh000933": "000933", "sh000934": "000934", "sh000935": "000935",
           "sh000936": "000936", "sh000937": "000937"}


def baidu_kline(code, freq="15", kind="index", date=None):
    """百度股市通 1min 分时 -> 聚合. 指数用 quotation_index_minute, 个股用 quotation_minute_ab."""
    try:
        num = _BD_MAP.get(code, code[-6:] if len(code) >= 6 else code)
        grp = "quotation_index_minute" if kind != "stock" else "quotation_minute_ab"
        day = date or datetime.now().strftime("%Y%m%d")
        url = ("https://finance.pae.baidu.com/vapi/v1/getquotation?srcid=5353&all=1&pointType=string"
               f"&code={num}&query={num}&group={grp}&market_type=ab")
        j = requests.get(url, headers=H_BD, timeout=12).json()
        md = (j.get("Result") or {}).get("newMarketData") or {}
        rows = []
        for blk in (md.get("marketData") or []):
            d = blk.get("date", "")
            for seg in str(blk.get("p", "")).split(";"):
                f = seg.split(",")
                if len(f) < 3:
                    continue
                tm = f[1].strip()          # "09-28 09:30"
                datepart, timepart = tm.split(" ") if " " in tm else (day[-4:], tm)
                mm, dd = datepart.split("-")
                dt = f"{day[:4]}-{mm}-{dd} {timepart}"
                rows.append([dt, float(f[2]), float(f[2])])
        if not rows:
            return None
        d1 = pd.DataFrame(rows, columns=["t", "close", "open"])
        d1["t"] = pd.to_datetime(d1["t"], format="%Y-%m-%d %H:%M")
        d1["high"] = d1["close"]
        d1["low"] = d1["close"]
        d1["volume"] = np.nan
        d1 = d1[["t", "open", "high", "low", "close", "volume"]]
        if str(freq) != "1":
            d1 = _resample(d1, freq, right=True)
            return d1.rename(columns={"day": "day"}).reset_index(drop=True) if "day" in d1.columns else d1.reset_index(drop=True)
        return d1.rename(columns={"t": "day"}).reset_index(drop=True)
    except Exception as e:
        TRIED[f"baidu_{code}_{freq}"] = str(e)[:60]
        return None


# ---------------- L3 新浪期指分时 (1min, 含OI) ----------------
def sina_fut_minline(symbol="IF0"):
    try:
        url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/"
               "InnerFuturesNewService.getMinLine?symbol=" + symbol)
        r = requests.get(url, headers=H_SINA, timeout=12)
        m = re.search(r"var t=\((\[.*\])\);?", r.text.strip())
        if not m:
            return None
        arr = json.loads(m.group(1))
        rows = []
        cur_date = None
        for it in arr:
            if len(it) >= 7:
                cur_date = it[6]
                tm, px, avg, vol, oi = it[0], it[1], it[2], it[3], it[4]
            elif len(it) >= 5:
                tm, px, avg, vol, oi = it[0], it[1], it[2], it[3], it[4]
            else:
                continue
            if cur_date is None:
                cur_date = datetime.now().strftime("%Y-%m-%d")
            rows.append([cur_date + " " + tm, float(px), float(px), float(vol), float(oi)])
        df = pd.DataFrame(rows, columns=["t", "open", "close", "volume", "oi"])
        df["t"] = pd.to_datetime(df["t"])
        return df.sort_values("t").reset_index(drop=True)
    except Exception as e:
        TRIED[f"sina_futmin_{symbol}"] = str(e)[:60]
        return None


def sina_fut_kline(symbol="IF0", freq="15"):
    """新浪期指分钟K线 (含 hold=OI)."""
    try:
        import akshare as ak
        d = ak.futures_zh_minute_sina(symbol=symbol, period=str(freq))
        if d is None or len(d) == 0:
            return None
        d = d.rename(columns={"datetime": "t", "hold": "oi"})
        d["t"] = pd.to_datetime(d["t"])
        for c in ["open", "high", "low", "close", "volume", "oi"]:
            if c in d.columns:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        return d.sort_values("t").reset_index(drop=True)
    except Exception as e:
        TRIED[f"sina_futk_{symbol}_{freq}"] = str(e)[:60]
        return None


# ---------------- 统一入口 ----------------
def get_kline(code, freq="15", n=320, kind="index", cache=True, date=None, cache_ttl_min=30):
    """多级获取分钟K线. 返回 DataFrame[day,open,close,high,low,volume,...] 或 None.

    kind: index|stock|board -> L1腾讯→L2新浪;  fut -> 新浪K线→新浪1min分时聚合

    ⚠️ 缓存 TTL（2026-10-23 修）：原逻辑「当天目录里有文件就直接读缓存」→ 盘中第一次跑
    （如 09:35）写下的缓存会被当天后续每一次调用复用，末根永远停在 09:35 那根，导致
    每小时监控看到的价格是 1 小时甚至半天前的（2026-10-23 沪深300 缓存末根 10:30
    3988.27，实际收盘 4105.33，方向都判断反了）。现在加 mtime TTL：
    缓存文件超过 cache_ttl_min 分钟就重新拉取。cache_ttl_min=0 表示永不使用缓存。
    """
    day = date or datetime.now().strftime("%Y%m%d")
    key = f"{kind}_{code}_{freq}"
    if cache and cache_ttl_min and cache_ttl_min > 0:
        p = os.path.join(CACHE, day, f"{key}.csv")
        if os.path.exists(p):
            try:
                age_min = (time.time() - os.path.getmtime(p)) / 60.0
                if age_min <= cache_ttl_min:
                    df = pd.read_csv(p)
                    df["day"] = pd.to_datetime(df["day"])
                    if len(df) > 0 and df["day"].iloc[-1].date() == pd.Timestamp(day).date():
                        return df
            except Exception:
                pass

    df = None
    if kind == "fut":
        if str(freq) == "15":
            df = sina_fut_kline(code, "15")
        if df is None:
            ml = sina_fut_minline(code)
            if ml is not None and str(freq) != "1":
                df = _resample(ml, freq)
            else:
                df = ml
        if df is not None and "oi" not in df.columns:
            df["oi"] = np.nan
    else:
        df = tx_kline(code, freq, n)
        if df is None:
            df = sina_kline_v2(code, freq)
        if df is None:
            df = baidu_kline(code, freq, kind=kind, date=day)
        if df is None:
            df = sina_kline(code, freq)

    if df is None:
        return None
    # 标准化列
    for c in ["open", "close", "high", "low", "volume"]:
        if c not in df.columns:
            df[c] = np.nan
    if "day" not in df.columns:
        df["day"] = df["t"]
    df = df[[c for c in ["day", "open", "high", "low", "close", "volume", "amount", "oi"] if c in df.columns]]
    df = df.dropna(subset=["close"]).sort_values("day").reset_index(drop=True)

    if cache:
        try:
            os.makedirs(os.path.join(CACHE, day), exist_ok=True)
            df.to_csv(os.path.join(CACHE, day, f"{key}.csv"), index=False)
        except Exception:
            pass
    return df


def _resample(ml, freq, right=False):
    """把 1min 分时聚合为 N 分钟 K线. right=True 用右闭合(时间戳=结束时间)."""
    d = ml.set_index("t")
    kw = {"label": "right", "closed": "right"} if right else {}
    o = d["open"].resample(f"{freq}min", **kw).first()
    c = d["close"].resample(f"{freq}min", **kw).last()
    h = d["high"].resample(f"{freq}min", **kw).max() if "high" in d.columns else c
    l = d["low"].resample(f"{freq}min", **kw).min() if "low" in d.columns else c
    v = d["volume"].resample(f"{freq}min", **kw).sum()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna(subset=["close"])
    if "oi" in d.columns:
        out["oi"] = d["oi"].resample(f"{freq}min", **kw).last()
    out = out.reset_index().rename(columns={"t": "day"})
    return out


def status(codes=None):
    """各层可用性自检."""
    codes = codes or ["sh000001", "sz399006", "sz300308"]
    rep = {}
    for c in codes:
        kd = "stock" if c[2:5] in ("300", "301", "600", "601", "603", "605", "000", "001", "002", "688", "5") else "index"
        rep[c] = {"L1_tx": tx_kline(c, "15") is not None,
                  "L2_sinaK": sina_kline_v2(c, "15") is not None,
                  "L3_baidu": baidu_kline(c, "15", kind=kd) is not None,
                  "L4_ak": sina_kline(c, "15") is not None}
    for f in ["IF0", "IC0", "IM0"]:
        rep[f] = {"L_futk": sina_fut_kline(f, "15") is not None,
                  "L_futmin": sina_fut_minline(f) is not None}
    return rep


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        r = status()
        for k, v in r.items():
            print(k, v)
    else:
        for c, k in [("sh000001", "index"), ("sz300308", "stock"), ("IF0", "fut")]:
            for fq in (["15"] if k == "fut" else ["15", "5", "1"]):
                d = get_kline(c, fq, kind=k, cache=False)
                print(f"{k} {c} {fq}min ->", "None" if d is None else f"{len(d)}行 last={d['day'].iloc[-1]} close={d['close'].iloc[-1]}")
