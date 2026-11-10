"""
A股日线行情采集器
===================
采集A股个股及全市场日线行情数据，支持三级降级。

支持三种模式：
1. 单只股票历史K线（按 ts_code + 日期范围）
2. 全市场单日行情（按 trade_date）
3. 多只股票批量日线（按 ts_code_list + 日期范围，按天全市场+筛选）

三级降级：
Tier 1: AKShare (stock_zh_a_hist / stock_zh_a_spot_em)
Tier 2: Tushare (daily 接口)
Tier 3: Web爬取 (东方财富K线/实时行情接口)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Union

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import QUALITY_THRESHOLDS, logger
from .. import config
from ..sources import AKShareSource, TushareSource, CozeGatewaySource, WebSource


class StockDailyCollector(BaseCollector):
    """
    A股日线行情采集器。

    用法：
        collector = StockDailyCollector()

        # 单只股票历史K线
        result = collector.collect(ts_code="000001.SZ", start_date="20260101", end_date="20260716")

        # 全市场单日行情
        result = collector.collect(trade_date="20260716")
    """

    data_type: str = "stock_daily"
    min_data_count: int = QUALITY_THRESHOLDS.min_daily_records

    def __init__(
        self,
        source_priority: Optional[List[str]] = None,
        tushare_token: Optional[str] = None,
    ):
        # stock_daily 默认优先级：tushare > akshare > coze_gateway > web
        # 收费版 tushare 配额充足，数据质量最高、接口最稳定，优先使用
        # akshare 其次；coze_gateway 作为技能级兜底；web 最后兜底
        # 受 config.USE_TUSHARE 开关控制，为 False 时直接跳过 tushare 层级
        if source_priority is None:
            source_priority = []
            if config.USE_TUSHARE:
                source_priority.append("tushare")
            source_priority.extend(["akshare", "coze_gateway", "web"])
        super().__init__(source_priority=source_priority)

        # 注册默认的四级数据源
        self.register_source(AKShareSource())
        self.register_source(TushareSource(token=tushare_token))
        self.register_source(CozeGatewaySource())
        self.register_source(WebSource())

    def is_data_sufficient(self, result: DataResult) -> bool:
        """判断日线数据是否充足。"""
        count = result.data_count
        # 全市场模式预期>500条，单只模式预期>1条
        # 这里用宽松阈值，具体判断结合参数
        if count >= self.min_data_count:
            return True
        # 单只股票如果返回至少1条也接受
        return count >= 1

    def collect(
        self,
        ts_code: Optional[str] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        symbol: Optional[str] = None,
        adjust: str = "qfq",
        **kwargs,
    ) -> DataResult:
        """
        获取A股日线行情。

        Args:
            ts_code: 股票代码（tushare格式，如 000001.SZ），单只
            symbol: 股票代码（6位数字），单只
            trade_date: 单日交易日期（YYYYMMDD），全市场或单只
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            adjust: 复权方式（qfq前复权 / hfq后复权 / ''不复权）

        Returns:
            DataResult.data: pd.DataFrame — 日线行情数据
                列包含: ts_code, trade_date, open, high, low, close, vol, amount, pct_chg 等
        """
        # 标准化参数
        if trade_date:
            trade_date = self._normalize_date(trade_date)
        if start_date:
            start_date = self._normalize_date(start_date)
        if end_date:
            end_date = self._normalize_date(end_date)

        # 确定模式
        if ts_code or symbol:
            mode = "single"
            code_info = ts_code or symbol
        elif trade_date and not ts_code and not symbol:
            mode = "all"
            code_info = f"all@{trade_date}"
        else:
            mode = "unknown"
            code_info = "unknown"

        logger.info(
            f"[stock_daily] 模式={mode}, 代码={code_info}, "
            f"start={start_date or '-'}, end={end_date or trade_date or '-'}"
        )

        result = super().collect(
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            symbol=symbol,
            adjust=adjust,
            **kwargs,
        )

        # 补充元数据
        result.meta["mode"] = mode
        result.meta["ts_code"] = ts_code or ""
        result.meta["symbol"] = symbol or ""
        result.meta["trade_date"] = trade_date or ""
        result.meta["start_date"] = start_date or ""
        result.meta["end_date"] = end_date or ""

        return result

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """标准化日期格式为 YYYYMMDD。"""
        date_str = str(date_str).strip()
        # 已经是 YYYYMMDD 格式
        if len(date_str) == 8 and date_str.isdigit():
            return date_str
        # YYYY-MM-DD 格式
        if '-' in date_str:
            return date_str.replace('-', '')
        # YYYY/MM/DD 格式
        if '/' in date_str:
            return date_str.replace('/', '')
        return date_str

    # ============================================================
    # 批量获取：多只股票 + 日期范围（按天全市场 + 筛选）
    # ============================================================

    def _collect_with_skip(self, skipped_sources: set, **kwargs) -> DataResult:
        """
        采集数据，但跳过已知失败的数据源（用于批量加速）。
        新失败的数据源会加入 skipped_sources 集合。
        """
        method_name = f"fetch_{self.data_type}"
        available_sources = self.get_available_sources()

        failures = []
        for source in available_sources:
            if source.name in skipped_sources:
                continue  # 跳过已知不支持的数据源

            result = self._call_source_method(source, method_name, **kwargs)

            if result.success:
                if self.is_data_sufficient(result):
                    return result
                else:
                    # 数据不足，也标记为跳过（避免后续重复尝试）
                    skipped_sources.add(source.name)
                    failures.append({
                        "source": source.name,
                        "error": f"数据不足 (预期>={self.min_data_count}, 实际={result.data_count})",
                    })
                    continue
            else:
                # 失败，标记为跳过
                skipped_sources.add(source.name)
                failures.append({
                    "source": source.name,
                    "error": result.error,
                })
                continue

        # 所有可用数据源都失败了
        from ..base import AllSourcesFailedError
        raise AllSourcesFailedError(self.data_type, failures)

    def collect_batch(
        self,
        ts_code_list: List[str],
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> DataResult:
        """
        批量获取多只股票在指定日期范围内的日线行情。

        实现策略：按交易日循环获取全市场单日行情，筛选目标股票后合并。
        这种方式对 Tushare 最经济（每天1次API调用），且复用三级降级。

        Args:
            ts_code_list: 目标股票代码列表（tushare格式，如 ['000001.SZ', ...]）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.DataFrame — 批量日线数据
                列包含: ts_code, trade_date, open, high, low, close, vol, amount 等
        """
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)

        logger.info(
            f"[stock_daily] 批量获取: {len(ts_code_list)}只股票, "
            f"{start_date} ~ {end_date}"
        )

        if not ts_code_list:
            return DataResult(
                data=pd.DataFrame(),
                data_type="stock_daily",
                source="",
                source_tier=0,
                success=False,
                error="股票代码列表为空",
                meta={"mode": "batch", "stock_count": 0},
            )

        stock_set = set(ts_code_list)

        # 1. 获取交易日列表
        trade_dates = self._get_trade_dates(start_date, end_date)
        if not trade_dates:
            logger.warning("[stock_daily] 批量获取：交易日列表为空，尝试按日历日期估算")
            trade_dates = self._estimate_trade_dates(start_date, end_date)

        if not trade_dates:
            return DataResult(
                data=pd.DataFrame(),
                data_type="stock_daily",
                source="",
                source_tier=0,
                success=False,
                error="无法获取交易日列表",
                meta={"mode": "batch", "stock_count": len(ts_code_list)},
            )

        logger.info(f"[stock_daily] 批量获取: 共 {len(trade_dates)} 个交易日")

        # 2. 按交易日循环获取全市场数据，筛选目标股票
        all_data = []
        used_source = ""
        used_tier = 0
        success_days = 0
        failed_days = 0
        primary_source = None  # 记录第一个成功的数据源，用于统一元数据
        skipped_sources = set()  # 已知不支持的数据源，直接跳过
        max_retries = 2  # 单日最多重试次数（总共尝试3次）

        # 快速判断：如果全市场模式第一天就失败（如历史日期不支持），
        # 连续失败 N 天后直接切换到逐只K线模式，避免浪费时间逐天试
        consecutive_fail_threshold = 3  # 连续失败3天就放弃全市场模式
        consecutive_fails = 0
        use_per_stock_fallback = False

        for i, td in enumerate(trade_dates):
            # 连续失败太多天，直接跳过去逐只回退
            if use_per_stock_fallback:
                failed_days += 1
                continue
            day_result = None
            for attempt in range(max_retries + 1):
                try:
                    # 动态调整：跳过已知失败的数据源，加速批量获取
                    day_result = self._collect_with_skip(
                        skipped_sources=skipped_sources,
                        trade_date=td,
                        **kwargs,
                    )
                    if day_result.success and day_result.data is not None and not day_result.data.empty:
                        break  # 成功，退出重试
                    elif attempt < max_retries:
                        logger.debug(
                            f"[stock_daily] {td} 第{attempt+1}次获取数据为空，重试中..."
                        )
                        import time
                        time.sleep(1)
                        # 重试时清掉跳过的源（可能是瞬时故障）
                        skipped_sources.clear()
                except Exception as e:
                    if attempt < max_retries:
                        logger.debug(
                            f"[stock_daily] {td} 第{attempt+1}次获取异常: {e}, 重试中..."
                        )
                        import time
                        time.sleep(1)
                        skipped_sources.clear()
                    else:
                        day_result = DataResult(
                            data=pd.DataFrame(),
                            data_type="stock_daily",
                            source="",
                            source_tier=0,
                            success=False,
                            error=str(e),
                        )
            # 重试结束，处理结果
            if day_result and day_result.success and day_result.data is not None and not day_result.data.empty:
                df_day = day_result.data
                # 确保有 ts_code 列
                if 'ts_code' not in df_day.columns:
                    # 尝试从其他列识别
                    for col in ['股票代码', 'code', 'symbol']:
                        if col in df_day.columns:
                            df_day = df_day.rename(columns={col: 'ts_code'})
                            break
                # 筛选目标股票
                if 'ts_code' in df_day.columns:
                    df_filtered = df_day[df_day['ts_code'].isin(stock_set)].copy()
                    if not df_filtered.empty:
                        # 确保 trade_date 列存在
                        if 'trade_date' not in df_filtered.columns:
                            df_filtered['trade_date'] = td
                        all_data.append(df_filtered)
                        success_days += 1
                        consecutive_fails = 0  # 重置连续失败计数
                        # 记录第一个成功的数据源
                        if primary_source is None:
                            primary_source = day_result.source
                            used_source = day_result.source
                            used_tier = day_result.source_tier
                else:
                    logger.warning(f"[stock_daily] {td} 数据中无 ts_code 列，跳过")
                    failed_days += 1
                    consecutive_fails += 1
            else:
                failed_days += 1
                consecutive_fails += 1
                err_msg = day_result.error if day_result else "未知错误"
                logger.warning(
                    f"[stock_daily] {td} 获取失败: {err_msg or '空数据'}"
                )

            # 连续失败达到阈值，直接切换到逐只回退（不再逐天尝试）
            if consecutive_fails >= consecutive_fail_threshold and not use_per_stock_fallback:
                use_per_stock_fallback = True
                logger.warning(
                    f"[stock_daily] 连续{consecutive_fails}天全市场模式失败，"
                    f"跳过剩余{len(trade_dates) - i - 1}天，直接切换到逐只K线回退"
                )

            # 每20天打印一次进度
            if (i + 1) % 20 == 0:
                logger.info(
                    f"[stock_daily] 批量进度: {i+1}/{len(trade_dates)}, "
                    f"成功{success_days}天, 失败{failed_days}天"
                )

        # 3. 合并结果
        if all_data:
            result_df = pd.concat(all_data, ignore_index=True)
            # 去重（以防不同天数据有重叠）
            if 'ts_code' in result_df.columns and 'trade_date' in result_df.columns:
                result_df = result_df.drop_duplicates(subset=['ts_code', 'trade_date'])

            total_records = len(result_df)
            unique_stocks = result_df['ts_code'].nunique() if 'ts_code' in result_df.columns else 0

            logger.info(
                f"[stock_daily] 批量获取完成: {total_records}条记录, "
                f"覆盖{unique_stocks}/{len(ts_code_list)}只股票, "
                f"成功{success_days}/{len(trade_dates)}天"
            )

            return DataResult(
                data=result_df,
                data_type="stock_daily",
                source=used_source,
                source_tier=used_tier,
                success=True,
                meta={
                    "mode": "batch",
                    "batch_strategy": "daily_all_market",
                    "stock_count": len(ts_code_list),
                    "matched_stocks": unique_stocks,
                    "total_records": total_records,
                    "trade_days": len(trade_dates),
                    "success_days": success_days,
                    "failed_days": failed_days,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )

        # 4. 回退策略：按天全市场全部失败时，改用逐只K线模式
        #    当 AKShare/Tushare 全市场接口不可用（如历史日期），
        #    Web 源全市场也是实时行情不支持历史，
        #    此时只能逐只请求K线再合并。
        logger.warning(
            f"[stock_daily] 按天全市场模式全部失败 ({success_days}/{len(trade_dates)}天成功)，"
            f"回退到逐只K线模式"
        )

        # 如果已有部分天的全市场数据成功获取，合并进去
        existing_df = pd.DataFrame()
        if all_data:
            existing_df = pd.concat(all_data, ignore_index=True)
            logger.info(
                f"[stock_daily] 逐只回退前已有{success_days}天全市场数据, "
                f"共{len(existing_df)}条记录"
            )

        per_stock_result = self._collect_batch_per_stock(
            ts_code_list=ts_code_list,
            start_date=start_date,
            end_date=end_date,
            # 注意：不继承全市场模式的 skipped_sources
            # 因为逐只K线模式和全市场模式使用的是完全不同的API接口
            # 全市场模式失败的源（如web全市场实时行情）
            # 在逐只K线模式下可能完全可用（如web单只历史K线）
            **kwargs,
        )

        # 合并全市场已有的数据 + 逐只回退的数据
        if per_stock_result.success and per_stock_result.data is not None:
            result_df = per_stock_result.data
            if not existing_df.empty:
                result_df = pd.concat([existing_df, result_df], ignore_index=True)
                if 'ts_code' in result_df.columns and 'trade_date' in result_df.columns:
                    result_df = result_df.drop_duplicates(subset=['ts_code', 'trade_date'])

            total_records = len(result_df)
            unique_stocks = result_df['ts_code'].nunique() if 'ts_code' in result_df.columns else 0

            # 更新元数据
            per_stock_result.data = result_df
            per_stock_result.meta["total_records"] = total_records
            per_stock_result.meta["matched_stocks"] = unique_stocks
            per_stock_result.meta["success_days"] = success_days
            per_stock_result.meta["failed_days"] = failed_days
            per_stock_result.meta["batch_strategy"] = "mixed_daily_and_per_stock"

            logger.info(
                f"[stock_daily] 混合模式完成: {total_records}条记录, "
                f"覆盖{unique_stocks}/{len(ts_code_list)}只股票"
            )

        return per_stock_result

    def _collect_batch_per_stock(
        self,
        ts_code_list: List[str],
        start_date: str,
        end_date: str,
        skipped_sources: Optional[set] = None,
        **kwargs,
    ) -> DataResult:
        """
        批量获取：逐只股票请求K线，再合并。

        作为按天全市场模式的回退策略，适用于：
        - 历史日期（全市场接口不支持历史）
        - 全市场接口全部挂掉
        - 目标股票数量不多（如指数成分股）

        设计原则：简单可靠优先，不搞复杂的优化逻辑。
        每只股票直接调用 self.collect() 走三级降级，
        虽然慢但数据质量有保障，不会漏数据。

        注意：此方式比全市场模式慢（N次调用 vs 1次调用），
        仅在全市场模式完全不可用时使用。
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if skipped_sources is None:
            skipped_sources = set()

        logger.info(
            f"[stock_daily] 逐只K线模式: {len(ts_code_list)}只股票, "
            f"{start_date} ~ {end_date}"
        )

        all_dfs = []
        success_stocks = 0
        failed_stocks = 0
        used_source = ""
        used_tier = 0
        primary_source = None

        # 快速探测：用第一只股票试各级源，找出可用的源
        # 避免每只股票都挨个试一遍（如akshare超时15秒太浪费）
        working_sources = []
        if ts_code_list:
            probe_code = ts_code_list[0]
            method_name = f"fetch_{self.data_type}"
            for src in self.get_available_sources():
                try:
                    r = self._call_source_method(src, method_name,
                        ts_code=probe_code,
                        start_date=start_date,
                        end_date=end_date,
                        **kwargs,
                    )
                    if r.success and r.data is not None and not r.data.empty:
                        working_sources.append(src)
                        logger.info(
                            f"[stock_daily] 逐只模式探测: {src.name} 可用"
                        )
                        # 找到第一个可用源就够了，不用继续探测（节省时间）
                        break
                except Exception:
                    logger.debug(
                        f"[stock_daily] 逐只模式探测: {src.name} 不可用"
                    )

        if not working_sources:
            logger.warning(
                "[stock_daily] 逐只模式探测：所有源都不可用，将完整走三级降级"
            )

        # 并发控制：根据股票数量动态调整
        # Web源（东方财富K线）10~15并发稳定无反爬
        # 设为 12 并发，兼顾速度和稳定性
        if len(ts_code_list) <= 10:
            max_workers = max(1, len(ts_code_list) // 2 + 1)
        elif len(ts_code_list) <= 50:
            max_workers = 10
        else:
            max_workers = 12
        max_workers = min(max_workers, len(ts_code_list))

        def _fetch_one(code: str):
            try:
                method_name = f"fetch_{self.data_type}"

                # 优先用探测出的可用源（节省逐个尝试的时间）
                if working_sources:
                    for src in working_sources:
                        try:
                            result = self._call_source_method(src, method_name,
                                ts_code=code,
                                start_date=start_date,
                                end_date=end_date,
                                **kwargs,
                            )
                            if result.success and result.data is not None and not result.data.empty:
                                return code, result
                        except Exception:
                            pass

                # 可用源都失败了，再完整走一遍三级降级（防止漏判）
                result = self.collect(
                    ts_code=code,
                    start_date=start_date,
                    end_date=end_date,
                    **kwargs,
                )
                return code, result
            except Exception as e:
                return code, DataResult(
                    data=pd.DataFrame(),
                    data_type="stock_daily",
                    source="",
                    source_tier=0,
                    success=False,
                    error=str(e),
                )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_fetch_one, code): code
                for code in ts_code_list
            }
            for i, future in enumerate(as_completed(future_map)):
                code, result = future.result()
                if result.success and result.data is not None and not result.data.empty:
                    all_dfs.append(result.data)
                    success_stocks += 1
                    if primary_source is None:
                        primary_source = result.source
                        used_source = result.source
                        used_tier = result.source_tier
                else:
                    failed_stocks += 1
                    logger.debug(
                        f"[stock_daily] 逐只K线失败: {code}, "
                        f"错误: {result.error or '空数据'}"
                    )

                # 每50只打印一次进度
                if (i + 1) % 50 == 0 or (i + 1) == len(ts_code_list):
                    logger.info(
                        f"[stock_daily] 逐只K线进度: {i+1}/{len(ts_code_list)}, "
                        f"成功{success_stocks}只, 失败{failed_stocks}只"
                    )

        if all_dfs:
            result_df = pd.concat(all_dfs, ignore_index=True)
            if 'ts_code' in result_df.columns and 'trade_date' in result_df.columns:
                result_df = result_df.drop_duplicates(subset=['ts_code', 'trade_date'])

            total_records = len(result_df)
            unique_stocks = result_df['ts_code'].nunique() if 'ts_code' in result_df.columns else 0

            logger.info(
                f"[stock_daily] 逐只K线模式完成: {total_records}条记录, "
                f"覆盖{unique_stocks}/{len(ts_code_list)}只股票"
            )

            return DataResult(
                data=result_df,
                data_type="stock_daily",
                source=used_source,
                source_tier=used_tier,
                success=True,
                meta={
                    "mode": "batch",
                    "batch_strategy": "per_stock_kline",
                    "stock_count": len(ts_code_list),
                    "matched_stocks": unique_stocks,
                    "total_records": total_records,
                    "success_stocks": success_stocks,
                    "failed_stocks": failed_stocks,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )
        else:
            return DataResult(
                data=pd.DataFrame(),
                data_type="stock_daily",
                source=used_source or "all",
                source_tier=used_tier or 0,
                success=False,
                error=f"逐只K线模式也失败: {success_stocks}/{len(ts_code_list)}只成功",
                meta={
                    "mode": "batch",
                    "batch_strategy": "per_stock_kline",
                    "stock_count": len(ts_code_list),
                    "success_stocks": success_stocks,
                    "failed_stocks": failed_stocks,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )

    def _get_trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """
        获取指定日期范围内的交易日列表。
        优先使用 Tushare trade_cal 接口（受 config.USE_TUSHARE 控制），失败则尝试 AKShare。
        """
        # 尝试 Tushare（受 config.USE_TUSHARE 开关控制）
        if config.USE_TUSHARE:
            tushare_src = self._sources.get("tushare")
            if tushare_src and tushare_src.is_available():
                try:
                    return self._get_trade_dates_tushare(tushare_src, start_date, end_date)
                except Exception as e:
                    logger.debug(f"[stock_daily] Tushare交易日历获取失败: {e}")

        # 尝试 AKShare
        akshare_src = self._sources.get("akshare")
        if akshare_src and akshare_src.is_available():
            try:
                return self._get_trade_dates_akshare(akshare_src, start_date, end_date)
            except Exception as e:
                logger.debug(f"[stock_daily] AKShare交易日历获取失败: {e}")

        return []

    def _get_trade_dates_tushare(self, source, start_date: str, end_date: str) -> List[str]:
        """从 Tushare 获取交易日历。"""
        pro = source._get_pro()
        source._rate_limit()
        df = pro.trade_cal(
            exchange='SSE',
            start_date=start_date,
            end_date=end_date,
            is_open='1',
        )
        if df is not None and not df.empty:
            dates = df['cal_date'].astype(str).tolist()
            dates.sort()
            return dates
        return []

    def _get_trade_dates_akshare(self, source, start_date: str, end_date: str) -> List[str]:
        """从 AKShare 获取交易日历。"""
        ak = source._get_ak()
        source._rate_limit()
        try:
            df = ak.tool_trade_date_hist_sina()
            if df is not None and not df.empty:
                # 列名可能是 'trade_date' 或 '日期'
                date_col = None
                for col in ['trade_date', '日期', 'date']:
                    if col in df.columns:
                        date_col = col
                        break
                if date_col:
                    df[date_col] = df[date_col].astype(str).str.replace('-', '')
                    mask = (df[date_col] >= start_date) & (df[date_col] <= end_date)
                    dates = df.loc[mask, date_col].tolist()
                    dates.sort()
                    return dates
        except Exception as e:
            logger.debug(f"[stock_daily] AKShare交易日历异常: {e}")
        return []

    @staticmethod
    def _estimate_trade_dates(start_date: str, end_date: str) -> List[str]:
        """
        粗略估算交易日（排除周六周日）。
        当所有数据源都无法获取交易日历时使用，保证批量功能仍能运行。
        """
        dates = []
        start_dt = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d')
        current = start_dt
        while current <= end_dt:
            if current.weekday() < 5:  # 0-4 是周一到周五
                dates.append(current.strftime('%Y%m%d'))
            current += timedelta(days=1)
        return dates


def get_stock_daily(
    ts_code: Optional[str] = None,
    trade_date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    symbol: Optional[str] = None,
    **kwargs,
) -> pd.DataFrame:
    """
    便捷函数：获取A股日线行情。

    Args:
        ts_code: 股票代码（tushare格式）
        trade_date: 单日日期（全市场模式）
        start_date: 开始日期
        end_date: 结束日期
        symbol: 6位股票代码

    Returns:
        pd.DataFrame — 日线数据，失败返回空DataFrame
    """
    try:
        collector = StockDailyCollector()
        result = collector.collect(
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            symbol=symbol,
            **kwargs,
        )
        return result.data if result.data is not None else pd.DataFrame()
    except Exception as e:
        logger.error(f"[stock_daily] 获取失败: {e}")
        return pd.DataFrame()


def get_market_daily(trade_date: Optional[str] = None, **kwargs) -> pd.DataFrame:
    """
    便捷函数：获取全市场某日日线行情。

    Args:
        trade_date: 交易日期（YYYYMMDD），默认当日

    Returns:
        pd.DataFrame — 全市场日线数据
    """
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y%m%d")

    return get_stock_daily(trade_date=trade_date, **kwargs)


def get_stock_daily_batch(
    ts_code_list: List[str],
    start_date: str,
    end_date: str,
    **kwargs,
) -> pd.DataFrame:
    """
    便捷函数：批量获取多只股票日线行情。

    实现策略：按交易日循环获取全市场单日行情，筛选目标股票后合并。
    对 Tushare 最经济（每天1次API调用），复用三级降级。

    Args:
        ts_code_list: 目标股票代码列表（tushare格式）
        start_date: 开始日期（YYYYMMDD）
        end_date: 结束日期（YYYYMMDD）

    Returns:
        pd.DataFrame — 批量日线数据，失败返回空DataFrame
    """
    try:
        collector = StockDailyCollector()
        result = collector.collect_batch(
            ts_code_list=ts_code_list,
            start_date=start_date,
            end_date=end_date,
            **kwargs,
        )
        return result.data if result.data is not None else pd.DataFrame()
    except Exception as e:
        logger.error(f"[stock_daily] 批量获取失败: {e}")
        return pd.DataFrame()


def get_trade_dates(start_date: str, end_date: str, exchange: str = "SSE") -> List[str]:
    """
    便捷函数：获取指定日期范围内的交易日列表。

    Args:
        start_date: 开始日期（YYYYMMDD）
        end_date: 结束日期（YYYYMMDD）
        exchange: 交易所（默认 SSE）

    Returns:
        list[str] — 交易日列表（YYYYMMDD格式）
    """
    try:
        collector = StockDailyCollector()
        dates = collector._get_trade_dates(start_date, end_date)
        if dates:
            return dates
        # 降级：用估算的交易日
        return StockDailyCollector._estimate_trade_dates(start_date, end_date)
    except Exception as e:
        logger.error(f"[stock_daily] 获取交易日历失败: {e}")
        return StockDailyCollector._estimate_trade_dates(start_date, end_date)
