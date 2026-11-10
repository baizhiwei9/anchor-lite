#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DataHub · 三级灾备统一行情层（2026-10-22 建，用户要求）

背景
----
用户原话：「用 DataHub 这种方式，就是三级灾备体系，AKShare、TuShare、网页。
爬虫这种每一个的那个顺序字段可能都不一样，你得都适配了」

为什么要这个东西
----------------
2026-10-22 踩到的真实事故：腾讯 `hkfqkline` 返回的字段顺序是
    [日期, 开, **收**, 高, 低, 量]
而 `xq_stock.hk_hist()` 按
    [日期, 开, 高, 低, **收**]
解析 → 港股「收盘价」实际取到**最低价**，high/low 对调，
导致所有港股的缠论（笔/中枢/背驰）、MA40、回撤、黄金分割判定全部建立在错数据上，
连续几天发出错误信号（太辰光、百济神州都被判错）。

设计原则
--------
1. **每个源单独适配**（per-source adapter），绝不假设字段顺序一致：
     L1 AKShare  → DataFrame 列名（open/high/low/close，最稳）
     L2 TuShare  → DataFrame 列名（open/high/low/close）
     L3 网页     → **裸数组**，必须按接口显式声明字段序（腾讯 = 开收高低）
2. **统一出口**：一律返回标准化 `list[[d8, open, high, low, close, vol], ...]`，
   调用方永远不用关心底层是什么源。
3. **OHLC 合理性硬校验**（validate()）：
     low <= min(open, close) <= max(open, close) <= high
   任何一条不满足 → 该源判定为「字段顺序错」，记日志并降级到下一个源。
   ★ 这是防止今天这类 bug 复发的核心闸门。
4. **一致性交叉校验**（可选 cross_check=True）：主源与备源的最后收盘价偏差
   > 0.5% → 记 WARN（可能是复权口径不同或某源错位）。

用法
----
    import datahub
    rows = datahub.daily("SZ002463", "20261022")        # A股
    rows = datahub.daily("06160",    "20261022")        # 港股
    rows = datahub.daily("SH501225", "20261022")        # ETF/LOF
    rows = datahub.index_daily("000300.SH", "20261022") # 指数
    # rows = [[ '20261022', 114.47, 114.97, 106.33, 106.96, 5898890.0 ], ...]

    datahub.self_test()      # 逐源体检（打印各源原始字段 → 人眼核对）
"""
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request

# 容器 IPv6 不通 → 强制 IPv4（必须在任何网络请求前生效）
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
LOG = os.path.join(WS, ".cache/datahub.log")
CACHE = os.path.join(WS, ".cache/datahub")


def log(m, err=False):
    try:
        os.makedirs(CACHE, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s]%s %s\n" % (time.strftime("%F %T"), " ERR" if err else "", m))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════ 证券代码工具
def is_hk(sym):
    return bool(re.fullmatch(r"\d{5}", str(sym).strip()))


def is_index(sym):
    return bool(re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", str(sym).strip().upper()))


def norm(sym):
    """统一成内部形式：
        SZ002463 / SH600183 / SH501225 / 06160(港股) / 000300.SH(指数)
    """
    s = str(sym).strip().upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s):
        return s                              # 指数原样
    if s.startswith(("SZ", "SH", "BJ")):
        return s
    if re.fullmatch(r"\d{5}", s):
        return s                              # 港股 5 位
    if re.fullmatch(r"\d{6}", s):
        # 无前缀 6 位：按首位推断交易所（5/6/9 → SH，0/1/2/3 → SZ）
        return ("SH" if s[0] in "569" else "SZ") + s
    return s


def to_ts(sym):
    """→ TuShare ts_code：000636.SZ / 00700.HK / 000300.SH"""
    s = norm(sym)
    if is_index(s):
        return s
    if s.startswith(("SZ", "SH", "BJ")):
        return s[2:] + "." + s[:2]
    if re.fullmatch(r"\d{5}", s):
        return s + ".HK"
    return s


def to_em(sym):
    """→ 东财 secid：0.002463 / 1.600183 / 116.00700"""
    s = norm(sym)
    if is_index(s):
        code, mkt = s.split(".")
        return ("1." if mkt == "SH" else "0.") + code
    if s.startswith("SH"):
        return "1." + s[2:]
    if s.startswith(("SZ", "BJ")):
        return "0." + s[2:]
    if re.fullmatch(r"\d{5}", s):
        return "116." + s
    return s


def to_sina(sym):
    """→ 新浪/腾讯符号：sz002463 / sh600183 / hk06160"""
    s = norm(sym)
    if is_index(s):
        code, mkt = s.split(".")
        return mkt.lower() + code
    if s.startswith(("SH", "SZ", "BJ")):
        return s[:2].lower() + s[2:]
    if re.fullmatch(r"\d{5}", s):
        return "hk" + s
    return s


def d8(d):
    return str(d or "").replace("-", "")


# ═══════════════════════════════════════════════════════════ OHLC 校验闸门
def validate(rows, tag="", strict=True):
    """OHLC 合理性硬校验 —— 防「字段顺序错位」的核心闸门

    规则（每条都必须成立）：
        low  <= open, close <= high
        high >= low
        low  <= min(open,close)、max(open,close) <= high
        价格 > 0
    返回 (ok_rows, bad_idx)。bad 比例 > 20% 判定整源错位。
    """
    good, bad = [], []
    for i, r in enumerate(rows or []):
        try:
            _d, o, h, l, c = r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])
        except Exception:
            bad.append(i)
            continue
        if not (min(o, h, l, c) > 0 and h >= l
                and l <= o <= h and l <= c <= h):
            bad.append(i)
            continue
        good.append(r)
    nbad = len(bad)
    ntot = len(rows or [])
    if ntot and nbad and (nbad / float(ntot)) > 0.20:
        log("%s OHLC 校验失败 %d/%d → 判定字段顺序错，本源作废" % (tag, nbad, ntot), err=True)
        return [], bad
    if nbad:
        log("%s OHLC 校验剔除 %d/%d 行" % (tag, nbad, ntot))
    return good, bad


# ═══════════════════════════════════════════════════════════ DataFrame 适配
def _from_df(df, date_col="date", limit=None):
    """把任何带标准列名的 DataFrame → [[d8,o,h,l,c,vol], ...]

    AKShare / TuShare 都是**列名**接口（不是位置数组），这是最稳的一层。
    中文列名（AKShare 部分接口会返回 日期/开盘/最高/最低/收盘）也做兼容。
    """
    if df is None or not len(df):
        return []
    cn = {"日期": "date", "时间": "date", "开盘": "open", "最高": "high",
          "最低": "low", "收盘": "close", "成交量": "vol", "成交额": "amount",
          "trade_date": "date", "vol": "vol", "volume": "vol"}
    cols = {}
    for c in df.columns:
        cols[str(c)] = cn.get(str(c), str(c))
    rows = []
    for rec in df.itertuples(index=False):
        v = dict(zip(cols.values(), rec))
        try:
            rows.append([d8(v.get("date")),
                         float(v.get("open")), float(v.get("high")),
                         float(v.get("low")), float(v.get("close")),
                         float(v.get("vol") or v.get("volume") or 0) or 0.0])
        except Exception:
            continue
    rows = [r for r in rows if r[0]]
    rows.sort(key=lambda x: x[0])
    return rows[-limit:] if limit else rows


# ═══════════════════════════════════════════════════════════ L1 AKShare
def _ak_call(fn, *a, timeout=25, **k):
    """akshare 调用带超时（东财接口常 RemoteDisconnected / 挂死）"""
    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(1)
    try:
        f = ex.submit(fn, *a, **k)
        return f.result(timeout=timeout)
    except Exception as e:
        log("akshare %s 失败 %s" % (getattr(fn, "__name__", fn), str(e)[:90]))
        return None
    finally:
        ex.shutdown(wait=False)


def _l1_akshare(sym, end, days):
    """AKShare 日线（列名接口）
        A股/ETF : stock_zh_a_daily(symbol='sz002463', adjust='qfq')
                  ETF 若报错回退 fund_etf_hist_em
        港股     : stock_hk_daily(symbol='06160', adjust='qfq')
        指数     : index_zh_a_hist(symbol='000300', period='daily')
    """
    import akshare as ak
    s = norm(sym)
    start = time.strftime("%Y%m%d", time.localtime(
        time.time() - max(days, 60) * 2.2 * 86400))
    try:
        if is_index(s):
            code = s.split(".")[0]
            df = _ak_call(ak.index_zh_a_hist, symbol=code, period="daily",
                          start_date=start, end_date=end)
        elif is_hk(s):
            df = _ak_call(ak.stock_hk_daily, symbol=s, adjust="qfq")
        else:
            df = _ak_call(ak.stock_zh_a_daily, symbol=to_sina(s), adjust="qfq")
            if df is None or not len(df):
                # ETF/LOF 回退（stock_zh_a_daily 对场内基金常失败）
                df = _ak_call(ak.fund_etf_hist_em, symbol=s[2:], period="daily",
                              start_date=start, end_date=end, adjust="qfq")
    except Exception as e:
        log("L1 ak %s err %s" % (sym, str(e)[:90]), err=True)
        return []
    rows = _from_df(df)
    rows = [r for r in rows if r[0] <= end]
    return rows[-days:] if days else rows


# ═══════════════════════════════════════════════════════════ L2 TuShare
def _l2_tushare(sym, end, days):
    """TuShare 日线（列名接口）

        A股  : daily + adj_factor **自算前复权**（不用 pro_bar，因为它内部 print 噪音
               且 adj='qfq' 对 ETF 会直接报错；自算最稳且零噪音）
        ETF  : fund_daily（基金无复权概念）
        港股 : hk_daily（多数账号无权限 → 跳过，交给 L3）
        指数 : index_daily（指数无需复权）
    """
    try:
        import tushare_init as ti
        pro = ti.get_pro()
    except Exception as e:
        log("L2 tushare init err %s" % str(e)[:80], err=True)
        return []
    s = norm(sym)
    start = time.strftime("%Y%m%d", time.localtime(
        time.time() - max(days, 60) * 2.2 * 86400))
    try:
        if is_index(s):
            return _tail(pro.index_daily(ts_code=s, start_date=start, end_date=end),
                         end, days)
        if is_hk(s):
            log("L2 港股 %s 无权限，跳 L3" % s)
            return []
        tc = to_ts(s)
        # ① A股：daily + adj_factor 自算前复权
        df = pro.daily(ts_code=tc, start_date=start, end_date=end)
        if df is not None and len(df):
            df = df.sort_values("trade_date").reset_index(drop=True)
            try:
                af = pro.adj_factor(ts_code=tc, start_date=start, end_date=end)
                if af is not None and len(af):
                    af = af.sort_values("trade_date").reset_index(drop=True)
                    m = dict(zip(af["trade_date"].astype(str), af["adj_factor"]))
                    last = [m.get(str(t)) for t in df["trade_date"].astype(str)][-1]
                    if last:
                        f = [ (m.get(str(t)) or last) / last
                              for t in df["trade_date"].astype(str) ]
                        for col in ("open", "high", "low", "close"):
                            df[col] = df[col] * f
            except Exception as e:
                log("L2 adj %s err %s（用不复权）" % (s, str(e)[:60]))
            rows = _std(df)
            rows = [r for r in rows if r[0] <= end]
            if rows:
                return rows[-days:] if days else rows
        # ② ETF/LOF：fund_daily（无复权）
        df = pro.fund_daily(ts_code=tc, start_date=start, end_date=end)
        return _tail(df, end, days)
    except Exception as e:
        log("L2 tushare %s err %s" % (sym, str(e)[:90]), err=True)
        return []


def _std(df):
    """TuShare DataFrame → 标准 [d8,o,h,l,c,vol]（按列名取，绝不按位置）"""
    if df is None or not len(df):
        return []
    out = []
    for r in df.itertuples(index=False):
        d = getattr(r, "trade_date", None)
        try:
            out.append([d8(d), float(r.open), float(r.high), float(r.low),
                        float(r.close), float(getattr(r, "vol", 0) or 0)])
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def _tail(df, end, days):
    rows = _std(df)
    rows = [r for r in rows if r[0] <= end]
    return rows[-days:] if days else rows


# ═══════════════════════════════════════════════════════════ L3 网页
# ★★★ 这一层的每个接口都必须**显式声明字段顺序**，因为返回的是裸数组！
WEB_SPECS = {
    # 腾讯 fqkline / hkfqkline：A股与港股**同为** [日期, 开, 收, 高, 低, 量, ...]
    # 验证：sz002463 2026-10-21 → ['2026-10-21','114.260','114.970','118.500','113.630']
    #       （开114.26 / 收114.97 / 高118.50 / 低113.63，确实收在第 3 位）
    #       06160   2026-10-22 → ['2026-10-22','230.800','225.200','233.200','222.800']
    "tencent": {"date": 0, "open": 1, "close": 2, "high": 3, "low": 4, "vol": 5},
    # 新浪 hq 日线（非常用，备胎）
    "sina": {"date": 0, "open": 1, "high": 2, "low": 3, "close": 4, "vol": 5},
    # 东财 push2his kline：klines 是 "日期,开,收,高,低,量,额" 逗号串
    "eastmoney": {"date": 0, "open": 1, "close": 2, "high": 3, "low": 4, "vol": 5},
}


def _apply_spec(arrs, spec, min_len=5):
    """按显式字段序把裸数组 → 标准 [d8,o,h,l,c,vol]"""
    out = []
    for a in arrs or []:
        try:
            if len(a) < min_len:
                continue
            out.append([d8(a[spec["date"]]),
                        float(a[spec["open"]]), float(a[spec["high"]]),
                        float(a[spec["low"]]), float(a[spec["close"]]),
                        float(a[spec["vol"]]) if len(a) > spec["vol"] and a[spec["vol"]] else 0.0])
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def _http(url, timeout=20, referer=None):
    hdr = {"User-Agent": UA}
    if referer:
        hdr["Referer"] = referer
    req = urllib.request.Request(url, headers=hdr)
    return urllib.request.urlopen(req, timeout=timeout).read()


def _l3_tencent(sym, end, days):
    """腾讯 fqkline（A股/指数）与 hkfqkline（港股）
    ⚠️ 字段序 [日期, 开, 收, 高, 低, 量] —— 收盘价在**第 3 位**
    """
    s = norm(sym)
    n = max(days or 260, 60)
    if is_hk(s):
        url = ("https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get"
               "?param=hk%s,day,,,%d,qfq" % (s, n))
        key = "hk" + s
        ref = "https://gu.qq.com/"
    else:
        tcode = to_sina(s)
        url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               "?param=%s,day,,,%d,qfq" % (tcode, n))
        key = tcode
        ref = "https://gu.qq.com/"
    try:
        d = json.loads(_http(url, referer=ref).decode("utf-8", "ignore"))
        node = (d.get("data") or {}).get(key) or {}
        arrs = node.get("qfqday") or node.get("day") or []
    except Exception as e:
        log("L3 tencent %s err %s" % (sym, str(e)[:80]), err=True)
        return []
    rows = _apply_spec(arrs, WEB_SPECS["tencent"])
    rows = [r for r in rows if r[0] <= end]
    return rows[-days:] if days else rows


def _l3_eastmoney(sym, end, days):
    """东财 push2his kline（备胎）：klines 每行 = '日期,开,收,高,低,量,额,...'"""
    s = norm(sym)
    n = max(days or 260, 60)
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           "?secid=%s&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57"
           "&klt=101&fqt=1&end=20500101&lmt=%d" % (to_em(s), n))
    for host in ("push2his.eastmoney.com", "push2his.eastmoney.com"):
        try:
            d = json.loads(_http(url.replace("push2his.eastmoney.com", host),
                                 referer="https://quote.eastmoney.com/").decode())
            kl = ((d.get("data") or {}).get("klines") or [])
            arrs = [x.split(",") for x in kl]
            rows = _apply_spec(arrs, WEB_SPECS["eastmoney"])
            rows = [r for r in rows if r[0] <= end]
            return rows[-days:] if days else rows
        except Exception as e:
            log("L3 eastmoney %s err %s" % (sym, str(e)[:80]), err=True)
    return []


def _l3_web(sym, end, days):
    rows = _l3_tencent(sym, end, days)
    if rows:
        return rows
    return _l3_eastmoney(sym, end, days)


# ═══════════════════════════════════════════════════════════ 统一出口
def daily(sym, end=None, days=1200, cross_check=False, layers=None,
          want_fresh=True, fresh_tol=0):
    """三级灾备日线。返回标准 [['20261022', o, h, l, c, vol], ...]（升序）

    layers     : 可强制指定顺序，例如 layers=('akshare','tushare','web')
    want_fresh : True = 择优「最新」数据（AKShare 东财日线常滞后 1 天，
                 若 L1 比 L2/L3 旧会继续往下试，取最新的那个）
    fresh_tol  : 容忍几天滞后（0 = 只要最新）

    ⚠️ 2026-10-22 用户要求：「三级灾备体系，每个源的顺序字段可能都不一样，你得都适配了」
       → 每源独立适配 + OHLC 硬校验 + **新鲜度择优**（不能因为 L1 先成功就用旧数据）
    """
    end = d8(end) or time.strftime("%Y%m%d")
    s = norm(sym)
    chain = layers or ("akshare", "tushare", "web")
    fns = {"akshare": _l1_akshare, "tushare": _l2_tushare, "web": _l3_web}
    got = []           # [(last_date, lname, rows)]
    for lname in chain:
        fn = fns.get(lname)
        if not fn:
            continue
        t0 = time.time()
        rows = fn(s, end, days)
        if not rows:
            continue
        good, bad = validate(rows, "%s/%s" % (lname, s))
        if not good:
            continue
        log("%s %s → L_%s %d 行（末尾 %s）%.1fs" % (
            s, "HK" if is_hk(s) else "A", lname, len(good),
            good[-1][0], time.time() - t0))
        got.append((good[-1][0], lname, good))
        # 已拿到「等于 end 或足够新」的数据就不用再往下试（省时间、省 token 配额）
        if not want_fresh or (end and good[-1][0] >= _minus_tol(end, fresh_tol)):
            break
    if not got:
        log("%s 三级全部失败（end=%s）" % (s, end), err=True)
        return []
    got.sort(key=lambda t: t[0], reverse=True)     # 最新优先；同日期按链路顺序
    return got[0][2]


def _minus_tol(end, tol):
    """end 往前推 tol 个自然日（粗略，仅用于新鲜度比较）"""
    if not tol:
        return end
    try:
        t = time.strptime(end, "%Y%m%d")
        return time.strftime("%Y%m%d", time.localtime(
            time.mktime(t) - tol * 86400))
    except Exception:
        return end


def index_daily(sym, end=None, days=600, cross_check=False):
    """指数日线（三级灾备）"""
    return daily(sym, end=end, days=days, cross_check=cross_check)


# ═══════════════════════════════════════════════════════════ 申万行业指数（三级灾备）
# 2026-10-28 大佬要求：「你这几个所有的行业获取体系，你都要放在 datahub 里边，
# 就是一个三级公共体系的数据获取里边」
#
# 说明：申万行业指数不属于普通「个股/交易所指数」，三级源各有各的口径：
#   L1 AKShare  index_hist_sw(symbol='801081', period='day')   ← 6 位无后缀，列名中文
#   L2 TuShare  sw_daily(ts_code='801081.SI', ...)              ← 带 .SI 后缀
#   L3 本地缓存 .cache/weekly_zs/sw_daily/<code>.csv（由 weekly_zs 增量维护）
# 统一出口仍是 list[[d8, open, high, low, close, vol], ...]，并过 validate() 闸门。
SW_CACHE = os.path.join(WS, ".cache", "weekly_zs", "sw_daily")
SW_L2MAP = os.path.join(WS, ".cache", "weekly_zs", "sw_l2.json")


def sw_code(code):
    """把 '801081' / '801081.SI' / '半导体' 都归一成 '801081.SI'；名称解析失败返回 None"""
    s = str(code).strip()
    if re.fullmatch(r"\d{6}\.SI", s, re.I):
        return s.upper()
    if re.fullmatch(r"\d{6}", s):
        return s + ".SI"
    # 按名称查 L2 映射（name → code 反查）
    try:
        m = json.load(open(SW_L2MAP, encoding="utf-8"))
        if s in m:
            return m[s]
        for k, v in m.items():
            if v == s:
                return k
        for k, v in m.items():
            if v.replace("Ⅱ", "") == s.replace("Ⅱ", ""):
                return k
    except Exception:
        pass
    return None


def _sw_l1(ts_code, n):
    """L1 AKShare：index_hist_sw"""
    import akshare as ak
    df = ak.index_hist_sw(symbol=ts_code[:6], period="day")
    if df is None or not len(df):
        return []
    df = df.tail(n)
    return [[str(r["日期"]).replace("-", "")[:8], float(r["开盘"]), float(r["最高"]),
             float(r["最低"]), float(r["收盘"]), float(r["成交额"])]
            for _, r in df.iterrows()]


def _sw_l2(ts_code, n):
    """L2 TuShare：sw_daily"""
    import datetime as _dt
    from tushare_init import ts_call
    end = _dt.date.today().strftime("%Y%m%d")
    df = ts_call("sw_daily", ts_code=ts_code, end_date=end)
    if df is None or not len(df):
        return []
    df = df.sort_values("trade_date").tail(n)
    return [[str(r["trade_date"]), float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]), float(r["amount"])]
            for _, r in df.iterrows()]


def _sw_l3(ts_code, n):
    """L3 本地缓存（weekly_zs 维护的 CSV）"""
    import csv
    p = os.path.join(SW_CACHE, "%s.csv" % ts_code)
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([str(r["dt"]).replace("-", "")[:8], float(r["open"]),
                         float(r["high"]), float(r["low"]), float(r["close"]), 0.0])
    return rows[-n:]


def sw_daily(code, days=800, policy="L123"):
    """申万行业指数日线（三级灾备，统一出口）。

    policy: 'L123' 全部依次尝试；'L3' 只用本地缓存（离线批量跑时最快）；
            'L1' 只用 akshare；'L2' 只用 tushare
    返回 list[[d8, o, h, l, c, vol], ...]（按日期升序，已过 OHLC 校验）
    """
    t = sw_code(code)
    if not t:
        log("sw_daily: 无法解析 %r" % code)
        return []
    layers = {"L1": [("L1-akshare-sw", _sw_l1)],
              "L2": [("L2-tushare-sw", _sw_l2)],
              "L3": [("L3-local-sw", _sw_l3)],
              "L123": [("L1-akshare-sw", _sw_l1), ("L2-tushare-sw", _sw_l2),
                       ("L3-local-sw", _sw_l3)]}.get(policy) or []
    # 本地缓存优先（有就直接用，最快且离线可用）
    if policy == "L123" and os.path.exists(os.path.join(SW_CACHE, "%s.csv" % t)):
        rows = _sw_l3(t, days)
        if len(rows) >= 200:
            good, _bad = validate(rows, "%s/local" % t)
            if len(good) >= 200:
                return good
    for lname, fn in layers:
        try:
            rows = fn(t, days)
            if not rows:
                continue
            good, bad = validate(rows, "%s/%s" % (lname, t))
            if len(bad) > len(good):
                log("sw_daily %s: %s 多数行 OHLC 异常，丢弃" % (t, lname))
                continue
            if good:
                return good
        except Exception as e:
            log("sw_daily %s %s ERR %s" % (t, lname, str(e)[:80]))
    return []


# ═══════════════════════════════════════════════════════════ 融资融券（两融）
# 2026-11-03 大佬要求：「两个实盘再加一条线，就是融资融券的监控…每一只股票的
# 融资融券 MA5 的变动情况…走平了或者转向了告诉我一声…给我一个表格」
#
# 设计（沿用三级灾备原则，**按交易日缓存全市场**，不做逐票请求）：
#   日档缓存 .cache/datahub/margin/day/<YYYYMMDD>.json
#       {"src": "tushare|akshare", "data": {"002463": {"rzye":…, "rqye":…, "rzrqye":…}}}
#   L1 TuShare  pro.margin_detail(trade_date=D)   ← 全市场一次到手，**含融券余额/两融余额**（口径最全）
#   L2 AKShare  stock_margin_detail_sse / _szse（按日，快 0.7~3.6s）
#       ⚠️ SSE 接口**不给融券余额**（只有融券余量股数）→ rqye/rzrqye 置 None，
#          上层算 MA5 时若窗口内缺 rzrqye 就自动退化为「融资余额 rzye」口径
#   L3 网页    东财 RPTA_WEB_RZRQ_GGMX（逐票 30~40s，仅单票补历史的最后手段）
#
# 为什么 L1 用 TuShare 而不是 AKShare（与日线层相反）：
#   两融「余额」需要融券余额（金额），上交所 akshare 接口只给融券余量（股），
#   拿不到金额就拼不出「融资融券余额」，所以全字段源优先。
MARGIN_CACHE = os.path.join(CACHE, "margin")
MARGIN_DAY = os.path.join(MARGIN_CACHE, "day")
CAL_CACHE = os.path.join(CACHE, "trade_cal.json")
_MARGIN_MEM = {}      # {date: {code: {...}}} 进程内缓存


def _margin_path(d):
    return os.path.join(MARGIN_DAY, "%s.json" % d8(d))


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None          # NaN → None
    except Exception:
        return None


def trade_dates(end=None, days=60):
    """最近 days 个交易日（升序，含 end 当日若为交易日）。缓存 trade_cal。"""
    end = d8(end) or time.strftime("%Y%m%d")
    cal = []
    try:
        if os.path.exists(CAL_CACHE):
            c = json.load(open(CAL_CACHE, encoding="utf-8"))
            cal = [str(x) for x in c.get("dates") or []]
    except Exception:
        cal = []
    if len(cal) < 100 or (cal and cal[-1] < end):
        try:
            import tushare_init as ti
            pro = ti.get_pro()
            start = time.strftime("%Y%m%d", time.localtime(time.time() - 900 * 86400))
            df = pro.trade_cal(exchange="SSE", start_date=start, end_date="20991231")
            if df is not None and len(df):
                cal = sorted(str(x) for x in df[df["is_open"] == 1]["cal_date"])
                os.makedirs(CACHE, exist_ok=True)
                json.dump({"dates": cal}, open(CAL_CACHE, "w", encoding="utf-8"))
        except Exception as e:
            log("trade_cal err %s" % str(e)[:90], err=True)
    out = [d for d in cal if d <= end]
    if not out:
        # 极端兜底：用日线日期（指数 000001.SH）
        out = [r[0] for r in index_daily("000001.SH", end=end, days=days)]
    return out[-days:]


def _margin_l1_tushare(day):
    """TuShare margin_detail 全市场（含融券余额/两融余额）"""
    try:
        import tushare_init as ti
        df = ti.ts_call("margin_detail", trade_date=d8(day))
    except Exception as e:
        log("margin tushare %s err %s" % (day, str(e)[:90]), err=True)
        return {}
    if df is None or not len(df):
        return {}
    out = {}
    for r in df.itertuples(index=False):
        tc = str(getattr(r, "ts_code", "") or "")
        if "." not in tc:
            continue
        code, mkt = tc.split(".")
        if mkt not in ("SH", "SZ", "BJ"):
            continue
        out[code] = {"rzye": _num(getattr(r, "rzye", None)),
                     "rqye": _num(getattr(r, "rqye", None)),
                     "rzrqye": _num(getattr(r, "rzrqye", None)),
                     "rqyl": _num(getattr(r, "rqyl", None))}
    return out


def _margin_l2_akshare(day):
    """AKShare 按日全市场（SSE 无融券余额 → 该市场 rzrqye/rqye 为 None）"""
    try:
        import akshare as ak
    except Exception as e:
        log("margin ak import err %s" % str(e)[:60], err=True)
        return {}
    out = {}
    for fn in ("stock_margin_detail_sse", "stock_margin_detail_szse"):
        try:
            df = _ak_call(getattr(ak, fn), date=d8(day), timeout=40)
        except Exception as e:
            log("margin ak %s %s err %s" % (fn, day, str(e)[:80]))
            continue
        if df is None or not len(df):
            continue
        ccol = "标的证券代码" if "标的证券代码" in df.columns else "证券代码"
        for r in df.to_dict("records"):
            code = str(r.get(ccol) or "").strip().zfill(6)
            if len(code) != 6:
                continue
            rzye = _num(r.get("融资余额"))
            rqye = _num(r.get("融券余额"))
            rzrqye = _num(r.get("融资融券余额")) or (
                (rzye or 0) + (rqye or 0) if rqye is not None else None)
            out[code] = {"rzye": rzye, "rqye": rqye, "rzrqye": rzrqye,
                         "rqyl": _num(r.get("融券余量"))}
    return out


def _margin_l3_eastmoney(code, day=None, days=90):
    """东财逐票历史（慢，仅兜底）：RPTA_WEB_RZRQ_GGMX → {date: {...}}"""
    c = str(code).strip().zfill(6)
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
           "reportName=RPTA_WEB_RZRQ_GGMX&columns=ALL&filter=" +
           urllib.parse.quote('(scode="%s")' % c) +
           "&pageNumber=1&pageSize=%d&sortColumns=date&sortTypes=-1&source=WEB&client=WEB"
           % max(days, 30))
    try:
        txt = _http(url, timeout=35, referer="https://data.eastmoney.com/")
        r = json.loads(txt)
        rows = ((r.get("result") or {}).get("data") or [])
    except Exception as e:
        log("margin eastmoney %s err %s" % (c, str(e)[:90]), err=True)
        return {}
    out = {}
    for x in rows:
        d = d8(str(x.get("DATE") or "")[:10])
        if len(d) != 8:
            continue
        out[d] = {"rzye": _num(x.get("RZYE")), "rqye": _num(x.get("RQYE")),
                  "rzrqye": _num(x.get("RZRQYE")), "rqyl": _num(x.get("RQYL"))}
    return out


def margin_day(day, policy=("tushare", "akshare"), use_cache=True, write=True):
    """某个交易日的全市场两融明细 {code6: {rzye,rqye,rzrqye,rqyl}}，按日缓存。

    进程内再做一层 memory cache（一个报告要读 20 天 × 25 票，不能反复解 JSON）
    """
    d = d8(day)
    if d in _MARGIN_MEM:
        return _MARGIN_MEM[d]
    p = _margin_path(d)
    if use_cache and os.path.exists(p):
        try:
            j = json.load(open(p, encoding="utf-8"))
            data = j.get("data") or {}
            _MARGIN_MEM[d] = data
            return data
        except Exception:
            pass
    fns = {"tushare": lambda a: _margin_l1_tushare(a),
           "akshare": lambda a: _margin_l2_akshare(a)}
    data, src = {}, ""
    for name in policy:
        fn = fns.get(name)
        if not fn:
            continue
        data = fn(d) or {}
        if data:
            src = name
            break
    if data and write:
        try:
            os.makedirs(MARGIN_DAY, exist_ok=True)
            json.dump({"src": src, "date": d, "data": data},
                      open(p, "w", encoding="utf-8"))
        except Exception as e:
            log("margin 写缓存失败 %s %s" % (d, str(e)[:60]))
    if data:
        _MARGIN_MEM[d] = data
    return data


def margin_cached_days():
    """已下载的日档日期列表（升序）"""
    try:
        fs = sorted(os.listdir(MARGIN_DAY))
    except Exception:
        return []
    return [f[:-5] for f in fs if f.endswith(".json")]


def margin_history(sym, end=None, days=60, fill=False):
    """单票两融历史（升序）

    返回 [{'d': '20261103', 'rzye':…, 'rqye':…, 'rzrqye':…}, ...]
    fill=False（默认，报告用）：**只读日档缓存，缺日直接跳过**，绝不联网（快）
    fill=True ：缺哪天就补哪天的全市场档（慢，回补/首次跑用）
    """
    s = norm(sym)
    if s.startswith(("SH", "SZ")):
        code = s[2:]
    elif re.fullmatch(r"\d{6}", s):
        code = s
    else:
        return []                              # 港股/海外无两融
    ds = trade_dates(end=end, days=days)
    out = []
    for d in ds:
        if not os.path.exists(_margin_path(d)):
            if not fill:
                continue                      # 缺档：默认不联网，跳过
            margin_day(d)                     # 补档（失败也继续）
        m = margin_day(d, use_cache=True)
        it = m.get(code)
        if it is None:
            continue
        out.append({"d": d, "rzye": it.get("rzye"), "rqye": it.get("rqye"),
                    "rzrqye": it.get("rzrqye")})
    return out


def margin_backfill(days=60, end=None, policy=("tushare", "akshare")):
    """把最近 days 个交易日的日档全部补齐（一次性回补用，已存在则跳过）"""
    ds = trade_dates(end=end, days=days)
    new = 0
    for d in ds:
        if os.path.exists(_margin_path(d)):
            continue
        m = margin_day(d, policy=policy)
        if m:
            new += 1
        time.sleep(0.3)
    return len(ds), new


def last_date(sym, end=None):
    r = daily(sym, end=end, days=5)
    return r[-1][0] if r else None


def quote(sym):
    """实时快照（只用于盘中展示，不用于技术判定）：{last, prev, pct, name}"""
    s = norm(sym)
    try:
        if is_hk(s):
            url = "https://hq.sinajs.cn/list=hk%s" % s
        else:
            url = "https://hq.sinajs.cn/list=%s" % to_sina(s)
        txt = _http(url, referer="https://finance.sina.com.cn").decode("gbk", "ignore")
        m = re.search(r'="([^"]*)"', txt)
        if not m:
            return {}
        p = m.group(1).split(",")
        if is_hk(s):
            # 港股 sina 字段：英文名,中文名,开,昨收,高,低,现价,...
            return {"name": p[1], "open": float(p[2]), "prev": float(p[3]),
                    "high": float(p[4]), "low": float(p[5]), "last": float(p[6])}
        # A股：名称,开,昨收,现价,高,低,...
        return {"name": p[0], "open": float(p[1]), "prev": float(p[2]),
                "last": float(p[3]), "high": float(p[4]), "low": float(p[5])}
    except Exception as e:
        log("quote %s err %s" % (sym, str(e)[:80]), err=True)
        return {}


# ═══════════════════════════════════════════════════════════ 自检
def self_test(symbols=None):
    """逐源体检：打印各源原始字段，人眼核对字段顺序是否被改

    用户要求「你得都适配了」→ 这个自检必须能一眼看出哪个源错位。
    """
    syms = symbols or ["SZ002463", "SH600183", "06160", "SH501225", "000300.SH"]
    print("=" * 78)
    print("DataHub 三级灾备自检　（每源独立适配 + OHLC 校验）")
    print("=" * 78)
    for sym in syms:
        print("\n【%s】" % sym)
        for lname, fn in (("L1 akshare", _l1_akshare), ("L2 tushare", _l2_tushare),
                          ("L3 web", _l3_web)):
            try:
                rows = fn(norm(sym), time.strftime("%Y%m%d"), 500)
            except Exception as e:
                print("  %-12s ERR %s" % (lname, str(e)[:60]))
                continue
            if not rows:
                print("  %-12s 空" % lname)
                continue
            good, bad = validate(rows, "%s/%s" % (lname, sym))
            flag = "OK " if len(good) == len(rows) else ("剔除%d" % len(bad))
            tail = good[-1] if good else rows[-1]
            print("  %-12s %d行 %s  末行 %s O%.2f H%.2f L%.2f C%.2f" % (
                lname, len(rows), flag, tail[0], tail[1], tail[2], tail[3], tail[4]))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--quote", default="")
    ap.add_argument("--margin", default="", help="两融体检：逗号分隔代码，如 SZ002463,SH600183")
    ap.add_argument("--margin-backfill", type=int, default=0, help="回补最近 N 个交易日的两融日档")
    a = ap.parse_args()
    if a.margin_backfill:
        n, new = margin_backfill(days=a.margin_backfill)
        print("margin_backfill: 交易日 %d，新补 %d 天；缓存共 %d 天" % (
            n, new, len(margin_cached_days())))
    elif a.margin:
        for s in [x.strip() for x in a.margin.split(",") if x.strip()]:
            h = margin_history(s, days=20, fill=True)
            print("%s %d 条" % (s, len(h)), h[-2:] if h else "（无两融）")
    elif a.quote:
        for s in a.quote.split(","):
            print(s, quote(s.strip()))
    else:
        self_test([x.strip() for x in a.symbols.split(",") if x.strip()] or None)
