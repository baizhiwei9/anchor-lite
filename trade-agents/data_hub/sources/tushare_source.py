"""
Tushare 数据源适配器
====================
二级数据源：基于 Tushare Pro API 的数据获取。
Tushare 是专业财经数据平台，数据质量高但有频率限制。

优先级：Tier 2（AKShare 失败后降级）
限制：免费版 100次/分钟，需全局串行控制，间隔 ≥ 0.55s
"""

from __future__ import annotations

import time
from typing import List, Optional

import pandas as pd

from ..base import BaseDataSource, DataResult
from ..config import (
    TUSHARE_CONFIG,
    TIMEOUT_CONFIG,
    QUALITY_THRESHOLDS,
    IP_LIMIT_RETRY_CONFIG,
    logger,
)

# 统一 Tushare 配置（含代理地址，可绕过 IP 限制）
try:
    from ..tushare_config import (
        TUSHARE_PROXY_TOKEN as TUSHARE_TOKEN,
        get_tushare_pro,
        TUSHARE_PROXY_URL,
        get_pro_mode,
        is_tushare_dead,
    )
    _TUSHARE_CONFIG_AVAILABLE = True
except ImportError:
    _TUSHARE_CONFIG_AVAILABLE = False
    get_tushare_pro = None
    TUSHARE_TOKEN = ""
    TUSHARE_PROXY_URL = ""
    get_pro_mode = None


class TushareSource(BaseDataSource):
    """Tushare 数据源适配器（二级数据源）。"""

    name: str = "tushare"
    tier: int = 2

    # 批量模式熔断配置
    # 权限类错误(401/402)：连续2次直接熔断
    BATCH_FUSE_PERMISSION_THRESHOLD = 2
    # 网络/超时类错误：连续5次才熔断（容忍临时波动）
    BATCH_FUSE_NETWORK_THRESHOLD = 5
    # 熔断冷却时间（秒）：5分钟后自动尝试恢复
    BATCH_FUSE_COOLDOWN = 300

    def __init__(self, token: Optional[str] = None):
        super().__init__()
        self._pro = None
        # 优先使用传入 token，其次使用 tushare_config 中的统一 token
        if token:
            self._token = token
        elif _TUSHARE_CONFIG_AVAILABLE and TUSHARE_TOKEN:
            self._token = TUSHARE_TOKEN
        else:
            self._token = TUSHARE_CONFIG.token
        # 首次探活结果缓存
        self._available_checked = False
        self._available = False
        # 批量模式熔断状态（按日批量获取全市场数据时使用）
        self._batch_fuse_on = False            # 是否处于熔断状态
        self._batch_fuse_ts = 0.0              # 熔断触发时间戳
        self._batch_permission_fail_count = 0  # 权限类连续失败次数
        self._batch_network_fail_count = 0     # 网络/超时类连续失败次数
        self._batch_last_error_type = ""       # 上次错误类型 ("permission"/"network"/"")

    def _get_pro(self):
        """获取 Tushare Pro 实例（全局单例，所有 TushareSource 实例共享）。

        设计说明：
        - 优先复用 tushare_config 中的全局单例 pro，确保同一进程内所有
          TushareSource 实例共享同一个连接，避免出口 IP 分散导致 IP 超限。
        - tushare_config 内部已实现自动探活 + 代理→官方降级，
          所有实例共享同一个已探活就绪的单例。
        - 最后兜底：全局单例不可用时，创建独立实例（不推荐，仅保底）。

        Returns:
            tushare.pro_api 实例
        """
        if self._pro is not None:
            return self._pro

        if _TUSHARE_CONFIG_AVAILABLE and get_tushare_pro is not None:
            try:
                # 全局单例（内部已完成探活和代理→官方降级）
                self._pro = get_tushare_pro(use_proxy=True)
                mode = get_pro_mode() if get_pro_mode else 'unknown'
                logger.info(
                    f"[tushare] 复用全局单例 Pro API (mode={mode}, id={id(self._pro)})"
                )
            except Exception as e:
                logger.warning(
                    f"[tushare] 全局单例初始化失败，回退到独立实例: {e}"
                )
                import tushare as ts
                if self._token:
                    ts.set_token(self._token)
                self._pro = ts.pro_api()
                # ⚠️ 独立实例也要走代理地址，否则官方地址会报token错误
                try:
                    self._pro._DataApi__http_url = TUSHARE_CONFIG.api_url
                except Exception:
                    pass
        else:
            # 回退到原有方式（独立实例）
            import tushare as ts
            if self._token:
                ts.set_token(self._token)
            self._pro = ts.pro_api()
            # ⚠️ 独立实例也要走代理地址，否则官方地址会报token错误
            try:
                self._pro._DataApi__http_url = TUSHARE_CONFIG.api_url
            except Exception:
                pass

        return self._pro

    def _rate_limit(self):
        """全局串行控制 Tushare 调用间隔。"""
        # 方案2：首次调用前随机抖动，错开不同任务并发（只执行一次）
        TUSHARE_CONFIG.wait_initial_jitter()
        # 全局频率控制
        TUSHARE_CONFIG.wait_for_rate_limit()

    def _call_pro_api(self, api_name: str, **params):
        """
        Tushare Pro API 统一调用封装（含 IP 超限自动重试 + 首次调用抖动）。

        方案1：遇到 IP 数量超限时，利用沙箱多 IP 轮动特性，
              等待 3s/5s/8s 后重试，最多 3 次。
        方案2：首次调用前加随机抖动（0~60 秒），错开不同定时任务的并发时间。

        Args:
            api_name: Tushare Pro 接口方法名（如 'index_weight', 'daily'）
            **params: 接口参数

        Returns:
            API 返回结果（通常为 DataFrame）

        Raises:
            所有重试后仍然失败时，抛出原始异常（交由上层捕获包装为 DataResult）
        """
        pro = self._get_pro()
        api_func = getattr(pro, api_name, None)
        if api_func is None:
            raise AttributeError(f"Tushare Pro 接口不存在: {api_name}")

        last_exception = None
        max_attempts = 1  # 首次调用
        if IP_LIMIT_RETRY_CONFIG.enabled:
            max_attempts += IP_LIMIT_RETRY_CONFIG.max_retries

        for attempt in range(max_attempts):
            try:
                # 全局频率控制
                self._rate_limit()
                result = api_func(**params)
                if attempt > 0:
                    logger.info(
                        f"[tushare] {api_name} 第 {attempt + 1} 次重试成功 "
                        f"(IP 超限重试机制生效)"
                    )
                return result

            except Exception as e:
                last_exception = e
                error_msg = str(e)

                # 方案1：判断是否为 IP 超限错误，是则等待后重试
                if (
                    IP_LIMIT_RETRY_CONFIG.enabled
                    and attempt < IP_LIMIT_RETRY_CONFIG.max_retries
                    and IP_LIMIT_RETRY_CONFIG.is_ip_limit_error(error_msg)
                ):
                    wait_sec = IP_LIMIT_RETRY_CONFIG.retry_intervals[
                        min(attempt, len(IP_LIMIT_RETRY_CONFIG.retry_intervals) - 1)
                    ]
                    logger.warning(
                        f"[tushare] {api_name} 触发 IP 超限错误 "
                        f"(attempt {attempt + 1}/{IP_LIMIT_RETRY_CONFIG.max_retries}), "
                        f"等待 {wait_sec}s 后重试: {error_msg}"
                    )
                    time.sleep(wait_sec)
                    continue

                # 非 IP 超限错误，或已达最大重试次数，直接抛出
                raise

        # 理论上不会走到这里（所有循环要么 return 要么 raise）
        if last_exception:
            raise last_exception
        raise RuntimeError(f"[tushare] {api_name} 调用异常结束")

    def is_available(self) -> bool:
        """检查 Tushare 是否可用（带探活 + 缓存 + dead 模式快速失败）。

        说明：
        - 如果 is_tushare_dead() 返回 True（两个 token 都失效），直接返回 False
        - 否则 _get_pro() 内部已完成代理探活和降级
        """
        if self._available_checked:
            return self._available
        try:
            # 快速判断：两个 token 都失效时直接返回不可用
            if _TUSHARE_CONFIG_AVAILABLE and is_tushare_dead is not None and is_tushare_dead():
                mode = get_pro_mode() if get_pro_mode else 'dead'
                logger.warning(
                    f"[tushare] 数据源不可用: mode={mode} (代理和官方token均失效)"
                )
                self._available = False
                self._available_checked = True
                return False
            pro = self._get_pro()
            self._available = pro is not None
        except Exception as e:
            logger.warning(f"[tushare] 数据源不可用: {e}")
            self._available = False
        self._available_checked = True
        return self._available

    def _check_available(self) -> bool:
        """兼容方法：检查 Tushare 是否可用（与 is_available 等价，用于部分采集器调用）。

        不可用时返回 False，上层采集器会优雅跳过并降级到下一层数据源。
        """
        return self.is_available()

    def _throttle(self):
        """兼容方法：速率控制（即 _rate_limit 的别名）。

        部分较老的 fetch_ 方法使用 _throttle 命名，此处做别名兼容。
        """
        self._rate_limit()

    # ============================================================
    # 批量模式熔断机制（按日批量获取全市场日线时使用）
    # ============================================================

    def _classify_error(self, error_msg: str) -> str:
        """
        错误类型分类：
        - permission: 权限/积分/Token 错误（401/402类）
        - network: 网络/超时/连接错误
        - other: 其他业务错误

        不同错误类型的熔断阈值不同：权限类更敏感，网络类更宽容。
        """
        if not error_msg:
            return "other"
        msg_lower = error_msg.lower()

        permission_keywords = [
            "401", "402",
            "权限", "无权", "没有权限", "访问被拒绝",
            "积分", "分数不足", "积分不足", "不够积分",
            "token", "令牌", "invalid token", "wrong token",
            "未授权", "未开通", "无权限",
            "account", "user not exist",
        ]
        network_keywords = [
            "timeout", "timed out", "超时",
            "connection", "连接", "connect",
            "network", "网络",
            "502", "503", "504",
            "server error", "服务端错误",
            "reset", "重置", "断开",
        ]

        for kw in permission_keywords:
            if kw.lower() in msg_lower:
                return "permission"
        for kw in network_keywords:
            if kw.lower() in msg_lower:
                return "network"
        return "other"

    def _check_batch_fuse(self) -> bool:
        """
        检查批量模式是否处于熔断状态，如冷却期已过则自动恢复。

        Returns:
            True 表示熔断中（不可调用），False 表示可以调用
        """
        if not self._batch_fuse_on:
            return False

        elapsed = time.time() - self._batch_fuse_ts
        if elapsed >= self.BATCH_FUSE_COOLDOWN:
            # 冷却期已过，自动恢复
            logger.info(
                f"[tushare] 批量模式熔断冷却期已过 ({elapsed:.0f}s >= "
                f"{self.BATCH_FUSE_COOLDOWN}s)，自动恢复"
            )
            self._batch_fuse_on = False
            self._batch_permission_fail_count = 0
            self._batch_network_fail_count = 0
            self._batch_last_error_type = ""
            return False

        logger.debug(
            f"[tushare] 批量模式仍在熔断中 (剩余 "
            f"{self.BATCH_FUSE_COOLDOWN - elapsed:.0f}s)"
        )
        return True

    def _record_batch_failure(self, error_msg: str):
        """
        记录一次批量模式失败，若达到阈值则触发熔断。

        Args:
            error_msg: 错误信息
        """
        err_type = self._classify_error(error_msg)
        self._batch_last_error_type = err_type

        if err_type == "permission":
            # 权限类：累加权限计数，重置网络计数
            self._batch_permission_fail_count += 1
            self._batch_network_fail_count = 0
            if self._batch_permission_fail_count >= self.BATCH_FUSE_PERMISSION_THRESHOLD:
                self._trigger_batch_fuse("permission", error_msg)
        elif err_type == "network":
            # 网络类：累加网络计数，权限计数保持（不重置，避免伪装）
            self._batch_network_fail_count += 1
            if self._batch_network_fail_count >= self.BATCH_FUSE_NETWORK_THRESHOLD:
                self._trigger_batch_fuse("network", error_msg)
        else:
            # 其他错误：不累计熔断计数，仅记录
            pass

    def _record_batch_success(self):
        """记录一次批量模式成功，重置所有失败计数。"""
        if self._batch_permission_fail_count > 0 or self._batch_network_fail_count > 0:
            logger.debug(
                f"[tushare] 批量模式成功，重置失败计数 "
                f"(permission={self._batch_permission_fail_count}, "
                f"network={self._batch_network_fail_count})"
            )
        self._batch_permission_fail_count = 0
        self._batch_network_fail_count = 0
        self._batch_last_error_type = ""

    def _trigger_batch_fuse(self, err_type: str, error_msg: str):
        """触发批量模式熔断。"""
        self._batch_fuse_on = True
        self._batch_fuse_ts = time.time()
        logger.warning(
            f"[tushare] 批量模式触发熔断 (类型={err_type}, "
            f"冷却={self.BATCH_FUSE_COOLDOWN}s): {error_msg}"
        )

    # ============================================================
    # 全市场日线批量获取（按日循环）
    # ============================================================

    def _fetch_stock_daily_batch_all(
        self,
        start_date: str,
        end_date: str,
    ) -> DataResult:
        """
        全市场多日日线批量获取：按交易日循环，每天一次 pro.daily(trade_date=...)。

        优势：
        - N天只需N次调用，而不是5500×N次逐只调用
        - 每次调用返回当日全市场所有股票，数据完整
        - 走批量模式独立熔断，不影响逐只模式

        Args:
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.DataFrame — 合并后的全市场日线数据
        """
        # 先检查熔断
        if self._check_batch_fuse():
            return DataResult(
                data_type="stock_daily",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="批量模式处于熔断状态，跳过",
                meta={"fuse": "on"},
            )

        pro = self._get_pro()

        # 1. 获取交易日历
        try:
            self._rate_limit()
            cal_df = pro.trade_cal(
                exchange='SSE',
                start_date=start_date,
                end_date=end_date,
                is_open='1',
            )
            if cal_df is None or cal_df.empty:
                return DataResult(
                    data_type="stock_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="交易日历获取为空",
                )
            trade_dates = sorted(cal_df['cal_date'].astype(str).tolist())
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            self._record_batch_failure(error_msg)
            return DataResult(
                data_type="stock_daily",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"交易日历获取失败: {error_msg}",
            )

        if not trade_dates:
            return DataResult(
                data_type="stock_daily",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"日期范围内无交易日 ({start_date} ~ {end_date})",
            )

        logger.info(
            f"[tushare] 全市场日线批量获取: {start_date} ~ {end_date}, "
            f"共 {len(trade_dates)} 个交易日"
        )

        # 2. 按交易日循环获取
        all_dfs = []
        success_days = 0
        failed_days = 0

        for i, td in enumerate(trade_dates):
            try:
                self._rate_limit()
                df = pro.daily(trade_date=td)
                if df is not None and not df.empty:
                    all_dfs.append(df)
                    success_days += 1
                    self._record_batch_success()
                else:
                    failed_days += 1
                    logger.debug(f"[tushare] {td} 日线数据为空")
            except Exception as e:
                failed_days += 1
                error_msg = f"{type(e).__name__}: {e}"
                self._record_batch_failure(error_msg)
                logger.warning(f"[tushare] {td} 日线获取失败: {error_msg}")
                # 如果已经熔断，剩余天数不再尝试
                if self._batch_fuse_on:
                    logger.warning(
                        f"[tushare] 批量模式已熔断，跳过剩余 "
                        f"{len(trade_dates) - i - 1} 天"
                    )
                    break

            # 每10天打印一次进度
            if (i + 1) % 10 == 0:
                logger.info(
                    f"[tushare] 批量进度: {i+1}/{len(trade_dates)}, "
                    f"成功{success_days}天, 失败{failed_days}天"
                )

        # 3. 合并结果
        if all_dfs:
            result_df = pd.concat(all_dfs, ignore_index=True)
            total_records = len(result_df)

            logger.info(
                f"[tushare] 全市场日线批量完成: {total_records} 条记录, "
                f"成功{success_days}/{len(trade_dates)}天"
            )

            # 如果有失败天数且熔断未触发，也返回成功数据（部分成功）
            return DataResult(
                data=result_df,
                data_type="stock_daily",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "mode": "batch_all_market",
                    "strategy": "daily_batch",
                    "total_records": total_records,
                    "trade_days": len(trade_dates),
                    "success_days": success_days,
                    "failed_days": failed_days,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )

        # 全部失败
        return DataResult(
            data_type="stock_daily",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error=(
                f"全市场批量获取全部失败 ({success_days}/{len(trade_dates)}天成功)，"
                f"最后错误类型: {self._batch_last_error_type}"
            ),
            meta={
                "mode": "batch_all_market",
                "strategy": "daily_batch",
                "success_days": success_days,
                "failed_days": failed_days,
                "fuse_on": self._batch_fuse_on,
            },
        )

    # ============================================================
    # 指数成分股
    # ============================================================

    def fetch_index_components(self, index_code: str, **kwargs) -> DataResult:
        """
        获取指数成分股（使用 index_weight 接口）。

        Args:
            index_code: 指数代码（tushare格式，如 000300.SH）

        Returns:
            DataResult.data: list[str] — tushare格式代码列表
        """
        try:
            pro = self._get_pro()
            self._rate_limit()

            df = pro.index_weight(index_code=index_code)

            if df is None or df.empty:
                return DataResult(
                    data_type="index_components",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"指数 {index_code} 成分股数据为空",
                )

            # 取最新交易日的成分股
            if 'trade_date' in df.columns and len(df['trade_date'].unique()) > 1:
                latest_date = df['trade_date'].max()
                df_latest = df[df['trade_date'] == latest_date]
            else:
                df_latest = df

            # 提取成分股代码
            code_col = None
            for candidate in ['con_code', 'const_code', '成分券代码', 'ts_code']:
                if candidate in df_latest.columns:
                    code_col = candidate
                    break

            if code_col is None:
                return DataResult(
                    data_type="index_components",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"无法识别成分股代码列，可用列: {list(df_latest.columns)}",
                )

            codes = df_latest[code_col].dropna().astype(str).tolist()
            codes = [c for c in codes if c and len(c) >= 6]

            # 去重
            codes = list(set(codes))

            meta = {
                "index_code": index_code,
                "latest_date": df_latest['trade_date'].iloc[0] if 'trade_date' in df_latest.columns and not df_latest.empty else "",
                "raw_count": len(df_latest),
                "column": code_col,
            }

            return DataResult(
                data=codes,
                data_type="index_components",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="index_components",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # 期权数据
    # ============================================================

    def fetch_option_data(
        self,
        exchange: str,
        trade_date: Optional[str] = None,
        underlying: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取期权数据（合约基本信息 + 日线行情）。

        Args:
            exchange: 交易所代码（SSE/SZSE/CFFEX）
            trade_date: 交易日期（YYYYMMDD格式）
            underlying: 标的代码（可选）

        Returns:
            DataResult.data: dict with keys:
                - contracts: pd.DataFrame 合约基本信息
                - daily: pd.DataFrame 日线行情
                - underlying_price: None（tushare期权接口不含现货价）
        """
        try:
            pro = self._get_pro()
            exchange_upper = exchange.upper()

            result = {
                "contracts": pd.DataFrame(),
                "daily": pd.DataFrame(),
                "underlying_price": None,
            }

            # 1. 获取合约基本信息
            try:
                self._rate_limit()
                opt_list = pro.opt_basic(exchange=exchange_upper)
                if opt_list is not None and not opt_list.empty:
                    # 按标的筛选
                    if underlying:
                        for col in ['call_code', 'ts_code', 'opt_code']:
                            if col in opt_list.columns:
                                # 尝试通过合约代码前缀匹配标的
                                break
                        # 简单通过标的名称或代码匹配
                        for col in ['name', '标的名称', 'opt_name']:
                            if col in opt_list.columns:
                                opt_list = opt_list[
                                    opt_list[col].astype(str).str.contains(
                                        underlying, na=False
                                    )
                                ]
                                break
                    result["contracts"] = opt_list
            except Exception as e:
                logger.debug(f"[tushare] opt_basic({exchange_upper}) 失败: {e}")

            # 2. 获取日线行情
            if trade_date:
                try:
                    self._rate_limit()
                    daily_df = pro.opt_daily(
                        exchange=exchange_upper,
                        start_date=trade_date,
                        end_date=trade_date,
                    )
                    if daily_df is not None and not daily_df.empty:
                        result["daily"] = daily_df
                except Exception as e:
                    logger.debug(f"[tushare] opt_daily({exchange_upper}) 失败: {e}")

            has_data = (
                not result["contracts"].empty or not result["daily"].empty
            )

            if not has_data:
                return DataResult(
                    data_type="option_data",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"交易所 {exchange_upper} 期权数据为空",
                )

            meta = {
                "exchange": exchange_upper,
                "underlying": underlying or "",
                "trade_date": trade_date or "",
                "contract_count": len(result["contracts"]),
                "daily_count": len(result["daily"]),
            }

            return DataResult(
                data=result,
                data_type="option_data",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="option_data",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # A股日线行情
    # ============================================================

    # 常见指数代码列表（用于识别指数代码，走 index_daily 接口）
    _INDEX_CODES = {
        # 上证系列
        '000001.SH', '000002.SH', '000003.SH', '000004.SH', '000005.SH',
        '000006.SH', '000007.SH', '000008.SH', '000009.SH', '000010.SH',
        '000016.SH',  # 上证50
        '000300.SH',  # 沪深300
        '000905.SH',  # 中证500
        '000852.SH',  # 中证1000
        '000688.SH',  # 科创50
        '000906.SH',  # 中证800
        # 深证系列
        '399001.SZ',  # 深证成指
        '399006.SZ',  # 创业板指
        '399106.SZ',  # 深证综指
        '399005.SZ',  # 中小板指
        '399300.SZ',  # 沪深300(深)
        '399905.SZ',  # 中证500(深)
    }

    def _is_index_code(self, ts_code: Optional[str]) -> bool:
        """判断是否为指数代码。"""
        if not ts_code:
            return False
        # 多只股票用逗号分隔时，只要有一个是指数就认为是指数查询（少见情况）
        codes = [c.strip() for c in ts_code.split(',')]
        for code in codes:
            if code in self._INDEX_CODES:
                return True
        return False

    def fetch_stock_daily(
        self,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        trade_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取A股日线行情（支持个股/指数/全市场）。

        路由策略：
        1. 全市场多日（无 ts_code，有 start_date + end_date，无 trade_date）
           → 走按日批量模式 _fetch_stock_daily_batch_all
           → 批量模式失败时回退到逐只模式（仅当指定了 ts_code 列表时）
        2. 全市场单日（无 ts_code，有 trade_date）
           → 直接调用 pro.daily(trade_date=...)
        3. 单只/多只股票（有 ts_code）
           → 直接调用 pro.daily(ts_code=...) + 指数 fallback

        Args:
            ts_code: 股票/指数代码（tushare格式），单只或多只；None表示全市场
            trade_date: 单日日期（YYYYMMDD），用于全市场或单只
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.DataFrame — 日线行情数据
        """
        # ===== 路径1：全市场多日 → 按日批量模式 =====
        if (
            not ts_code
            and not trade_date
            and start_date
            and end_date
        ):
            logger.info(
                f"[tushare] fetch_stock_daily: 全市场多日批量模式 "
                f"({start_date} ~ {end_date})"
            )
            batch_result = self._fetch_stock_daily_batch_all(
                start_date=start_date,
                end_date=end_date,
            )
            if batch_result.success:
                return batch_result
            # 批量模式失败，返回失败结果（让上层降级到其他源）
            logger.warning(
                f"[tushare] 全市场批量模式失败: {batch_result.error}"
            )
            return batch_result

        # ===== 路径2 & 3：单只/指数/全市场单日 → 原有逻辑 =====
        try:
            pro = self._get_pro()

            params = {}
            if ts_code:
                params["ts_code"] = ts_code
            if trade_date:
                params["trade_date"] = trade_date
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date

            df = None
            api_used = "daily"

            # 判断是否是指数代码
            is_index = self._is_index_code(ts_code)

            if is_index:
                # 指数走 index_daily 接口
                self._rate_limit()
                try:
                    df = pro.index_daily(**params)
                    api_used = "index_daily"
                except Exception as idx_e:
                    logger.debug(f"[tushare] index_daily 失败，回退到daily: {idx_e}")
                    df = None

            # 如果指数接口失败或不是指数，尝试 daily 接口
            if df is None or df.empty:
                if not is_index:
                    self._rate_limit()
                try:
                    df = pro.daily(**params)
                    api_used = "daily"
                except Exception as daily_e:
                    # 如果 daily 也失败，且还没试过 index_daily，再试一次
                    if not is_index and ts_code and '.' in ts_code:
                        # 可能是未识别的指数代码
                        self._rate_limit()
                        try:
                            df = pro.index_daily(**params)
                            api_used = "index_daily"
                        except Exception:
                            raise daily_e
                    else:
                        raise daily_e

            if df is None or df.empty:
                return DataResult(
                    data_type="stock_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"日线数据为空 (params={params}, api={api_used})",
                )

            meta = {
                "ts_code": ts_code or "all",
                "trade_date": trade_date or "",
                "start_date": start_date or "",
                "end_date": end_date or "",
                "record_count": len(df),
                "api": api_used,
                "is_index": is_index,
            }

            return DataResult(
                data=df,
                data_type="stock_daily",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="stock_daily",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )


    # ============================================================
    # 股指期货数据（基差）
    # ============================================================

    def fetch_futures_basis(
        self,
        trade_date: Optional[str] = None,
        products: Optional[list] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取股指期货基差数据（Tushare fut_daily + index_daily）。

        Args:
            trade_date: 交易日期（YYYYMMDD）
            products: 期货品种列表，默认 ['IF', 'IC', 'IM']

        Returns:
            DataResult.data: list[dict] — 基差数据列表
        """
        try:
            pro = self._get_pro()
            if products is None:
                products = ['IF', 'IC', 'IM']

            # 指数代码映射
            index_map = {
                'IF': {'ts_code': '000300.SH', 'name': '沪深300'},
                'IC': {'ts_code': '000905.SH', 'name': '中证500'},
                'IM': {'ts_code': '000852.SH', 'name': '中证1000'},
            }

            result_list = []

            # 1. 获取各指数现货价格
            spot_prices = {}
            for product in products:
                if product not in index_map:
                    continue
                try:
                    self._rate_limit()
                    idx_ts_code = index_map[product]['ts_code']
                    df_idx = pro.index_daily(
                        ts_code=idx_ts_code,
                        start_date=trade_date,
                        end_date=trade_date,
                        fields='trade_date,close',
                    )
                    if df_idx is not None and not df_idx.empty:
                        spot_prices[product] = float(df_idx.iloc[0]['close'])
                except Exception as e:
                    logger.debug(f"[tushare] 指数现货 {product} 获取失败: {e}")

            # 2. 获取各品种期货当月连续价格
            for product in products:
                if product not in index_map:
                    continue

                futures_price = None
                contract_type = "当月连续"

                try:
                    self._rate_limit()
                    # 获取当月连续合约（用主力合约或当月合约）
                    # Tushare fut_daily 需要具体合约代码
                    # 先获取品种合约列表，找当月或主力
                    from datetime import datetime as _dt
                    now = _dt.now()
                    year = now.year
                    month = now.month

                    # 尝试不同的合约月份格式
                    # CFFEX股指期货: IF2407 格式
                    contract_codes = [
                        f"{product}{year % 100:02d}{month:02d}.CFX",
                        f"{product}{year % 100:02d}{month:02d}.CFFEX",
                    ]

                    for contract_code in contract_codes:
                        try:
                            df_fut = pro.fut_daily(
                                ts_code=contract_code,
                                start_date=trade_date,
                                end_date=trade_date,
                                fields='trade_date,close',
                            )
                            if df_fut is not None and not df_fut.empty:
                                futures_price = float(df_fut.iloc[0]['close'])
                                contract_type = f"{year % 100:02d}{month:02d}"
                                break
                        except Exception:
                            continue

                    # 如果当月合约没数据，尝试获取全部合约找最新
                    if futures_price is None:
                        try:
                            self._rate_limit()
                            df_basic = pro.fut_basic(
                                exchange='CFFEX',
                                fut_type='1',  # 股指期货
                            )
                            if df_basic is not None and not df_basic.empty:
                                # 筛选对应品种
                                prod_df = df_basic[df_basic['ts_code'].str.startswith(product, na=False)]
                                if not prod_df.empty:
                                    # 找未到期且最近的合约
                                    from datetime import datetime as _dt2
                                    today = _dt2.now().strftime('%Y%m%d')
                                    if 'delist_date' in prod_df.columns:
                                        active = prod_df[prod_df['delist_date'] >= today]
                                        if not active.empty:
                                            active = active.sort_values('delist_date')
                                            target_code = active.iloc[0]['ts_code']
                                            self._rate_limit()
                                            df_fut = pro.fut_daily(
                                                ts_code=target_code,
                                                start_date=trade_date,
                                                end_date=trade_date,
                                                fields='trade_date,close',
                                            )
                                            if df_fut is not None and not df_fut.empty:
                                                futures_price = float(df_fut.iloc[0]['close'])
                                                contract_type = target_code.split('.')[0][-4:]
                        except Exception as e:
                            logger.debug(f"[tushare] 期货 {product} 合约列表查询失败: {e}")

                except Exception as e:
                    logger.debug(f"[tushare] 期货 {product} 获取失败: {e}")

                if futures_price is None or product not in spot_prices:
                    continue

                spot_price = spot_prices[product]
                if spot_price <= 0 or futures_price <= 0:
                    continue

                basis = futures_price - spot_price
                basis_rate = basis / spot_price * 100

                # 年化基差率
                days_to_expiry = 60
                basis_rate_annual = basis_rate * (365.0 / days_to_expiry)

                result_list.append({
                    'product': product,
                    'index_name': index_map[product]['name'],
                    'contract_type': contract_type,
                    'futures_price': round(futures_price, 2),
                    'spot_price': round(spot_price, 2),
                    'basis': round(basis, 2),
                    'basis_rate': round(basis_rate, 2),
                    'basis_rate_annual': round(basis_rate_annual, 2),
                    'rank_252': None,
                })

            if not result_list:
                return DataResult(
                    data_type="futures_basis",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare 股指期货基差数据为空",
                )

            meta = {
                "trade_date": trade_date or "",
                "products": products,
                "count": len(result_list),
            }

            return DataResult(
                data=result_list,
                data_type="futures_basis",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="futures_basis",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # 单个期货合约历史日线（指定具体合约，非主力连续）
    # ============================================================

    def fetch_futures_contract_daily(
        self,
        contract: Optional[str] = None,
        sina_symbol: Optional[str] = None,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取单个具体期货合约的历史日线（Tushare fut_daily）。

        Args:
            contract: 原始合约代码（如 IF2609 或 IF2609.CFX）
            sina_symbol: 新浪合约代码（如 IF2609），仅用于推断
            ts_code: tushare 合约代码（如 IF2609.CFX），优先使用
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.DataFrame
                标准列 date(YYYYMMDD), open, high, low, close, volume, oi，按 date 升序
        """
        try:
            from ..collectors.futures_contract_daily import normalize_futures_contract_df

            pro = self._get_pro()

            # 确定 tushare 合约代码，兼容 .CFFEX/.CFX 两种写法
            code = (ts_code or contract or "").strip().upper()
            if not code:
                return DataResult(
                    data_type="futures_contract_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="合约代码为空",
                )
            candidates = [code]
            if code.endswith('.CFX'):
                candidates.append(code[:-4] + '.CFFEX')
            elif code.endswith('.CFFEX'):
                candidates.append(code[:-6] + '.CFX')
            elif '.' not in code:
                candidates.append(code + '.CFX')
                candidates.append(code + '.CFFEX')

            df = None
            used_code = None
            last_err = ""
            for cand in candidates:
                try:
                    params = {"ts_code": cand}
                    if start_date:
                        params["start_date"] = start_date
                    if end_date:
                        params["end_date"] = end_date
                    params["fields"] = (
                        "ts_code,trade_date,open,high,low,close,vol,oi"
                    )
                    self._rate_limit()  # 全局串行限流（≥0.55s 间隔）
                    df = pro.fut_daily(**params)
                    if df is not None and not df.empty:
                        used_code = cand
                        break
                    last_err = f"fut_daily({cand}) 返回空"
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
                    df = None
                    continue

            if df is None or df.empty:
                return DataResult(
                    data_type="futures_contract_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"Tushare 合约 {code} 日线数据为空 ({last_err})",
                )

            # trade_date→date, vol→volume（normalize 统一处理），oi 已存在
            df = normalize_futures_contract_df(df)

            # fut_daily 已按 start/end 过滤，这里再做一次边界保护
            if start_date and not df.empty:
                df = df[df['date'] >= str(start_date)]
            if end_date and not df.empty:
                df = df[df['date'] <= str(end_date)]

            if df.empty:
                return DataResult(
                    data_type="futures_contract_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"合约 {code} 在 {start_date or '-'}~{end_date or '-'} 区间无数据",
                )

            df = df.reset_index(drop=True)

            return DataResult(
                data=df,
                data_type="futures_contract_daily",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "contract": contract or code,
                    "ts_code": used_code or code,
                    "start_date": start_date or "",
                    "end_date": end_date or "",
                    "api": "fut_daily",
                    "count": len(df),
                },
            )
        except Exception as e:
            return DataResult(
                data_type="futures_contract_daily",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )


    # ============================================================
    # 利率数据
    # ============================================================

    def fetch_interest_rate(
        self,
        rate_type: str = "shibor",
        **kwargs,
    ) -> DataResult:
        """
        获取利率数据。

        Args:
            rate_type: 利率类型（shibor / lpr / hibor / bond_yield）

        Returns:
            DataResult.data — 利率数据
        """
        rate_type = rate_type.lower()

        try:
            if rate_type == "shibor":
                return self._fetch_shibor()
            elif rate_type == "lpr":
                return self._fetch_lpr()
            elif rate_type == "hibor":
                # Tushare 无 HIBOR 接口
                return DataResult(
                    data_type="hibor",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare 不支持 HIBOR 数据",
                )
            elif rate_type == "bond_yield":
                return self._fetch_bond_yield()
            elif rate_type == "cd_rate_1y":
                return self._fetch_cd_rate_1y(**kwargs)
            else:
                return DataResult(
                    data_type=f"interest_rate_{rate_type}",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"不支持的利率类型: {rate_type}",
                )
        except Exception as e:
            return DataResult(
                data_type=f"interest_rate_{rate_type}",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def _fetch_shibor(self) -> DataResult:
        """获取 SHIBOR 利率（Tushare shibor 接口）。"""
        try:
            pro = self._get_pro()
            self._rate_limit()

            # 获取最近的 SHIBOR 数据
            df = pro.shibor()
            if df is None or df.empty:
                return DataResult(
                    data_type="shibor",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare SHIBOR 数据为空",
                )

            # 按日期排序，取最新
            if 'date' in df.columns:
                df = df.sort_values('date', ascending=False)
                latest_row = df.iloc[0]
                latest_date = str(latest_row['date']).replace('-', '')
            else:
                latest_row = df.iloc[0]
                latest_date = ""

            # 提取各期限利率
            terms_map = {
                '1Y': '1y', '9M': '9m', '6M': '6m', '3M': '3m',
                '1M': '1m', '2W': '2w', '1W': '1w', 'ON': 'on',
            }

            latest_rates = {}
            for display_term, ts_term in terms_map.items():
                if ts_term in df.columns:
                    try:
                        latest_rates[display_term] = float(latest_row[ts_term])
                    except (ValueError, TypeError):
                        latest_rates[display_term] = None

            # 构建历史 DataFrame
            history_df = df.copy()
            if 'date' in history_df.columns:
                history_df = history_df.rename(columns={'date': 'trade_date'})
                history_df['trade_date'] = history_df['trade_date'].astype(str).str.replace('-', '')

            result = {
                "latest": latest_rates,
                "latest_date": latest_date,
                "history": history_df,
                "terms": list(latest_rates.keys()),
            }

            meta = {
                "latest_date": latest_date,
                "history_days": len(df),
            }

            return DataResult(
                data=result,
                data_type="shibor",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="shibor",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare SHIBOR 获取失败: {type(e).__name__}: {e}",
            )

    def _fetch_lpr(self) -> DataResult:
        """获取 LPR 利率（Tushare shibor_lpr 接口）。"""
        try:
            pro = self._get_pro()
            self._rate_limit()

            df = pro.shibor_lpr()
            if df is None or df.empty:
                return DataResult(
                    data_type="lpr",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare LPR 数据为空",
                )

            # 按日期排序
            if 'date' in df.columns:
                df = df.sort_values('date', ascending=False)
                latest_row = df.iloc[0]
                latest_date = str(latest_row['date']).replace('-', '')
            else:
                latest_row = df.iloc[0]
                latest_date = ""

            # 提取 LPR1Y 和 LPR5Y
            latest_rates = {}
            if '1y' in df.columns:
                try:
                    latest_rates['LPR1Y'] = float(latest_row['1y'])
                except (ValueError, TypeError):
                    pass
            if '5y' in df.columns:
                try:
                    latest_rates['LPR5Y'] = float(latest_row['5y'])
                except (ValueError, TypeError):
                    pass

            # 构建历史 DataFrame
            history_df = df.copy()
            if 'date' in history_df.columns:
                history_df = history_df.rename(columns={'date': 'trade_date'})
                history_df['trade_date'] = history_df['trade_date'].astype(str).str.replace('-', '')

            result = {
                "latest": latest_rates,
                "latest_date": latest_date,
                "history": history_df,
            }

            meta = {
                "latest_date": latest_date,
                "history_days": len(df),
            }

            return DataResult(
                data=result,
                data_type="lpr",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="lpr",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare LPR 获取失败: {type(e).__name__}: {e}",
            )

    def _fetch_bond_yield(self) -> DataResult:
        """获取国债收益率曲线（Tushare yc_cb 接口）。"""
        try:
            pro = self._get_pro()
            self._rate_limit()

            # 获取中债收益率曲线
            df = pro.yc_cb(
                curve_type='0',  # 国债收益率曲线
                field='trade_date,curve_type,bond_year,yield',
            )
            if df is None or df.empty:
                return DataResult(
                    data_type="bond_yield",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare 国债收益率数据为空",
                )

            meta = {
                "record_count": len(df),
            }

            return DataResult(
                data=df,
                data_type="bond_yield",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="bond_yield",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare 国债收益率获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 同业存单利率（cd_rate_1y）
    # ============================================================

    CD_RATE_CURVE_TYPE = "10"  # 中债商业银行普通债收益率曲线(AAA) 对应代码（需确认）

    def _fetch_cd_rate_1y(self, start_date: Optional[str] = None, end_date: Optional[str] = None, **kwargs) -> DataResult:
        """
        获取1年期同业存单利率（Tushare yc_cb 接口）。

        注意：yc_cb 接口需要积分权限，免费版可能无法访问。
        曲线类型：商业银行普通债(AAA) - 需确认正确的 curve_type 代码。

        Args:
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: dict with latest, latest_date, curve_name, history
        """
        try:
            pro = self._get_pro()

            params = {}
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date

            # 尝试多种曲线类型代码找商业银行普通债(AAA)
            # curve_type 参考：0=国债, 1=地方政府债, 2=央行票据, 3=政策性银行债,
            #                   4=商业银行债, 5=中期票据, ...
            # 先尝试 curve_type=4 (商业银行债)，如果失败再尝试其他
            curve_types_to_try = ['4', '10', '11', '12']
            df = None
            curve_type_used = None

            for ct in curve_types_to_try:
                try:
                    self._rate_limit()
                    params["curve_type"] = ct
                    df = pro.yc_cb(**params)
                    if df is not None and not df.empty:
                        curve_type_used = ct
                        # 筛选1年期
                        if 'bond_year' in df.columns:
                            df_1y = df[df['bond_year'] == 1.0]
                            if not df_1y.empty:
                                df = df_1y
                                break
                except Exception:
                    continue

            if df is None or df.empty:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare 同业存单利率数据为空（可能无yc_cb接口权限）",
                )

            # 按日期排序取最新
            if 'trade_date' in df.columns:
                df = df.sort_values('trade_date', ascending=False)
                latest_row = df.iloc[0]
                latest_date = str(latest_row['trade_date'])
                latest_rate = float(latest_row['yield']) if 'yield' in df.columns else None
            else:
                latest_row = df.iloc[0]
                latest_date = ""
                latest_rate = float(latest_row['yield']) if 'yield' in df.columns else None

            if latest_rate is None:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare yc_cb 返回数据中无 yield 字段",
                )

            # 构建历史 DataFrame
            history_df = pd.DataFrame()
            if 'trade_date' in df.columns and 'yield' in df.columns:
                history_df = df[['trade_date', 'yield']].copy()
                history_df = history_df.sort_values('trade_date', ascending=False).reset_index(drop=True)

            result = {
                "latest": {"1Y": latest_rate},
                "latest_date": latest_date,
                "curve_name": f"Tushare yc_cb curve_type={curve_type_used}",
                "period": "1年",
                "history": history_df,
            }

            meta = {
                "curve_type": curve_type_used,
                "period": "1年",
                "latest_date": latest_date,
                "latest_rate": latest_rate,
                "history_days": len(history_df),
            }

            return DataResult(
                data=result,
                data_type="cd_rate_1y",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="cd_rate_1y",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare 同业存单利率获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 同业存单利率（cd_rate_1y）
    # ============================================================

    CD_RATE_CURVE_TYPE = "10"  # 中债商业银行普通债收益率曲线(AAA) 对应代码（需确认）

    def _fetch_cd_rate_1y(self, start_date=None, end_date=None, **kwargs):
        """
        获取1年期同业存单利率（Tushare yc_cb 接口）。

        注意：yc_cb 接口需要积分权限，免费版可能无法访问。
        曲线类型：商业银行普通债(AAA) - 需确认正确的 curve_type 代码。

        Args:
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: dict with latest, latest_date, curve_name, history
        """
        try:
            pro = self._get_pro()

            params = {}
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date

            # 尝试多种曲线类型代码找商业银行普通债(AAA)
            curve_types_to_try = ['4', '10', '11', '12']
            df = None
            curve_type_used = None

            for ct in curve_types_to_try:
                try:
                    self._rate_limit()
                    params["curve_type"] = ct
                    df = pro.yc_cb(**params)
                    if df is not None and not df.empty:
                        curve_type_used = ct
                        # 筛选1年期
                        if 'bond_year' in df.columns:
                            df_1y = df[df['bond_year'] == 1.0]
                            if not df_1y.empty:
                                df = df_1y
                                break
                except Exception:
                    continue

            if df is None or df.empty:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare 同业存单利率数据为空（可能无yc_cb接口权限）",
                )

            # 按日期排序取最新
            if 'trade_date' in df.columns:
                df = df.sort_values('trade_date', ascending=False)
                latest_row = df.iloc[0]
                latest_date = str(latest_row['trade_date'])
                latest_rate = float(latest_row['yield']) if 'yield' in df.columns else None
            else:
                latest_row = df.iloc[0]
                latest_date = ""
                latest_rate = float(latest_row['yield']) if 'yield' in df.columns else None

            if latest_rate is None:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare yc_cb 返回数据中无 yield 字段",
                )

            # 构建历史 DataFrame
            history_df = pd.DataFrame()
            if 'trade_date' in df.columns and 'yield' in df.columns:
                history_df = df[['trade_date', 'yield']].copy()
                history_df = history_df.sort_values('trade_date', ascending=False).reset_index(drop=True)

            result = {
                "latest": {"1Y": latest_rate},
                "latest_date": latest_date,
                "curve_name": f"Tushare yc_cb curve_type={curve_type_used}",
                "period": "1年",
                "history": history_df,
            }

            meta = {
                "curve_type": curve_type_used,
                "period": "1年",
                "latest_date": latest_date,
                "latest_rate": latest_rate,
                "history_days": len(history_df),
            }

            return DataResult(
                data=result,
                data_type="cd_rate_1y",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="cd_rate_1y",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare 同业存单利率获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # ETF 份额数据
    # ============================================================

    def fetch_etf_share(self, ts_code: str = None, symbol: str = None,
                        trade_date: str = None, start_date: str = None,
                        end_date: str = None, lookback_days: int = 10,
                        **kwargs) -> DataResult:
        """
        获取ETF份额数据（使用 fund_share 接口）。

        Args:
            ts_code: ETF代码（tushare格式，如 510300.SH）
            symbol: ETF代码（6位数字）
            trade_date: 单日日期（YYYYMMDD）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            lookback_days: 回溯天数

        Returns:
            DataResult.data: pd.DataFrame — ETF份额数据
                列: ts_code, trade_date, fund_share（份额，单位：份）
        """
        try:
            pro = self._get_pro()

            # 标准化代码
            if symbol and not ts_code:
                if symbol.startswith('5'):
                    ts_code = f"{symbol}.SH"
                else:
                    ts_code = f"{symbol}.SZ"

            if not ts_code:
                return DataResult(
                    data_type="etf_share",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="缺少ETF代码参数（ts_code 或 symbol）",
                )

            # 确定日期范围
            from datetime import datetime, timedelta
            if end_date:
                end_str = end_date
            elif trade_date:
                end_str = trade_date
            else:
                end_str = datetime.now().strftime('%Y%m%d')

            if start_date:
                start_str = start_date
            else:
                end_dt = datetime.strptime(end_str, '%Y%m%d')
                start_dt = end_dt - timedelta(days=lookback_days + 10)
                start_str = start_dt.strftime('%Y%m%d')

            self._rate_limit()
            df = pro.fund_share(
                ts_code=ts_code,
                start_date=start_str,
                end_date=end_str,
            )

            if df is None or df.empty:
                return DataResult(
                    data_type="etf_share",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"Tushare fund_share 返回空数据: {ts_code}",
                )

            # 标准化列名
            result_df = df.copy()
            if 'fd_share' in result_df.columns:
                # tushare 的 fd_share 单位是万份，转换为份
                result_df['fund_share'] = result_df['fd_share'] * 10000
            elif 'fund_share' not in result_df.columns:
                # 尝试找份额列
                for c in result_df.columns:
                    if 'share' in c.lower():
                        result_df['fund_share'] = result_df[c]
                        break

            if 'fund_share' not in result_df.columns:
                return DataResult(
                    data_type="etf_share",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"无法识别份额列，可用列: {list(result_df.columns)}",
                )

            # 按日期排序
            result_df = result_df.sort_values('trade_date').reset_index(drop=True)

            meta = {
                "ts_code": ts_code,
                "total_days": len(result_df),
                "latest_date": result_df.iloc[-1]['trade_date'] if len(result_df) > 0 else '',
                "latest_share": float(result_df.iloc[-1]['fund_share']) if len(result_df) > 0 else 0,
            }

            return DataResult(
                data=result_df,
                data_type="etf_share",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )
        except Exception as e:
            return DataResult(
                data_type="etf_share",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare ETF份额获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 两融数据
    # ============================================================

    def fetch_margin_trading(self, trade_date: str = None, start_date: str = None,
                             end_date: str = None, market: str = 'all',
                             **kwargs) -> DataResult:
        """
        获取两融数据（使用 margin 接口）。

        Args:
            trade_date: 单日日期（YYYYMMDD）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            market: 市场（sh/sz/all）

        Returns:
            DataResult.data: pd.DataFrame — 两融数据
                列: trade_date, rzye, rzmre, rqye, rzrqye, exchange
        """
        try:
            pro = self._get_pro()

            # 确定日期范围
            from datetime import datetime, timedelta
            if end_date:
                end_str = end_date
            elif trade_date:
                end_str = trade_date
            else:
                end_str = datetime.now().strftime('%Y%m%d')

            if start_date:
                start_str = start_date
            else:
                end_dt = datetime.strptime(end_str, '%Y%m%d')
                start_dt = end_dt - timedelta(days=15)
                start_str = start_dt.strftime('%Y%m%d')

            # 获取数据
            self._rate_limit()
            df = pro.margin(start_date=start_str, end_date=end_str)

            if df is None or df.empty:
                return DataResult(
                    data_type="margin_trading",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare margin 返回空数据",
                )

            result_df = df.copy()

            # 标准化 exchange_id 列
            if 'exchange_id' in result_df.columns:
                result_df = result_df.rename(columns={'exchange_id': 'exchange'})

            # 按市场筛选
            if market == 'sh':
                result_df = result_df[result_df['exchange'] == 'SSE'].reset_index(drop=True)
            elif market == 'sz':
                result_df = result_df[result_df['exchange'] == 'SZSE'].reset_index(drop=True)
            elif market == 'all':
                # 按日期汇总
                numeric_cols = ['rzye', 'rzmre', 'rzche', 'rqye', 'rqmcl', 'rzrqye']
                numeric_cols = [c for c in numeric_cols if c in result_df.columns]
                if numeric_cols:
                    df_summary = result_df.groupby('trade_date')[numeric_cols].sum().reset_index()
                    df_summary['exchange'] = 'ALL'
                    result_df = df_summary

            # 按日期排序
            if 'trade_date' in result_df.columns:
                result_df = result_df.sort_values('trade_date').reset_index(drop=True)

            meta = {
                "market": market,
                "total_days": len(result_df),
            }
            if len(result_df) > 0:
                latest = result_df.iloc[-1]
                meta["latest_date"] = str(latest.get('trade_date', ''))
                if 'rzye' in result_df.columns:
                    meta["latest_rzye"] = float(latest['rzye'])
                if 'rzrqye' in result_df.columns:
                    meta["latest_rzrqye"] = float(latest['rzrqye'])

            return DataResult(
                data=result_df,
                data_type="margin_trading",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )
        except Exception as e:
            return DataResult(
                data_type="margin_trading",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare 两融数据获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 板块资金流数据
    # ============================================================

    def fetch_sector_flow(self, sector_type: str = 'industry', trade_date: str = None,
                          **kwargs) -> DataResult:
        """
        获取板块资金流数据（Tushare 没有直接的板块资金流接口，使用 index_daily + 成分股估算）。
        Tushare 层返回空，降级到 AKShare。

        注：Tushare 目前没有直接的板块资金流接口，
            可以使用 moneyflow 接口获取个股资金流再汇总，但调用量太大。
            此方法返回失败，让系统自动降级到 AKShare。
        """
        return DataResult(
            data_type="sector_flow",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Tushare 无直接板块资金流接口，已降级到 AKShare",
        )

    # ============================================================
    # 央行OMO操作数据
    # ============================================================

    def fetch_central_bank_omo(self, trade_date: str = None, lookback_days: int = 30,
                               **kwargs) -> DataResult:
        """获取央行OMO操作数据。Tushare无直接接口，降级到Web。"""
        return DataResult(
            data_type="central_bank_omo",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Tushare 无央行OMO接口，已降级",
        )

    # ============================================================
    # 海外宏观数据
    # ============================================================

    def fetch_global_macro(self, trade_date: str = None, us_treasury_lookback_days: int = 20,
                           **kwargs) -> DataResult:
        """
        获取海外宏观数据。Tushare: us_tycr(美债收益率曲线)。
        """
        try:
            from datetime import datetime, timedelta
            pro = self._get_pro()
            if trade_date is None:
                trade_date = datetime.now().strftime("%Y%m%d")

            # 尝试 us_tycr 接口获取美债收益率
            us_2y = None
            us_10y = None
            us_2y_history = pd.DataFrame()
            us_10y_history = pd.DataFrame()
            us_10y_up_ratio_20d = None

            try:
                self._rate_limit()
                end_dt = datetime.strptime(trade_date, "%Y%m%d")
                start_dt = end_dt - timedelta(days=us_treasury_lookback_days + 10)
                df_us = pro.us_tycr(
                    start_date=start_dt.strftime("%Y%m%d"),
                    end_date=trade_date,
                )
                if df_us is not None and not df_us.empty:
                    df_us = df_us.sort_values('date', ascending=False)
                    latest = df_us.iloc[0]
                    # 列: date, y2, y5, y10, y30 等
                    us_2y = self._safe_float(latest.get('y2'))
                    us_10y = self._safe_float(latest.get('y10'))

                    # 历史
                    df_hist = df_us.head(us_treasury_lookback_days).sort_values('date', ascending=True)
                    us_2y_history = df_hist[['date', 'y2']].rename(columns={'y2': 'us_2y'}).reset_index(drop=True) if 'y2' in df_hist.columns else pd.DataFrame()
                    us_10y_history = df_hist[['date', 'y10']].rename(columns={'y10': 'us_10y'}).reset_index(drop=True) if 'y10' in df_hist.columns else pd.DataFrame()

                    # 20日上涨天数占比
                    if not us_10y_history.empty and len(us_10y_history) >= 2:
                        vals = us_10y_history['us_10y'].dropna().values
                        if len(vals) >= 2:
                            diffs = vals[1:] - vals[:-1]
                            us_10y_up_ratio_20d = float((diffs > 0).sum()) / len(diffs)
            except Exception as e:
                logger.debug(f"[tushare] us_tycr 失败: {e}")

            if us_2y is None and us_10y is None:
                return DataResult(
                    data_type="global_macro",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare us_tycr 获取美债收益率失败",
                )

            result_data = {
                "date": trade_date,
                "dxy": None,
                "us_2y": us_2y,
                "us_10y": us_10y,
                "brent": None,
                "cnh": None,
                "us_2y_history": us_2y_history,
                "us_10y_history": us_10y_history,
                "us_10y_up_ratio_20d": us_10y_up_ratio_20d,
            }

            valid_count = sum(1 for k in ["us_2y", "us_10y"] if result_data.get(k) is not None)

            return DataResult(
                data=result_data,
                data_type="global_macro",
                source=self.name,
                source_tier=self.tier,
                success=valid_count >= 1,
                meta={"trade_date": trade_date, "valid_count": valid_count},
            )
        except Exception as e:
            return DataResult(
                data_type="global_macro",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare 海外宏观获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 交易所回购利率
    # ============================================================

    def fetch_repo_exchange(self, trade_date: str = None, lookback_days: int = 20,
                            **kwargs) -> DataResult:
        """获取交易所回购利率。Tushare无直接接口，降级。"""
        return DataResult(
            data_type="repo_exchange",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Tushare 无交易所回购利率接口，已降级",
        )

    # ============================================================
    # 国债期货历史数据
    # ============================================================

    def fetch_treasury_futures(self, symbol: str = "TL", lookback_days: int = 30,
                               **kwargs) -> DataResult:
        """
        获取国债期货数据。Tushare: rb_daily (回购利率日数据)。
        注：Tushare的期货数据接口有限，尝试 rb_daily 或直接降级。
        """
        try:
            from datetime import datetime, timedelta
            pro = self._get_pro()

            end_date = datetime.now()
            start_date = end_date - timedelta(days=lookback_days + 10)
            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")

            # Tushare 没有直接的国债期货K线接口
            # 尝试 tt_daily (国债期货日数据，部分版本支持)
            try:
                self._rate_limit()
                df = pro.tt_daily(
                    ts_code=f"{symbol.upper()}.CFX",
                    start_date=start_str,
                    end_date=end_str,
                )
                if df is not None and not df.empty:
                    df = df.sort_values('trade_date', ascending=True)

                    latest_close = self._safe_float(df.iloc[-1].get('close'))
                    latest_date = df.iloc[-1].get('trade_date', None)

                    # 上涨天数占比
                    up_ratio_20d = None
                    if 'close' in df.columns and len(df) >= 2:
                        n = min(20, len(df))
                        vals = df.tail(n)['close'].astype(float).values
                        diffs = vals[1:] - vals[:-1]
                        up_ratio_20d = float((diffs > 0).sum()) / len(diffs)

                    result_data = {
                        "symbol": symbol,
                        "latest_close": latest_close,
                        "latest_date": latest_date,
                        "up_ratio_20d": up_ratio_20d,
                        "history": df,
                    }

                    return DataResult(
                        data=result_data,
                        data_type="treasury_futures",
                        source=self.name,
                        source_tier=self.tier,
                        success=True,
                        meta={"symbol": symbol, "record_count": len(df)},
                    )
            except Exception as e:
                logger.debug(f"[tushare] tt_daily 失败: {e}")

            return DataResult(
                data_type="treasury_futures",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="Tushare 国债期货接口不可用，已降级",
            )
        except Exception as e:
            return DataResult(
                data_type="treasury_futures",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare 国债期货获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 大小盘对比指标
    # ============================================================

    def fetch_size_style(self, lookback_days: int = 60, base_date: str = "20211231",
                         **kwargs) -> DataResult:
        """
        获取大小盘对比指标。Tushare: index_daily 获取指数日线。
        """
        try:
            from datetime import datetime, timedelta
            pro = self._get_pro()

            end_date = datetime.now()
            start_date = end_date - timedelta(days=lookback_days + 30)
            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")

            # 获取沪深300和中证2000数据
            csi300_df = None
            csi2000_df = None

            # 沪深300
            try:
                self._rate_limit()
                df = pro.index_daily(ts_code='000300.SH', start_date=start_str, end_date=end_str)
                if df is not None and not df.empty:
                    df = df.sort_values('trade_date', ascending=True)
                    csi300_df = df[['trade_date', 'close']].rename(
                        columns={'trade_date': 'date', 'close': 'csi300_close'}
                    )
                    csi300_df['date'] = pd.to_datetime(csi300_df['date']).dt.strftime('%Y-%m-%d')
                    csi300_df['csi300_close'] = csi300_df['csi300_close'].astype(float)
            except Exception as e:
                logger.debug(f"[tushare] 沪深300获取失败: {e}")

            # 中证2000
            try:
                self._rate_limit()
                df = pro.index_daily(ts_code='932000.SH', start_date=start_str, end_date=end_str)
                if df is not None and not df.empty:
                    df = df.sort_values('trade_date', ascending=True)
                    csi2000_df = df[['trade_date', 'close']].rename(
                        columns={'trade_date': 'date', 'close': 'csi2000_close'}
                    )
                    csi2000_df['date'] = pd.to_datetime(csi2000_df['date']).dt.strftime('%Y-%m-%d')
                    csi2000_df['csi2000_close'] = csi2000_df['csi2000_close'].astype(float)
            except Exception as e:
                logger.debug(f"[tushare] 中证2000获取失败: {e}")

            if csi300_df is None or csi2000_df is None or csi300_df.empty or csi2000_df.empty:
                return DataResult(
                    data_type="size_style",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare 沪深300或中证2000数据为空",
                )

            # 合并
            merged = pd.merge(csi2000_df, csi300_df, on='date', how='inner')
            if merged.empty:
                return DataResult(
                    data_type="size_style",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="沪深300和中证2000日期无交集",
                )

            # 归一化比值
            base_dash = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:]}"
            base_row = merged[merged['date'] <= base_dash]
            if base_row.empty:
                base_row = merged.head(1)
            else:
                base_row = base_row.tail(1)

            base_ratio_val = base_row.iloc[0]['csi2000_close'] / base_row.iloc[0]['csi300_close']
            merged['ratio'] = (merged['csi2000_close'] / merged['csi300_close']) / base_ratio_val * 100.0

            latest_row = merged.iloc[-1]
            ratio = self._safe_float(latest_row['ratio'])
            ratio_date = latest_row['date']
            small_cap_close = self._safe_float(latest_row['csi2000_close'])
            large_cap_close = self._safe_float(latest_row['csi300_close'])

            ratio_change_5d = None
            ratio_change_20d = None
            if len(merged) >= 6:
                prev_5 = merged.iloc[-6]['ratio']
                if prev_5 > 0:
                    ratio_change_5d = (ratio - prev_5) / prev_5 * 100
            if len(merged) >= 21:
                prev_20 = merged.iloc[-21]['ratio']
                if prev_20 > 0:
                    ratio_change_20d = (ratio - prev_20) / prev_20 * 100

            result_data = {
                "base_date": base_date,
                "ratio": ratio,
                "ratio_date": ratio_date,
                "small_cap_close": small_cap_close,
                "large_cap_close": large_cap_close,
                "history": merged,
                "ratio_change_5d": ratio_change_5d,
                "ratio_change_20d": ratio_change_20d,
            }

            return DataResult(
                data=result_data,
                data_type="size_style",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"base_date": base_date, "record_count": len(merged)},
            )
        except Exception as e:
            return DataResult(
                data_type="size_style",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Tushare 大小盘指标获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # A股实时行情（stock_spot）
    # ============================================================

    def fetch_stock_spot(self, market: str = "all", **kwargs) -> DataResult:
        """
        获取A股实时行情（Tushare realtime_quote 接口）。

        Args:
            market: 市场（all/SH/SZ/BJ）

        Returns:
            DataResult.data: pd.DataFrame — 实时行情数据
                列: ts_code, name, price, change, change_pct, open, high, low,
                    pre_close, vol, amount, bid1_price, ask1_price 等
        """
        try:
            pro = self._get_pro()

            # Tushare realtime_quote 需要 list of ts_codes（一次最多查1000只）
            # 全市场模式需要先用 daily 接口拿到全部 ts_code，成本较高
            # 这里改为返回"不支持全市场实时"，由上层逻辑降级
            # 但如果传入了 ts_code_list，则支持批量查询
            ts_code_list = kwargs.get('ts_code_list', None)
            if ts_code_list:
                # 分批查询，每批最多 100 只（Tushare限制）
                all_dfs = []
                batch_size = 100
                for i in range(0, len(ts_code_list), batch_size):
                    batch = ','.join(ts_code_list[i:i+batch_size])
                    self._rate_limit()
                    df_batch = pro.realtime_quote(ts_code=batch)
                    if df_batch is not None and not df_batch.empty:
                        all_dfs.append(df_batch)

                if all_dfs:
                    df = pd.concat(all_dfs, ignore_index=True)
                    # 标准化列名
                    df = self._normalize_stock_spot_columns(df)

                    return DataResult(
                        data=df,
                        data_type="stock_spot",
                        source=self.name,
                        source_tier=self.tier,
                        success=True,
                        meta={
                            "mode": "batch",
                            "record_count": len(df),
                            "api": "realtime_quote",
                        },
                    )

            # 全市场模式：Tushare realtime_quote 不支持全量拉取
            # 使用 daily 接口当日数据近似（延迟较大），直接返回不支持
            return DataResult(
                data_type="stock_spot",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="Tushare全市场实时行情需指定ts_code_list，不支持全量拉取",
            )

        except Exception as e:
            return DataResult(
                data_type="stock_spot",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def _normalize_stock_spot_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化Tushare实时行情列名。"""
        # Tushare realtime_quote 列名：
        # ts_code, name, pre_close, price, high, low, bid, ask, vol, amount,
        # time, open, change, pct_chg, ...
        col_map = {
            'ts_code': 'ts_code',
            'name': 'name',
            'price': 'price',
            'change': 'change',
            'pct_chg': 'change_pct',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'pre_close': 'prev_close',
            'vol': 'volume',
            'amount': 'amount',
            'time': 'tick_time',
            'bid': 'bid1_price',
            'ask': 'ask1_price',
        }

        new_cols = {}
        for col in df.columns:
            if col in col_map:
                new_cols[col] = col_map[col]
            else:
                new_cols[col] = col

        df = df.rename(columns=new_cols)

        if 'ts_code' in df.columns:
            df['symbol'] = df['ts_code'].astype(str).apply(
                lambda x: (x.split('.')[0] + '.' + x.split('.')[1].lower())
                if '.' in x else x.lower()
            )

        numeric_cols = ['price', 'change', 'change_pct', 'open', 'high', 'low',
                        'prev_close', 'volume', 'amount']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        return df

    # ============================================================
    # 指数日线行情（index_daily）
    # ============================================================

    def fetch_index_daily(
        self,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        trade_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取指数日线行情（专用 index_daily 接口）。

        Args:
            ts_code: 指数代码（tushare格式，如 000300.SH）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            trade_date: 单日交易日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.DataFrame — 指数日线行情
        """
        try:
            pro = self._get_pro()

            params = {}
            if ts_code:
                params["ts_code"] = ts_code
            if trade_date:
                params["trade_date"] = trade_date
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date

            self._rate_limit()
            df = pro.index_daily(**params)

            if df is None or df.empty:
                return DataResult(
                    data_type="index_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"指数日线数据为空 (params={params})",
                )

            return DataResult(
                data=df,
                data_type="index_daily",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "ts_code": ts_code or "",
                    "trade_date": trade_date or "",
                    "start_date": start_date or "",
                    "end_date": end_date or "",
                },
            )
        except Exception as e:
            logger.error(f"[tushare] index_daily 获取异常: {e}")
            return DataResult(
                data_type="index_daily",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=str(e),
            )

    # ============================================================
    # 指数实时行情（index_spot）
    # ============================================================

    def fetch_index_spot(self, index_codes: Optional[List[str]] = None, **kwargs) -> DataResult:
        """
        获取指数实时行情（Tushare realtime_quote）。

        Args:
            index_codes: 指数代码列表（tushare格式）

        Returns:
            DataResult.data: pd.DataFrame — 指数实时行情
        """
        try:
            pro = self._get_pro()

            # 默认主要指数
            if not index_codes:
                index_codes = [
                    '000001.SH', '000300.SH', '000905.SH', '000852.SH',
                    '000016.SH', '000688.SH',
                    '399001.SZ', '399006.SZ', '399106.SZ',
                ]

            batch = ','.join(index_codes)
            self._rate_limit()
            df = pro.realtime_quote(ts_code=batch)

            if df is None or df.empty:
                return DataResult(
                    data_type="index_spot",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="指数实时行情数据为空",
                )

            df = self._normalize_stock_spot_columns(df)

            return DataResult(
                data=df,
                data_type="index_spot",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "record_count": len(df),
                    "api": "realtime_quote",
                    "index_codes": index_codes,
                },
            )

        except Exception as e:
            return DataResult(
                data_type="index_spot",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # 期权IV历史序列（Tier 2：Tushare期权日线 + BS反推）
    # ============================================================

    def fetch_option_iv_history(
        self,
        underlying_code: str,
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> DataResult:
        """
        获取期权IV历史序列（Tier 2：Tushare）。

        从Tushare拉取期权合约基本信息和日线行情，用BS公式反推每日ATM IV。
        仅支持ETF期权标的（有对应opt_code前缀）。

        Args:
            underlying_code: 标的代码（如 510300、588000、159915 等）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.Series
                index: 日期字符串（YYYYMMDD）
                value: IV百分比（如 25.3 = 25.3%）
        """
        from ..collectors.option_iv_history import (
            IV_EXCHANGE_MAP,
            IV_OPT_CODE_PREFIX,
            IV_FUND_MAP,
            IV_INDEX_MAP,
        )
        from ..option_metrics import implied_vol

        try:
            pro = self._get_pro()

            exchange = IV_EXCHANGE_MAP.get(underlying_code, "")
            opt_code_prefix = IV_OPT_CODE_PREFIX.get(underlying_code, "")
            fund_code = IV_FUND_MAP.get(underlying_code, "")

            if not exchange or not opt_code_prefix:
                return DataResult(
                    data_type="option_iv_history",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"不支持的标的: {underlying_code} (无对应交易所/合约前缀)",
                )

            # 1. 获取标的ETF日线（用于每日spot价格）
            spot_df = None
            if fund_code:
                try:
                    self._rate_limit()
                    spot_df = pro.fund_daily(
                        ts_code=fund_code,
                        start_date=start_date,
                        end_date=end_date,
                    )
                except Exception as e:
                    logger.debug(f"[tushare] fund_daily({fund_code}) 失败: {e}")

            # 如果ETF日线失败，尝试指数日线
            if spot_df is None or spot_df.empty:
                idx_code = IV_INDEX_MAP.get(underlying_code, "")
                if idx_code:
                    try:
                        self._rate_limit()
                        spot_df = pro.index_daily(
                            ts_code=idx_code,
                            start_date=start_date,
                            end_date=end_date,
                        )
                    except Exception as e:
                        logger.debug(f"[tushare] index_daily({idx_code}) 失败: {e}")

            if spot_df is None or spot_df.empty or len(spot_df) < 30:
                return DataResult(
                    data_type="option_iv_history",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"标的日线数据不足: {len(spot_df) if spot_df is not None else 0}天",
                )

            spot_df = spot_df.sort_values('trade_date').reset_index(drop=True)

            # 2. 获取期权合约基本信息
            self._rate_limit()
            opt_list = pro.opt_basic(exchange=exchange)
            if opt_list is None or opt_list.empty:
                return DataResult(
                    data_type="option_iv_history",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"opt_basic({exchange}) 返回空",
                )

            # 筛选指定标的的合约
            if 'opt_code' in opt_list.columns:
                target_opts = opt_list[opt_list['opt_code'] == opt_code_prefix]
            else:
                # 从ts_code前缀匹配
                target_opts = opt_list[
                    opt_list['ts_code'].astype(str).str.startswith(
                        opt_code_prefix.replace('.SH', '').replace('.SZ', '')
                    )
                ]

            if target_opts.empty:
                return DataResult(
                    data_type="option_iv_history",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"未找到标的合约: {opt_code_prefix}",
                )

            # 3. 筛选在日期范围内存续的合约
            target_opts['maturity_date'] = target_opts['maturity_date'].astype(str)
            if 'list_date' in target_opts.columns:
                target_opts['list_date'] = target_opts['list_date'].astype(str)
            else:
                target_opts['list_date'] = start_date

            active = target_opts[
                (target_opts['maturity_date'] >= start_date) &
                (target_opts['list_date'] <= end_date)
            ]
            if active.empty:
                return DataResult(
                    data_type="option_iv_history",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"日期范围内无活跃合约",
                )

            target_ts_codes = set(active['ts_code'].tolist())
            logger.debug(
                f"[tushare] IV历史 {underlying_code} 候选合约数: {len(target_ts_codes)}"
            )

            # 4. 按月批量拉取期权日线（避免单次数据过大）
            from datetime import datetime as dt
            start_dt = dt.strptime(start_date, '%Y%m%d')
            end_dt = dt.strptime(end_date, '%Y%m%d')

            all_opt_daily = []
            cur = start_dt
            while cur <= end_dt:
                month_start = cur.strftime('%Y%m01')
                if cur.month == 12:
                    next_month = dt(cur.year + 1, 1, 1)
                else:
                    next_month = dt(cur.year, cur.month + 1, 1)
                from datetime import timedelta
                month_end = (next_month - timedelta(days=1)).strftime('%Y%m%d')
                if month_end > end_date:
                    month_end = end_date

                try:
                    self._rate_limit()
                    df = pro.opt_daily(
                        exchange=exchange,
                        start_date=month_start,
                        end_date=month_end,
                    )
                    if df is not None and not df.empty:
                        df_filtered = df[df['ts_code'].isin(target_ts_codes)]
                        if len(df_filtered) > 0:
                            all_opt_daily.append(df_filtered)
                except Exception as e:
                    logger.debug(
                        f"[tushare] opt_daily {month_start}~{month_end} 失败: {e}"
                    )

                cur = next_month

            if not all_opt_daily:
                return DataResult(
                    data_type="option_iv_history",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="未获取到期权日线数据",
                )

            opt_daily_df = pd.concat(all_opt_daily, ignore_index=True)

            # 5. 合并合约基本信息
            basic_cols = ['ts_code', 'exercise_price', 'maturity_date']
            if 'call_put' in active.columns:
                basic_cols.append('call_put')
            merged = opt_daily_df.merge(active[basic_cols], on='ts_code', how='left')

            # 推断call/put
            if 'call_put' not in merged.columns or merged['call_put'].isna().all():
                merged['call_put'] = merged['ts_code'].apply(
                    lambda x: 'C' if ('C' in str(x)[-8:] or '购' in str(x)) else 'P'
                )

            # 6. 按交易日分组计算ATM IV
            spot_dict = dict(zip(spot_df['trade_date'].astype(str), spot_df['close']))
            iv_records = []

            trade_dates = sorted(merged['trade_date'].unique())
            for td in trade_dates:
                td_str = str(td)
                day_df = merged[merged['trade_date'] == td]
                spot = spot_dict.get(td_str)
                if spot is None or spot <= 0:
                    continue

                iv = self._calc_atm_iv_from_day_df(
                    day_df, float(spot), td_str, r=0.025,
                    implied_vol_func=implied_vol,
                )
                if iv > 0.01:
                    iv_records.append({'trade_date': td_str, 'iv_pct': iv * 100})

            if len(iv_records) < 30:
                return DataResult(
                    data_type="option_iv_history",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"有效IV日数不足: {len(iv_records)} (需>=30)",
                )

            iv_df = pd.DataFrame(iv_records).sort_values('trade_date').reset_index(drop=True)
            iv_series = pd.Series(
                data=iv_df['iv_pct'].values,
                index=iv_df['trade_date'].values,
            )
            iv_series.name = "iv_pct"
            iv_series.attrs["source"] = "tushare_bs"

            meta = {
                "underlying_code": underlying_code,
                "start_date": start_date,
                "end_date": end_date,
                "data_days": len(iv_series),
            }

            return DataResult(
                data=iv_series,
                data_type="option_iv_history",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="option_iv_history",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    @staticmethod
    def _calc_atm_iv_from_day_df(
        day_df: pd.DataFrame,
        spot_price: float,
        trade_date: str,
        r: float = 0.025,
        implied_vol_func=None,
    ) -> float:
        """从单日合约数据计算ATM IV（取最近到期、平值附近的看涨+看跌均值）。"""
        from datetime import datetime as dt

        if implied_vol_func is None:
            from ..option_metrics import implied_vol
            implied_vol_func = implied_vol

        trade_dt = dt.strptime(trade_date, '%Y%m%d')

        valid = day_df[
            (day_df['close'] > 0) &
            (day_df['exercise_price'] > 0)
        ].copy()
        if len(valid) < 4:
            return 0.0

        valid['maturity_date'] = valid['maturity_date'].astype(str)
        valid['dte'] = valid['maturity_date'].apply(
            lambda x: max(0, (dt.strptime(x[:8], '%Y%m%d') - trade_dt).days)
        )

        # 找最近到期（>=3天DTE）
        near_expiry = valid[valid['dte'] >= 3]
        if near_expiry.empty:
            near_expiry = valid[valid['dte'] >= 1]
        if near_expiry.empty:
            return 0.0

        nearest_exp = near_expiry['dte'].min()
        near_month = near_expiry[near_expiry['dte'] == nearest_exp]
        T = nearest_exp / 365.0

        strikes = sorted(near_month['exercise_price'].unique())
        if not strikes:
            return 0.0

        atm_strike = min(strikes, key=lambda k: abs(k - spot_price))
        atm_idx = strikes.index(atm_strike)
        lo = max(0, atm_idx - 1)
        hi = min(len(strikes), atm_idx + 2)
        near_strikes = strikes[lo:hi]

        call_ivs = []
        put_ivs = []
        for K in near_strikes:
            call_row = near_month[
                (near_month['call_put'] == 'C') &
                (near_month['exercise_price'] == K)
            ]
            if len(call_row) > 0:
                iv = implied_vol_func(
                    float(call_row.iloc[0]['close']),
                    spot_price, float(K), T, r, is_call=True,
                )
                if iv > 0.01:
                    call_ivs.append(iv)

            put_row = near_month[
                (near_month['call_put'] == 'P') &
                (near_month['exercise_price'] == K)
            ]
            if len(put_row) > 0:
                iv = implied_vol_func(
                    float(put_row.iloc[0]['close']),
                    spot_price, float(K), T, r, is_call=False,
                )
                if iv > 0.01:
                    put_ivs.append(iv)

        all_ivs = call_ivs + put_ivs
        if len(all_ivs) >= 2:
            return sum(all_ivs) / len(all_ivs)
        elif len(all_ivs) == 1:
            return all_ivs[0]
        return 0.0

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        """安全转换为float，失败返回None。"""
        if val is None:
            return None
        try:
            f = float(val)
            import math
            if pd.isna(f) or math.isinf(f):
                return None
            return f
        except (ValueError, TypeError):
            return None
    # ============================================================
    # 北向资金数据
    # ============================================================

    def fetch_northbound_flow(
        self,
        start_date: str = None,
        end_date: str = None,
        lookback_days: int = 60,
        **kwargs,
    ) -> DataResult:
        """
        获取北向资金历史数据（Tushare moneyflow_hsgt）。

        Args:
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            lookback_days: 回溯天数

        Returns:
            DataResult.data: pd.DataFrame
                列: trade_date, north_money(当日成交净买额, 万元)
        """
        try:
            if not self._check_available():
                return DataResult(
                    data_type="northbound_flow",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare不可用",
                )

            pro = self._get_pro()

            # 确定日期范围
            from datetime import datetime, timedelta
            if end_date:
                end_d = end_date
            else:
                end_d = datetime.now().strftime('%Y%m%d')

            if start_date:
                start_d = start_date
            else:
                start_dt = datetime.strptime(end_d, '%Y%m%d') - timedelta(days=lookback_days + 30)
                start_d = start_dt.strftime('%Y%m%d')

            self._throttle()
            df = pro.moneyflow_hsgt(start_date=start_d, end_date=end_d)

            if df is None or df.empty:
                return DataResult(
                    data_type="northbound_flow",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare返回空数据",
                )

            df = df.copy()

            # 标准化列名
            col_map = {}
            for c in df.columns:
                cl = str(c).lower()
                if cl == 'north_money' or 'north' in cl:
                    col_map[c] = 'north_money'
                elif cl == 'trade_date' or 'trade' in cl:
                    col_map[c] = 'trade_date'
            df = df.rename(columns=col_map)

            # 数值转换
            if 'north_money' in df.columns:
                df['north_money'] = pd.to_numeric(df['north_money'], errors='coerce')

            # 排序
            if 'trade_date' in df.columns:
                df = df.sort_values('trade_date').reset_index(drop=True)

            return DataResult(
                data=df,
                data_type="northbound_flow",
                source=self.name,
                source_tier=self.tier,
                success=True,

                meta={"rows": len(df)},
            )

        except Exception as e:
            return DataResult(
                data_type="northbound_flow",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # 股权质押数据
    # ============================================================

    def fetch_equity_pledge(
        self,
        end_date: str = None,
        **kwargs,
    ) -> DataResult:
        """
        获取股权质押数据（Tushare pledge_stat）。

        Args:
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.DataFrame
                列: ts_code, pledge_ratio(质押比例%), trade_date 等
        """
        try:
            if not self._check_available():
                return DataResult(
                    data_type="equity_pledge",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare不可用",
                )

            pro = self._get_pro()

            if not end_date:
                from datetime import datetime
                end_date = datetime.now().strftime('%Y%m%d')

            self._throttle()
            df = pro.pledge_stat(end_date=end_date)

            if df is None or df.empty:
                return DataResult(
                    data_type="equity_pledge",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare返回空数据",
                )

            df = df.copy()

            # 标准化列名
            col_map = {}
            for c in df.columns:
                cl = str(c).lower()
                if cl == 'pledge_ratio':
                    col_map[c] = 'pledge_ratio'
            df = df.rename(columns=col_map)

            return DataResult(
                data=df,
                data_type="equity_pledge",
                source=self.name,
                source_tier=self.tier,
                success=True,

                meta={"rows": len(df)},
            )

        except Exception as e:
            return DataResult(
                data_type="equity_pledge",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_research_report(
        self,
        trade_date: str = None,
        start_date: str = None,
        end_date: str = None,
        **kwargs,
    ) -> DataResult:
        """
        获取券商个股研报（Tushare report_rc 接口）。

        Args:
            trade_date: 单日研报日期（YYYYMMDD），与 start_date/end_date 二选一
            start_date: 起始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data — DataFrame，含 ts_code/name/report_date/report_title/
                              report_type/classify/org_name/rating/eps/pe/roe/tp 等
        """
        try:
            params = {}
            if trade_date:
                params["trade_date"] = trade_date
            else:
                if start_date:
                    params["start_date"] = start_date
                if end_date:
                    params["end_date"] = end_date
            if not params:
                # 默认取最近一个交易日
                params["trade_date"] = pd.Timestamp.now().strftime("%Y%m%d")

            df = self._call_pro_api("report_rc", **params)
            if df is None or len(df) == 0:
                return DataResult(
                    data_type="research_report",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare report_rc 数据为空",
                )

            return DataResult(
                data=df,
                data_type="research_report",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"rows": len(df), "params": params},
            )
        except Exception as e:
            return DataResult(
                data_type="research_report",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_news(self, news_type: str = "stock", symbol: str = None,
                   date: str = None, **kwargs) -> DataResult:
        """
        获取新闻数据（Tushare news / major_news 接口）。

        注意：多数账号无 news 接口权限，会自动降级到 AKShare。
        """
        try:
            params = {}
            if date is None:
                date = pd.Timestamp.now().strftime("%Y%m%d")
            if news_type == "stock" and symbol:
                # major_news: 个股新闻
                ts_code = symbol
                if not ('.' in ts_code):
                    ts_code = (ts_code + ".SH") if ts_code.startswith("6") else (ts_code + ".SZ")
                df = self._call_pro_api("major_news", ts_code=ts_code,
                                        start_date=date, end_date=date)
            else:
                # news: 快讯（需要权限）
                df = self._call_pro_api("news", src="sina",
                                        start_date=date, end_date=date)
            if df is None or len(df) == 0:
                return DataResult(
                    data_type="news",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Tushare news 数据为空",
                )
            return DataResult(
                data=df,
                data_type="news",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"rows": len(df), "news_type": news_type},
            )
        except Exception as e:
            return DataResult(
                data_type="news",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )
