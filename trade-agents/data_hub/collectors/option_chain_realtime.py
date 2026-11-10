# -*- coding: utf-8 -*-
"""
实时期权链采集器（盘中实时持仓量 OI / 成交量 / 报价）
====================================================================

定位
----
日频 ``option_chain.py`` 走 tushare opt_daily，只能给盘后收盘快照；
半小时级的「期权期货持仓量监控」要的是**盘中实时**持仓量，本模块补齐这一能力。

数据源（多源冗余架构，逐源落地）
--------------------------------
- ``sina``   新浪财经 hq.sinajs.cn（CON_OP 批量T型报价）+ CFFEX openapi
             —— 2026-10 实测全链路可用，作为**首个落地源**。
- (预留) eastmoney 东财 push2 f62 持仓量；当前节点 RemoteDisconnected，待恢复后接入。
- (预留) tencent  腾讯自选股；ths 同花顺。

设计原则：宁慢勿空、绝不造假。新浪 CFFEX openapi 不含成交量/成交额字段时，
对应字段返回 None，不编造；非交易时段返回上一交易日收盘快照，由 ``is_trading_hours``
/``quote_time`` 如实标注新鲜度。

输出列与日频 ``option_chain.CHAIN_COLUMNS`` 对齐，可直接喂给
``option_metrics.standardize_contracts / calc_pcr / calc_25d_risk_reversal``。
"""

from __future__ import annotations

import re
import time
import logging
import requests
from datetime import datetime, date

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 标的注册表（6 标的，与生产 OI 监控 TARGET_MAP 对齐）
#   kind: etf=ETF期权 / cffex=中金所股指期权
#   5 大宽基（300/500/创业板/中证1000MO/科创50）供单一真源指标计算；
#   IO 为服务生产 OI 监控保留。
# ---------------------------------------------------------------------------
REALTIME_OPTION_TARGETS = {
    "300ETF":    {"label": "沪深300",    "kind": "etf",   "code": "510300", "cate": "300ETF", "spot_quote": "sh510300", "exchange": "SSE"},
    "500ETF":    {"label": "中证500",    "kind": "etf",   "code": "510500", "cate": "500ETF", "spot_quote": "sh510500", "exchange": "SSE"},
    "创业板ETF": {"label": "创业板",     "kind": "etf",   "code": "159915", "cate": "创业板", "spot_quote": "sz159915", "exchange": "SZSE"},
    "科创50ETF": {"label": "科创50",     "kind": "etf",   "code": "588000", "cate": "科创50", "spot_quote": "sh588000", "exchange": "SSE"},
    "IO":        {"label": "沪深300股指", "kind": "cffex", "prefix": "io",  "spot_quote": "sh000300", "exchange": "CFFEX"},
    "MO":        {"label": "中证1000股指", "kind": "cffex", "prefix": "mo",  "spot_quote": "sh000852", "exchange": "CFFEX"},
}

# 5 大宽基（与日频 option_chain.OPTION_CHAIN_TARGETS 口径一致）
CORE_TARGET_KEYS = ["300ETF", "500ETF", "创业板ETF", "MO", "科创50ETF"]

# 与日频链对齐的标准化输出列（实时额外报价字段挂在同一 dict）
REALTIME_CHAIN_COLUMNS = [
    "ts_code", "call_put", "exercise_price", "maturity_date",
    "close", "vol", "oi", "amount", "dte", "source",
]

_SINA_HQ = "https://hq.sinajs.cn/list="
_SINA_MONTH_URL = ("https://stock.finance.sina.com.cn/futures/api/openapi.php/"
                   "StockOptionService.getStockName")
_SINA_CFFEX_URL = ("https://stock.finance.sina.com.cn/futures/api/openapi.php/"
                   "OptionService.getOptionData")
_SINA_HEADERS = {
    "Referer": "https://stock.finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
_BATCH = 60
_RETRIES = 3


def _request(url, params=None, encoding=None, retries=_RETRIES):
    """带重试的 GET，失败返回 None（不抛、不造假）。"""
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=_SINA_HEADERS, timeout=15)
            if encoding:
                r.encoding = encoding
            if r.status_code == 200 and r.text and '=""' not in r.text:
                return r
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(0.4 * (i + 1))
    if last_err:
        logger.debug("sina request failed %s: %s", url, last_err)
    return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_trading_hours(now: datetime | None = None) -> bool:
    """A 股/中金所连续竞价时段（工作日 09:30-11:30 / 13:00-15:00）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)


# ===========================================================================
# 现货实时价（新浪 hq：name,今开,昨收,最新价,...）
# ===========================================================================
def get_realtime_spot(spot_quote: str) -> dict:
    r = _request(_SINA_HQ + spot_quote, encoding="gbk")
    if not r:
        return {"spot": None, "spot_name": None, "source": "sina"}
    m = re.search(r'="(.*)";', r.text)
    if not m or not m.group(1):
        return {"spot": None, "spot_name": None, "source": "sina"}
    f = m.group(1).split(",")
    # 指数/股票: [0]名称 [1]今开 [2]昨收 [3]最新价
    return {"spot": _f(f[3]) if len(f) > 3 else None,
            "spot_name": f[0] if f else None,
            "source": "sina"}


# ===========================================================================
# ETF 期权（CON_OP 批量 T 型报价）
# ===========================================================================
def _etf_months(cate: str) -> list[str]:
    """getStockName -> ['2610','2611',...]（去重保序，YYYY-MM 转 YYMM）。"""
    r = _request(_SINA_MONTH_URL, params={"exchange": "null", "cate": cate})
    if not r:
        return []
    try:
        raw = r.json().get("result", {}).get("data", {}).get("contractMonth", [])
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for ym in raw:
        yymm = str(ym).replace("-", "")[2:]
        if yymm not in seen:
            seen.add(yymm)
            out.append(yymm)
    return out


def _etf_contract_codes(etf_code: str, yymm: str, call: bool) -> list[str]:
    """hq.sinajs.cn/list=OP_UP_5103002611 -> [CON_OP 数字代码,...]。"""
    flag = "OP_UP_" if call else "OP_DOWN_"
    r = _request(f"{_SINA_HQ}{flag}{etf_code}{yymm}", encoding="gbk")
    if not r:
        return []
    try:
        inner = r.text.split('"')[1]
        return [c.replace("CON_OP_", "") for c in inner.split(",") if c.startswith("CON_OP_")]
    except IndexError:
        return []


def _con_op_quotes(codes: list[str]) -> list[dict]:
    """批量拉 CON_OP 报价并解析为标准化合约 dict。"""
    rows = []
    for i in range(0, len(codes), _BATCH):
        batch = codes[i:i + _BATCH]
        r = _request(_SINA_HQ + ",".join(f"CON_OP_{c}" for c in batch), encoding="gbk")
        if not r:
            continue
        for line in r.text.strip().splitlines():
            m = re.match(r'var hq_str_CON_OP_(\d+)="(.+)";', line.strip())
            if not m:
                continue
            v = m.group(2).split(",")
            if len(v) < 48:
                continue
            cp = v[45] if v[45] in ("C", "P") else ("C" if "购" in v[37] else "P")
            maturity = v[46].replace("-", "") if v[46] else ""
            rows.append({
                "ts_code": m.group(1),
                "name": v[37],
                "call_put": cp,
                "exercise_price": _f(v[7]),
                "maturity_date": maturity,
                "dte": int(_f(v[47]) or 0) if v[47] else None,
                "close": _f(v[2]),
                "vol": _f(v[41]),
                "oi": _f(v[5]),
                "amount": _f(v[42]),
                "bid": _f(v[1]), "ask": _f(v[3]),
                "bid_vol": _f(v[0]), "ask_vol": _f(v[4]),
                "open": _f(v[9]), "high": _f(v[39]), "low": _f(v[40]),
                "prev_close": _f(v[8]), "prev_settle": _f(v[44]),
                "pct": _f(v[6]),
                "quote_time": v[32] if len(v) > 32 else None,
                "source": "sina_realtime",
            })
    return rows


def _fetch_etf_chain(cfg: dict, months: list[str] | None) -> list[dict]:
    cate, code = cfg["cate"], cfg["code"]
    ym_list = months or _etf_months(cate)
    if not ym_list:
        logger.warning("[realtime] %s 取不到月份列表", cate)
        return []
    chain: list[dict] = []
    for ym in ym_list:
        up = _etf_contract_codes(code, ym, True)
        dn = _etf_contract_codes(code, ym, False)
        quotes = _con_op_quotes(up + dn)
        chain.extend(quotes)
        logger.debug("[realtime] %s %s: %d calls + %d puts", cate, ym, len(up), len(dn))
    return chain


# ===========================================================================
# 中金所股指期权（CFFEX openapi：无 vol/amount 字段，如实置 None）
# ===========================================================================
def _cffex_third_friday(yymm: str) -> str:
    yy = 2000 + int(yymm[:2]); mm = int(yymm[2:])
    first = date(yy, mm, 1)
    first_fri = 1 + ((4 - first.weekday()) % 7)
    return date(yy, mm, first_fri + 14).strftime("%Y%m%d")


def _enum_cffex_months(prefix: str, n: int = 6) -> list[str]:
    """枚举未来 n 个 YYMM，逐个探活（不依赖页面解析）。"""
    today = date.today()
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{str(y)[2:]}{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _fetch_cffex_chain(cfg: dict, months: list[str] | None) -> list[dict]:
    prefix = cfg["prefix"]
    ym_list = months or _enum_cffex_months(prefix)
    today = date.today()
    chain: list[dict] = []
    for ym in ym_list:
        pinzhong = f"{prefix}{ym}"
        r = _request(_SINA_CFFEX_URL, params={
            "type": "futures", "product": prefix,
            "exchange": "cffex", "pinzhong": pinzhong})
        if not r:
            continue
        try:
            data = r.json().get("result", {}).get("data", {}) or {}
        except Exception:  # noqa: BLE001
            continue
        up, down = data.get("up", []), data.get("down", [])
        if not up and not down:
            continue
        maturity = _cffex_third_friday(ym)
        try:
            dte = (datetime.strptime(maturity, "%Y%m%d").date() - today).days
        except Exception:  # noqa: BLE001
            dte = None
        # 整月已到期（如当月第三个周五已过）直接跳过，不污染实时链
        if dte is not None and dte < 0:
            continue
        # call: [买量,买价,最新,卖价,卖量,持仓,涨跌,行权价,代码]
        for it in up:
            if len(it) < 9:
                continue
            chain.append({"ts_code": it[8], "name": it[8], "call_put": "C",
                          "exercise_price": _f(it[7]), "maturity_date": maturity, "dte": dte,
                          "close": _f(it[2]), "vol": None, "oi": _f(it[5]), "amount": None,
                          "bid": _f(it[1]), "ask": _f(it[3]),
                          "bid_vol": _f(it[0]), "ask_vol": _f(it[4]), "pct": _f(it[6]),
                          "quote_time": None, "source": "sina_realtime"})
        # put: 无行权价字段，从代码 ...P3900 提取
        for it in down:
            if len(it) < 8:
                continue
            ccode = it[7]
            mm = re.search(r"[CP](\d+)$", ccode)
            chain.append({"ts_code": ccode, "name": ccode, "call_put": "P",
                          "exercise_price": _f(mm.group(1)) if mm else None,
                          "maturity_date": maturity, "dte": dte,
                          "close": _f(it[2]), "vol": None, "oi": _f(it[5]), "amount": None,
                          "bid": _f(it[1]), "ask": _f(it[3]),
                          "bid_vol": _f(it[0]), "ask_vol": _f(it[4]), "pct": _f(it[6]),
                          "quote_time": None, "source": "sina_realtime"})
    return chain


# ===========================================================================
# 深交所官方当日合约（第二冗余，权威免费；仅深市 ETF，核心价值=官方实时持仓量）
#   akshare option_current_day_szse() -> 深交所期权子网当日合约
#   只含 OI/前结算/合约静态信息，无实时最新价/成交量 → close/vol 如实置 None。
#   定位：新浪（全字段）不可用时为深市标的兜底 OI，并可用于官方口径交叉校验。
# ===========================================================================
def _fetch_szse_official_chain(cfg: dict, months: list[str] | None) -> list[dict]:
    if cfg.get("exchange") != "SZSE":
        return []  # 该源仅覆盖深市（创业板159915等）；沪市/中金所直接跳过
    try:
        import akshare as ak
        df = ak.option_current_day_szse()
    except Exception as e:  # noqa: BLE001
        logger.warning("[realtime] 深交所官方当日合约取数失败: %s", str(e)[:120])
        return []
    if df is None or df.empty:
        return []
    code = cfg["code"]
    sub = df[df["标的证券简称(代码)"].astype(str).str.contains(code, regex=False)]
    if sub.empty:
        return []
    chain = []
    for _, r in sub.iterrows():
        cp = "C" if str(r.get("合约类型", "")) == "认购" else "P"
        mat = str(r.get("到期日", "")).replace("-", "")
        chain.append({
            "ts_code": str(r.get("合约编码", "")),
            "name": str(r.get("合约简称", "")),
            "call_put": cp,
            "exercise_price": _f(r.get("行权价")),
            "maturity_date": mat,
            "dte": int(r.get("合约到期剩余自然天数")) if _f(r.get("合约到期剩余自然天数")) is not None else None,
            "close": None,                                   # 官方当日合约表无实时最新价
            "vol": None, "amount": None,                     # 无成交量/额
            "oi": _f(r.get("合约总持仓")),                    # ★官方实时持仓量
            "prev_settle": _f(r.get("前结算价")),
            "bid": None, "ask": None, "bid_vol": None, "ask_vol": None,
            "open": None, "high": None, "low": None,
            "prev_close": None, "pct": None,
            "quote_time": str(r.get("交易日期", "")),
            "source": "szse_official",
        })
    return chain


# ===========================================================================
# 多源 dispatch
# ---------------------------------------------------------------------------
# 新增冗余源时：实现 _fetch_<source>_chain(cfg, months)，注册进 _SOURCE_FETCHERS
# 并把源名按优先级加入 SOURCE_PRIORITY；get_realtime_option_chain 自动按序降级。
#
# 已接入：
#   sina          新浪 CON_OP + CFFEX openapi（全字段，6标的，首源）
#   szse_official 深交所官方当日合约（仅深市ETF，权威OI兜底/校验）
# 未接入（2026-10 实测免费端无可用公开期权链，详见模块/记忆说明）：
#   eastmoney 东财 push2 f62 —— 节点 RemoteDisconnected（主人已明确放弃）
#   tencent   腾讯自选股 —— qt.gtimg 不识别期权代码、ifzq option controller 403，
#             免费公开端无期权链（仅 App 私有协议，逆向脆弱）
#   ths       同花顺 —— 免费端期权必须 X-api-key(付费)；data-provider 授权网关
#             (ifind-data-skill) 的 real-time-quotation 指标表无 OI/期权，
#             需单独开通 iFinD 期权权限后方可接入
# ===========================================================================
_SOURCE_FETCHERS = {
    "sina": {"etf": _fetch_etf_chain, "cffex": _fetch_cffex_chain},
    "szse_official": {"etf": _fetch_szse_official_chain},
    # "tencent": {...},   # 免费端无公开期权链，待 App 私有协议或付费方案
    # "ths":     {...},   # 待开通 iFinD 期权权限 / 提供 X-api-key
}
SOURCE_PRIORITY = ["sina", "szse_official"]


def get_realtime_option_chain(key: str, months: list[str] | None = None,
                              sources: list[str] | None = None) -> dict:
    """
    获取单个标的的**实时期权链**（含盘中实时持仓量 OI）。

    :param key: REALTIME_OPTION_TARGETS 键，如 '300ETF' / 'MO'
    :param months: 仅取指定到期月（YYMM），None=全部挂牌月
    :param sources: 源优先级，None=SOURCE_PRIORITY（当前仅新浪）
    :return: {key,label,chain,spot,count,source,fetched_at,is_trading_hours,error}
    """
    cfg = REALTIME_OPTION_TARGETS.get(key)
    if not cfg:
        return {"key": key, "chain": [], "count": 0, "source": None,
                "error": f"未知标的: {key}"}
    fetched_at = datetime.now()
    result = {"key": key, "label": cfg["label"], "chain": [], "count": 0,
              "source": None, "fetched_at": fetched_at.strftime("%Y-%m-%d %H:%M:%S"),
              "is_trading_hours": is_trading_hours(fetched_at), "error": None}
    spot = get_realtime_spot(cfg["spot_quote"])
    result["spot"] = spot.get("spot")
    result["spot_name"] = spot.get("spot_name")

    for src in (sources or SOURCE_PRIORITY):
        fetcher = _SOURCE_FETCHERS.get(src, {}).get(cfg["kind"])
        if not fetcher:
            continue
        try:
            chain = fetcher(cfg, months)
        except Exception as e:  # noqa: BLE001
            logger.warning("[realtime] %s via %s 异常: %s", key, src, e)
            chain = []
        if chain:
            result["chain"] = chain
            result["count"] = len(chain)
            result["source"] = src
            return result
    result["error"] = f"所有源均未取到数据: {sources or SOURCE_PRIORITY}"
    logger.warning("[realtime] %s 全源为空", key)
    return result


def get_all_realtime_option_chains(keys: list[str] | None = None,
                                   months: list[str] | None = None,
                                   sources: list[str] | None = None) -> dict:
    """
    批量获取实时期权链（默认 5 大宽基；传 keys 可含 IO）。
    :return: {key: get_realtime_option_chain(...)}
    """
    keys = keys or CORE_TARGET_KEYS
    out = {}
    for k in keys:
        out[k] = get_realtime_option_chain(k, months=months, sources=sources)
        time.sleep(0.2)
    return out


# ===========================================================================
# 期货实时报价 / 持仓量（新浪 nf_ 连续合约）
#   fields: [0]今开 [1]最高? [2]最低 [3]最新 [4]成交量 [5]成交额 [6]持仓量
#           [13]涨停 [14]跌停 [36]日期 [37]时间 [49]名称
# ===========================================================================
# OI/基差监控默认金融期货（主力连续 nf_XX0）；商品期货由调用方传 codes。
REALTIME_FUTURES_DEFAULT = ["IF0", "IC0", "IH0", "IM0", "T0", "TF0", "TS0"]


def get_realtime_futures(symbols: list[str] | None = None) -> dict:
    """
    批量获取期货主力连续合约的**实时持仓量/成交量/最新价**。

    :param symbols: 合约符号（不含 nf_ 前缀、主力加 0），如 ['IF0','CU0']；
                    None=REALTIME_FUTURES_DEFAULT（金融期货）
    :return: {symbol: {last,volume,open_interest,amount,name,quote_date,
                       quote_time,source}, ...}，取不到的标的不出现
    """
    symbols = symbols or REALTIME_FUTURES_DEFAULT
    codes = [f"nf_{s}" for s in symbols]
    out: dict = {}
    for i in range(0, len(codes), _BATCH):
        r = _request(_SINA_HQ + ",".join(codes[i:i + _BATCH]), encoding="gbk",
                     )
        if not r:
            continue
        for line in r.text.strip().splitlines():
            line = line.strip()
            if "=" not in line or '=""' in line:
                continue
            var, _, val = line.partition("=")
            sym = var.replace("var hq_str_nf_", "").strip()
            f = val.strip().strip(";").strip('"').split(",")
            if len(f) < 7:
                continue
            try:
                out[sym[:-1] if sym.endswith("0") else sym] = {
                    "last": _f(f[3]),
                    "volume": _f(f[4]),
                    "open_interest": _f(f[6]),
                    "amount": _f(f[5]),
                    "limit_up": _f(f[13]) if len(f) > 14 else None,
                    "limit_down": _f(f[14]) if len(f) > 14 else None,
                    "name": f[49] if len(f) > 49 else None,
                    "quote_date": f[36] if len(f) > 36 else None,
                    "quote_time": f[37] if len(f) > 37 else None,
                    "source": "sina_realtime",
                }
            except Exception as e:  # noqa: BLE001
                logger.debug("解析期货实时行失败 %s: %s", sym, e)
    return out


__all__ = [
    "REALTIME_OPTION_TARGETS", "CORE_TARGET_KEYS", "REALTIME_CHAIN_COLUMNS",
    "REALTIME_FUTURES_DEFAULT",
    "is_trading_hours", "get_realtime_spot",
    "get_realtime_option_chain", "get_all_realtime_option_chains",
    "get_realtime_futures",
    "SOURCE_PRIORITY",
]
