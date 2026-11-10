"""
AKShare 数据源适配器
===================
一级数据源：基于 AKShare 库的数据获取。
AKShare 是免费开源的财经数据接口，数据来源于东方财富/新浪等。

优先级：Tier 1（首选）
限制：调用间隔建议 ≥ 0.1s，无明确频率限制
"""

from __future__ import annotations

from datetime import datetime
import threading
import time
from typing import List, Optional

import pandas as pd

from ..base import BaseDataSource, DataResult
from ..config import TIMEOUT_CONFIG, QUALITY_THRESHOLDS, logger


class AKShareSource(BaseDataSource):
    """AKShare 数据源适配器（一级数据源）。"""

    name: str = "akshare"
    tier: int = 1

    def __init__(self):
        super().__init__()
        self._ak = None
        self._last_call_ts: float = 0.0

    def _get_ak(self):
        """延迟导入 akshare，避免未安装时报错。"""
        if self._ak is None:
            import akshare as ak
            self._ak = ak
        return self._ak

    def _rate_limit(self):
        """AKShare 调用间隔控制。"""
        elapsed = time.time() - self._last_call_ts
        if elapsed < TIMEOUT_CONFIG.akshare_min_interval:
            time.sleep(TIMEOUT_CONFIG.akshare_min_interval - elapsed)
        self._last_call_ts = time.time()

    def is_available(self) -> bool:
        """检查 AKShare 是否可用。"""
        try:
            self._get_ak()
            return True
        except ImportError:
            logger.warning("[akshare] AKShare 库未安装，数据源不可用")
            return False

    # ============================================================
    # 指数成分股
    # ============================================================

    def fetch_index_components(self, index_code: str, **kwargs) -> DataResult:
        """
        获取指数成分股。

        防御性设计：
        - 主接口：index_stock_cons（新浪/东方财富源）
        - 降级接口：stock_zh_index_spot_em 提取成分（仅东财指数页面结构有效时）
        - 任何内部异常（如 AKShare 页面结构变化导致 AttributeError）都被捕获并返回失败，
          交由上层降级逻辑处理，不会让单个源的异常中断整个采集链。

        Args:
            index_code: 指数代码（6位数字，如 000300）

        Returns:
            DataResult.data: list[str] — tushare格式代码列表（如 ['000001.SZ', ...]）
        """
        try:
            ak = self._get_ak()

            # 校验参数
            if not index_code or not isinstance(index_code, str):
                return DataResult(
                    data_type="index_components",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"无效的指数代码: {index_code}",
                )

            # 提取6位数字代码
            symbol = index_code.split('.')[0].strip()
            if not symbol.isdigit() or len(symbol) != 6:
                return DataResult(
                    data_type="index_components",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"无法解析为6位数字代码: {index_code}",
                )

            df = None
            api_used = ""
            last_error = ""

            # ========== 方案1：index_stock_cons（主接口） ==========
            self._rate_limit()
            try:
                df = ak.index_stock_cons(symbol=symbol)
                api_used = "index_stock_cons"
                # 防御：确保返回的是 DataFrame（某些版本/接口可能返回 None 或其他类型）
                if df is not None and not hasattr(df, 'empty'):
                    logger.debug(f"[akshare] {api_used} 返回类型异常: {type(df).__name__}, 重置为 None")
                    df = None
            except AttributeError as ae:
                # 常见于 AKShare 内部 HTML 解析时页面结构变化，如 None.find()
                last_error = f"AttributeError: {ae}"
                logger.warning(
                    f"[akshare] index_stock_cons({symbol}) 内部解析失败 "
                    f"(AttributeError), 尝试降级接口: {ae}"
                )
                df = None
            except Exception as e1:
                last_error = f"{type(e1).__name__}: {e1}"
                logger.debug(f"[akshare] index_stock_cons({symbol}) 失败: {e1}")
                df = None

            # ========== 方案2：备选接口（数据不足时尝试） ==========
            if df is None or df.empty:
                # 尝试 index_stock_cons_csindex（中证指数公司源）
                self._rate_limit()
                try:
                    df2 = ak.index_stock_cons_csindex(symbol=symbol)
                    if df2 is not None and hasattr(df2, 'empty') and not df2.empty:
                        df = df2
                        api_used = "index_stock_cons_csindex"
                        logger.info(f"[akshare] 降级使用 {api_used} 获取 {symbol} 成分股")
                except AttributeError as ae:
                    last_error = f"csindex AttributeError: {ae}"
                    logger.debug(f"[akshare] index_stock_cons_csindex({symbol}) AttributeError: {ae}")
                except Exception as e2:
                    logger.debug(f"[akshare] index_stock_cons_csindex({symbol}) 失败: {e2}")

            # 如果仍然无数据，返回失败，让上层降级
            if df is None or df.empty:
                err_msg = f"指数 {index_code} 成分股获取失败"
                if last_error:
                    err_msg += f" ({last_error})"
                elif api_used:
                    err_msg += f" (api={api_used} 返回空)"
                return DataResult(
                    data_type="index_components",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=err_msg,
                )

            # 判断列名，兼容不同版本 AKShare / 不同接口
            code_col = None
            for candidate in [
                '品种代码', '股票代码', '成分券代码',
                '成分券代码\n交易所', '代码',
                'code', 'ts_code', 'con_code',
            ]:
                if candidate in df.columns:
                    code_col = candidate
                    break
            if code_col is None:
                return DataResult(
                    data_type="index_components",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"无法识别成分股代码列，可用列: {list(df.columns)} (api={api_used})",
                )

            # 转换为 tushare 格式（带 .SH/.SZ 后缀）
            raw_codes = df[code_col].astype(str).tolist()
            codes = []
            for code in raw_codes:
                code = str(code).strip().zfill(6)
                if len(code) != 6 or not code.isdigit():
                    continue
                # 判断交易所（顺序：6开头SH / 0和3开头SZ / 688SH / 8和4 BJ）
                if code.startswith('688'):
                    codes.append(code + '.SH')
                elif code.startswith('6'):
                    codes.append(code + '.SH')
                elif code.startswith('0') or code.startswith('3'):
                    codes.append(code + '.SZ')
                elif code.startswith('8') or code.startswith('4'):
                    # 北交所
                    codes.append(code + '.BJ')
                else:
                    # 默认根据指数代码判断
                    if index_code.endswith('.SH') or ('.' not in index_code and symbol.startswith(('000', '001', '688'))):
                        codes.append(code + '.SH')
                    else:
                        codes.append(code + '.SZ')

            # 去重
            codes = list(set(codes))

            meta = {
                "index_code": index_code,
                "raw_count": len(raw_codes),
                "valid_count": len(codes),
                "column": code_col,
                "api": api_used,
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
            # 最后一道防线：任何未预期的异常都优雅返回失败
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
        获取期权数据。

        Args:
            exchange: 交易所代码（SSE/SZSE/CFFEX）
            trade_date: 交易日期（YYYYMMDD格式），默认当日
            underlying: 标的代码（如 510300、159919 等），可选

        Returns:
            DataResult.data: dict with keys:
                - contracts: pd.DataFrame 合约基本信息
                - daily: pd.DataFrame 日线行情
                - underlying_price: float 标的现货价格（如果能获取）
        """
        try:
            ak = self._get_ak()
            self._rate_limit()

            result = {
                "contracts": pd.DataFrame(),
                "daily": pd.DataFrame(),
                "underlying_price": None,
            }

            exchange_upper = exchange.upper()

            if exchange_upper == "SSE":
                result = self._fetch_option_sse(ak, underlying, trade_date, result)
            elif exchange_upper == "SZSE":
                result = self._fetch_option_szse(ak, underlying, trade_date, result)
            elif exchange_upper == "CFFEX":
                result = self._fetch_option_cffex(ak, underlying, trade_date, result)
            else:
                return DataResult(
                    data_type="option_data",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"不支持的交易所: {exchange}",
                )

            has_data = (
                not result["contracts"].empty or not result["daily"].empty
            )

            if not has_data:
                return DataResult(
                    data_type="option_data",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"交易所 {exchange} 期权数据为空",
                )

            meta = {
                "exchange": exchange_upper,
                "underlying": underlying,
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

    def _fetch_option_sse(self, ak, underlying, trade_date, result: dict) -> dict:
        """获取上交所期权数据。"""
        try:
            df = ak.option_current_day_sse()
            if df is not None and not df.empty:
                # 按标的筛选
                if underlying:
                    # 尝试匹配标的代码列（兼容不同AKShare版本列名）
                    #  标的券名称及代码：如 50ETF(510050) / 科创50(588000)
                    #  合约交易代码：如 510050C2608M02750 / 588000C2608M01450
                    matched = False
                    for col in [
                        '标的券名称及代码', '标的代码', '合约标的',
                        'underlying', '标的证券', '标的证券简称(代码)',
                    ]:
                        if col in df.columns:
                            df = df[df[col].astype(str).str.contains(underlying, na=False)]
                            matched = True
                            break
                    # 如果以上列都没有，用合约交易代码前缀匹配
                    if not matched:
                        for col in ['合约交易代码', '合约代码', 'ts_code']:
                            if col in df.columns:
                                df = df[df[col].astype(str).str.startswith(underlying)]
                                matched = True
                                break
                result["contracts"] = df
        except Exception as e:
            logger.debug(f"[akshare] SSE option_current_day_sse 失败: {e}")

        # 如果合约数据不足，尝试用 option_finance_board 补充
        if len(result["contracts"]) < QUALITY_THRESHOLDS.min_option_contracts and underlying:
            try:
                self._rate_limit()
                # 尝试不同的看板名称
                board_names = [
                    f"{underlying}期权",
                    f"{underlying}ETF期权",
                    underlying,
                ]
                for board_name in board_names:
                    try:
                        end_month = ""  # 默认全部
                        df2 = ak.option_finance_board(
                            symbol=board_name, end_month=end_month
                        )
                        if df2 is not None and not df2.empty:
                            if result["contracts"].empty:
                                result["contracts"] = df2
                            else:
                                result["daily"] = df2
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"[akshare] SSE option_finance_board 失败: {e}")

        return result

    def _fetch_option_szse(self, ak, underlying, trade_date, result: dict) -> dict:
        """获取深交所期权数据。"""
        try:
            df = ak.option_current_day_szse()
            if df is not None and not df.empty:
                if underlying:
                    matched = False
                    for col in [
                        '标的证券简称(代码)', '标的券名称及代码', '标的代码',
                        '合约标的', 'underlying', '标的证券',
                    ]:
                        if col in df.columns:
                            df = df[df[col].astype(str).str.contains(underlying, na=False)]
                            matched = True
                            break
                    if not matched:
                        for col in ['合约代码', '合约交易代码', 'ts_code']:
                            if col in df.columns:
                                df = df[df[col].astype(str).str.startswith(underlying)]
                                matched = True
                                break
                result["contracts"] = df
        except Exception as e:
            logger.debug(f"[akshare] SZSE option_current_day_szse 失败: {e}")

        return result

    def _fetch_option_cffex(self, ak, underlying, trade_date, result: dict) -> dict:
        """获取中金所期权数据（中证1000股指期权 MO）。"""
        try:
            # 合约列表
            df_list = ak.option_cffex_zz1000_list_sina()
            if df_list is not None and not df_list.empty:
                result["contracts"] = df_list

                # 尝试获取当月合约的行情
                if not df_list.empty:
                    # 找当月合约（第一个非空合约月份）
                    month_col = None
                    for col in ['合约月份', 'month', 'symbol']:
                        if col in df_list.columns:
                            month_col = col
                            break
                    if month_col:
                        months = df_list[month_col].dropna().unique()
                        if len(months) > 0:
                            self._rate_limit()
                            try:
                                df_spot = ak.option_cffex_zz1000_spot_sina(
                                    symbol=str(months[0])
                                )
                                if df_spot is not None and not df_spot.empty:
                                    result["daily"] = df_spot
                            except Exception as e:
                                logger.debug(f"[akshare] CFFEX spot 失败: {e}")
        except Exception as e:
            logger.debug(f"[akshare] CFFEX option 失败: {e}")

        return result

    # ============================================================
    # A股日线行情
    # ============================================================

    # 常见指数代码（用于识别指数，走 stock_zh_index_daily 接口）
    _INDEX_CODES = {
        '000001', '000002', '000003', '000016', '000300', '000688',
        '000852', '000905', '000906',
        '399001', '399005', '399006', '399106', '399300', '399905',
    }

    def _is_index_code(self, code: Optional[str], ts_code: Optional[str] = None) -> bool:
        """判断是否为指数代码。"""
        if not code:
            return False
        # 6位数字代码判断
        if code in self._INDEX_CODES:
            return True
        # 通过 ts_code 的后缀判断（指数也有.SH/.SZ后缀）
        if ts_code:
            # 上证指数、上证50、沪深300、中证500等都是 SH 开头的6位数字
            # 深证成指、创业板指是 SZ 开头的6位数字
            # 但平安银行也是 000001.SZ，所以不能只靠后缀
            pass
        return False

    def _get_ak_index_symbol(self, code: str, ts_code: Optional[str] = None) -> str:
        """构造 AKShare 指数代码格式（sh000001 / sz399001）。"""
        if ts_code:
            if ts_code.endswith('.SH'):
                return f"sh{code}"
            elif ts_code.endswith('.SZ'):
                return f"sz{code}"
        # 默认根据代码前缀判断
        if code.startswith('000') or code.startswith('001'):
            return f"sh{code}"  # 上证指数系列
        elif code.startswith('399'):
            return f"sz{code}"  # 深证指数系列
        return f"sh{code}"

    def fetch_stock_daily(
        self,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        trade_date: Optional[str] = None,
        symbol: Optional[str] = None,
        period: str = "daily",
        adjust: str = "qfq",
        **kwargs,
    ) -> DataResult:
        """
        获取A股日线行情（支持个股和指数）。

        Args:
            ts_code: tushare格式股票/指数代码（如 000001.SZ），单只
            symbol: AKShare格式股票代码（6位数字），单只
            trade_date: 单日日期（YYYYMMDD），用于全市场或单只
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            period: 周期（daily/weekly/monthly）
            adjust: 复权方式（qfq前复权/hfq后复权/''不复权）

        Returns:
            DataResult.data: pd.DataFrame — 日线行情数据
        """
        try:
            ak = self._get_ak()

            code = symbol or (ts_code.split('.')[0] if ts_code else None)

            if code:
                # 单只股票/指数历史行情
                sd = start_date or trade_date
                ed = end_date or trade_date
                if sd:
                    sd = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}"
                if ed:
                    ed = f"{ed[:4]}-{ed[4:6]}-{ed[6:8]}"

                df = None
                is_index = False

                # 先尝试指数接口（如果是指数代码）
                if self._is_index_code(code, ts_code):
                    self._rate_limit()
                    try:
                        idx_symbol = self._get_ak_index_symbol(code, ts_code)
                        df = ak.stock_zh_index_daily(symbol=idx_symbol)
                        if df is not None and not df.empty:
                            is_index = True
                            # 按日期过滤
                            if sd and 'date' in df.columns:
                                df = df[df['date'] >= sd]
                            if ed and 'date' in df.columns:
                                df = df[df['date'] <= ed]
                    except Exception as idx_e:
                        logger.debug(f"[akshare] 指数日线 {code} 获取失败，尝试个股: {idx_e}")
                        df = None

                # 如果指数接口失败或不是指数，尝试个股接口
                if df is None or (hasattr(df, 'empty') and df.empty):
                    self._rate_limit()
                    try:
                        df = ak.stock_zh_a_hist(
                            symbol=code,
                            period=period,
                            start_date=sd if sd else "",
                            end_date=ed if ed else "",
                            adjust=adjust,
                        )
                    except Exception as stock_e:
                        # 如果个股接口也失败，且还没试过指数接口，再试一次
                        if not is_index and not self._is_index_code(code, ts_code):
                            self._rate_limit()
                            try:
                                idx_symbol = self._get_ak_index_symbol(code, ts_code)
                                df = ak.stock_zh_index_daily(symbol=idx_symbol)
                                if df is not None and not df.empty:
                                    is_index = True
                                    if sd and 'date' in df.columns:
                                        df = df[df['date'] >= sd]
                                    if ed and 'date' in df.columns:
                                        df = df[df['date'] <= ed]
                            except Exception:
                                raise stock_e
                        else:
                            raise stock_e

                if df is None or df.empty:
                    return DataResult(
                        data_type="stock_daily",
                        source=self.name,
                        source_tier=self.tier,
                        success=False,
                        error=f"代码 {code} 日线数据为空",
                    )

                # 标准化列名
                df = self._normalize_stock_daily_columns(df, ts_code or code)

                meta = {
                    "code": code,
                    "ts_code": ts_code or "",
                    "start_date": start_date or trade_date or "",
                    "end_date": end_date or trade_date or "",
                    "adjust": adjust if not is_index else "",
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
            else:
                # 全市场数据 — AKShare 无直接全市场历史日线接口
                # stock_zh_a_spot_em 只返回当日实时行情，非今日直接快速失败
                from datetime import datetime as _dt_now
                today_str = _dt_now.now().strftime("%Y%m%d")
                if trade_date and str(trade_date) != today_str:
                    return DataResult(
                        data_type="stock_daily",
                        source=self.name,
                        source_tier=self.tier,
                        success=False,
                        error=f"AKShare无全市场历史日线接口 (trade_date={trade_date}, 仅支持当日)",
                    )
                
                # ---- 全市场实时行情：东方财富优先，失败降级新浪 ----
                df = None
                spot_source = "eastmoney"
                
                # 1) 东方财富源（stock_zh_a_spot_em），字段更全
                self._rate_limit()
                try:
                    df = ak.stock_zh_a_spot_em()
                    if df is None or df.empty:
                        df = None
                except Exception as em_e:
                    logger.debug(f"[akshare] stock_zh_a_spot_em 失败，降级新浪: {em_e}")
                    df = None

                # 2) 新浪源兜底（stock_zh_a_spot），列名为中文
                if df is None:
                    spot_source = "sina"
                    self._rate_limit()
                    try:
                        df = ak.stock_zh_a_spot()
                        if df is not None and not df.empty:
                            df = self._normalize_sina_spot_columns(df)
                        else:
                            df = None
                    except Exception as sina_e:
                        logger.debug(f"[akshare] stock_zh_a_spot 也失败: {sina_e}")
                        df = None

                if df is None or df.empty:
                    return DataResult(
                        data_type="stock_daily",
                        source=self.name,
                        source_tier=self.tier,
                        success=False,
                        error="全市场行情数据为空 (东方财富+新浪均失败)",
                    )
                # 补充 trade_date 列（实时行情 = 当日）
                from datetime import datetime as _dt
                _today = _dt.now().strftime("%Y%m%d")
                df["trade_date"] = trade_date or _today

                meta = {
                    "mode": "spot_all",
                    "spot_source": spot_source,
                    "trade_date": trade_date or "",
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

    def _normalize_sina_spot_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化新浪 stock_zh_a_spot 返回的中文字段为统一格式。
        
        新浪源列: 代码, 名称, 最新价, 涨跌额, 涨跌幅, 买入, 卖出, 昨收, 今开, 最高, 最低, 成交量, 成交额, 时间戳
        统一列: ts_code, name, close, change, pct_chg, pre_close, open, high, low, vol, amount
        """
        column_mapping = {
            '代码': 'ts_code_raw',
            '名称': 'name',
            '最新价': 'close',
            '涨跌额': 'change',
            '涨跌幅': 'pct_chg',
            '昨收': 'pre_close',
            '今开': 'open',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'vol',
            '成交额': 'amount',
        }
        df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})
        
        # 代码列统一为 tushare 格式（带交易所后缀）
        if 'ts_code_raw' in df.columns:
            def _to_ts_code(x):
                s = str(x).strip().lower()
                if s.startswith('bj'):
                    return f"{s[2:]}.BJ"
                elif s.startswith('sh'):
                    return f"{s[2:]}.SH"
                elif s.startswith('sz'):
                    return f"{s[2:]}.SZ"
                # 纯数字代码
                if s.startswith('6') or s.startswith('9'):
                    return f"{s}.SH"
                else:
                    return f"{s}.SZ"
            df['ts_code'] = df['ts_code_raw'].apply(_to_ts_code)
            df = df.drop(columns=['ts_code_raw'], errors='ignore')
        
        # 数值类型处理（涨跌幅可能带 % 号）
        if 'pct_chg' in df.columns:
            df['pct_chg'] = df['pct_chg'].astype(str).str.replace('%', '').astype(float)
        
        return df

    def _normalize_stock_daily_columns(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        """标准化日线行情列名为类tushare格式。"""
        column_mapping = {
            '日期': 'trade_date',
            'date': 'trade_date',
            '股票代码': 'ts_code',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'vol',
            'volume': 'vol',
            '成交额': 'amount',
            '振幅': 'amplitude',
            '涨跌幅': 'pct_chg',
            '涨跌额': 'change',
            '换手率': 'turnover',
        }
        df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

        # 添加 ts_code 列
        if 'ts_code' not in df.columns and code:
            df['ts_code'] = code

        # 日期格式转换
        if 'trade_date' in df.columns:
            df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '')

        return df


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
        获取股指期货基差数据（期货价格 + 指数现货 + 基差计算）。

        Args:
            trade_date: 交易日期（YYYYMMDD），默认当日
            products: 期货品种列表，默认 ['IF', 'IC', 'IM']

        Returns:
            DataResult.data: list[dict] — 基差数据列表
                每项包含: product, index_name, futures_price, spot_price,
                         basis, basis_rate, basis_rate_annual, contract_type
        """
        try:
            ak = self._get_ak()
            if products is None:
                products = ['IF', 'IC', 'IM']

            # 指数代码映射
            index_map = {
                'IF': {'code': '000300', 'name': '沪深300'},
                'IC': {'code': '000905', 'name': '中证500'},
                'IM': {'code': '000852', 'name': '中证1000'},
            }

            result_list = []

            # 1. 获取各指数现货价格
            spot_prices = {}
            for product in products:
                if product not in index_map:
                    continue
                try:
                    self._rate_limit()
                    idx_code = index_map[product]['code']
                    # 用 stock_zh_index_daily 获取指数日线
                    df_idx = ak.stock_zh_index_daily(symbol=f"sh{idx_code}")
                    if df_idx is not None and not df_idx.empty:
                        df_idx = df_idx.sort_values('date')
                        latest = df_idx.iloc[-1]
                        spot_prices[product] = float(latest['close'])
                except Exception as e:
                    logger.debug(f"[akshare] 指数现货 {product} 获取失败: {e}")

            # 2. 获取各品种期货主力合约价格
            for product in products:
                if product not in index_map:
                    continue

                futures_price = None
                contract_month = None

                try:
                    self._rate_limit()
                    # 用 futures_zh_daily_sina 获取期货日线
                    # 新浪期货代码：IF0（主力连续）
                    symbol = f"{product}0"
                    df_fut = ak.futures_zh_daily_sina(symbol=symbol)
                    if df_fut is not None and not df_fut.empty:
                        # 列: date, open, high, low, close, volume, hold
                        df_fut = df_fut.sort_values('date')
                        latest_fut = df_fut.iloc[-1]
                        futures_price = float(latest_fut['close'])
                        # 主力连续不区分合约月份，标记为 "主力连续"
                        contract_month = "主力连续"
                except Exception as e:
                    logger.debug(f"[akshare] 期货 {product} 主力连续获取失败: {e}")

                # 尝试用具体合约代码
                if futures_price is None:
                    try:
                        self._rate_limit()
                        # 获取当前月合约
                        from datetime import datetime as _dt
                        now = _dt.now()
                        year = str(now.year)[2:]  # 后两位
                        month = f"{now.month:02d}"
                        contract = f"{product}{year}{month}"
                        try:
                            df_fut = ak.futures_zh_daily_sina(symbol=contract)
                            if df_fut is not None and not df_fut.empty:
                                df_fut = df_fut.sort_values('date')
                                latest_fut = df_fut.iloc[-1]
                                futures_price = float(latest_fut['close'])
                                contract_month = f"{year}{month}"
                        except Exception:
                            pass
                    except Exception as e:
                        logger.debug(f"[akshare] 期货 {product} 当月合约获取失败: {e}")

                if futures_price is None or product not in spot_prices:
                    continue

                spot_price = spot_prices[product]
                if spot_price <= 0 or futures_price <= 0:
                    continue

                basis = futures_price - spot_price
                basis_rate = basis / spot_price * 100

                # 年化基差率（估算距到期约60天）
                days_to_expiry = 60
                basis_rate_annual = basis_rate * (365.0 / days_to_expiry)

                result_list.append({
                    'product': product,
                    'index_name': index_map[product]['name'],
                    'contract_type': contract_month or '主力连续',
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
                    error="AKShare 股指期货基差数据为空",
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
        获取单个具体期货合约的历史日线（AKShare / 新浪财经源）。

        Args:
            contract: 原始合约代码（如 IF2609 或 IF2609.CFX）
            sina_symbol: 新浪/akshare 合约代码（如 IF2609），优先使用
            ts_code: tushare 合约代码（如 IF2609.CFX），仅作元数据
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.DataFrame
                标准列 date(YYYYMMDD), open, high, low, close, volume, oi，按 date 升序
        """
        try:
            from ..collectors.futures_contract_daily import normalize_futures_contract_df

            ak = self._get_ak()

            symbol = sina_symbol or (str(contract).split('.')[0] if contract else None)
            if not symbol:
                return DataResult(
                    data_type="futures_contract_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="合约代码为空",
                )

            self._rate_limit()
            # akshare futures_zh_daily_sina：具体合约（如 IF2609），
            # 原始列: date, open, high, low, close, volume, hold, settle
            df = ak.futures_zh_daily_sina(symbol=symbol)

            if df is None or df.empty:
                return DataResult(
                    data_type="futures_contract_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"AKShare 合约 {symbol} 日线数据为空",
                )

            df = normalize_futures_contract_df(df)

            # 按日期区间过滤
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
                    error=f"合约 {symbol} 在 {start_date or '-'}~{end_date or '-'} 区间无数据",
                )

            df = df.reset_index(drop=True)

            return DataResult(
                data=df,
                data_type="futures_contract_daily",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "contract": contract or symbol,
                    "sina_symbol": symbol,
                    "ts_code": ts_code or "",
                    "start_date": start_date or "",
                    "end_date": end_date or "",
                    "api": "futures_zh_daily_sina",
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

    # SHIBOR 期限定义
    _SHIBOR_TERMS = ['ON', '1W', '2W', '1M', '3M', '6M', '9M', '1Y']

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
                return self._fetch_hibor()
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
        """获取 SHIBOR 利率（AKShare macro_china_shibor_all 接口）。"""
        try:
            ak = self._get_ak()
            self._rate_limit()

            df = ak.macro_china_shibor_all()
            if df is None or df.empty:
                return DataResult(
                    data_type="shibor",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="AKShare SHIBOR 数据为空",
                )

            # 按日期排序，取最新（大小写不敏感匹配日期列）
            date_col = None
            date_candidates = ['日期', 'date', 'trade_date', 'TRADE_DATE', 'Date']
            for col in date_candidates:
                if col in df.columns:
                    date_col = col
                    break
            # 如果精确匹配失败，尝试模糊匹配
            if date_col is None:
                for col in df.columns:
                    col_lower = col.lower()
                    if 'date' in col_lower or '日期' in col:
                        date_col = col
                        break

            if date_col:
                df = df.sort_values(date_col, ascending=False)
                latest_row = df.iloc[0]
                latest_date = str(latest_row[date_col]).replace('-', '')
            else:
                # 默认取最后一行（通常最新在末尾）
                latest_row = df.iloc[-1]
                latest_date = ""

            # 提取各期限利率（支持多种列名格式：ON / O/N-定价 / 隔夜 等）
            term_patterns = {
                'ON': ['O/N', 'ON', '隔夜'],
                '1W': ['1W', '1周', '一周'],
                '2W': ['2W', '2周', '两周'],
                '1M': ['1M', '1月', '1个月'],
                '3M': ['3M', '3月', '3个月'],
                '6M': ['6M', '6月', '6个月'],
                '9M': ['9M', '9月', '9个月'],
                '1Y': ['1Y', '1年', '一年'],
            }

            # 构建 列名小写 -> 原始列名 的映射
            col_lower_map = {col.lower(): col for col in df.columns}

            latest_rates = {}
            for term, patterns in term_patterns.items():
                found_col = None
                for pattern in patterns:
                    pat_lower = pattern.lower()
                    # 优先精确匹配
                    if pat_lower in col_lower_map:
                        found_col = col_lower_map[pat_lower]
                        break
                    # 其次模糊匹配（包含关系，且包含"定价"或纯利率列）
                    for col_lower, orig_col in col_lower_map.items():
                        if pat_lower in col_lower and ('定价' in orig_col or 'rate' in col_lower):
                            found_col = orig_col
                            break
                    if found_col:
                        break

                if found_col:
                    try:
                        val = latest_row[found_col]
                        if val is not None and str(val) != '' and str(val) != 'nan':
                            latest_rates[term] = float(val)
                        else:
                            latest_rates[term] = None
                    except (ValueError, TypeError):
                        latest_rates[term] = None

            # 构建历史 DataFrame
            history_df = df.copy()
            if date_col and date_col != 'trade_date':
                history_df = history_df.rename(columns={date_col: 'trade_date'})
            if 'trade_date' in history_df.columns:
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
                error=f"AKShare SHIBOR 获取失败: {type(e).__name__}: {e}",
            )

    def _fetch_lpr(self) -> DataResult:
        """获取 LPR 利率（AKShare macro_china_lpr 接口）。"""
        try:
            ak = self._get_ak()
            self._rate_limit()

            df = ak.macro_china_lpr()
            if df is None or df.empty:
                return DataResult(
                    data_type="lpr",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="AKShare LPR 数据为空",
                )

            # 按日期排序，取最新（大小写不敏感匹配日期列）
            date_col = None
            date_candidates = ['日期', 'date', 'trade_date', 'TRADE_DATE', 'Date']
            for col in date_candidates:
                if col in df.columns:
                    date_col = col
                    break
            # 如果精确匹配失败，尝试模糊匹配
            if date_col is None:
                for col in df.columns:
                    col_lower = col.lower()
                    if 'date' in col_lower or '日期' in col:
                        date_col = col
                        break

            if date_col:
                df = df.sort_values(date_col, ascending=False)
                latest_row = df.iloc[0]
                latest_date = str(latest_row[date_col]).replace('-', '')
            else:
                # 默认取最后一行（通常最新在末尾）
                latest_row = df.iloc[-1]
                latest_date = ""

            # 提取 LPR1Y 和 LPR5Y（大小写不敏感 + 模糊匹配）
            latest_rates = {}
            col_lower_map = {col.lower(): col for col in df.columns}
            
            lpr_targets = {
                'LPR1Y': ['lpr1y', '1年lpr', '1年', 'lpr_1y'],
                'LPR5Y': ['lpr5y', '5年lpr', '5年', 'lpr_5y'],
            }
            for key, patterns in lpr_targets.items():
                found_col = None
                for pattern in patterns:
                    if pattern in col_lower_map:
                        found_col = col_lower_map[pattern]
                        break
                if found_col:
                    try:
                        val = latest_row[found_col]
                        if val is not None and str(val) != '' and str(val) != 'nan':
                            latest_rates[key] = float(val)
                    except (ValueError, TypeError):
                        pass

            # 构建历史 DataFrame
            history_df = df.copy()
            if date_col and date_col != 'trade_date':
                history_df = history_df.rename(columns={date_col: 'trade_date'})
            if 'trade_date' in history_df.columns:
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
                error=f"AKShare LPR 获取失败: {type(e).__name__}: {e}",
            )


    def _fetch_hibor(self) -> DataResult:
        """获取 HIBOR 利率（AKShare 可能不支持，返回失败）。"""
        return DataResult(
            data_type="hibor",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="AKShare HIBOR 数据暂不可用",
        )

    def _fetch_bond_yield(self) -> DataResult:
        """获取国债收益率曲线。"""
        try:
            ak = self._get_ak()
            from datetime import datetime, timedelta
            # 窄范围查询（最近30天），宽范围返回空数据
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            self._rate_limit()
            df = ak.bond_china_yield(
                start_date=start_date,
                end_date=end_date,
            )
            if df is None or df.empty:
                return DataResult(
                    data_type="bond_yield",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="AKShare 国债收益率数据为空",
                )
            meta = {
                "record_count": len(df),
                "columns": list(df.columns),
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
                error=f"AKShare 国债收益率获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 同业存单利率（cd_rate_1y）
    # ============================================================

    CD_RATE_CURVE_NAME = "中债商业银行普通债收益率曲线(AAA)"
    CD_RATE_PERIOD_COL = "1年"

    def _fetch_cd_rate_1y(self, start_date=None, end_date=None, **kwargs):
        """
        获取1年期同业存单利率（代理：中债商业银行普通债收益率曲线(AAA) 1年期）。
        """
        try:
            ak = self._get_ak()
            from datetime import datetime, timedelta
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            if not start_date:
                sd_dt = datetime.now() - timedelta(days=365)
                start_date = sd_dt.strftime("%Y%m%d")

            self._rate_limit()
            df = ak.bond_china_yield(
                start_date=start_date,
                end_date=end_date,
            )
            if df is None or df.empty:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="AKShare 债券收益率数据为空",
                )

            # 筛选目标曲线
            curve_col = None
            for col in ['曲线名称', 'curve_name', 'curve']:
                if col in df.columns:
                    curve_col = col
                    break
            if curve_col is None:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"找不到曲线名称列，可用列: {list(df.columns)}",
                )

            df_curve = df[df[curve_col] == self.CD_RATE_CURVE_NAME].copy()
            if df_curve.empty:
                for idx, row in df.iterrows():
                    if '商业银行' in str(row[curve_col]) and 'AAA' in str(row[curve_col]):
                        df_curve = df[df[curve_col] == row[curve_col]].copy()
                        break

            if df_curve.empty:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"未找到曲线 '{self.CD_RATE_CURVE_NAME}'",
                )

            # 找日期列
            date_col = None
            for col in ['日期', 'date', 'trade_date', 'TRADE_DATE']:
                if col in df_curve.columns:
                    date_col = col
                    break

            # 找1年期列
            period_col = None
            for col in ['1年', '1Y', '1年期', '1y']:
                if col in df_curve.columns:
                    period_col = col
                    break

            if period_col is None:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"找不到1年期利率列，可用列: {list(df_curve.columns)}",
                )

            # 按日期排序
            if date_col:
                df_curve = df_curve.sort_values(date_col, ascending=False)
                latest_row = df_curve.iloc[0]
                latest_date = str(latest_row[date_col]).replace('-', '')
                latest_val = latest_row[period_col]
            else:
                latest_row = df_curve.iloc[-1]
                latest_date = ""
                latest_val = latest_row[period_col]

            try:
                latest_rate = float(latest_val)
            except (ValueError, TypeError):
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"1年期利率值无法解析: {latest_val}",
                )

            # 构建历史 DataFrame
            history_df = pd.DataFrame()
            if date_col:
                history_df = df_curve[[date_col, period_col]].copy()
                history_df.columns = ['trade_date', 'yield']
                history_df['trade_date'] = history_df['trade_date'].astype(str).str.replace('-', '')
                history_df = history_df.sort_values('trade_date', ascending=False).reset_index(drop=True)

            result = {
                "latest": {"1Y": latest_rate},
                "latest_date": latest_date,
                "curve_name": self.CD_RATE_CURVE_NAME,
                "period": "1年",
                "history": history_df,
            }

            meta = {
                "curve_name": self.CD_RATE_CURVE_NAME,
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
                error=f"AKShare 同业存单利率获取失败: {type(e).__name__}: {e}",
            )


    # ============================================================
    # ETF 份额数据
    # ============================================================

    def fetch_etf_share(self, ts_code: str = None, symbol: str = None,
                        trade_date: str = None, start_date: str = None,
                        end_date: str = None, lookback_days: int = 10,
                        **kwargs) -> DataResult:
        """
        获取ETF份额数据。

        Args:
            ts_code: ETF代码（tushare格式，如 510300.SH）
            symbol: ETF代码（6位数字，如 510300）
            trade_date: 单日日期（YYYYMMDD）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            lookback_days: 回溯天数（默认10天）

        Returns:
            DataResult.data: pd.DataFrame — ETF份额历史数据
                列: trade_date, ts_code, fund_share（份额，单位：份）
        """
        try:
            ak = self._get_ak()
            self._rate_limit()

            # 标准化代码（6位数字）
            code = None
            if symbol:
                code = symbol
            elif ts_code:
                code = ts_code.split('.')[0]

            if not code:
                return DataResult(
                    data_type="etf_share",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="缺少ETF代码参数（ts_code 或 symbol）",
                )

            # 确定交易所
            if code.startswith('5'):
                exchange = 'SSE'  # 上交所
            else:
                exchange = 'SZSE'  # 深交所

            # 确定日期范围
            from datetime import datetime, timedelta
            if end_date:
                end_dt = datetime.strptime(end_date, '%Y%m%d')
            elif trade_date:
                end_dt = datetime.strptime(trade_date, '%Y%m%d')
            else:
                end_dt = datetime.now()

            if start_date:
                start_dt = datetime.strptime(start_date, '%Y%m%d')
            else:
                start_dt = end_dt - timedelta(days=lookback_days + 10)  # 多取一些天数跳过非交易日

            # 逐日获取份额数据（AKShare只能按天获取）
            all_data = []
            current_dt = start_dt
            max_attempts = lookback_days * 2  # 最多尝试天数
            attempts = 0
            found_days = 0

            while current_dt <= end_dt and attempts < max_attempts:
                date_str = current_dt.strftime('%Y%m%d')
                attempts += 1

                try:
                    self._rate_limit()
                    if exchange == 'SSE':
                        df = ak.fund_etf_scale_sse(date=date_str)
                    else:
                        df = ak.fund_etf_scale_szse(date=date_str)

                    if df is not None and not df.empty:
                        # 标准化列名
                        code_col = None
                        share_col = None
                        date_col = None
                        for c in df.columns:
                            if '代码' in str(c):
                                code_col = c
                            elif '份额' in str(c) and '基金份额' in str(c):
                                share_col = c
                            elif '日期' in str(c) or '统计' in str(c):
                                date_col = c

                        if code_col and share_col:
                            # 筛选目标ETF
                            row = df[df[code_col].astype(str) == code]
                            if not row.empty:
                                share_val = float(row.iloc[0][share_col])
                                date_val = date_str
                                if date_col and date_col in row.columns:
                                    try:
                                        d = row.iloc[0][date_col]
                                        if hasattr(d, 'strftime'):
                                            date_val = d.strftime('%Y%m%d')
                                        else:
                                            date_val = str(d).replace('-', '')[:8]
                                    except:
                                        pass

                                all_data.append({
                                    'ts_code': f"{code}.{'SH' if exchange == 'SSE' else 'SZ'}",
                                    'trade_date': date_val,
                                    'fund_share': share_val,
                                })
                                found_days += 1
                except Exception:
                    pass  # 非交易日或数据不可用，跳过

                current_dt += timedelta(days=1)

            if not all_data:
                return DataResult(
                    data_type="etf_share",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"未获取到ETF {code} 的份额数据（尝试了 {attempts} 天）",
                )

            result_df = pd.DataFrame(all_data)
            result_df = result_df.sort_values('trade_date').reset_index(drop=True)

            meta = {
                "exchange": exchange,
                "ts_code": f"{code}.{'SH' if exchange == 'SSE' else 'SZ'}",
                "total_days": len(result_df),
                "latest_date": result_df.iloc[-1]['trade_date'],
                "latest_share": float(result_df.iloc[-1]['fund_share']),
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
                error=f"AKShare ETF份额获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 两融数据
    # ============================================================

    def fetch_margin_trading(self, trade_date: str = None, start_date: str = None,
                             end_date: str = None, market: str = 'all',
                             **kwargs) -> DataResult:
        """
        获取两融（融资融券）数据。

        Args:
            trade_date: 单日日期（YYYYMMDD）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            market: 市场（sh/sz/all，默认all汇总两市）

        Returns:
            DataResult.data: pd.DataFrame — 两融数据
                列: trade_date, rzye(融资余额), rzmre(融资买入额), 
                     rqye(融券余额), rzrqye(两融余额), exchange
        """
        try:
            ak = self._get_ak()

            # 确定日期范围
            from datetime import datetime, timedelta
            if end_date:
                end_dt = datetime.strptime(end_date, '%Y%m%d')
            elif trade_date:
                end_dt = datetime.strptime(trade_date, '%Y%m%d')
            else:
                end_dt = datetime.now()

            if start_date:
                start_dt = datetime.strptime(start_date, '%Y%m%d')
            else:
                start_dt = end_dt - timedelta(days=15)

            start_str = start_dt.strftime('%Y%m%d')
            end_str = end_dt.strftime('%Y%m%d')

            all_dfs = []

            # 沪市
            if market in ['sh', 'all', 'SSE', 'all']:
                self._rate_limit()
                try:
                    df_sh = ak.stock_margin_sse(start_date=start_str, end_date=end_str)
                    if df_sh is not None and not df_sh.empty:
                        df_sh = df_sh.copy()
                        df_sh['exchange'] = 'SSE'
                        # 统一列名（与深市对齐），避免 concat 后出现重复列名。
                        # 沪市日期列为「信用交易日期」、融券余额列为「融券余量金额」，
                        # 深市分别叫「trade_date」「融券余额」；若不统一，
                        # 重命名后会出现重复列，去重时保留第一列会导致深市整行日期为空，
                        # 进而在 groupby 汇总时被丢弃（表现为 all 市场只等于沪市）。
                        for _c in list(df_sh.columns):
                            if '日期' in str(_c):
                                df_sh = df_sh.rename(columns={_c: 'trade_date'})
                                break
                        if '融券余量金额' in df_sh.columns:
                            df_sh = df_sh.rename(columns={'融券余量金额': '融券余额'})
                        all_dfs.append(df_sh)
                except Exception:
                    pass

            # 深市
            if market in ['sz', 'all', 'SZSE', 'all']:
                self._rate_limit()
                try:
                    # 深市按日获取
                    sz_dfs = []
                    current = start_dt
                    while current <= end_dt:
                        d = current.strftime('%Y%m%d')
                        try:
                            self._rate_limit()
                            df = ak.stock_margin_szse(date=d)
                            if df is not None and not df.empty:
                                df['trade_date'] = d
                                sz_dfs.append(df)
                        except Exception:
                            pass
                        current += timedelta(days=1)
                    if sz_dfs:
                        df_sz = pd.concat(sz_dfs, ignore_index=True)
                    else:
                        df_sz = None

                    if df_sz is not None and not df_sz.empty:
                        df_sz['exchange'] = 'SZSE'
                        # 单位统一：深市 stock_margin_szse 金额列单位为「亿元」，
                        # 沪市 stock_margin_sse 为「元」。此处将深市金额列换算为元，
                        # 否则 all 市场 groupby().sum() 时深市会因差 1e8 倍而被淹没。
                        _sz_amount_cols = [
                            c for c in df_sz.columns
                            if any(k in str(c) for k in
                                   ['融资余额', '融资买入额', '融券余额', '融资融券余额'])
                        ]
                        for _c in _sz_amount_cols:
                            df_sz[_c] = pd.to_numeric(df_sz[_c], errors='coerce') * 1e8
                        all_dfs.append(df_sz)
                except Exception:
                    pass

            if not all_dfs:
                return DataResult(
                    data_type="margin_trading",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="未获取到两融数据",
                )

            df_all = pd.concat(all_dfs, ignore_index=True)

            # 标准化列名
            col_map = {}
            for c in df_all.columns:
                cl = str(c).lower()
                if '日期' in str(c) or 'trade_date' == cl:
                    col_map[c] = 'trade_date'
                elif '融资余额' in str(c) or 'rzye' == cl:
                    col_map[c] = 'rzye'
                elif '融资买入' in str(c) or 'rzmre' == cl:
                    col_map[c] = 'rzmre'
                elif '融券余额' in str(c) or ('融券' in str(c) and '金额' in str(c)) or 'rqye' == cl:
                    col_map[c] = 'rqye'
                elif '两融余额' in str(c) or '融资融券余额' in str(c) or 'rzrqye' == cl:
                    col_map[c] = 'rzrqye'

            df_all = df_all.rename(columns=col_map)

            # 修复：处理可能的重复列名（如 SSE "日期" + SZSE 自带日期列都被重命名为 trade_date）
            # 重复列会导致 groupby 报错 "Grouper for 'trade_date' not 1-dimensional"
            if df_all.columns.duplicated().any():
                dup_cols = df_all.columns[df_all.columns.duplicated()].unique().tolist()
                self.logger.debug(
                    f"[akshare] margin_trading 存在重复列: {dup_cols}，正在合并"
                )
                # 对重复列取第一列有效数据，丢弃后续重复列
                df_all = df_all.loc[:, ~df_all.columns.duplicated()]

            # 确保 trade_date 为一维 Series（避免 MultiIndex 或 DataFrame 导致 groupby 失败）
            if 'trade_date' in df_all.columns:
                trade_series = df_all['trade_date']
                if hasattr(trade_series, 'shape') and len(trade_series.shape) > 1:
                    # 多维，取第一列
                    df_all['trade_date'] = trade_series.iloc[:, 0]
                # 标准化类型为字符串
                df_all['trade_date'] = df_all['trade_date'].astype(str).str.replace('-', '').str.strip()

            # 如果是 all 市场，按日期汇总
            if market == 'all' and 'exchange' in df_all.columns:
                numeric_cols = [c for c in ['rzye', 'rzmre', 'rqye', 'rzrqye'] if c in df_all.columns]
                if numeric_cols and 'trade_date' in df_all.columns:
                    # 用 values 确保是一维分组键，避免列名重复导致的维度问题
                    df_summary = (
                        df_all
                        .groupby(df_all['trade_date'].values)[numeric_cols]
                        .sum()
                        .reset_index(drop=False)
                    )
                    # reset_index 后列名可能是 index，重命名回 trade_date
                    if df_summary.columns[0] != 'trade_date':
                        df_summary = df_summary.rename(
                            columns={df_summary.columns[0]: 'trade_date'}
                        )
                    df_summary['exchange'] = 'ALL'
                    result_df = df_summary.sort_values('trade_date').reset_index(drop=True)
                else:
                    result_df = df_all
            else:
                if 'trade_date' in df_all.columns:
                    result_df = df_all.sort_values('trade_date').reset_index(drop=True)
                else:
                    result_df = df_all.reset_index(drop=True)

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
                error=f"AKShare 两融数据获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 板块资金流数据
    # ============================================================

    def fetch_sector_flow(self, sector_type: str = 'industry', trade_date: str = None,
                          **kwargs) -> DataResult:
        """
        获取板块资金流数据。

        Args:
            sector_type: 板块类型（industry 行业 / concept 概念）
            trade_date: 交易日期（YYYYMMDD），默认今日

        Returns:
            DataResult.data: pd.DataFrame — 板块资金流数据
                列: sector_name, main_net_inflow, net_inflow_pct, change_pct, ...
        """
        try:
            ak = self._get_ak()
            self._rate_limit()

            # 确定板块类型参数
            if sector_type in ['industry', '行业', '行业资金流']:
                sector_param = '行业资金流'
            elif sector_type in ['concept', '概念', '概念资金流']:
                sector_param = '概念资金流'
            else:
                sector_param = '行业资金流'

            df = ak.stock_sector_fund_flow_rank(indicator='今日', sector_type=sector_param)

            if df is None or df.empty:
                return DataResult(
                    data_type="sector_flow",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="板块资金流数据为空",
                )

            # 标准化列名
            col_map = {}
            for c in df.columns:
                cl = str(c)
                if '名称' in cl or '板块' in cl:
                    col_map[c] = 'sector_name'
                elif '主力净流入' in cl and '净额' in cl:
                    col_map[c] = 'main_net_inflow'
                elif '主力净流入' in cl and '净占比' in cl:
                    col_map[c] = 'main_net_inflow_pct'
                elif '涨跌幅' in cl:
                    col_map[c] = 'change_pct'
                elif '公司家数' in cl:
                    col_map[c] = 'company_count'
                elif '领涨股' in cl:
                    col_map[c] = 'leading_stock'

            df = df.rename(columns=col_map)

            # 计算全市场主力净流入总额，用于计算占比
            if 'main_net_inflow' in df.columns:
                try:
                    total_inflow = df['main_net_inflow'].abs().sum()
                    if total_inflow > 0:
                        df['flow_pct_of_total'] = df['main_net_inflow'].abs() / total_inflow
                except Exception:
                    pass

            meta = {
                "sector_type": sector_type,
                "sector_count": len(df),
                "trade_date": trade_date or datetime.now().strftime('%Y%m%d'),
            }

            return DataResult(
                data=df,
                data_type="sector_flow",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )
        except Exception as e:
            return DataResult(
                data_type="sector_flow",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"AKShare 板块资金流获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 央行OMO操作数据
    # ============================================================

    def fetch_central_bank_omo(
        self,
        trade_date: str = None,
        lookback_days: int = 30,
        **kwargs,
    ) -> DataResult:
        """
        获取央行OMO操作数据。

        Args:
            trade_date: 查询日期（YYYYMMDD）
            lookback_days: 回溯天数

        Returns:
            DataResult.data: dict — OMO数据
        """
        try:
            ak = self._get_ak()
            from datetime import datetime, timedelta

            if trade_date is None:
                trade_date = datetime.now().strftime("%Y%m%d")

            # 尝试从 macro_china_new_financial_credit 获取新增信贷数据
            # 央行OMO操作量没有直接AKShare接口，使用Web降级
            # 尝试从东财获取逆回购操作数据
            omo_data = self._fetch_omo_from_eastmoney(ak, trade_date, lookback_days)

            if omo_data is not None:
                return omo_data

            # 无直接接口，返回不可用
            return DataResult(
                data_type="central_bank_omo",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="AKShare无央行OMO直接接口，需降级到Web",
            )
        except Exception as e:
            return DataResult(
                data_type="central_bank_omo",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"AKShare OMO获取失败: {type(e).__name__}: {e}",
            )

    def _fetch_omo_from_eastmoney(self, ak, trade_date: str, lookback_days: int) -> Optional[DataResult]:
        """从东方财富尝试获取OMO数据（逆回购操作公告）。"""
        try:
            from datetime import datetime, timedelta
            import requests

            # 使用东财公开市场操作接口
            end_date = datetime.strptime(trade_date, "%Y%m%d")
            start_date = end_date - timedelta(days=lookback_days)
            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")

            # 尝试 bond_china_omo 接口（部分AKShare版本支持）
            omo_func = getattr(ak, 'bond_china_omo', None)
            if omo_func is not None:
                self._rate_limit()
                df = omo_func(start_date=start_str, end_date=end_str)
                if df is not None and not df.empty:
                    return self._parse_omo_dataframe(df, trade_date)

            # 尝试 macro_china_open_market_operations
            omo_func2 = getattr(ak, 'macro_china_open_market_operations', None)
            if omo_func2 is not None:
                self._rate_limit()
                df = omo_func2()
                if df is not None and not df.empty:
                    return self._parse_omo_dataframe(df, trade_date)

            return None
        except Exception as e:
            logger.debug(f"[akshare] _fetch_omo_from_eastmoney 失败: {e}")
            return None

    def _parse_omo_dataframe(self, df: pd.DataFrame, trade_date: str) -> DataResult:
        """解析OMO操作DataFrame。"""
        try:
            # 标准化列名
            col_map = {}
            for c in df.columns:
                cl = str(c)
                if '日期' in cl or 'date' in cl.lower():
                    col_map[c] = 'date'
                elif '操作' in cl or 'operation' in cl.lower():
                    col_map[c] = 'operation'
                elif '期限' in cl or 'term' in cl.lower():
                    col_map[c] = 'term'
                elif '金额' in cl or 'amount' in cl.lower() or '亿' in cl:
                    col_map[c] = 'amount'
                elif '利率' in cl or 'rate' in cl.lower():
                    col_map[c] = 'rate'

            if col_map:
                df = df.rename(columns=col_map)

            # 按日期筛选
            if 'date' in df.columns:
                td_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                day_df = df[df['date'] == td_dash]
            else:
                day_df = df.tail(1)

            # 构建结果
            operations = {}
            net_injection = 0.0

            if 'term' in df.columns and 'amount' in df.columns:
                for _, row in day_df.iterrows():
                    term = str(row.get('term', ''))
                    amt = row.get('amount', 0)
                    try:
                        amt = float(amt) if pd.notna(amt) else 0
                    except (ValueError, TypeError):
                        amt = 0
                    operations[term] = operations.get(term, 0) + amt
                    net_injection += amt

            result_data = {
                "date": trade_date,
                "net_injection": net_injection if net_injection != 0 else None,
                "operations": operations if operations else None,
                "outstanding_balance": None,  # AKShare接口通常不提供
                "history": df,
            }

            return DataResult(
                data=result_data,
                data_type="central_bank_omo",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"trade_date": trade_date, "record_count": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="central_bank_omo",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"OMO数据解析失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 海外宏观数据
    # ============================================================

    def fetch_global_macro(
        self,
        trade_date: str = None,
        us_treasury_lookback_days: int = 20,
        **kwargs,
    ) -> DataResult:
        """
        获取海外宏观数据（DXY/美债/原油/CNH）。

        Args:
            trade_date: 查询日期（YYYYMMDD）
            us_treasury_lookback_days: 美债历史回溯天数

        Returns:
            DataResult.data: dict
        """
        try:
            ak = self._get_ak()
            from datetime import datetime, timedelta

            if trade_date is None:
                trade_date = datetime.now().strftime("%Y%m%d")

            td_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

            # 并行获取各指标
            dxy = None
            us_2y = None
            us_10y = None
            brent = None
            cnh = None
            us_2y_history = pd.DataFrame()
            us_10y_history = pd.DataFrame()
            us_10y_up_ratio_20d = None

            # 1. 美债利率 — bond_zh_us_rate
            try:
                self._rate_limit()
                df_us = ak.bond_zh_us_rate()
                if df_us is not None and not df_us.empty:
                    # 取最近N日
                    date_col = '日期'
                    df_us[date_col] = pd.to_datetime(df_us[date_col])
                    df_us = df_us.sort_values(date_col, ascending=False)

                    # 最新值
                    latest_row = df_us.iloc[0]
                    us_2y = self._safe_float(latest_row.get('美国国债收益率2年'))
                    us_10y = self._safe_float(latest_row.get('美国国债收益率10年'))

                    # 历史数据
                    history_rows = min(us_treasury_lookback_days, len(df_us))
                    df_hist = df_us.head(history_rows).sort_values(date_col, ascending=True)

                    us_2y_history = df_hist[[date_col, '美国国债收益率2年']].rename(
                        columns={date_col: 'date', '美国国债收益率2年': 'us_2y'}
                    ).reset_index(drop=True)
                    us_10y_history = df_hist[[date_col, '美国国债收益率10年']].rename(
                        columns={date_col: 'date', '美国国债收益率10年': 'us_10y'}
                    ).reset_index(drop=True)

                    # 计算20日上涨天数占比
                    if len(us_10y_history) >= 2:
                        us_10y_vals = us_10y_history['us_10y'].dropna().values
                        if len(us_10y_vals) >= 2:
                            diffs = us_10y_vals[1:] - us_10y_vals[:-1]
                            up_days = int((diffs > 0).sum())
                            total_days = len(diffs)
                            us_10y_up_ratio_20d = up_days / total_days if total_days > 0 else None
            except Exception as e:
                logger.debug(f"[akshare] bond_zh_us_rate 失败: {e}")

            # 2. 美元指数 — index_global_spot_em 或 index_global_hist_em
            try:
                self._rate_limit()
                # 实时接口
                spot_func = getattr(ak, 'index_global_spot_em', None)
                if spot_func is not None:
                    df_dxy = spot_func()
                    if df_dxy is not None and not df_dxy.empty:
                        # 找美元指数
                        for col_idx in range(min(3, len(df_dxy.columns))):
                            mask = df_dxy.iloc[:, col_idx].astype(str).str.contains('美元', na=False)
                            if mask.any():
                                row = df_dxy[mask].iloc[0]
                                # 尝试找数值列
                                for c in df_dxy.columns:
                                    val = self._safe_float(row[c])
                                    if val is not None and 90 < val < 120:
                                        dxy = val
                                        break
                                break
            except Exception as e:
                logger.debug(f"[akshare] DXY获取失败: {e}")

            # 3. 布伦特原油 — futures_foreign_hist 或 futures_foreign_commodity_realtime
            try:
                self._rate_limit()
                # 尝试实时接口
                rt_func = getattr(ak, 'futures_foreign_commodity_realtime', None)
                if rt_func is not None:
                    try:
                        df_brent = rt_func(symbol='I')
                        if df_brent is not None and not df_brent.empty:
                            for _, row in df_brent.iterrows():
                                name = str(row.iloc[0]) if len(row) > 0 else ''
                                if '布伦特' in name or 'Brent' in name:
                                    for c in df_brent.columns[1:]:
                                        val = self._safe_float(row[c])
                                        if val is not None and 50 < val < 200:
                                            brent = val
                                            break
                                    break
                    except Exception:
                        pass

                # 备用: 尝试 futures_foreign_hist
                if brent is None:
                    hist_func = getattr(ak, 'futures_foreign_hist', None)
                    if hist_func is not None:
                        try:
                            df_brent = hist_func(symbol='Brent')
                            if df_brent is not None and not df_brent.empty:
                                brent = self._safe_float(df_brent.iloc[-1].iloc[-1])
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[akshare] Brent获取失败: {e}")

            # 4. 离岸人民币 CNH — currency_boc_safe 或 forex_spot_em
            try:
                self._rate_limit()
                # 使用 currency_boc_safe 获取最近CNY中间价作为代理
                df_fx = ak.currency_boc_safe()
                if df_fx is not None and not df_fx.empty:
                    df_fx = df_fx.sort_values('日期', ascending=False)
                    latest_fx = df_fx.iloc[0]
                    # 美元列就是 CNY 中间价
                    usd_cny = self._safe_float(latest_fx.get('美元'))
                    if usd_cny is not None:
                        # USD/CNY 作为 CNH 的代理（差异通常很小）
                        cnh = usd_cny / 100.0  # currency_boc_safe 单位是元/100美元
            except Exception as e:
                logger.debug(f"[akshare] CNH获取失败: {e}")

            result_data = {
                "date": trade_date,
                "dxy": dxy,
                "us_2y": us_2y,
                "us_10y": us_10y,
                "brent": brent,
                "cnh": cnh,
                "us_2y_history": us_2y_history,
                "us_10y_history": us_10y_history,
                "us_10y_up_ratio_20d": us_10y_up_ratio_20d,
            }

            valid_count = sum(1 for k in ["dxy", "us_2y", "us_10y", "brent", "cnh"]
                            if result_data.get(k) is not None)

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
                error=f"AKShare 海外宏观获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 交易所回购利率
    # ============================================================

    def fetch_repo_exchange(
        self,
        trade_date: str = None,
        lookback_days: int = 20,
        **kwargs,
    ) -> DataResult:
        """
        获取交易所回购利率数据。

        Args:
            trade_date: 查询日期（YYYYMMDD）
            lookback_days: 历史回溯天数

        Returns:
            DataResult.data: dict
        """
        try:
            ak = self._get_ak()
            from datetime import datetime, timedelta

            if trade_date is None:
                trade_date = datetime.now().strftime("%Y%m%d")

            # 使用 repo_rate_query 获取回购定盘利率
            self._rate_limit()
            df_repo = ak.repo_rate_query()

            if df_repo is None or df_repo.empty:
                return DataResult(
                    data_type="repo_exchange",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="AKShare repo_rate_query 数据为空",
                )

            # 标准化日期列
            if 'date' in df_repo.columns:
                df_repo['date'] = pd.to_datetime(df_repo['date']).dt.strftime('%Y-%m-%d')
            elif '日期' in df_repo.columns:
                df_repo = df_repo.rename(columns={'日期': 'date'})
                df_repo['date'] = pd.to_datetime(df_repo['date']).dt.strftime('%Y-%m-%d')

            # 按日期排序
            if 'date' in df_repo.columns:
                df_repo = df_repo.sort_values('date', ascending=False)

            # 获取最新值
            latest_row = df_repo.iloc[0] if len(df_repo) > 0 else None

            latest = {}
            gc001 = None
            gc007 = None

            if latest_row is not None:
                # FR001/FR007/FR014 定盘利率
                for col_name, key in [('FR001', 'FR001'), ('FR007', 'FR007'), ('FR014', 'FR014')]:
                    val = self._safe_float(latest_row.get(col_name))
                    latest[key] = val

                # GC001 ≈ FR001 (回购定盘利率近似交易所逆回购)
                gc001 = latest.get('FR001')
                gc007 = latest.get('FR007')

            # 截取最近N日
            history = df_repo.head(lookback_days).sort_values('date', ascending=True) if 'date' in df_repo.columns else df_repo

            result_data = {
                "date": trade_date,
                "latest": latest,
                "gc001": gc001,
                "gc007": gc007,
                "history": history,
            }

            return DataResult(
                data=result_data,
                data_type="repo_exchange",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"trade_date": trade_date, "record_count": len(df_repo)},
            )
        except Exception as e:
            return DataResult(
                data_type="repo_exchange",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"AKShare 回购利率获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 国债期货历史数据
    # ============================================================

    def fetch_treasury_futures(
        self,
        symbol: str = "TL",
        lookback_days: int = 30,
        **kwargs,
    ) -> DataResult:
        """
        获取国债期货主力合约日K线数据。

        Args:
            symbol: 品种代码（TL/T/TF/TS）
            lookback_days: 回溯天数

        Returns:
            DataResult.data: dict
        """
        try:
            ak = self._get_ak()
            from datetime import datetime, timedelta

            # 品种映射
            futures_map = {
                "TL": "TL0",
                "T": "T0",
                "TF": "TF0",
                "TS": "TS0",
            }

            sina_symbol = futures_map.get(symbol.upper())
            if sina_symbol is None:
                return DataResult(
                    data_type="treasury_futures",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"不支持的国债期货品种: {symbol}",
                )

            # 计算日期范围
            end_date = datetime.now()
            start_date = end_date - timedelta(days=lookback_days + 10)  # 多取一些确保覆盖
            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")

            self._rate_limit()
            df = ak.futures_main_sina(symbol=sina_symbol, start_date=start_str, end_date=end_str)

            if df is None or df.empty:
                return DataResult(
                    data_type="treasury_futures",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"AKShare 国债期货 {symbol} 数据为空",
                )

            # 标准化列名
            col_map = {}
            for c in df.columns:
                cl = str(c)
                if '日期' in cl or 'date' in cl.lower():
                    col_map[c] = 'date'
                elif '开盘' in cl or 'open' in cl.lower():
                    col_map[c] = 'open'
                elif '最高' in cl or 'high' in cl.lower():
                    col_map[c] = 'high'
                elif '最低' in cl or 'low' in cl.lower():
                    col_map[c] = 'low'
                elif '收盘' in cl or 'close' in cl.lower():
                    col_map[c] = 'close'
                elif '成交' in cl or 'volume' in cl.lower():
                    col_map[c] = 'volume'
                elif '持仓' in cl or 'hold' in cl.lower():
                    col_map[c] = 'hold'

            df = df.rename(columns=col_map)

            # 确保date列为字符串
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                df = df.sort_values('date', ascending=True)

            # 计算衍生指标
            latest_close = None
            latest_date = None
            up_ratio_20d = None

            if 'close' in df.columns and len(df) > 0:
                latest_close = self._safe_float(df.iloc[-1]['close'])
                latest_date = df.iloc[-1].get('date', None)

                # 近20日上涨天数占比
                if len(df) >= 2:
                    n_days = min(20, len(df))
                    recent = df.tail(n_days)
                    close_vals = recent['close'].astype(float).values
                    if len(close_vals) >= 2:
                        diffs = close_vals[1:] - close_vals[:-1]
                        up_days = int((diffs > 0).sum())
                        up_ratio_20d = up_days / len(diffs)

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
            return DataResult(
                data_type="treasury_futures",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"AKShare 国债期货获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 大小盘对比指标
    # ============================================================

    def fetch_size_style(
        self,
        lookback_days: int = 60,
        base_date: str = "20211231",
        **kwargs,
    ) -> DataResult:
        """
        获取大小盘对比指标数据。

        Args:
            lookback_days: 回溯天数
            base_date: 归一化基期（YYYYMMDD）

        Returns:
            DataResult.data: dict
        """
        try:
            ak = self._get_ak()
            from datetime import datetime, timedelta

            end_date = datetime.now()
            start_date = end_date - timedelta(days=lookback_days + 30)
            start_str = start_date.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")

            # 获取沪深300和中证2000数据
            csi300_df = None
            csi2000_df = None

            # 1. 沪深300 — stock_zh_index_daily (sina)
            try:
                self._rate_limit()
                df = ak.stock_zh_index_daily(symbol='sh000300')
                if df is not None and not df.empty:
                    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                    df = df.sort_values('date', ascending=True)
                    # 筛选日期范围
                    start_dash = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:]}"
                    end_dash = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:]}"
                    csi300_df = df[(df['date'] >= start_dash) & (df['date'] <= end_dash)].copy()
                    csi300_df = csi300_df[['date', 'close']].rename(columns={'close': 'csi300_close'})
                    csi300_df['csi300_close'] = csi300_df['csi300_close'].astype(float)
            except Exception as e:
                logger.debug(f"[akshare] 沪深300获取失败: {e}")

            # 2. 中证2000 — stock_zh_index_hist_csindex
            try:
                self._rate_limit()
                df = ak.stock_zh_index_hist_csindex(symbol='932000', start_date=start_str, end_date=end_str)
                if df is not None and not df.empty:
                    # csindex接口列: 日期, 收盘, etc.
                    df = df.rename(columns={'日期': 'date', '收盘': 'close'})
                    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                    df = df.sort_values('date', ascending=True)
                    csi2000_df = df[['date', 'close']].rename(columns={'close': 'csi2000_close'})
                    csi2000_df['csi2000_close'] = csi2000_df['csi2000_close'].astype(float)
            except Exception as e:
                logger.debug(f"[akshare] 中证2000获取失败: {e}")

            if csi300_df is None or csi2000_df is None or csi300_df.empty or csi2000_df.empty:
                return DataResult(
                    data_type="size_style",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="沪深300或中证2000数据获取为空",
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

            # 计算归一化比值（基期=100）
            # ratio = (csi2000/csi300) / (csi2000_base/csi300_base) * 100
            base_dash = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:]}"
            # 获取基期数据
            base_row = merged[merged['date'] <= base_dash]
            if base_row.empty:
                # 使用最早日期作为基期
                base_row = merged.head(1)
                actual_base_date = base_row.iloc[0]['date']
            else:
                base_row = base_row.tail(1)
                actual_base_date = base_row.iloc[0]['date']

            base_ratio = base_row.iloc[0]['csi2000_close'] / base_row.iloc[0]['csi300_close']
            merged['ratio'] = (merged['csi2000_close'] / merged['csi300_close']) / base_ratio * 100.0

            # 最新值
            latest_row = merged.iloc[-1]
            ratio = self._safe_float(latest_row['ratio'])
            ratio_date = latest_row['date']
            small_cap_close = self._safe_float(latest_row['csi2000_close'])
            large_cap_close = self._safe_float(latest_row['csi300_close'])

            # 变化率
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
                error=f"AKShare 大小盘指标获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # A股实时行情（stock_spot）
    # ============================================================

    def fetch_stock_spot(self, market: str = "all", **kwargs) -> DataResult:
        """
        获取A股实时行情（全市场）。

        Args:
            market: 市场类型（all/沪市/深市/北交所等），默认 all

        Returns:
            DataResult.data: pd.DataFrame — 实时行情数据
                列包含: ts_code, symbol, name, price, change, change_pct,
                       open, high, low, prev_close, volume, amount, turnover 等
        """
        try:
            ak = self._get_ak()
            self._rate_limit()

            # 优先尝试东财接口；已知东财接口偶发 RemoteDisconnected
            df = None
            api_used = ""
            try:
                df = ak.stock_zh_a_spot_em()
                api_used = "stock_zh_a_spot_em"
            except Exception as e_eastmoney:
                logger.debug(
                    f"[akshare] 东财实时行情失败 ({type(e_eastmoney).__name__}), "
                    f"回退到新浪接口"
                )
                df = None

            # 东财失败，回退到新浪接口
            if df is None or (hasattr(df, 'empty') and df.empty):
                self._rate_limit()
                df = ak.stock_zh_a_spot()
                api_used = "stock_zh_a_spot"

            if df is None or df.empty:
                return DataResult(
                    data_type="stock_spot",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"A股实时行情数据为空 (api={api_used})",
                )

            # 标准化列名
            df = self._normalize_stock_spot_columns(df, api_used)

            meta = {
                "api": api_used,
                "market": market,
                "record_count": len(df),
            }

            return DataResult(
                data=df,
                data_type="stock_spot",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="stock_spot",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def _normalize_stock_spot_columns(self, df: pd.DataFrame, api_used: str) -> pd.DataFrame:
        """标准化A股实时行情列名（统一为 tushare 风格列名）。"""
        col_map = {
            '代码': 'symbol_raw',
            '名称': 'name',
            '最新价': 'price',
            '涨跌额': 'change',
            '涨跌幅': 'change_pct',
            '今开': 'open',
            '最高': 'high',
            '最低': 'low',
            '昨收': 'prev_close',
            '成交量': 'volume',
            '成交额': 'amount',
            '换手率': 'turnover',
            '时间戳': 'tick_time',
        }

        new_cols = {}
        for col in df.columns:
            if col in col_map:
                new_cols[col] = col_map[col]
            else:
                new_cols[col] = col.lower()

        df = df.rename(columns=new_cols)

        # 生成 ts_code 列（tushare格式）
        if 'symbol_raw' in df.columns:
            symbols = df['symbol_raw'].astype(str)
            ts_codes = []
            for s in symbols:
                s_lower = s.strip().lower()
                if s_lower.startswith('sh'):
                    ts_codes.append(s_lower[2:] + '.SH')
                elif s_lower.startswith('sz'):
                    ts_codes.append(s_lower[2:] + '.SZ')
                elif s_lower.startswith('bj'):
                    ts_codes.append(s_lower[2:] + '.BJ')
                elif len(s_lower) == 6 and s_lower.isdigit():
                    if s_lower.startswith('6'):
                        ts_codes.append(s_lower + '.SH')
                    elif s_lower.startswith(('0', '3')):
                        ts_codes.append(s_lower + '.SZ')
                    elif s_lower.startswith(('4', '8', '9')):
                        ts_codes.append(s_lower + '.BJ')
                    else:
                        ts_codes.append(s_lower + '.SH')
                else:
                    ts_codes.append(s)
            df['ts_code'] = ts_codes
            df['symbol'] = df['symbol_raw'].astype(str).str.lower()
            df = df.drop(columns=['symbol_raw'])

        numeric_cols = ['price', 'change', 'change_pct', 'open', 'high', 'low',
                        'prev_close', 'volume', 'amount', 'turnover']
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
        获取指数日线行情（专用接口，强制走指数数据源）。

        Args:
            ts_code: 指数代码（tushare格式，如 000300.SH）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            trade_date: 单日交易日期（YYYYMMDD）

        Returns:
            DataResult.data: pd.DataFrame — 指数日线行情
                列: trade_date, open, high, low, close, vol, amount, pct_chg 等
        """
        try:
            ak = self._get_ak()

            code = ts_code.split('.')[0] if ts_code else None
            if not code:
                return DataResult(
                    data_type="index_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="指数代码为空",
                )

            # 构造日期范围
            sd = start_date or trade_date
            ed = end_date or trade_date
            if sd:
                sd = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}"
            if ed:
                ed = f"{ed[:4]}-{ed[4:6]}-{ed[6:8]}"

            df = None
            # 优先用 stock_zh_index_daily（新浪数据源，稳定）
            self._rate_limit()
            try:
                idx_symbol = self._get_ak_index_symbol(code, ts_code)
                df = ak.stock_zh_index_daily(symbol=idx_symbol)
                if df is not None and not df.empty:
                    # 确保 date 列为字符串
                    if 'date' in df.columns:
                        if hasattr(df['date'].iloc[0], 'strftime'):
                            df['date'] = df['date'].apply(lambda x: x.strftime('%Y-%m-%d'))
                    # 按日期过滤
                    if sd and 'date' in df.columns:
                        df = df[df['date'] >= sd]
                    if ed and 'date' in df.columns:
                        df = df[df['date'] <= ed]
            except Exception as e1:
                logger.debug(f"[akshare] index_daily stock_zh_index_daily({code})失败: {e1}")
                df = None

            # 降级：index_zh_a_hist（东方财富数据源）
            if df is None or df.empty:
                self._rate_limit()
                try:
                    df = ak.index_zh_a_hist(
                        symbol=code,
                        period="daily",
                        start_date=sd if sd else "",
                        end_date=ed if ed else "",
                    )
                except Exception as e2:
                    logger.debug(f"[akshare] index_daily index_zh_a_hist({code})失败: {e2}")
                    df = None

            if df is None or df.empty:
                return DataResult(
                    data_type="index_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"指数 {ts_code or code} 日线数据为空",
                )

            # 标准化列名（复用 stock_daily 的标准化函数）
            df = self._normalize_stock_daily_columns(df, ts_code or code)

            return DataResult(
                data=df,
                data_type="index_daily",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "ts_code": ts_code or code,
                    "start_date": start_date or trade_date or "",
                    "end_date": end_date or trade_date or "",
                    "api": "stock_zh_index_daily/index_zh_a_hist",
                },
            )
        except Exception as e:
            logger.error(f"[akshare] index_daily 获取异常: {e}")
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
        获取指数实时行情。

        Args:
            index_codes: 指数代码列表（tushare格式或新浪格式），None=全量

        Returns:
            DataResult.data: pd.DataFrame — 指数实时行情
                列: ts_code, symbol, name, price, change, change_pct,
                    open, high, low, prev_close, volume, amount
        """
        try:
            ak = self._get_ak()
            self._rate_limit()

            df = None
            api_used = ""
            try:
                df = ak.stock_zh_index_spot_em()
                api_used = "stock_zh_index_spot_em"
            except Exception as e_eastmoney:
                logger.debug(
                    f"[akshare] 东财指数实时行情失败 ({type(e_eastmoney).__name__}), "
                    f"回退到新浪接口"
                )
                df = None

            if df is None or (hasattr(df, 'empty') and df.empty):
                self._rate_limit()
                df = ak.stock_zh_index_spot_sina()
                api_used = "stock_zh_index_spot_sina"

            if df is None or df.empty:
                return DataResult(
                    data_type="index_spot",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"指数实时行情数据为空 (api={api_used})",
                )

            # 标准化列名
            df = self._normalize_index_spot_columns(df, api_used)

            # 过滤指定指数
            if index_codes:
                lower_codes = [str(c).lower() for c in index_codes]
                mask = df['symbol'].isin(lower_codes) | df['ts_code'].isin(index_codes)
                df = df[mask].reset_index(drop=True)

            meta = {
                "api": api_used,
                "record_count": len(df),
                "index_codes": index_codes or [],
            }

            return DataResult(
                data=df,
                data_type="index_spot",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="index_spot",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def _normalize_index_spot_columns(self, df: pd.DataFrame, api_used: str) -> pd.DataFrame:
        """标准化指数实时行情列名。"""
        col_map = {
            '代码': 'symbol_raw',
            '名称': 'name',
            '最新价': 'price',
            '涨跌额': 'change',
            '涨跌幅': 'change_pct',
            '今开': 'open',
            '最高': 'high',
            '最低': 'low',
            '昨收': 'prev_close',
            '成交量': 'volume',
            '成交额': 'amount',
            '时间戳': 'tick_time',
        }

        new_cols = {}
        for col in df.columns:
            if col in col_map:
                new_cols[col] = col_map[col]
            else:
                new_cols[col] = col.lower()

        df = df.rename(columns=new_cols)

        if 'symbol_raw' in df.columns:
            symbols = df['symbol_raw'].astype(str).str.lower()
            ts_codes = []
            for s in symbols:
                s_lower = s.strip().lower()
                if s_lower.startswith('sh'):
                    ts_codes.append(s_lower[2:] + '.SH')
                elif s_lower.startswith('sz'):
                    ts_codes.append(s_lower[2:] + '.SZ')
                elif len(s_lower) == 6 and s_lower.isdigit():
                    if s_lower.startswith('399'):
                        ts_codes.append(s_lower + '.SZ')
                    else:
                        ts_codes.append(s_lower + '.SH')
                else:
                    ts_codes.append(s_lower)
            df['ts_code'] = ts_codes
            df['symbol'] = symbols
            df = df.drop(columns=['symbol_raw'])

        numeric_cols = ['price', 'change', 'change_pct', 'open', 'high', 'low',
                        'prev_close', 'volume', 'amount']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        return df

    # ============================================================
    # 期权IV历史序列

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
        获取北向资金历史数据。

        Args:
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            lookback_days: 回溯天数（默认60天）

        Returns:
            DataResult.data: pd.DataFrame
                列: trade_date, north_money(当日成交净买额, 万元),
                     buy_amount(买入成交额), sell_amount(卖出成交额),
                     cum_net_amount(历史累计净买额), hold_value(持股市值, 万元)
        """
        try:
            ak = self._get_ak()
            self._rate_limit()
            df = ak.stock_hsgt_hist_em(symbol="北向资金")
            if df is None or df.empty:
                return DataResult(
                    data_type="northbound_flow",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="AKShare返回空数据",
                )

            df = df.copy()

            # 标准化列名
            col_map = {}
            for c in df.columns:
                cl = str(c)
                if '日期' in cl:
                    col_map[c] = 'trade_date'
                elif '当日成交净买额' in cl:
                    col_map[c] = 'north_money'
                elif '买入成交额' in cl:
                    col_map[c] = 'buy_amount'
                elif '卖出成交额' in cl:
                    col_map[c] = 'sell_amount'
                elif '历史累计净买额' in cl:
                    col_map[c] = 'cum_net_amount'
                elif '持股市值' in cl and '领涨股' not in cl and '领' not in cl:
                    col_map[c] = 'hold_value'
            df = df.rename(columns=col_map)

            # 日期标准化
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(
                    df['trade_date'], errors='coerce'
                ).dt.strftime('%Y%m%d')

            # 数值列转换
            for col in ['north_money', 'buy_amount', 'sell_amount',
                        'cum_net_amount', 'hold_value']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # 日期过滤
            if end_date:
                df = df[df['trade_date'] <= end_date]
            if start_date:
                df = df[df['trade_date'] >= start_date]
            elif lookback_days and 'trade_date' in df.columns and not df.empty:
                from datetime import timedelta
                latest = df['trade_date'].max()
                try:
                    start_dt = pd.to_datetime(latest) - timedelta(days=lookback_days)
                    start_str = start_dt.strftime('%Y%m%d')
                    df = df[df['trade_date'] >= start_str]
                except Exception:
                    pass

            # 排序
            if 'trade_date' in df.columns:
                df = df.sort_values('trade_date').reset_index(drop=True)

            if df.empty:
                return DataResult(
                    data_type="northbound_flow",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="日期范围内无北向资金数据",
                )

            return DataResult(
                data=df,
                data_type="northbound_flow",
                source=self.name,
                source_tier=self.tier,
                success=True,

                meta={"rows": len(df), "latest_date": df['trade_date'].max() if 'trade_date' in df.columns else None},
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
        获取股权质押比例数据。

        Args:
            end_date: 结束日期（YYYYMMDD），默认最新

        Returns:
            DataResult.data: pd.DataFrame
                列: ts_code, name, trade_date, industry,
                     pledge_ratio(质押比例%), pledge_shares(质押股数, 万股),
                     pledge_market_value(质押市值, 万元), pledge_count(质押笔数)
        """
        try:
            ak = self._get_ak()
            self._rate_limit()
            df = ak.stock_gpzy_pledge_ratio_em()
            if df is None or df.empty:
                return DataResult(
                    data_type="equity_pledge",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="AKShare返回空数据",
                )

            df = df.copy()

            # 标准化列名
            col_map = {}
            for c in df.columns:
                cl = str(c)
                if '股票代码' in cl:
                    col_map[c] = 'ts_code'
                elif '股票简称' in cl:
                    col_map[c] = 'name'
                elif '交易日期' in cl:
                    col_map[c] = 'trade_date'
                elif '所属行业' in cl and '代码' not in cl:
                    col_map[c] = 'industry'
                elif '质押比例' in cl:
                    col_map[c] = 'pledge_ratio'
                elif '质押股数' in cl:
                    col_map[c] = 'pledge_shares'
                elif '质押市值' in cl:
                    col_map[c] = 'pledge_market_value'
                elif '质押笔数' in cl:
                    col_map[c] = 'pledge_count'
            df = df.rename(columns=col_map)

            # ts_code 补全后缀（AKShare只有6位代码）
            if 'ts_code' in df.columns:
                def _to_ts_code(code):
                    s = str(code).zfill(6)
                    if s.startswith('6') or s.startswith('9'):
                        return s + '.SH'
                    else:
                        return s + '.SZ'
                df['ts_code'] = df['ts_code'].apply(_to_ts_code)

            # 日期标准化
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(
                    df['trade_date'], errors='coerce'
                ).dt.strftime('%Y%m%d')

            # 数值列转换
            for col in ['pledge_ratio', 'pledge_shares', 'pledge_market_value', 'pledge_count']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # 按 end_date 过滤（AKShare返回的是截至最新日期的数据）
            if end_date and 'trade_date' in df.columns:
                df = df[df['trade_date'] <= end_date]

            if df.empty:
                return DataResult(
                    data_type="equity_pledge",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="无股权质押数据",
                )

            return DataResult(
                data=df,
                data_type="equity_pledge",
                source=self.name,
                source_tier=self.tier,
                success=True,

                meta={"rows": len(df), "trade_date": df['trade_date'].iloc[0] if 'trade_date' in df.columns and len(df) > 0 else None},
            )

        except Exception as e:
            return DataResult(
                data_type="equity_pledge",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )


    # ============================================================
    # 期权原始行情接口（T型报价 / 标的当日行情 / 中金所MO / 现货快照）
    # ------------------------------------------------------------
    # 说明：本组接口为「市场情绪监控」等业务脚本原有的 akshare 直连链路
    #       提供 data_hub 唯一入口，返回的 DataFrame 与 akshare 原生函数
    #       完全一致（列名、行数、数值），保证上层业务解析逻辑零改动。
    # ============================================================

    def fetch_option_board(
        self,
        symbol: str,
        end_month: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """期权T型报价（akshare: option_finance_board）。

        Args:
            symbol: 期权看板名称，如 '华泰柏瑞沪深300ETF期权' / '嘉实沪深300ETF期权'
            end_month: 到期年月，'YYMM'（如 '2610'）或 'YYYYMM'，缺省取当前月
        """
        try:
            ak = self._get_ak()
            self._rate_limit()
            if not end_month:
                from datetime import datetime as _dt
                end_month = _dt.now().strftime("%y%m")
            end_month = str(end_month)[-4:]
            df = ak.option_finance_board(symbol=symbol, end_month=end_month)
            if df is None or len(df) == 0:
                return DataResult(
                    data_type="option_board",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"option_finance_board({symbol}, {end_month}) 返回空",
                )
            return DataResult(
                data=df,
                data_type="option_board",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"symbol": symbol, "end_month": end_month, "rows": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="option_board",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_option_underlying_quote(self, symbol: str, **kwargs) -> DataResult:
        """期权标的当日行情（akshare: option_finance_sse_underlying）。

        列: 代码 / 名称 / 当前价 / 涨跌 / 涨跌幅 / 振幅 / 成交量(手) / 成交额(万元) / 更新日期
        """
        try:
            ak = self._get_ak()
            self._rate_limit()
            df = ak.option_finance_sse_underlying(symbol=symbol)
            if df is None or len(df) == 0:
                return DataResult(
                    data_type="option_underlying_quote",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"option_finance_sse_underlying({symbol}) 返回空",
                )
            return DataResult(
                data=df,
                data_type="option_underlying_quote",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"symbol": symbol, "rows": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="option_underlying_quote",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_option_cffex_zz1000_list(self, **kwargs) -> DataResult:
        """中金所中证1000股指期权合约月份列表（akshare: option_cffex_zz1000_list_sina）。

        返回 dict: {'中证1000指数': ['mo2611', 'mo2612', ...]}
        """
        try:
            ak = self._get_ak()
            self._rate_limit()
            result = ak.option_cffex_zz1000_list_sina()
            if not result:
                return DataResult(
                    data_type="option_cffex_zz1000_list",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="option_cffex_zz1000_list_sina 返回空",
                )
            return DataResult(
                data=result,
                data_type="option_cffex_zz1000_list",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"months": len(result.get("中证1000指数", [])) if isinstance(result, dict) else 0},
            )
        except Exception as e:
            return DataResult(
                data_type="option_cffex_zz1000_list",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_option_cffex_zz1000_spot(self, symbol: str, **kwargs) -> DataResult:
        """中金所中证1000股指期权T型报价（akshare: option_cffex_zz1000_spot_sina）。"""
        try:
            ak = self._get_ak()
            self._rate_limit()
            df = ak.option_cffex_zz1000_spot_sina(symbol=symbol)
            if df is None or len(df) == 0:
                return DataResult(
                    data_type="option_cffex_zz1000_spot",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"option_cffex_zz1000_spot_sina({symbol}) 返回空",
                )
            return DataResult(
                data=df,
                data_type="option_cffex_zz1000_spot",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"symbol": symbol, "rows": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="option_cffex_zz1000_spot",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_index_spot_sina(self, **kwargs) -> DataResult:
        """新浪指数实时行情全表（akshare: stock_zh_index_spot_sina）。

        列: 代码(sh000300) / 名称 / 最新价 / 涨跌额 / 涨跌幅 / 昨收 / 今开 / 最高 / 最低 / 成交量 / 成交额
        """
        try:
            ak = self._get_ak()
            self._rate_limit()
            df = ak.stock_zh_index_spot_sina()
            if df is None or len(df) == 0:
                return DataResult(
                    data_type="index_spot_sina",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="stock_zh_index_spot_sina 返回空",
                )
            return DataResult(
                data=df,
                data_type="index_spot_sina",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"rows": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="index_spot_sina",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_etf_spot_em(self, **kwargs) -> DataResult:
        """东财ETF实时行情全表（akshare: fund_etf_spot_em）。

        列: 代码(510300) / 名称 / 最新价 / ... （代码不带交易所前缀）
        """
        try:
            ak = self._get_ak()
            self._rate_limit()
            df = ak.fund_etf_spot_em()
            if df is None or len(df) == 0:
                return DataResult(
                    data_type="etf_spot_em",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="fund_etf_spot_em 返回空",
                )
            return DataResult(
                data=df,
                data_type="etf_spot_em",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"rows": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="etf_spot_em",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_news(self, news_type: str = "stock", symbol: str = None,
                  date: str = None, **kwargs) -> DataResult:
        """
        获取新闻数据（AKShare 新闻接口）。

        Args:
            news_type: 新闻类型（stock 个股新闻 / cctv 央视新闻）
            symbol: 股票代码（news_type=stock 时必填，6位数字）
            date: 日期（news_type=cctv 时可选，YYYYMMDD）

        Returns:
            DataResult.data — DataFrame
        """
        try:
            ak = self._get_ak()
            self._rate_limit()
            news_type = (news_type or "stock").lower()
            if news_type == "stock":
                if not symbol:
                    return DataResult(
                        data_type="news",
                        source=self.name,
                        source_tier=self.tier,
                        success=False,
                        error="news_type=stock 需要 symbol 参数",
                    )
                df = ak.stock_news_em(symbol=symbol)
            elif news_type == "cctv":
                if date is None:
                    date = pd.Timestamp.now().strftime("%Y%m%d")
                df = ak.news_cctv(date=date)
            else:
                return DataResult(
                    data_type="news",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"不支持的新闻类型: {news_type}",
                )
            if df is None or len(df) == 0:
                return DataResult(
                    data_type="news",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="AKShare 新闻返回空",
                )
            return DataResult(
                data=df,
                data_type="news",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"rows": len(df), "news_type": news_type,
                      "symbol": symbol, "date": date},
            )
        except Exception as e:
            return DataResult(
                data_type="news",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )
