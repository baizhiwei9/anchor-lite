"""
指数成分股采集器
=================
采集各大指数的成分股列表，支持三级降级。

覆盖指数：
- 沪深300 (000300.SH)
- 中证500 (000905.SH)
- 中证1000 (000852.SH)
- 创业板指 (399006.SZ)
- 科创50 (000688.SH)
- 上证50 (000016.SH)
- 创业板50 (399673.SZ)

三级降级：
Tier 1: AKShare (index_stock_cons)
Tier 2: Tushare (index_weight)
Tier 3: Web爬取 (东方财富/新浪)
"""

from __future__ import annotations

from typing import List, Optional

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from .. import config
from ..sources import AKShareSource, TushareSource, WebSource


# 常用指数代码映射（简称 → tushare代码）
INDEX_CODE_MAP = {
    "沪深300": "000300.SH",
    "hs300": "000300.SH",
    "000300": "000300.SH",
    "中证500": "000905.SH",
    "zz500": "000905.SH",
    "000905": "000905.SH",
    "中证1000": "000852.SH",
    "zz1000": "000852.SH",
    "000852": "000852.SH",
    "创业板指": "399006.SZ",
    "创业板": "399006.SZ",
    "cyb": "399006.SZ",
    "399006": "399006.SZ",
    "科创50": "000688.SH",
    "科创板": "000688.SH",
    "kcb": "000688.SH",
    "000688": "000688.SH",
    "上证50": "000016.SH",
    "sz50": "000016.SH",
    "000016": "000016.SH",
    "创业板50": "399673.SZ",
    "cyb50": "399673.SZ",
    "399673": "399673.SZ",
    "上证指数": "000001.SH",
    "000001": "000001.SH",
}


class IndexComponentsCollector(BaseCollector):
    """
    指数成分股采集器。

    用法：
        collector = IndexComponentsCollector()
        result = collector.collect(index_code="000300.SH")
        codes = result.data  # list[str]
    """

    data_type: str = "index_components"
    min_data_count: int = QUALITY_THRESHOLDS.min_index_components

    def __init__(
        self,
        source_priority: Optional[List[str]] = None,
        tushare_token: Optional[str] = None,
    ):
        # 指数成分股默认用 Tushare 作为第一来源，因为 AKShare 数据不完整
        # （如沪深300只有288/300只，中证1000只有700+/1000只）
        # 受 config.USE_TUSHARE 开关控制，为 False 时降级到 AKShare 开始
        if source_priority is None:
            source_priority = []
            if config.USE_TUSHARE:
                source_priority.append("tushare")
            source_priority.extend(["akshare", "coze_gateway", "web"])

        super().__init__(source_priority=source_priority)

        # 注册默认的三级数据源
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(AKShareSource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """判断指数成分股数据是否充足。"""
        count = result.data_count
        return count >= self.min_data_count

    def collect(self, index_code: str, **kwargs) -> DataResult:
        """
        获取指数成分股。

        Args:
            index_code: 指数代码（支持简称、6位代码、tushare格式）

        Returns:
            DataResult.data: list[str] — tushare格式成分股代码列表
        """
        # 标准化指数代码
        normalized = self._normalize_index_code(index_code)
        logger.info(f"[index_components] 查询指数: {index_code} → {normalized}")

        result = super().collect(index_code=normalized, **kwargs)
        # 更新元数据中的标准化代码
        result.meta["index_code_input"] = index_code
        result.meta["index_code_normalized"] = normalized
        return result

    @staticmethod
    def _normalize_index_code(code: str) -> str:
        """将各种格式的指数代码统一为 tushare 格式。"""
        code_lower = code.lower().strip()

        # 直接命中映射表
        if code in INDEX_CODE_MAP:
            return INDEX_CODE_MAP[code]
        if code_lower in INDEX_CODE_MAP:
            return INDEX_CODE_MAP[code_lower]

        # 已经是 tushare 格式（带 .SH/.SZ 后缀）
        if '.' in code and len(code.split('.')[0]) == 6:
            return code

        # 6位数字代码，自动判断交易所
        if len(code) == 6 and code.isdigit():
            if code.startswith(('000', '001', '688', '000016')):
                return code + '.SH'
            elif code.startswith(('399', '159')):
                return code + '.SZ'
            else:
                return code + '.SH'  # 默认上交所

        return code  # 返回原值，让底层处理


def get_index_components(index_code: str, **kwargs) -> List[str]:
    """
    便捷函数：获取指数成分股列表。

    Args:
        index_code: 指数代码（支持简称/6位代码/tushare格式）

    Returns:
        list[str] — tushare格式成分股代码列表，失败返回空列表
    """
    try:
        collector = IndexComponentsCollector()
        result = collector.collect(index_code=index_code, **kwargs)
        return result.data or []
    except Exception as e:
        logger.error(f"[index_components] 获取失败: {e}")
        return []


def get_top2000_stocks(**kwargs) -> List[str]:
    """
    便捷函数：获取全市场前2000只股票（沪深300+中证500+中证1000合并去重）。

    Returns:
        list[str] — tushare格式股票代码列表
    """
    all_codes = set()
    index_codes = ["000300.SH", "000905.SH", "000852.SH"]

    for idx_code in index_codes:
        try:
            codes = get_index_components(idx_code, **kwargs)
            all_codes.update(codes)
            logger.info(f"[top2000] + {idx_code}: 新增{len(codes)}只，累计{len(all_codes)}只")
        except Exception as e:
            logger.warning(f"[top2000] 获取 {idx_code} 失败: {e}")

    logger.info(f"[top2000] 最终获取 {len(all_codes)} 只股票（合并去重后）")
    return list(all_codes)
