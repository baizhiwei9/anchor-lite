"""
期权IV历史序列采集器
=======================
采集指定标的的ATM IV历史时间序列，支持四级降级。

新接口对外暴露：
    get_option_iv_history(underlying_code, start_date, end_date) -> pd.Series
    get_iv_ma_data(underlying_code, trade_date) -> dict

返回的IV均为百分比数值（如 25.3 表示 25.3%），index 为交易日期字符串(YYYYMMDD)。

四级降级：
Tier 1: [已废弃 2026-10-23] 原 AKShare QVIX 现成值链路已移除
Tier 2: Tushare 期权日线 + BS公式反推ATM IV
Tier 3: Coze数据网关（技能CLI兜底）
Tier 4: 网页爬取 + 指数已实现波动率(RV)代替
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource
from .option_data import (
    OPTION_UNDERLYINGS,
    OPTION_SYMBOL_ALIASES,
)



# 标的 → 对应指数代码（用于RV降级时取指数行情）
IV_INDEX_MAP = {
    "510300": "000300.SH",    # 300ETF → 沪深300
    "510500": "000905.SH",    # 500ETF → 中证500
    "588000": "000688.SH",    # 科创50ETF → 科创50
    "159919": "000300.SH",    # 300ETF深 → 沪深300
    "159915": "399006.SZ",    # 创业板ETF → 创业板指
    "MO": "000852.SH",        # 中证1000
    "000300": "000300.SH",
    "000905": "000905.SH",
    "000688": "000688.SH",
    "399006": "399006.SZ",
    "000852": "000852.SH",
}

# 标的 → ETF代码（用于Tushare Tier 2取现货价）
IV_FUND_MAP = {
    "510300": "510300.SH",
    "510500": "510500.SH",
    "588000": "588000.SH",
    "159919": "159919.SZ",
    "159915": "159915.SZ",
}

# 标的 → 交易所映射
IV_EXCHANGE_MAP = {
    "510300": "SSE",
    "510500": "SSE",
    "588000": "SSE",
    "159919": "SZSE",
    "159915": "SZSE",
    "MO": "CFFEX",
}

# 标的 → Tushare opt_code 前缀
IV_OPT_CODE_PREFIX = {
    "510300": "OP510300.SH",
    "510500": "OP510500.SH",
    "588000": "OP588000.SH",
    "159919": "OP159919.SZ",
    "159915": "OP159915.SZ",
    "MO": "MO",
}


class OptionIvHistoryCollector(BaseCollector):
    """
    期权IV历史序列采集器。

    用法：
        collector = OptionIvHistoryCollector()
        result = collector.collect(
            underlying_code="510300",
            start_date="20260101",
            end_date="20260801",
        )
        iv_series = result.data  # pd.Series, index=日期(YYYYMMDD), value=IV百分比
    """

    data_type: str = "option_iv_history"
    min_data_count: int = 30  # 至少30个有效交易日才认为数据充足

    def __init__(
        self,
        source_priority: Optional[List[str]] = None,
        tushare_token: Optional[str] = None,
    ):
        super().__init__(source_priority=source_priority)

        # 注册默认的四级数据源
        self.register_source(AKShareSource())
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(CozeGatewaySource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """判断IV历史数据是否充足。"""
        if result.data is None:
            return False
        # data is pd.Series
        if hasattr(result.data, "shape"):
            return int(result.data.shape[0]) >= self.min_data_count
        return result.data_count >= self.min_data_count

    def collect(
        self,
        underlying_code: str,
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> DataResult:
        """
        获取指定标的的ATM IV历史序列。

        Args:
            underlying_code: 标的代码（如 510300、588000、159915、MO 等）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.Series
                index: 交易日期字符串（YYYYMMDD）
                value: ATM IV百分比（如 25.3 表示 25.3%）
        """
        # 标准化标的代码
        underlying_code = self._normalize_underlying(underlying_code)

        # 标准化日期
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)

        logger.info(
            f"[option_iv_history] 查询: underlying={underlying_code}, "
            f"start={start_date}, end={end_date}"
        )

        result = super().collect(
            underlying_code=underlying_code,
            start_date=start_date,
            end_date=end_date,
            **kwargs,
        )

        # 补充元数据
        result.meta["underlying_code"] = underlying_code
        result.meta["start_date"] = start_date
        result.meta["end_date"] = end_date

        return result

    @staticmethod
    def _normalize_underlying(code: str) -> str:
        """标准化标的代码。"""
        # 去掉后缀
        if "." in code:
            base = code.split(".")[0]
            if base in OPTION_UNDERLYINGS or base in IV_INDEX_MAP:
                code = base
        if code in OPTION_SYMBOL_ALIASES:
            return OPTION_SYMBOL_ALIASES[code]
        if code in OPTION_UNDERLYINGS:
            return code
        if code in IV_INDEX_MAP:
            return code
        return code

    @staticmethod
    def _normalize_date(d: str) -> str:
        """标准化日期为 YYYYMMDD 格式。"""
        if not d:
            return d
        d = str(d).replace("-", "").replace("/", "")
        return d[:8]


# ============================================================
# 便捷函数
# ============================================================

def get_option_iv_history(
    underlying_code: str,
    start_date: str,
    end_date: str,
    **kwargs,
) -> Optional[pd.Series]:
    """
    便捷函数：获取指定标的的ATM IV历史序列。

    Args:
        underlying_code: 标的代码（510300 / 588000 / 159915 / MO 等）
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD

    Returns:
        pd.Series — index为日期(YYYYMMDD), value为ATM IV百分比（如25.3=25.3%）
        None — 所有数据源均失败
    """
    try:
        collector = OptionIvHistoryCollector()
        result = collector.collect(
            underlying_code=underlying_code,
            start_date=start_date,
            end_date=end_date,
            **kwargs,
        )
        if result.success and result.data is not None:
            return result.data
        return None
    except Exception as e:
        logger.error(f"[option_iv_history] 获取失败: {e}")
        return None


def get_iv_ma_data(
    underlying_code: str,
    trade_date: Optional[str] = None,
    lookback_days: int = 180,
    ma_windows: Optional[List[int]] = None,
) -> Dict:
    """
    便捷函数：获取指定标的的IV均线数据。

    Args:
        underlying_code: 标的代码
        trade_date: 截止日期（YYYYMMDD），默认当日
        lookback_days: 回溯日历天数（确保能算MA80需要约120个交易日）
        ma_windows: 均线窗口列表，默认 [40, 80]

    Returns:
        dict with keys:
            iv_current: float 当前IV(%)，失败为None
            ma40: float MA40(%)，None表示数据不足
            ma80: float MA80(%)，None表示数据不足
            signal_stars: int 星级信号 (0=平稳, 1=偏强, 2=极度超买/恐慌)
            signal: str 信号描述
            data_source: str 使用的数据源
            data_days: int 有效数据天数
            underlying_code: str 标的代码
    """
    if ma_windows is None:
        ma_windows = [40, 80]

    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")
    else:
        trade_date = OptionIvHistoryCollector._normalize_date(trade_date)

    # 计算起始日期
    end_dt = datetime.strptime(trade_date, "%Y%m%d")
    start_dt = end_dt - timedelta(days=lookback_days)
    start_date = start_dt.strftime("%Y%m%d")

    result = {
        "iv_current": None,
        "ma40": None,
        "ma80": None,
        "signal_stars": 0,
        "signal": "数据缺失",
        "data_source": "missing",
        "data_days": 0,
        "underlying_code": underlying_code,
    }

    try:
        iv_series = get_option_iv_history(
            underlying_code=underlying_code,
            start_date=start_date,
            end_date=trade_date,
        )

        if iv_series is None or len(iv_series) < 20:
            logger.warning(
                f"[iv_ma_data] {underlying_code} 数据不足: "
                f"{len(iv_series) if iv_series is not None else 0}天"
            )
            return result

        iv_series = iv_series.sort_index()
        result["data_days"] = len(iv_series)

        # 尝试从series属性获取数据源
        attrs = getattr(iv_series, "attrs", {})
        result["data_source"] = attrs.get("source", "unknown")

        # 计算均线
        for window in ma_windows:
            min_periods = max(int(window * 0.7), 10)
            ma = iv_series.rolling(window=window, min_periods=min_periods).mean()
            last_val = ma.iloc[-1] if len(ma) > 0 and pd.notna(ma.iloc[-1]) else None
            result[f"ma{window}"] = round(float(last_val), 2) if last_val else None

        # 当前IV
        result["iv_current"] = round(float(iv_series.iloc[-1]), 2)

        # 信号判断
        current = result["iv_current"]
        ma40 = result.get("ma40")
        ma80 = result.get("ma80")

        if ma80 is not None and current > ma80:
            result["signal_stars"] = 2
            result["signal"] = "★★ 极度超买/恐慌"
        elif ma40 is not None and current > ma40:
            result["signal_stars"] = 1
            result["signal"] = "★ 偏强"
        else:
            result["signal_stars"] = 0
            result["signal"] = "平稳"

    except Exception as e:
        logger.error(f"[iv_ma_data] {underlying_code} 计算异常: {e}")

    return result


# ============================================================
# IV均线横线可视化工具（与流动性监控保持一致）
# ============================================================

def iv_bar_visual(
    current_iv: Optional[float],
    ma40: Optional[float],
    ma80: Optional[float],
    width: int = 40,
) -> str:
    """
    生成文字版IV均线横线可视化（与流动性监控展示方式一致）。

    Args:
        current_iv: 当前IV(%)
        ma40: MA40(%)
        ma80: MA80(%)
        width: 横线长度（字符数）

    Returns:
        str 多行文字可视化
    """
    if current_iv is None or ma40 is None or ma80 is None:
        return "  数据不足，无法展示"

    values = [current_iv, ma40, ma80]
    vmin = min(values)
    vmax = max(values)
    vrange = vmax - vmin if vmax > vmin else 1.0
    # 留15%边距
    vmin_pad = vmin - vrange * 0.15
    vmax_pad = vmax + vrange * 0.15
    vrange_pad = vmax_pad - vmin_pad

    def pos(v):
        return (v - vmin_pad) / vrange_pad

    # 定义每条线的标签和位置
    lines = [
        ("IV ", current_iv, '─' * width),
        ("MA40", ma40, '─' * width),
        ("MA80", ma80, '─' * width),
    ]
    # 按位置从高到低排序（值越大越靠上）
    lines_sorted = sorted(lines, key=lambda x: pos(x[1]), reverse=True)

    rows = []
    for label, val, bar in lines_sorted:
        star = ''
        if label.startswith('IV'):
            if ma80 is not None and val > ma80:
                star = ' ★★'
            elif ma40 is not None and val > ma40:
                star = ' ★'
        rows.append(f"  {label:>4} {val:>6.2f}% │{bar}│{star}")

    return '\n'.join(rows)
