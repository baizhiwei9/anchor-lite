"""
利率数据采集器
===============
采集各类利率数据（SHIBOR/LPR/HIBOR/债券收益率），支持四级降级。

覆盖利率类型：
- SHIBOR: 上海银行间同业拆放利率（O/N~1Y）
- LPR: 贷款市场报价利率（1Y/5Y）
- HIBOR: 香港银行同业拆息（O/N~1Y）
- 中债收益率曲线: 国债收益率曲线

四级降级：
Tier 1: AKShare (macro_china_shibor_all / macro_china_lpr / bond_china_yield)
Tier 2: Tushare (shibor / shibor_lpr / yc_cb)
Tier 3: Coze 数据网关 (macro-china-shibor / macro-china-lpr / rate-hibor)
Tier 4: Web爬取 (Shibor官网 / 央行官网)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 支持的利率类型
SUPPORTED_RATE_TYPES = ["shibor", "lpr", "hibor", "bond_yield", "cd_rate_1y"]

# sub_type 到 rate_type 的映射（便于调用方使用 sub_type 参数）
SUB_TYPE_MAP = {
    "cd_rate_1y": "cd_rate_1y",  # 1年期同业存单利率
}


class InterestRateCollector(BaseCollector):
    """
    利率数据采集器。

    用法：
        collector = InterestRateCollector()
        result = collector.collect(rate_type="shibor")
        shibor_data = result.data  # dict with latest, history, etc.
    """

    data_type: str = "interest_rate"
    min_data_count: int = 1  # 至少1条利率数据

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
        """判断利率数据是否充足。"""
        if not result.success or result.data is None:
            return False

        data = result.data

        # dict 格式：检查 latest 字段
        if isinstance(data, dict):
            latest = data.get("latest")
            if latest and isinstance(latest, dict) and len(latest) > 0:
                # 至少有一个利率值
                has_valid = any(
                    v is not None for v in latest.values()
                )
                if has_valid:
                    return True

            # 检查 history
            history = data.get("history")
            if history is not None and hasattr(history, "shape"):
                if history.shape[0] >= self.min_data_count:
                    return True

            # 其他格式按 data_count 判断
            return result.data_count >= self.min_data_count

        # DataFrame 格式
        if hasattr(data, "shape"):
            return data.shape[0] >= self.min_data_count

        return result.data_count >= self.min_data_count

    def collect(
        self,
        rate_type: str = "shibor",
        sub_type: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取利率数据。

        Args:
            rate_type: 利率类型（shibor / lpr / hibor / bond_yield / cd_rate_1y）
            sub_type: 子类型（如 cd_rate_1y），与 rate_type 二选一，优先使用 sub_type
            **kwargs: 其他参数（如 start_date, end_date 等）

        Returns:
            DataResult.data: 利率数据，格式因 rate_type 而异：
                shibor: dict with latest (dict[term]=rate), latest_date, history (DataFrame)
                lpr: dict with latest (dict[LPR1Y/LPR5Y]=rate), latest_date, history (DataFrame)
                hibor: dict with latest (dict[term]=rate), latest_date, history (DataFrame)
                bond_yield: DataFrame of yield curve data
                cd_rate_1y: dict with latest (dict["1Y"]=rate), latest_date, curve_name, history
        """
        # sub_type 优先于 rate_type
        if sub_type:
            sub = sub_type.lower()
            if sub in SUB_TYPE_MAP:
                rate_type = SUB_TYPE_MAP[sub]
            else:
                rate_type = sub  # 直接作为rate_type尝试

        # 标准化利率类型
        rate_type = rate_type.lower()
        if rate_type not in SUPPORTED_RATE_TYPES:
            return DataResult(
                data_type=self.data_type,
                success=False,
                error=f"不支持的利率类型: {rate_type}，支持: {SUPPORTED_RATE_TYPES}",
            )

        logger.info(f"[interest_rate] 查询利率: type={rate_type}")

        result = super().collect(
            rate_type=rate_type,
            **kwargs,
        )

        # 补充元数据
        result.meta["rate_type"] = rate_type
        if sub_type:
            result.meta["sub_type"] = sub_type

        return result


def get_interest_rate(
    rate_type: str = "shibor",
    **kwargs,
) -> Dict[str, Any]:
    """
    便捷函数：获取利率数据。

    Args:
        rate_type: 利率类型（shibor / lpr / hibor / bond_yield）
        **kwargs: 其他参数

    Returns:
        dict — 利率数据，失败返回空dict
    """
    try:
        collector = InterestRateCollector()
        result = collector.collect(rate_type=rate_type, **kwargs)
        return result.data if result.data and result.success else {}
    except Exception as e:
        logger.error(f"[interest_rate] 获取 {rate_type} 失败: {e}")
        return {}


def get_shibor(**kwargs) -> Dict[str, Any]:
    """便捷函数：获取 SHIBOR 利率。"""
    return get_interest_rate("shibor", **kwargs)


def get_lpr(**kwargs) -> Dict[str, Any]:
    """便捷函数：获取 LPR 利率。"""
    return get_interest_rate("lpr", **kwargs)


def get_hibor(**kwargs) -> Dict[str, Any]:
    """便捷函数：获取 HIBOR 利率。"""
    return get_interest_rate("hibor", **kwargs)


def get_bond_yield(**kwargs) -> pd.DataFrame:
    """便捷函数：获取国债收益率曲线数据。"""
    try:
        collector = InterestRateCollector()
        result = collector.collect(rate_type="bond_yield", **kwargs)
        if result.success and result.data is not None:
            return result.data
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"[interest_rate] 获取 bond_yield 失败: {e}")
        return pd.DataFrame()


def get_cd_rate_1y(**kwargs) -> Dict[str, Any]:
    """
    便捷函数：获取1年期同业存单利率（代理：中债商业银行普通债收益率曲线(AAA) 1年期）。

    Args:
        **kwargs: start_date, end_date 等参数

    Returns:
        dict with keys:
            - latest: {"1Y": rate_value} 最新1年期利率(%)
            - latest_date: 最新日期(YYYYMMDD)
            - curve_name: 曲线名称
            - period: 期限
            - history: DataFrame 历史数据
        失败返回空dict
    """
    try:
        collector = InterestRateCollector()
        result = collector.collect(rate_type="cd_rate_1y", **kwargs)
        if result.success and result.data:
            return result.data
        return {}
    except Exception as e:
        logger.error(f"[interest_rate] 获取 cd_rate_1y 失败: {e}")
        return {}
