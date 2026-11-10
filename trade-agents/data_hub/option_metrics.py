"""
期权指标计算模块
================
基于期权链数据计算各种波动率和情绪指标：
- IV隐含波动率（BS公式反推）
- VIX类指数（方差互换法）
- PCR（成交量比、持仓量比）
- Skew（偏度，25delta看跌/看涨IV比）
- 期限结构（近月/远月平值IV差）
- ATM IV

纯Python零LLM，硬编码公式。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

import numpy as np
import pandas as pd


# ============================================================
# BS公式反推IV（牛顿迭代法）
# ============================================================

def _norm_cdf(x: float) -> float:
    """标准正态分布CDF。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """标准正态分布PDF。"""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool = True) -> float:
    """
    Black-Scholes 期权定价。

    Args:
        S: 标的现货价格
        K: 行权价
        T: 剩余期限（年）
        r: 无风险利率（年化）
        sigma: 波动率（年化）
        is_call: True=看涨，False=看跌

    Returns:
        期权理论价格
    """
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if is_call:
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """BS公式的Vega（对sigma的偏导）。"""
    if sigma <= 0 or T <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return S * math.sqrt(T) * _norm_pdf(d1)


def implied_vol(
    option_price: float,
    S: float,
    K: float,
    T: float,
    r: float = 0.025,
    is_call: bool = True,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> float:
    """
    牛顿迭代法反推隐含波动率。

    Args:
        option_price: 期权市场价格
        S: 标的现货价格
        K: 行权价
        T: 剩余期限（年）
        r: 无风险利率
        is_call: True=看涨，False=看跌
        max_iter: 最大迭代次数
        tol: 收敛精度

    Returns:
        年化IV（0.2 = 20%），失败返回0
    """
    if option_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return 0.0

    # 检查套利边界
    intrinsic = max(S - K, 0) if is_call else max(K - S, 0)
    if option_price < intrinsic:
        return 0.0

    sigma = 0.3  # 初始值30%

    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, is_call)
        diff = option_price - price

        if abs(diff) < tol:
            return sigma

        vega = bs_vega(S, K, T, r, sigma)
        if vega < 1e-10:
            break

        sigma = sigma + diff / vega

        # 限制范围
        if sigma < 0.001:
            sigma = 0.001
        if sigma > 5.0:
            sigma = 5.0

    return max(sigma, 0.0)


# ============================================================
# 期权链标准化
# ============================================================

@dataclass
class OptionContract:
    """标准化期权合约。"""
    code: str              # 合约代码
    underlying: str        # 标的代码
    strike: float          # 行权价
    expiry: str            # 到期日（YYYYMMDD）
    opt_type: str          # 'C' 或 'P'
    close: float = 0.0     # 收盘价
    volume: float = 0.0    # 成交量
    open_interest: float = 0.0  # 持仓量
    amount: float = 0.0    # 成交额
    iv: float = 0.0        # 隐含波动率（如果数据源提供）
    delta: float = 0.0     # delta（如果数据源提供）
    dte: int = 0           # 剩余天数
    bid: float = 0.0       # 买一价（实时链有，历史日线通常为0；VIX 优先用中间价）
    ask: float = 0.0       # 卖一价


def standardize_contracts(
    df: pd.DataFrame,
    source: str = "tushare",
    exchange: str = "",
    trade_date: str = "",
) -> List[OptionContract]:
    """
    将不同数据源的期权合约DataFrame标准化为OptionContract列表。

    Args:
        df: 原始合约DataFrame
        source: 数据源名称
        exchange: 交易所
        trade_date: 交易日期（YYYYMMDD）

    Returns:
        List[OptionContract]
    """
    contracts = []
    if df is None:
        return contracts
    # 兼容实时链 list[dict]（列与 tushare 对齐：ts_code/call_put/exercise_price/...）
    if isinstance(df, list):
        if not df:
            return contracts
        df = pd.DataFrame(df)
    if df.empty:
        return contracts

    trade_dt = pd.Timestamp(trade_date) if trade_date else pd.Timestamp.now()

    if source == "tushare":
        # opt_basic + opt_daily 合并后的格式
        # 列可能有: ts_code, opt_code, call_code, put_code, exercise_price,
        #          maturity_date, opt_type, close, vol, oi, amount
        for _, row in df.iterrows():
            try:
                code = str(row.get('ts_code', row.get('opt_code', '')))
                strike = float(row.get('exercise_price', row.get('strike', 0)))
                expiry = str(row.get('maturity_date', row.get('expiry', '')))
                opt_type = str(row.get('opt_type', row.get('call_put', ''))).upper()
                # 上游已按名称推断好的 call_put 列（购/沽→C/P）优先
                if 'call_put' in row.index:
                    cp = str(row.get('call_put', '')).upper()
                    if cp in ('C', 'P'):
                        opt_type = cp
                if opt_type in ('C', 'CALL'):
                    opt_type = 'C'
                elif opt_type in ('P', 'PUT'):
                    opt_type = 'P'
                else:
                    # 从合约代码推断
                    if 'C' in code.upper():
                        opt_type = 'C'
                    elif 'P' in code.upper():
                        opt_type = 'P'
                    else:
                        continue

                close = float(row.get('close', row.get('settle', 0)) or 0)
                volume = float(row.get('vol', row.get('volume', 0)) or 0)
                oi = float(row.get('oi', row.get('open_interest', 0)) or 0)
                amount = float(row.get('amount', row.get('turnover', 0)) or 0)
                iv_val = float(row.get('iv', 0) or 0)
                # 实时链带 bid/ask（新浪 CON_OP）；历史 opt_daily 没有，默认0，
                # VIX 计算时自动退回 close（见 calc_vix._opt_price）。
                bid_val = float(row.get('bid', 0) or 0)
                ask_val = float(row.get('ask', 0) or 0)

                # 计算剩余天数
                dte = 0
                if expiry and expiry != 'nan':
                    try:
                        exp_dt = pd.Timestamp(expiry)
                        dte = max((exp_dt - trade_dt).days, 0)
                    except Exception:
                        pass

                contracts.append(OptionContract(
                    code=code,
                    underlying="",
                    strike=strike,
                    expiry=expiry,
                    opt_type=opt_type,
                    close=close,
                    volume=volume,
                    open_interest=oi,
                    amount=amount,
                    iv=iv_val,
                    dte=dte,
                    bid=bid_val,
                    ask=ask_val,
                ))
            except Exception:
                continue

    elif source == "akshare_sse":
        # 上交所期权：列名含中文
        for _, row in df.iterrows():
            try:
                code = str(row.get('期权代码', row.get('合约代码', '')))
                strike = float(row.get('行权价', row.get('行权价格', 0)) or 0)
                expiry = str(row.get('到期日', row.get('合约到期日', '')))
                opt_type_str = str(row.get('期权类型', row.get('类型', ''))).strip()
                if '购' in opt_type_str or '看涨' in opt_type_str:
                    opt_type = 'C'
                elif '沽' in opt_type_str or '看跌' in opt_type_str:
                    opt_type = 'P'
                else:
                    if 'C' in code.upper() or '购' in code:
                        opt_type = 'C'
                    elif 'P' in code.upper() or '沽' in code:
                        opt_type = 'P'
                    else:
                        continue

                close = float(row.get('收盘价', row.get('最新价', 0)) or 0)
                volume = float(row.get('成交量', 0) or 0)
                oi = float(row.get('持仓量', 0) or 0)
                amount = float(row.get('成交额', 0) or 0)
                iv_val = float(row.get('隐含波动率', row.get('IV', 0)) or 0)
                # 百分比转小数
                if iv_val > 1:
                    iv_val = iv_val / 100.0

                dte = 0
                if expiry and expiry != 'nan':
                    try:
                        exp_dt = pd.Timestamp(expiry)
                        dte = max((exp_dt - trade_dt).days, 0)
                    except Exception:
                        pass

                contracts.append(OptionContract(
                    code=code,
                    underlying="",
                    strike=strike,
                    expiry=expiry,
                    opt_type=opt_type,
                    close=close,
                    volume=volume,
                    open_interest=oi,
                    amount=amount,
                    iv=iv_val,
                    dte=dte,
                ))
            except Exception:
                continue

    elif source == "akshare_cffex":
        # 中金所股指期权（新浪源）- 旧格式：每行一个合约
        for _, row in df.iterrows():
            try:
                code = str(row.get('option_code', row.get('代码', row.get('symbol', ''))))
                strike = float(row.get('行权价', row.get('exercise_price', 0)) or 0)
                expiry = str(row.get('到期日', row.get('expiry', '')))
                opt_type_str = str(row.get('类型', row.get('opt_type', ''))).strip().upper()
                if 'C' in opt_type_str or '看涨' in opt_type_str:
                    opt_type = 'C'
                elif 'P' in opt_type_str or '看跌' in opt_type_str:
                    opt_type = 'P'
                else:
                    # 从合约代码推断：MO2408C5500
                    s = code.upper()
                    if 'C' in s and 'P' not in s:
                        opt_type = 'C'
                    elif 'P' in s and 'C' not in s:
                        opt_type = 'P'
                    else:
                        import re
                        m = re.search(r'[CP]\d', s)
                        if m:
                            opt_type = m.group()[0]
                        else:
                            continue

                close = float(row.get('收盘价', row.get('最新价', row.get('close', 0))) or 0)
                volume = float(row.get('成交量', row.get('volume', 0)) or 0)
                oi = float(row.get('持仓量', row.get('open_interest', 0)) or 0)
                iv_val = float(row.get('隐含波动率', row.get('IV', row.get('iv', 0))) or 0)
                if iv_val > 1:
                    iv_val = iv_val / 100.0

                dte = 0
                expiry_str = ""
                # 从合约代码解析到期月
                import re
                m = re.search(r'(\d{4})', code.upper())
                if m:
                    year_month = m.group(1)
                    # 2608 -> 2026-08
                    try:
                        exp_month = pd.Timestamp(f"20{year_month[:2]}-{year_month[2:4]}-01")
                        # 股指期权到期日为到期月份的第三个星期五，简化处理
                        from calendar import monthrange
                        _, last_day = monthrange(exp_month.year, exp_month.month)
                        # 找第三个周五
                        fridays = [d for d in range(1, last_day+1)
                                   if pd.Timestamp(exp_month.year, exp_month.month, d).weekday() == 4]
                        if len(fridays) >= 3:
                            expiry_dt = pd.Timestamp(exp_month.year, exp_month.month, fridays[2])
                            expiry_str = expiry_dt.strftime('%Y%m%d')
                            dte = max((expiry_dt - trade_dt).days, 0)
                    except Exception:
                        pass

                contracts.append(OptionContract(
                    code=code,
                    underlying="",
                    strike=strike,
                    expiry=expiry_str,
                    opt_type=opt_type,
                    close=close,
                    volume=volume,
                    open_interest=oi,
                    iv=iv_val,
                    dte=dte,
                ))
            except Exception:
                continue

    elif source == "akshare_cffex_wide":
        # 中金所期权宽表格式（看涨+看跌在同一行，按行权价对齐）
        # 列: 看涨合约-最新价, 看跌合约-最新价, 行权价, 看涨合约-持仓量, 看跌合约-持仓量, ...
        import re

        # 先从数据中获取到期月（从任意合约标识解析）
        expiry_str = ""
        for col in ['看涨合约-标识', '看跌合约-标识']:
            if col in df.columns:
                sample = df[col].dropna()
                if len(sample) > 0:
                    code = str(sample.iloc[0]).upper()
                    m = re.search(r'(\d{4})', code)
                    if m:
                        ym = m.group(1)
                        try:
                            from calendar import monthrange
                            exp_month = pd.Timestamp(f"20{ym[:2]}-{ym[2:4]}-01")
                            _, last_day = monthrange(exp_month.year, exp_month.month)
                            fridays = [d for d in range(1, last_day+1)
                                       if pd.Timestamp(exp_month.year, exp_month.month, d).weekday() == 4]
                            if len(fridays) >= 3:
                                expiry_dt = pd.Timestamp(exp_month.year, exp_month.month, fridays[2])
                                expiry_str = expiry_dt.strftime('%Y%m%d')
                                dte = max((expiry_dt - trade_dt).days, 0)
                            else:
                                dte = 30  # 默认约30天
                        except Exception:
                            dte = 30
                    break
        else:
            dte = 30

        for _, row in df.iterrows():
            try:
                strike = float(row.get('行权价', 0) or 0)
                if strike <= 0:
                    continue

                # 看涨
                call_code = str(row.get('看涨合约-标识', f'C{int(strike)}'))
                call_close = float(row.get('看涨合约-最新价', 0) or 0)
                call_oi = float(row.get('看涨合约-持仓量', 0) or 0)
                call_volume = float(row.get('看涨合约-买量', 0) or 0) + float(row.get('看涨合约-卖量', 0) or 0)

                if call_close > 0:
                    contracts.append(OptionContract(
                        code=call_code,
                        underlying="",
                        strike=strike,
                        expiry=expiry_str,
                        opt_type='C',
                        close=call_close,
                        volume=call_volume,
                        open_interest=call_oi,
                        iv=0.0,
                        dte=dte,
                    ))

                # 看跌
                put_code = str(row.get('看跌合约-标识', f'P{int(strike)}'))
                put_close = float(row.get('看跌合约-最新价', 0) or 0)
                put_oi = float(row.get('看跌合约-持仓量', 0) or 0)
                put_volume = float(row.get('看跌合约-买量', 0) or 0) + float(row.get('看跌合约-卖量', 0) or 0)

                if put_close > 0:
                    contracts.append(OptionContract(
                        code=put_code,
                        underlying="",
                        strike=strike,
                        expiry=expiry_str,
                        opt_type='P',
                        close=put_close,
                        volume=put_volume,
                        open_interest=put_oi,
                        iv=0.0,
                        dte=dte,
                    ))
            except Exception:
                continue

    elif source == "akshare_finance_board":
        # 期权金融看板格式（东财）
        for _, row in df.iterrows():
            try:
                code = str(row.get('期权代码', row.get('代码', '')))
                strike = float(row.get('行权价', 0) or 0)
                expiry = str(row.get('到期日', ''))
                opt_type_str = str(row.get('类型', '')).strip()
                if '认购' in opt_type_str or '看涨' in opt_type_str:
                    opt_type = 'C'
                elif '认沽' in opt_type_str or '看跌' in opt_type_str:
                    opt_type = 'P'
                else:
                    continue

                close = float(row.get('最新价', row.get('收盘价', 0)) or 0)
                volume = float(row.get('成交量', 0) or 0)
                oi = float(row.get('持仓量', 0) or 0)
                iv_val = float(row.get('隐含波动率', 0) or 0)
                if iv_val > 1:
                    iv_val = iv_val / 100.0

                dte = 0
                if expiry and expiry != 'nan':
                    try:
                        exp_dt = pd.Timestamp(expiry)
                        dte = max((exp_dt - trade_dt).days, 0)
                    except Exception:
                        pass

                contracts.append(OptionContract(
                    code=code,
                    underlying="",
                    strike=strike,
                    expiry=expiry,
                    opt_type=opt_type,
                    close=close,
                    volume=volume,
                    open_interest=oi,
                    iv=iv_val,
                    dte=dte,
                ))
            except Exception:
                continue

    return contracts


# ============================================================
# 指标计算函数
# ============================================================

def calc_iv_for_chain(
    contracts: List[OptionContract],
    spot_price: float,
    r: float = 0.025,
) -> List[OptionContract]:
    """
    为期权链中没有IV的合约计算隐含波动率。

    Args:
        contracts: 期权合约列表
        spot_price: 标的现货价格
        r: 无风险利率

    Returns:
        更新了iv字段的合约列表
    """
    for c in contracts:
        if c.iv <= 0 and c.close > 0 and c.dte > 0 and c.strike > 0:
            T = c.dte / 365.0
            c.iv = implied_vol(
                option_price=c.close,
                S=spot_price,
                K=c.strike,
                T=T,
                r=r,
                is_call=(c.opt_type == 'C'),
            )
    return contracts


def get_atm_iv(
    contracts: List[OptionContract],
    spot_price: float,
    expiry: Optional[str] = None,
) -> float:
    """
    计算平值（ATM）隐含波动率。
    取行权价最接近现货价的看涨和看跌IV的均值。

    Args:
        contracts: 期权合约列表
        spot_price: 标的现货价格
        expiry: 指定到期月份（YYYYMM），None取最近到期

    Returns:
        ATM IV（小数，如0.2 = 20%）
    """
    # 筛选到期月
    if expiry:
        filtered = [c for c in contracts if c.expiry.startswith(expiry) and c.iv > 0]
    else:
        # 取最近到期且有IV的
        expiries = sorted(set(c.expiry for c in contracts if c.iv > 0 and c.dte > 3))
        if not expiries:
            return 0.0
        filtered = [c for c in contracts if c.expiry == expiries[0] and c.iv > 0]

    if not filtered:
        return 0.0

    calls = [c for c in filtered if c.opt_type == 'C']
    puts = [c for c in filtered if c.opt_type == 'P']

    if not calls and not puts:
        return 0.0

    # 找最接近ATM的call和put
    def find_closest(items, target):
        if not items:
            return None
        return min(items, key=lambda c: abs(c.strike - target))

    atm_call = find_closest(calls, spot_price)
    atm_put = find_closest(puts, spot_price)

    ivs = []
    if atm_call:
        ivs.append(atm_call.iv)
    if atm_put:
        ivs.append(atm_put.iv)

    return sum(ivs) / len(ivs) if ivs else 0.0


def calc_iv_skew(
    contracts: List[OptionContract],
    spot_price: float,
    expiry: Optional[str] = None,
    delta_level: float = 0.25,
) -> float:
    """
    ⚠️【已弃用 / DEPRECATED — 2026-11-09 偏度口径统一】
    简化版偏度，**禁止任何新业务代码使用**。请改用 `calc_25d_risk_reversal()`。

    为什么弃用：
      本函数把「行权价距现货 ±25%」当作 25Δ，实际是固定行权价距离，不是
      delta 口径。不同标的/不同波动率环境下，同一行权价距离对应的真实 delta
      差异极大（低波时 ±25% 行权价可能只有 0.10Δ，高波时可能 0.40Δ），
      导致跨品种不可比。

    保留原因：`data_hub/__init__.py` 历史上已导出本函数，可能有外部引用，
    为不破坏兼容性保留函数体不变，仅加此标注。

    ⚠️ 注意：自 2026-11-09 起，`compute_metrics_from_chain()` 内的
    `OptionMetrics.iv_skew` 已改用 `calc_25d_risk_reversal()['rr']`，
    不再调用本函数。本函数当前无任何生产调用点。

    --- 原始说明 ---
    计算IV偏度（25delta看跌IV - 25delta看涨IV）。
    简化版：用行权价距离现货±25%的深度期权IV替代。

    Args:
        contracts: 期权合约列表
        spot_price: 标的现货价格
        expiry: 指定到期月
        delta_level: delta绝对值（用于找深度合约）

    Returns:
        IV Skew（小数，如0.05 = 5%）
    """
    if expiry:
        filtered = [c for c in contracts if c.expiry.startswith(expiry) and c.iv > 0]
    else:
        expiries = sorted(set(c.expiry for c in contracts if c.iv > 0 and c.dte > 3))
        if not expiries:
            return 0.0
        filtered = [c for c in contracts if c.expiry == expiries[0] and c.iv > 0]

    if not filtered:
        return 0.0

    calls = [c for c in filtered if c.opt_type == 'C' and c.strike > spot_price]
    puts = [c for c in filtered if c.opt_type == 'P' and c.strike < spot_price]

    if not calls or not puts:
        return 0.0

    # 找 OTM 25% 附近的行权价
    otm_put_strike = spot_price * (1 - delta_level)
    otm_call_strike = spot_price * (1 + delta_level)

    closest_put = min(puts, key=lambda c: abs(c.strike - otm_put_strike))
    closest_call = min(calls, key=lambda c: abs(c.strike - otm_call_strike))

    return closest_put.iv - closest_call.iv


def calc_cboe_skew(
    contracts: List[OptionContract],
    spot_price: float,
    expiry: Optional[str] = None,
) -> float:
    """
    ⚠️【库落库专用 / 非决策口径 — 2026-11-09 偏度口径统一】

    本函数**不是标准 CBOE SKEW 指数**，是本系统自造的「虚值20%看跌IV / ATM IV × 100」
    比率口径。经济含义上它恒 >100（股票期权 OTM put IV 系统性高于 ATM IV），
    不具备以 100 为中轴的可解释性。

    允许用途（唯一）：
      - `option_intraday._compute_one()` / `option_metrics_history._compute_one()`
        通过 `compute_metrics_from_data()` → `OptionMetrics.cboe_skew` 落库到
        `option_daily.cboe_skew` / `daily_metrics.cboe_skew`。
      - 该落库口径已冻结，禁止改动（历史分位/序列连续性依赖它）。

    ⛔ 禁止用途：
      - **禁止**用本函数结果做方向判断（`classify_skew_direction` 等）。方向判断
        请用 `calc_cboe_skew_directional()`（以 100 为中轴、与阈值 120/105/95/80 匹配）。
      - 禁止在新增业务中调用。

    ⚠️ 已知缺陷（2026-11-09 实测，仅记录不改）：
      本口径对中证1000/沪深300 等标的系统性偏高，被 `compute_metrics_from_chain`
      的 `max(80.0, min(x, 150.0))` 钳位后大量堆在 150：
        - option_daily(000852)：435 个交易日中 286 天(65.7%) 钳在 150，
          median=150.00、mean=139.53、min=100.00
        - daily_metrics(510300)：445 天中 345 天(77.5%) 钳在 150
      详见《偏度口径统一说明.md》。

    --- 原始说明 ---
    计算类CBOE SKEW指数（尾部风险）。
    简化版：OTM看跌IV / ATM IV * 100

    Returns:
        SKEW指数（>100表示看跌尾部溢价高，风险厌恶）
    """
    atm = get_atm_iv(contracts, spot_price, expiry)
    if atm <= 0:
        return 100.0

    if expiry:
        puts = [c for c in contracts if c.opt_type == 'P' and c.expiry.startswith(expiry) and c.iv > 0 and c.strike < spot_price]
    else:
        expiries = sorted(set(c.expiry for c in contracts if c.iv > 0 and c.dte > 3))
        if not expiries:
            return 100.0
        puts = [c for c in contracts if c.opt_type == 'P' and c.expiry == expiries[0] and c.iv > 0 and c.strike < spot_price]

    if not puts:
        return 100.0

    # 取虚值20%看跌IV
    target = spot_price * 0.8
    otm_put = min(puts, key=lambda c: abs(c.strike - target))

    return (otm_put.iv / atm) * 100.0


# ============================================================
# 决策层 CBOE SKEW（全系统唯一方向判断口径）
# ============================================================

# 本口径的中轴与历史校准阈值（与 derivative_sentiment_monitor.classify_skew_direction
# 的 120/105/95/80 配套）。禁止单独修改阈值。
CBOE_SKEW_DIRECTIONAL_BASE = 100.0
CBOE_SKEW_DIRECTIONAL_COEF = 30.0


def calc_cboe_skew_directional(
    atm_iv: float,
    put25_iv: Optional[float],
) -> float:
    """
    决策层 CBOE SKEW（全系统唯一方向判断口径，以 100 为中轴）。

    公式（2026-11-09 统一，源自原 `option_monitor.calc_skew`）：
        cboe_skew = 100 - 10 * (-3 * (put25_iv - atm_iv) / atm_iv)
                  = 100 + 30 * (put25_iv - atm_iv) / atm_iv

    经济含义：25Δ 看跌 IV 相对 ATM IV 的溢价，按 30 倍放大后叠加到 100 中枢。
      - put25 溢价 0    → 100（中性）
      - put25 溢价 +1%  → 100.3
      - put25 溢价 +10% → 103.0
      - put25 溢价 -10% → 97.0

    ⚠️ 本函数**不是标准 CBOE SKEW 指数**，是本系统自造口径。之所以采用它而不用
    `calc_cboe_skew()`（OTM20%put/ATM 比率口径），是因为：
      1. 本口径以 100 为中轴，与 `classify_skew_direction` 的 120/105/95/80 阈值
         设计一致（该阈值沿用历史，已按本口径校准，禁止改动）；
      2. `calc_cboe_skew()` 口径恒 >100 且大量被钳在 150，无法区分强弱。
    详见《偏度口径统一说明.md》。

    Args:
        atm_iv: ATM 隐含波动率（小数，如 0.21）
        put25_iv: 25Δ 看跌隐含波动率（小数）。None 或非正时回退到 atm_iv（结果=100）

    Returns:
        cboe_skew（数值，中轴 100）
    """
    try:
        atm = float(atm_iv) if atm_iv is not None else 0.0
        p25 = float(put25_iv) if put25_iv is not None else atm
    except (TypeError, ValueError):
        return CBOE_SKEW_DIRECTIONAL_BASE
    if atm <= 0.01:
        return CBOE_SKEW_DIRECTIONAL_BASE
    return CBOE_SKEW_DIRECTIONAL_BASE + \
        CBOE_SKEW_DIRECTIONAL_COEF * (p25 - atm) / atm


# ============================================================
# 专业版 25-delta Risk Reversal 偏度（唯一推荐口径，非简写）
# 口径：BS反推真IV → BS delta 定位 25Δ 购/沽行权价 →
#       取该点真IV，RR = IV(Put25Δ) - IV(Call25Δ)
#       归一化 skew = RR / ATM_IV，便于跨品种/跨时间比较
# ============================================================

def bs_delta(S: float, K: float, T: float, r: float, sigma: float, is_call: bool = True) -> float:
    """BS delta（无股息）。"""
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) if is_call else (_norm_cdf(d1) - 1.0)


def delta_to_strike(S: float, T: float, r: float, sigma: float, target_delta: float,
                    is_put: bool = True) -> float:
    """
    给定目标 delta（绝对值，如0.25），反解对应行权价。
    Call: N(d1)=target_delta ; Put: N(d1)-1=-target_delta。
    """
    if sigma <= 0 or T <= 0:
        return S * (1 - target_delta) if is_put else S * (1 + target_delta)
    # put: N(d1)=1-|Δ|；call: N(d1)=Δ
    if is_put:
        d1_target = _inverse_norm_cdf(1.0 - target_delta)
    else:
        d1_target = _inverse_norm_cdf(target_delta)
    # d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)  => 解 K
    return S * math.exp(-d1_target * sigma * math.sqrt(T) + (r + 0.5 * sigma * sigma) * T)


def _inverse_norm_cdf(p: float) -> float:
    """标准正态分位函数（Acklam 算法近似）。"""
    if p <= 0:
        return -8.0
    if p >= 1:
        return 8.0
    # Acklam's algorithm coefficients
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
           ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def calc_25d_risk_reversal(
    contracts: List[OptionContract],
    spot_price: float,
    trade_date: Optional[str] = None,
    r: float = 0.03,
    min_dte: int = 3,
    delta_level: float = 0.25,
    normalize: bool = True,
    preferred_min_dte: int = 10,
) -> Optional[Dict]:
    """
    专业版 25-delta Risk Reversal 偏度（全系统唯一推荐 Skew 口径）。

    步骤：
      1. 过滤临到期合约（避免近到期深虚值 IV 反推系统性失真）；
      2. 优先在 dte>=preferred_min_dte 的到期月中取最近月（主力次月），
         全部到期月都短于该阈值时才退到最近月，并逐级放宽 dte；
      3. BS 反推 ATM 购/沽真 IV；
      4. 用 BS delta 反解 25Δ Put / 25Δ Call 行权价，取最近合约再反推真 IV；
      5. RR = IV(Put25Δ) - IV(Call25Δ)；
         skew = RR / ATM_IV（normalize=True，跨品种可比；False 返回原始差值）。

    Args:
        contracts: 标准化合约列表（需 close/strike/expiry/opt_type/dte 有效）
        spot_price: 标的现货价
        trade_date: 交易日 YYYYMMDD，缺省取当天
        r: 无风险利率
        min_dte: 兜底最短剩余天数（仅当所有到期月都很近时生效）
        delta_level: 目标 delta 绝对值（默认0.25）
        normalize: True 返回 ATM 归一化偏度
        preferred_min_dte: 优先选择的到期月最短剩余天数（默认10，避开临到期）

    Returns:
        dict: {skew, rr, atm_iv, put25_iv, call25_iv, dte, expiry, contract_count}
        数据不足返回 None
    """
    if not contracts or spot_price <= 0:
        return None

    td = pd.Timestamp(trade_date) if trade_date else pd.Timestamp.now()

    def _pick_expiry(pool, floor):
        """在 dte>=floor 的合约里选最近到期月（要求该月购/沽各≥3）。"""
        cand = [c for c in pool if c.dte >= floor and c.expiry]
        for exp in sorted(set(c.expiry for c in cand)):
            m = [c for c in cand if c.expiry == exp]
            if sum(1 for c in m if c.opt_type == 'C') >= 3 and \
               sum(1 for c in m if c.opt_type == 'P') >= 3:
                return exp
        return None

    # 1. 选月：优先主力（dte>=preferred_min_dte），否则逐级放宽
    nearest_exp = _pick_expiry(contracts, preferred_min_dte)
    if nearest_exp is None:
        nearest_exp = _pick_expiry(contracts, min_dte)
    if nearest_exp is None:
        nearest_exp = _pick_expiry(contracts, max(min_dte - 1, 2))
    if nearest_exp is None:
        nearest_exp = _pick_expiry(contracts, 1)
    if nearest_exp is None:
        return None

    # 2. 选中到期月的全部合约
    chain = [c for c in contracts if c.expiry == nearest_exp and c.dte >= 1]
    calls = sorted([c for c in chain if c.opt_type == 'C'], key=lambda x: x.strike)
    puts = sorted([c for c in chain if c.opt_type == 'P'], key=lambda x: x.strike)
    if len(calls) < 3 or len(puts) < 3:
        return None

    dte = max((pd.Timestamp(nearest_exp) - td).days, 1)
    T = dte / 365.0

    # ATM 行权价（最接近现货）
    atm_strike = min(chain, key=lambda c: abs(c.strike - spot_price)).strike
    atm_call = min(calls, key=lambda c: abs(c.strike - atm_strike))
    atm_put = min(puts, key=lambda c: abs(c.strike - atm_strike))

    atm_call_iv = implied_vol(atm_call.close, spot_price, atm_call.strike, T, r, True)
    atm_put_iv = implied_vol(atm_put.close, spot_price, atm_put.strike, T, r, False)
    parts = [v for v in (atm_call_iv, atm_put_iv) if v and v > 0.01]
    atm_iv = sum(parts) / len(parts) if parts else 0.0
    if atm_iv < 0.01:
        return None

    # 3. 25Δ 行权价 → 最近合约 → 真 IV
    put_k = delta_to_strike(spot_price, T, r, atm_iv, delta_level, is_put=True)
    call_k = delta_to_strike(spot_price, T, r, atm_iv, delta_level, is_put=False)
    near_put = min(puts, key=lambda c: abs(c.strike - put_k))
    near_call = min(calls, key=lambda c: abs(c.strike - call_k))
    put25_iv = implied_vol(near_put.close, spot_price, near_put.strike, T, r, False)
    call25_iv = implied_vol(near_call.close, spot_price, near_call.strike, T, r, True)
    if not put25_iv or not call25_iv:
        return None

    rr = put25_iv - call25_iv
    skew = rr / atm_iv if normalize else rr

    return {
        "skew": round(skew, 6),
        "rr": round(rr, 6),
        "atm_iv": round(atm_iv, 6),
        "put25_iv": round(put25_iv, 6),
        "call25_iv": round(call25_iv, 6),
        "dte": dte,
        "expiry": nearest_exp,
        "contract_count": len(chain),
    }


def calc_pcr(
    contracts: List[OptionContract],
    metric: str = "volume",
) -> float:
    """
    计算PCR（Put/Call Ratio）。

    Args:
        contracts: 期权合约列表
        metric: 'volume' 成交量比, 'oi' 持仓量比, 'amount' 成交额比

    Returns:
        PCR值，失败返回1.0
    """
    calls = [c for c in contracts if c.opt_type == 'C']
    puts = [c for c in contracts if c.opt_type == 'P']

    if metric == "volume":
        call_total = sum(c.volume for c in calls)
        put_total = sum(c.volume for c in puts)
    elif metric == "oi":
        call_total = sum(c.open_interest for c in calls)
        put_total = sum(c.open_interest for c in puts)
    elif metric == "amount":
        call_total = sum(c.amount for c in calls)
        put_total = sum(c.amount for c in puts)
    else:
        call_total = sum(c.volume for c in calls)
        put_total = sum(c.volume for c in puts)

    if call_total <= 0:
        return 0.0 if put_total <= 0 else float('inf')

    return put_total / call_total


def calc_pcr_from_df(df, call_put_col='call_put', vol_col='vol', oi_col='oi', 
                     nearest_month_only=True, t_col='T'):
    """
    从DataFrame计算PCR（Put/Call Ratio）- 统一口径。
    
    全系统PCR计算统一使用此函数，确保口径一致。
    
    Args:
        df: DataFrame with columns [call_put, vol, oi, T(可选)]
        call_put_col: 认购/认沽列名
        vol_col: 成交量列名
        oi_col: 持仓量列名
        nearest_month_only: 是否只用最近月合约
        t_col: 到期时间列名（年）
    
    Returns:
        dict: {'vol_pcr': float, 'oi_pcr': float} 或 None
    """
    import pandas as pd
    
    if df is None or (hasattr(df, 'empty') and df.empty):
        return None
    
    work_df = df.copy()
    
    # 确保call_put列为C/P
    if call_put_col in work_df.columns:
        import re
        work_df['_cp'] = work_df[call_put_col].astype(str).apply(
            lambda x: 'C' if re.search(r'C|购|call', x, re.IGNORECASE) 
            else ('P' if re.search(r'P|沽|put', x, re.IGNORECASE) else None)
        )
        work_df = work_df.dropna(subset=['_cp'])
    else:
        return None
    
    # 过滤最近月合约
    if nearest_month_only and t_col in work_df.columns:
        min_dte = 3 / 365.0
        valid = work_df[work_df[t_col] >= min_dte]
        if len(valid) < 4:
            valid = work_df[work_df[t_col] >= 2/365.0]
        if len(valid) >= 4:
            work_df = valid.copy()
            work_df['_month_T'] = (work_df[t_col] * 12).round() / 12
            nearest_T = work_df['_month_T'].min()
            work_df = work_df[work_df['_month_T'] == nearest_T]
    
    calls = work_df[work_df['_cp'] == 'C']
    puts = work_df[work_df['_cp'] == 'P']
    
    result = {}
    
    # 成交量PCR
    if vol_col in work_df.columns:
        call_vol = calls[vol_col].sum()
        put_vol = puts[vol_col].sum()
        if call_vol > 0:
            result['vol_pcr'] = round(put_vol / call_vol, 4)
    
    # 持仓量PCR
    if oi_col in work_df.columns:
        call_oi = calls[oi_col].sum()
        put_oi = puts[oi_col].sum()
        if call_oi > 0:
            result['oi_pcr'] = round(put_oi / call_oi, 4)
    
    return result if result else None


def calc_term_structure(
    contracts: List[OptionContract],
    spot_price: float,
) -> Dict[str, float]:
    """
    计算期限结构（各到期月的ATM IV）。

    Returns:
        dict: {expiry: atm_iv}，按到期时间排序
    """
    expiries = sorted(set(
        c.expiry for c in contracts
        if c.iv > 0 and c.dte > 1 and c.expiry
    ))

    result = {}
    for exp in expiries:
        iv = get_atm_iv(contracts, spot_price, expiry=exp[:6])
        if iv > 0:
            result[exp] = iv

    return result


def _vix_parse_date(s: str) -> datetime:
    """解析 YYYYMMDD / YYYY-MM-DD 为 naive datetime。"""
    s = str(s).strip().replace('-', '').replace('/', '')
    return datetime.strptime(s[:8], '%Y%m%d')


# --- CBOE VIX 分钟到期法常量（2019 Whitepaper） ---
# 国内权益期权全部在到期日 15:00 到期/交割（CFFEX 股指期权最后交易日15:00
# 收盘交割，ETF 期权到期日 15:00 行权交割）。分钟 T 的两个时点：
#   估值时刻：历史日线=当日 08:30（盘前锚点，保证逐日口径连续）；
#             盘中实时=真实 now。
#   结算时点：到期日 15:00。到期日当天盘中 T=0~390 分钟仍属有效近月，
#             15:00 后 M_T=0 自动顺延后续两个到期日。
_VIX_SETTLE_HOUR = 15
_VIX_SETTLE_MIN = 0
_VIX_HIST_VAL_HOUR = 8
_VIX_HIST_VAL_MIN = 30
_VIX_MINUTES_PER_YEAR = 525600
_VIX_N30 = 30 * 1440          # 43200
_VIX_N365 = 365 * 1440        # 525600


def _vix_valuation_dt(
    trade_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> datetime:
    """VIX 估值时刻。显式 now 优先；历史日线按当日 08:30；都不传取当前时刻。"""
    if now is not None:
        return now
    if trade_date is not None:
        d = _vix_parse_date(trade_date)
        return datetime(d.year, d.month, d.day, _VIX_HIST_VAL_HOUR, _VIX_HIST_VAL_MIN)
    return datetime.now()


def _expiry_settlement_dt(expiry: str) -> datetime:
    """到期日 15:00 结算时点（A 股权益期权统一到期时刻）。"""
    d = _vix_parse_date(expiry)
    return datetime(d.year, d.month, d.day, _VIX_SETTLE_HOUR, _VIX_SETTLE_MIN)


def _opt_price(c: "OptionContract") -> float:
    """期权报价：实时链优先 (bid+ask)/2（CBOE 口径），无盘口退回最新成交价。"""
    bid, ask = getattr(c, 'bid', 0.0) or 0.0, getattr(c, 'ask', 0.0) or 0.0
    if bid > 0 and ask > 0 and ask >= bid:
        return (bid + ask) / 2.0
    return c.close


def calc_vix_single_term(
    contracts: List["OptionContract"],
    expiry: str,
    val_dt: datetime,
    r: float = 0.015,
) -> Optional[Dict[str, Any]]:
    """单个到期月的方差互换 σ²（CBOE 2019 Whitepaper 公式 1-5）。

    T 以分钟计：T = M_T / 525600，M_T = 到期日15:00 - 估值时刻 的分钟数。
    到期日 15:00 后 M_T<=0 → 返回 None（调用方顺延下一到期日）。
    """
    settle = _expiry_settlement_dt(expiry)
    m_seconds = (settle - val_dt).total_seconds()
    if m_seconds <= 0:
        return None
    minutes_to_expiry = m_seconds / 60.0
    T = minutes_to_expiry / _VIX_MINUTES_PER_YEAR

    put_map: Dict[float, "OptionContract"] = {}
    call_map: Dict[float, "OptionContract"] = {}
    for c in contracts:
        if c.expiry != expiry or c.strike <= 0:
            continue
        p = _opt_price(c)
        if p <= 0:
            continue
        if c.opt_type == 'P':
            put_map[c.strike] = c
        elif c.opt_type == 'C':
            call_map[c.strike] = c

    common = sorted(set(put_map) & set(call_map))
    if len(common) < 4:
        return None

    # 公式1：F = K + e^(rT)*(call-put)，取价差绝对值最小的 K
    fk = min(common, key=lambda k: abs(_opt_price(call_map[k]) - _opt_price(put_map[k])))
    diff = _opt_price(call_map[fk]) - _opt_price(put_map[fk])
    F = fk + math.exp(r * T) * diff
    # K0 = 紧邻 F 下方的行权价
    below = [k for k in common if k <= F]
    K0 = max(below) if below else min(common, key=lambda k: abs(k - F))

    # 公式2：逐行权价选报价（K0 用 call/put 均值，上方取 call，下方取 put）
    q: Dict[float, float] = {}
    for k in common:
        if k == K0:
            q[k] = (_opt_price(call_map[k]) + _opt_price(put_map[k])) / 2.0
        elif k > K0:
            q[k] = _opt_price(call_map[k])
        else:
            q[k] = _opt_price(put_map[k])

    # CBOE 规则：两端遇 0 报价即截断
    strikes_sorted = sorted(q)
    lo = 0
    while lo < len(strikes_sorted) and q[strikes_sorted[lo]] <= 0:
        lo += 1
    hi = len(strikes_sorted) - 1
    while hi >= 0 and q[strikes_sorted[hi]] <= 0:
        hi -= 1
    if hi <= lo:
        return None
    used = strikes_sorted[lo:hi + 1]
    if len(used) < 4:
        return None

    # 公式3：ΔK 积分（边缘行权价 ΔK = 到相邻一个行权价的距离）
    contrib = 0.0
    for i, k in enumerate(used):
        if i == 0:
            dk = used[1] - used[0]
        elif i == len(used) - 1:
            dk = used[-1] - used[-2]
        else:
            dk = (used[i + 1] - used[i - 1]) / 2.0
        price = q[k]
        if price > 0:
            contrib += dk / (k * k) * price

    # 公式4-5
    variance = 2.0 / T * contrib * math.exp(r * T) - (F / K0 - 1.0) ** 2 / T
    if not np.isfinite(variance) or variance <= 0:
        return None
    return {
        'expiry': expiry,
        'minutes': minutes_to_expiry,
        'T': T,
        'F': F,
        'K0': K0,
        'variance': variance,
        'n_strikes': len(used),
        'term_vix': 100.0 * math.sqrt(variance),
    }


def calc_vix(
    contracts: List[OptionContract],
    spot_price: float,
    r: float = 0.015,
    trade_date: Optional[str] = None,
    now: Optional[datetime] = None,
    return_detail: bool = False,
) -> Any:
    """标准 CBOE VIX（30日历日，分钟到期时间，方差互换法）。

    2026-10-23 修复（旧版按日历日 dte 筛 7~20/21~40 天，到期周近月被
    跳空错配，曾输出 500~1300 的荒谬值）：
    1. T 按分钟：M_T=(到期日15:00-估值时刻)分钟，T=M_T/525600，近月允许
       <7 天（到期日当天盘中 0~390 分钟仍有效），不再按日历日跳空。
    2. 到期日 15:00 后近月 M_T=0 → 自动顺延后续两个到期日，不报错。
    3. 权重按分钟：w=(N_T2-N30)/(N_T2-N_T1)；N_T1>=N30 时 w=1 只用近月。
    4. 远期 F、ΔK 边缘项严格按 CBOE 2019 Whitepaper 公式 1-5。
    5. 实时链有盘口时用 (bid+ask)/2，历史日线退回 close。

    Args:
        contracts: 全量期权合约（standardize_contracts 输出）
        spot_price: 标的现价（保留参数；远期由 put-call 平价推出）
        r: 无风险利率（默认1.5%）
        trade_date: 估值交易日 YYYYMMDD/YYYY-MM-DD；历史回补按当日 08:30
                    估值、到期日按 15:00 结算
        now: 显式估值时刻（盘中实时传 datetime.now()，优先级高于trade_date）
        return_detail: True 返回诊断 dict；False 只返回 VIX 浮点（向后兼容）

    Returns:
        float: VIX 百分数(如 18.6 = 18.6%)，无法计算为 0.0
        dict(return_detail=True): vix/t1_days/t2_days/w/n1/n2/f1/f2/k0_1/
            k0_2/val_time/spot/error/terms
    """
    detail: Dict[str, Any] = {
        'vix': 0.0, 't1_days': None, 't2_days': None, 'w': None,
        'n1': 0, 'n2': 0, 'f1': None, 'f2': None, 'k0_1': None, 'k0_2': None,
        'val_time': None, 'spot': spot_price, 'error': None, 'terms': [],
    }
    val_dt = _vix_valuation_dt(trade_date=trade_date, now=now)
    detail['val_time'] = val_dt.strftime('%Y-%m-%d %H:%M')

    all_expiries = sorted({c.expiry for c in contracts if c.expiry})
    terms: List[Dict[str, Any]] = []
    for exp in all_expiries:
        term = calc_vix_single_term(contracts, exp, val_dt, r=r)
        if term is not None:
            terms.append(term)

    if not terms:
        detail['error'] = 'no_unexpired_valid_term'
        return detail if return_detail else 0.0

    # 到期日15:00后近月 M_T=0 已在 single_term 返回 None 自动顺延
    t1, t2 = terms[0], (terms[1] if len(terms) >= 2 else None)
    detail['t1_days'] = round(t1['minutes'] / 1440.0, 3)
    detail['n1'] = t1['n_strikes']
    detail['f1'] = round(t1['F'], 4)
    detail['k0_1'] = t1['K0']
    detail['terms'] = [
        {'expiry': x['expiry'], 'days': round(x['minutes'] / 1440.0, 3),
         'term_vix': round(x['term_vix'], 2), 'n_strikes': x['n_strikes']}
        for x in terms[:4]
    ]

    n_t1 = t1['minutes']
    if n_t1 >= _VIX_N30 or t2 is None:
        vix = t1['term_vix']
        detail['w'] = 1.0
    else:
        n_t2 = t2['minutes']
        detail['t2_days'] = round(n_t2 / 1440.0, 3)
        detail['n2'] = t2['n_strikes']
        detail['f2'] = round(t2['F'], 4)
        detail['k0_2'] = t2['K0']
        w = (n_t2 - _VIX_N30) / (n_t2 - n_t1)
        w = min(max(w, 0.0), 1.0)
        detail['w'] = round(w, 4)
        # 30日方差 = (T1·w·σ1² + T2·(1-w)·σ2²) × N365/N30
        var30 = (t1['T'] * w * t1['variance']
                 + t2['T'] * (1.0 - w) * t2['variance']) * _VIX_N365 / _VIX_N30
        vix = 100.0 * math.sqrt(var30) if np.isfinite(var30) and var30 > 0 else 0.0
        if vix <= 0:
            detail['error'] = 'nonpositive_interpolated_variance'

    detail['vix'] = round(vix, 2)
    return detail if return_detail else round(vix, 2)

# ============================================================
# 综合期权指标计算
# ============================================================

@dataclass
class OptionMetrics:
    """期权综合指标。"""
    vix: float = 0.0              # VIX类指数（百分数，如20.5）
    atm_iv: float = 0.0           # 平值IV（小数）
    iv_skew: float = 0.0          # IV偏度（小数，看跌-看涨）
    cboe_skew: float = 100.0      # 类CBOE SKEW指数
    vol_pcr: float = 1.0          # 成交量PCR
    oi_pcr: float = 1.0           # 持仓量PCR
    term_structure: Dict[str, float] = None  # 期限结构 {expiry: iv}
    near_iv: float = 0.0          # 近月平值IV
    far_iv: float = 0.0           # 远月平值IV
    term_spread: float = 0.0      # 期限差（近-远）
    contract_count: int = 0       # 有效合约数
    underlying_price: float = 0.0  # 标的价格

    def __post_init__(self):
        if self.term_structure is None:
            self.term_structure = {}


def calc_all_option_metrics(
    contracts: List[OptionContract],
    spot_price: float,
    r: float = 0.025,
    trade_date: Optional[str] = None,
    vix_r: float = 0.015,
) -> OptionMetrics:
    """
    一键计算所有期权指标。

    Args:
        contracts: 标准化的期权合约列表
        spot_price: 标的现货价格
        r: 无风险利率

    Returns:
        OptionMetrics
    """
    if not contracts or spot_price <= 0:
        return OptionMetrics(underlying_price=spot_price)

    # 先给没有IV的合约计算IV
    contracts = calc_iv_for_chain(contracts, spot_price, r)

    # 过滤有效合约
    valid = [c for c in contracts if c.iv > 0 and c.strike > 0 and c.dte > 0]
    if not valid:
        return OptionMetrics(contract_count=0, underlying_price=spot_price)

    # 1. VIX（CBOE 分钟到期法；历史回补传 trade_date，盘中取 now）
    vix = calc_vix(contracts, spot_price, r=vix_r, trade_date=trade_date)

    # 2. ATM IV
    atm = get_atm_iv(contracts, spot_price)

    # 3. IV Skew —— 统一口径：25Δ Risk Reversal 原始差值（小数，可负）
    #    2026-11-09 偏度口径统一：原为 calc_iv_skew（行权价±25% 简化版，非真25Δ），
    #    现改为 calc_25d_risk_reversal()['rr']，与 option_daily / daily_metrics
    #    落库口径（put25_iv − call25_iv）完全一致。
    #    注意：calc_25d_risk_reversal 数据不足时返回 None，此时回退到简化版，
    #    保证 OptionMetrics 结构稳定（下游无业务代码读取 .iv_skew，已全仓 grep 确认）。
    iv_skew = 0.0
    try:
        _rr = calc_25d_risk_reversal(contracts, spot_price, trade_date=trade_date, r=r)
        if _rr and _rr.get("rr") is not None:
            iv_skew = float(_rr["rr"])
        else:
            iv_skew = calc_iv_skew(contracts, spot_price)
    except Exception:  # noqa: BLE001
        iv_skew = calc_iv_skew(contracts, spot_price)

    # 4. CBOE SKEW
    cboe_skew = calc_cboe_skew(contracts, spot_price)
    # 限制合理范围（正常80-120），避免异常值
    cboe_skew = max(80.0, min(cboe_skew, 150.0))

    # 5. PCR
    vol_pcr = calc_pcr(contracts, "volume")
    oi_pcr = calc_pcr(contracts, "oi")

    # 6. 期限结构
    ts = calc_term_structure(contracts, spot_price)

    # 近月/远月
    near_iv = 0.0
    far_iv = 0.0
    term_spread = 0.0
    if ts:
        sorted_exp = sorted(ts.keys())
        if len(sorted_exp) >= 1:
            near_iv = ts[sorted_exp[0]]
        if len(sorted_exp) >= 2:
            far_iv = ts[sorted_exp[1]]
        else:
            far_iv = near_iv
        term_spread = near_iv - far_iv

    return OptionMetrics(
        vix=vix,
        atm_iv=atm,
        iv_skew=iv_skew,
        cboe_skew=cboe_skew,
        vol_pcr=vol_pcr,
        oi_pcr=oi_pcr,
        term_structure=ts,
        near_iv=near_iv,
        far_iv=far_iv,
        term_spread=term_spread,
        contract_count=len(valid),
        underlying_price=spot_price,
    )


# ============================================================
# 便捷函数：从原始数据直接计算指标
# ============================================================

def compute_metrics_from_data(
    contracts_df: pd.DataFrame,
    spot_price: float,
    source: str = "tushare",
    exchange: str = "",
    trade_date: str = "",
    r: float = 0.025,
) -> OptionMetrics:
    """
    从原始DataFrame直接计算期权指标。

    Args:
        contracts_df: 原始合约数据
        spot_price: 标的现货价格
        source: 数据源类型
        exchange: 交易所
        trade_date: 交易日期
        r: 无风险利率

    Returns:
        OptionMetrics
    """
    contracts = standardize_contracts(contracts_df, source, exchange, trade_date)
    return calc_all_option_metrics(contracts, spot_price, r)
