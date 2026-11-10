"""
指数日线行情采集器
===================
采集A股主要指数日线行情数据，支持四级降级。

支持两种模式：
1. 单只指数历史K线（按 ts_code + 日期范围）
2. 批量多只指数日线（按 ts_code_list + 日期范围）

四级降级：
Tier 1: AKShare (stock_zh_index_daily / index_zh_a_hist)
Tier 2: Tushare (index_daily 接口)
Tier 3: Coze Gateway（技能CLI兜底）
Tier 4: Web爬取 (新浪财经指数K线接口)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Union

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from .. import config
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


# 常用指数代码（tushare格式 → 新浪格式前缀）
INDEX_CODE_PREFIX_MAP = {
    '000001.SH': 'sh', '000002.SH': 'sh', '000003.SH': 'sh',
    '000016.SH': 'sh', '000300.SH': 'sh', '000688.SH': 'sh',
    '000852.SH': 'sh', '000905.SH': 'sh', '000906.SH': 'sh',
    '399001.SZ': 'sz', '399005.SZ': 'sz', '399006.SZ': 'sz',
    '399106.SZ': 'sz', '399300.SZ': 'sz', '399905.SZ': 'sz',
    '000010.SH': 'sh',
}


def _normalize_index_code(ts_code: str) -> str:
    """标准化指数代码为 tushare 格式（6位.SH/SZ）。"""
    ts_code = str(ts_code).strip().upper()
    if '.' in ts_code:
        return ts_code
    # 6位数字，自动补后缀
    if len(ts_code) == 6 and ts_code.isdigit():
        if ts_code.startswith('000') or ts_code.startswith('001'):
            return f"{ts_code}.SH"
        elif ts_code.startswith('399'):
            return f"{ts_code}.SZ"
    return ts_code


def _normalize_date(date_str: str) -> str:
    """标准化日期格式为 YYYYMMDD。"""
    date_str = str(date_str).strip()
    if len(date_str) == 8 and date_str.isdigit():
        return date_str
    if '-' in date_str:
        return date_str.replace('-', '')
    if '/' in date_str:
        return date_str.replace('/', '')
    return date_str


class IndexDailyCollector(BaseCollector):
    """
    指数日线行情采集器。

    用法：
        collector = IndexDailyCollector()

        # 单只指数历史K线
        result = collector.collect(ts_code="000300.SH", start_date="20260101", end_date="20260716")

        # 批量多只指数日线
        result = collector.collect(
            ts_code_list=["000300.SH", "399006.SZ", "000688.SH"],
            start_date="20260101",
            end_date="20260716",
        )
    """

    data_type: str = "index_daily"
    min_data_count: int = 1  # 单只模式至少1条

    def __init__(
        self,
        source_priority: Optional[List[str]] = None,
        tushare_token: Optional[str] = None,
    ):
        # 默认优先级：akshare > tushare > coze_gateway > web
        # 受 config.USE_TUSHARE 开关控制，为 False 时直接跳过 tushare 层级
        if source_priority is None:
            source_priority = ["akshare"]
            if config.USE_TUSHARE:
                source_priority.append("tushare")
            source_priority.extend(["coze_gateway", "web"])

        super().__init__(source_priority=source_priority)

        # 注册四级数据源
        self.register_source(AKShareSource())
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(CozeGatewaySource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """判断指数日线数据是否充足。"""
        count = result.data_count
        return count >= self.min_data_count

    def collect(
        self,
        ts_code: Optional[str] = None,
        ts_code_list: Optional[List[str]] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback: Optional[int] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取指数日线行情。

        Args:
            ts_code: 指数代码（tushare格式，如 000300.SH），单只
            ts_code_list: 指数代码列表（tushare格式），批量
            trade_date: 单日交易日期（YYYYMMDD）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            lookback: 回溯天数（从 end_date 或 trade_date 往前推）

        Returns:
            DataResult.data:
                - 单只模式: pd.DataFrame — 日线行情数据
                    列: trade_date, open, high, low, close, vol, amount, pct_chg 等
                - 批量模式: Dict[str, pd.DataFrame] — {ts_code: df}
        """
        # 标准化日期
        if trade_date:
            trade_date = _normalize_date(trade_date)
        if start_date:
            start_date = _normalize_date(start_date)
        if end_date:
            end_date = _normalize_date(end_date)

        # 计算日期范围（lookback 优先）
        if lookback and lookback > 0:
            ref_date = end_date or trade_date or datetime.now().strftime("%Y%m%d")
            ref_date = _normalize_date(ref_date)
            ref_dt = datetime.strptime(ref_date, "%Y%m%d")
            start_dt = ref_dt - timedelta(days=lookback + 10)  # 多取10天防假日
            computed_start = start_dt.strftime("%Y%m%d")
            if not start_date or computed_start < start_date:
                start_date = computed_start
            if not end_date:
                end_date = ref_date

        # 确定模式
        if ts_code_list and len(ts_code_list) > 0:
            mode = "batch"
            normalized_list = [_normalize_index_code(c) for c in ts_code_list]
            code_info = f"batch[{len(normalized_list)}]"
        elif ts_code:
            mode = "single"
            ts_code = _normalize_index_code(ts_code)
            code_info = ts_code
        elif trade_date:
            # 单日 + 无代码 → 默认主要指数
            mode = "batch"
            normalized_list = [
                '000001.SH', '000300.SH', '000905.SH', '000852.SH',
                '000688.SH', '399001.SZ', '399006.SZ', '399106.SZ',
            ]
            code_info = f"main@{trade_date}"
            # 若未指定 start_date 且有 lookback，start_date 已在上方计算
            if not start_date:
                start_date = trade_date
            if not end_date:
                end_date = trade_date
        else:
            mode = "unknown"
            code_info = "unknown"

        logger.info(
            f"[index_daily] 模式={mode}, 代码={code_info}, "
            f"start={start_date or '-'}, end={end_date or trade_date or '-'}"
        )

        # 批量模式：逐个获取后合并成 dict
        if mode == "batch":
            return self._collect_batch(
                ts_code_list=normalized_list,
                start_date=start_date,
                end_date=end_date,
                trade_date=trade_date,
                **kwargs,
            )

        # 单只模式
        result = super().collect(
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            **kwargs,
        )

        # 补充元数据
        result.meta["mode"] = mode
        result.meta["ts_code"] = ts_code or ""
        result.meta["trade_date"] = trade_date or ""
        result.meta["start_date"] = start_date or ""
        result.meta["end_date"] = end_date or ""

        return result

    def _collect_batch(
        self,
        ts_code_list: List[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        trade_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """批量获取多只指数日线数据，返回 dict。"""
        results_dict: Dict[str, pd.DataFrame] = {}
        used_source = ""
        used_tier = 0
        success_count = 0
        failures = []

        for ts_code in ts_code_list:
            try:
                day_result = super().collect(
                    ts_code=ts_code,
                    trade_date=trade_date,
                    start_date=start_date,
                    end_date=end_date,
                    **kwargs,
                )
                if day_result.success and day_result.data is not None:
                    df = day_result.data
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        results_dict[ts_code] = df
                        success_count += 1
                        if not used_source:
                            used_source = day_result.source
                            used_tier = day_result.source_tier
                    else:
                        failures.append({"code": ts_code, "error": "空数据"})
                else:
                    failures.append({"code": ts_code, "error": day_result.error})
            except Exception as e:
                failures.append({"code": ts_code, "error": str(e)})
                logger.warning(f"[index_daily] {ts_code} 获取异常: {e}")

        # 至少成功一个就算成功
        if success_count > 0:
            return DataResult(
                data=results_dict,
                data_type="index_daily",
                source=used_source,
                source_tier=used_tier,
                success=True,
                meta={
                    "mode": "batch",
                    "index_count": len(ts_code_list),
                    "success_count": success_count,
                    "failed_count": len(failures),
                    "failures": failures,
                    "start_date": start_date or "",
                    "end_date": end_date or trade_date or "",
                },
            )
        else:
            from ..base import AllSourcesFailedError
            raise AllSourcesFailedError(
                "index_daily",
                [{"source": "all", "error": f"全部{len(ts_code_list)}只指数获取失败"}]
                + failures,
            )


def get_index_daily(
    ts_code: Optional[str] = None,
    ts_code_list: Optional[List[str]] = None,
    trade_date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    lookback: Optional[int] = None,
    **kwargs,
) -> Union[pd.DataFrame, Dict[str, pd.DataFrame]]:
    """
    便捷函数：获取指数日线行情。

    Args:
        ts_code: 指数代码（tushare格式）
        ts_code_list: 指数代码列表
        trade_date: 单日日期
        start_date: 开始日期
        end_date: 结束日期
        lookback: 回溯天数

    Returns:
        单只模式: pd.DataFrame
        批量模式: Dict[str, pd.DataFrame] — {ts_code: df}
        失败: 空DataFrame 或 空dict
    """
    try:
        collector = IndexDailyCollector()
        result = collector.collect(
            ts_code=ts_code,
            ts_code_list=ts_code_list,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            lookback=lookback,
            **kwargs,
        )
        if result.success and result.data is not None:
            return result.data
        # 失败返回对应类型的空值
        if ts_code_list or (trade_date and not ts_code):
            return {}
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"[index_daily] 获取失败: {e}")
        if ts_code_list or (trade_date and not ts_code):
            return {}
        return pd.DataFrame()
