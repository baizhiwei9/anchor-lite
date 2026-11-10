"""
Coze 数据网关数据源适配器
=========================
三级数据源：通过 Coze data-provider 网关访问 akshare 期货期权 + 宏观固收两个技能的 CLI。
优先级：Tier 3（AKShare -> Tushare -> Coze网关 -> Web）
用途：当本地AKShare和Tushare都失败时，走Coze官方网关兜底
      （午盘实时数据也优先尝试从网关拿，更稳定）
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseDataSource, DataResult
from ..config import TIMEOUT_CONFIG, QUALITY_THRESHOLDS, logger


# 两个技能的CLI相对路径（相对于工作目录）
FUTURES_OPTIONS_CLI = ".skills/skill_akshare-futures-options/bin/_cli_wrapper.py"
MACRO_FIXED_INCOME_CLI = ".skills/skill_akshare-macro-fixed-income-forex/bin/_cli_wrapper.py"

# 工作目录根
WORKDIR = "/Coze/Drive/蓁宝/所有对话/主对话"


class CozeGatewaySource(BaseDataSource):
    """Coze数据网关适配器（三级数据源）。"""

    name: str = "coze_gateway"
    tier: int = 3

    def __init__(self):
        super().__init__()
        self._last_call_ts: float = 0.0

    def _rate_limit(self):
        """网关调用间隔控制。"""
        elapsed = time.time() - self._last_call_ts
        min_interval = getattr(TIMEOUT_CONFIG, "coze_gateway_min_interval", 0.3)
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_call_ts = time.time()

    def _call_cli(self, cli_path: str, op: str, params: Optional[Dict[str, str]] = None) -> Optional[str]:
        """调用技能CLI，返回stdout原始文本。失败返回None。"""
        try:
            self._rate_limit()
            cmd = ["python3", cli_path, "call", op]
            if params:
                for k, v in params.items():
                    cmd.extend([f"--param", f"{k}={v}"])

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=getattr(TIMEOUT_CONFIG, "coze_gateway_timeout", 30),
                cwd=WORKDIR,
            )

            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")[:200]
                logger.debug(f"[coze_gateway] CLI {op} 失败 exit={result.returncode}: {stderr}")
                return None

            # 输出可能是GBK/UTF-8混合编码，用utf-8宽松解码
            try:
                output = result.stdout.decode("utf-8").strip()
            except UnicodeDecodeError:
                output = result.stdout.decode("gbk", errors="replace").strip()
            if not output:
                logger.debug(f"[coze_gateway] CLI {op} 返回空")
                return None

            return output
        except subprocess.TimeoutExpired:
            logger.warning(f"[coze_gateway] CLI {op} 超时")
            return None
        except Exception as e:
            logger.debug(f"[coze_gateway] CLI {op} 异常: {e}")
            return None

    def _parse_json_result(self, text: str) -> Optional[Any]:
        """尝试解析JSON，返回数据部分（自动剥 result/data 包装）。"""
        try:
            data = json.loads(text)
            # 格式1: {result: {data: [...], ...}, success: true}
            if isinstance(data, dict) and "result" in data:
                inner = data["result"]
                if isinstance(inner, dict) and "data" in inner:
                    return inner["data"]
                return inner
            # 格式2: {products, keys, values} (shibor类)
            if isinstance(data, dict) and ("products" in data or "values" in data):
                return data
            return data
        except json.JSONDecodeError:
            return None

    def is_available(self) -> bool:
        """检查CLI是否存在。"""
        return (
            os.path.exists(os.path.join(WORKDIR, FUTURES_OPTIONS_CLI))
            or os.path.exists(os.path.join(WORKDIR, MACRO_FIXED_INCOME_CLI))
        )

    # ============================================================
    # 期货实时行情
    # ============================================================

    def fetch_futures_spot(self, symbols: List[str], **kwargs) -> DataResult:
        """
        获取期货实时行情。
        注意：仅交易时段返回有效数据，非交易时段可能为空。

        Args:
            symbols: 合约代码列表，如 ['nf_IF0', 'nf_IC0']
                     金融期货前缀 nf_，商品期货前缀 nc_

        Returns:
            DataResult.data: dict {合约代码: {name, pre_settlement, open, high, low,
                                            last, bid1, ask1, volume, open_interest, date, time}}
        """
        try:
            symbol_str = ",".join(symbols)
            output = self._call_cli(FUTURES_OPTIONS_CLI, "futures-zh-spot", {"symbols": symbol_str})
            if output is None:
                return DataResult(
                    data_type="futures_spot", source=self.name, source_tier=self.tier,
                    success=False, error="CLI调用失败或返回空",
                )

            data = self._parse_hq_str(output)
            if not data:
                return DataResult(
                    data_type="futures_spot", source=self.name, source_tier=self.tier,
                    success=False, error="解析结果为空（可能非交易时段）",
                )

            return DataResult(
                data=data, data_type="futures_spot", source=self.name, source_tier=self.tier,
                success=True, meta={"count": len(data)},
            )
        except Exception as e:
            return DataResult(
                data_type="futures_spot", source=self.name, source_tier=self.tier,
                success=False, error=f"{type(e).__name__}: {e}",
            )

    @staticmethod
    def _parse_hq_str(text: str) -> Dict[str, Dict[str, Any]]:
        """解析 hq_str_ 格式文本。
        金融期货字段顺序（新浪 hq.sinajs.cn）:
        0=最新价 1=昨结算 2=今开盘 3=最高 4=最低 5=买1价 6=卖1价
        7=成交量(手) 8=成交额 9=持仓量 ...
        倒数第几个字段是日期/时间

        因为不同品种字段顺序略有差异，我们用启发式识别：
        - 找日期格式 YYYY-MM-DD 和时间 HH:MM:SS
        - 前几个字段按固定位置解析（0-6一般是价格相关）
        """
        result = {}
        for line in text.strip().split("\n"):
            line = line.strip().rstrip(";")
            if not line.startswith("var hq_str_"):
                continue
            try:
                left, right = line.split("=", 1)
                code = left.replace("var hq_str_", "").strip()
                values = right.strip('"').split(",")
                if len(values) < 10:
                    continue

                # 找日期和时间
                date_val = None
                time_val = None
                for i in range(len(values) - 1, max(0, len(values) - 15), -1):
                    v = values[i].strip()
                    if not date_val and len(v) == 10 and v[4] == '-' and v[7] == '-':
                        date_val = v
                    elif not time_val and len(v) >= 5 and ':' in v:
                        time_val = v
                    if date_val and time_val:
                        break

                def _sf(idx):
                    """safe float"""
                    if idx < len(values) and values[idx]:
                        try:
                            return float(values[idx])
                        except (ValueError, TypeError):
                            return None
                    return None

                def _si(idx):
                    """safe int"""
                    v = _sf(idx)
                    return int(v) if v is not None else None

                # 金融期货字段（经验位置）
                result[code] = {
                    "name": code,  # 名字编码到code里了，文本在最后
                    "last": _sf(0),          # 最新价
                    "pre_settlement": _sf(1), # 昨结算
                    "open": _sf(2),          # 今开盘
                    "high": _sf(3),          # 最高
                    "low": _sf(4),           # 最低
                    "bid1": _sf(5),          # 买1
                    "ask1": _sf(6),          # 卖1
                    "volume": _si(7),        # 成交量(手)
                    "turnover": _sf(8),      # 成交额
                    "open_interest": _sf(9), # 持仓量
                    "date": date_val,
                    "time": time_val,
                }
            except Exception:
                continue
        return result

    # ============================================================
    # 期权 - 上交所合约列表
    # ============================================================

    def fetch_option_sse_list(self, symbol: str = "510300", **kwargs) -> DataResult:
        """获取上交所ETF期权合约列表。"""
        try:
            output = self._call_cli(FUTURES_OPTIONS_CLI, "option-sse-list-sina", {"symbol": symbol})
            if output is None:
                return DataResult(
                    data_type="option_sse_list", source=self.name, source_tier=self.tier,
                    success=False, error="CLI调用失败",
                )
            data = self._parse_json_result(output)
            if data is None:
                return DataResult(
                    data_type="option_sse_list", source=self.name, source_tier=self.tier,
                    success=False, error="返回格式无法解析",
                )
            return DataResult(
                data=data, data_type="option_sse_list", source=self.name, source_tier=self.tier,
                success=True,
            )
        except Exception as e:
            return DataResult(
                data_type="option_sse_list", source=self.name, source_tier=self.tier,
                success=False, error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # 期权 - 上交所单合约实时行情
    # ============================================================

    def fetch_option_sse_spot(self, symbol: str, **kwargs) -> DataResult:
        """获取上交所期权单合约实时行情（含IV/Greeks）。"""
        try:
            output = self._call_cli(FUTURES_OPTIONS_CLI, "option-sse-spot-sina", {"symbol": symbol})
            if output is None:
                return DataResult(
                    data_type="option_sse_spot", source=self.name, source_tier=self.tier,
                    success=False, error="CLI调用失败",
                )
            data = self._parse_json_result(output)
            if data is None:
                return DataResult(
                    data_type="option_sse_spot", source=self.name, source_tier=self.tier,
                    success=False, error="返回格式无法解析",
                )
            return DataResult(
                data=data, data_type="option_sse_spot", source=self.name, source_tier=self.tier,
                success=True,
            )
        except Exception as e:
            return DataResult(
                data_type="option_sse_spot", source=self.name, source_tier=self.tier,
                success=False, error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # 期权 - 中金所合约列表
    # ============================================================

    def fetch_option_cffex_list(self, underlying: str = "hs300", **kwargs) -> DataResult:
        """获取中金所股指期权合约列表。underlying: hs300/zz1000/sz50"""
        try:
            output = self._call_cli(FUTURES_OPTIONS_CLI, "option-cffex-list-sina", {"underlying": underlying})
            if output is None:
                return DataResult(
                    data_type="option_cffex_list", source=self.name, source_tier=self.tier,
                    success=False, error="CLI调用失败",
                )
            data = self._parse_json_result(output)
            if data is None:
                return DataResult(
                    data_type="option_cffex_list", source=self.name, source_tier=self.tier,
                    success=False, error="返回格式无法解析",
                )
            return DataResult(
                data=data, data_type="option_cffex_list", source=self.name, source_tier=self.tier,
                success=True,
            )
        except Exception as e:
            return DataResult(
                data_type="option_cffex_list", source=self.name, source_tier=self.tier,
                success=False, error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # SHIBOR
    # ============================================================

    def fetch_shibor(self, date: Optional[str] = None, **kwargs) -> DataResult:
        """
        获取SHIBOR利率。
        返回DataFrame: date, O/N, 1W, 2W, 1M, 3M, 6M, 9M, 1Y
        """
        try:
            params = {}
            if date:
                params["date"] = date
            output = self._call_cli(MACRO_FIXED_INCOME_CLI, "macro-china-shibor", params)
            if output is None:
                return DataResult(
                    data_type="shibor", source=self.name, source_tier=self.tier,
                    success=False, error="CLI调用失败",
                )

            raw = self._parse_json_result(output)
            if raw is None or not isinstance(raw, dict) or "values" not in raw:
                return DataResult(
                    data_type="shibor", source=self.name, source_tier=self.tier,
                    success=False, error="返回格式无法解析",
                )

            # 转换为 DataFrame: 每行一个日期，列是各期限
            products = raw.get("products", ["O/N", "1W", "2W", "1M", "3M", "6M", "9M", "1Y"])
            values = raw.get("values", {})
            rows = []
            for dt, term_data in sorted(values.items(), reverse=True):
                row = {"date": dt}
                for term in products:
                    if term in term_data and term_data[term]:
                        try:
                            row[term] = float(term_data[term][0])
                        except (ValueError, TypeError):
                            row[term] = None
                    else:
                        row[term] = None
                rows.append(row)

            df = pd.DataFrame(rows)
            return DataResult(
                data=df, data_type="shibor", source=self.name, source_tier=self.tier,
                success=True, meta={"count": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="shibor", source=self.name, source_tier=self.tier,
                success=False, error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # LPR
    # ============================================================

    def fetch_lpr(self, **kwargs) -> DataResult:
        """
        获取LPR贷款市场报价利率。
        返回DataFrame: TRADE_DATE, LPR1Y, LPR5Y
        """
        try:
            output = self._call_cli(MACRO_FIXED_INCOME_CLI, "macro-china-lpr", {})
            if output is None:
                return DataResult(
                    data_type="lpr", source=self.name, source_tier=self.tier,
                    success=False, error="CLI调用失败",
                )

            raw = self._parse_json_result(output)
            if raw is None:
                return DataResult(
                    data_type="lpr", source=self.name, source_tier=self.tier,
                    success=False, error="返回格式无法解析",
                )

            if isinstance(raw, list):
                df = pd.DataFrame(raw)
            elif isinstance(raw, dict):
                df = pd.DataFrame([raw])
            else:
                df = pd.DataFrame()

            return DataResult(
                data=df, data_type="lpr", source=self.name, source_tier=self.tier,
                success=True, meta={"count": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="lpr", source=self.name, source_tier=self.tier,
                success=False, error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # HIBOR
    # ============================================================

    def fetch_hibor(self, start_date: Optional[str] = None, end_date: Optional[str] = None, **kwargs) -> DataResult:
        """
        获取HIBOR香港银行同业拆息。
        返回DataFrame: date, O/N, 1W, 2W, 1M, ... （格式与SHIBOR一致）
        """
        try:
            params = {}
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            output = self._call_cli(MACRO_FIXED_INCOME_CLI, "rate-hibor", params)
            if output is None:
                return DataResult(
                    data_type="hibor", source=self.name, source_tier=self.tier,
                    success=False, error="CLI调用失败",
                )

            raw = self._parse_json_result(output)
            if raw is None or not isinstance(raw, dict) or "values" not in raw:
                return DataResult(
                    data_type="hibor", source=self.name, source_tier=self.tier,
                    success=False, error="返回格式无法解析",
                )

            # HIBOR格式与SHIBOR相同: {products, keys, values:{日期:{期限:[定价,涨跌]}}}
            # 但期限命名可能略有不同（ON vs O/N），统一转成标准名
            products = raw.get("products", [])
            values = raw.get("values", {})

            # 期限名称映射（统一为O/N, 1W等格式）
            term_map = {
                "ON": "O/N", "O/N": "O/N",
                "1W": "1W", "2W": "2W",
                "1M": "1M", "2M": "2M", "3M": "3M",
                "6M": "6M", "9M": "9M", "1Y": "1Y",
            }
            norm_products = [term_map.get(p, p) for p in products]

            rows = []
            for dt, term_data in sorted(values.items(), reverse=True):
                row = {"date": dt}
                for orig_term, norm_term in zip(products, norm_products):
                    if orig_term in term_data and term_data[orig_term]:
                        try:
                            row[norm_term] = float(term_data[orig_term][0])
                        except (ValueError, TypeError):
                            row[norm_term] = None
                    else:
                        row[norm_term] = None
                rows.append(row)

            df = pd.DataFrame(rows)
            return DataResult(
                data=df, data_type="hibor", source=self.name, source_tier=self.tier,
                success=True, meta={"count": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="hibor", source=self.name, source_tier=self.tier,
                success=False, error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # 外汇即期汇率
    # ============================================================

    def fetch_forex_quote(self, symbol: str = "USDCNH", **kwargs) -> DataResult:
        """获取外汇即期汇率。"""
        try:
            output = self._call_cli(MACRO_FIXED_INCOME_CLI, "forex-pair-quote", {"symbol": symbol})
            if output is None:
                return DataResult(
                    data_type="forex_quote", source=self.name, source_tier=self.tier,
                    success=False, error="CLI调用失败",
                )
            data = self._parse_json_result(output)
            if data is None:
                return DataResult(
                    data_type="forex_quote", source=self.name, source_tier=self.tier,
                    success=False, error="返回格式无法解析",
                )
            return DataResult(
                data=data, data_type="forex_quote", source=self.name, source_tier=self.tier,
                success=True,
            )
        except Exception as e:
            return DataResult(
                data_type="forex_quote", source=self.name, source_tier=self.tier,
                success=False, error=f"{type(e).__name__}: {e}",
            )

    # ============================================================
    # 利率数据（统一入口，被 InterestRateCollector 调用）
    # ============================================================

    def fetch_interest_rate(
        self,
        rate_type: str = "shibor",
        **kwargs,
    ) -> DataResult:
        """
        获取利率数据（统一入口，与AKShare/Tushare source保持接口一致）。

        Args:
            rate_type: shibor / lpr / hibor / bond_yield

        Returns:
            DataResult.data — dict with latest, latest_date, history
        """
        rate_type = rate_type.lower()

        try:
            if rate_type == "shibor":
                return self._build_rate_result("shibor", self.fetch_shibor(**kwargs))
            elif rate_type == "lpr":
                return self._build_lpr_result(self.fetch_lpr(**kwargs))
            elif rate_type == "hibor":
                return self._build_rate_result("hibor", self.fetch_hibor(**kwargs))
            elif rate_type == "bond_yield":
                # 网关没有bond_yield接口，直接返回失败让上层降级
                return DataResult(
                    data_type="interest_rate_bond_yield",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="网关不支持bond_yield",
                )
            elif rate_type == "cd_rate_1y":
                return self._fetch_cd_rate_1y_gateway(**kwargs)
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

    @staticmethod
    def _build_rate_result(rate_name: str, raw_result: DataResult) -> DataResult:
        """把fetch_shibor/fetch_hibor返回的DataFrame格式转为{latest, latest_date, history}格式。"""
        if not raw_result.success or raw_result.data is None:
            return DataResult(
                data_type=f"interest_rate_{rate_name}",
                source=raw_result.source,
                source_tier=raw_result.source_tier,
                success=False,
                error=raw_result.error,
            )

        df = raw_result.data
        if df.empty:
            return DataResult(
                data_type=f"interest_rate_{rate_name}",
                source=raw_result.source,
                source_tier=raw_result.source_tier,
                success=False,
                error="数据为空",
            )

        latest_row = df.iloc[0]
        latest_date = str(latest_row.get("date", ""))

        # 构建latest dict（排除date列）
        latest = {}
        for col in df.columns:
            if col != "date":
                val = latest_row.get(col)
                if val is not None and not pd.isna(val):
                    latest[col] = float(val)

        result_data = {
            "latest": latest,
            "latest_date": latest_date,
            "history": df,
        }

        return DataResult(
            data=result_data,
            data_type=f"interest_rate_{rate_name}",
            source=raw_result.source,
            source_tier=raw_result.source_tier,
            success=True,
            meta=raw_result.meta,
        )

    @staticmethod
    def _build_lpr_result(raw_result: DataResult) -> DataResult:
        """把fetch_lpr返回的DataFrame转为{latest, latest_date, history}格式。"""
        if not raw_result.success or raw_result.data is None:
            return DataResult(
                data_type="interest_rate_lpr",
                source=raw_result.source,
                source_tier=raw_result.source_tier,
                success=False,
                error=raw_result.error,
            )

        df = raw_result.data
        if df.empty:
            return DataResult(
                data_type="interest_rate_lpr",
                source=raw_result.source,
                source_tier=raw_result.source_tier,
                success=False,
                error="数据为空",
            )

        latest_row = df.iloc[0]
        # 列名可能是 TRADE_DATE, LPR1Y, LPR5Y, RATE_1, RATE_2
        latest_date = str(latest_row.get("TRADE_DATE", latest_row.get("trade_date", "")))
        latest = {}
        if "LPR1Y" in df.columns:
            latest["LPR1Y"] = float(latest_row["LPR1Y"])
        if "LPR5Y" in df.columns:
            latest["LPR5Y"] = float(latest_row["LPR5Y"])

        result_data = {
            "latest": latest,
            "latest_date": latest_date,
            "history": df,
        }

        return DataResult(
            data=result_data,
            data_type="interest_rate_lpr",
            source=raw_result.source,
            source_tier=raw_result.source_tier,
            success=True,
            meta=raw_result.meta,
        )

    # ============================================================
    # 同业存单利率（cd_rate_1y）- 网关层
    # ============================================================

    def _fetch_cd_rate_1y_gateway(self, **kwargs) -> DataResult:
        """
        通过Coze网关获取1年期同业存单利率（兜底）。

        使用 bond-zh-hs-daily 接口，尝试从交易所债券数据中估算1年期信用债利率。
        注意：交易所债券价格数据不直接提供收益率曲线，此层仅作兜底尝试，
              成功率较低，主要依赖 AKShare 层获取准确数据。
        """
        try:
            # 尝试用 bond-zh-hs-daily 获取国债数据，用1年期国债收益率作参考
            # （网关没有直接的收益率曲线接口）
            output = self._call_cli(
                MACRO_FIXED_INCOME_CLI,
                "bond-zh-hs-daily",
                {"symbol": "019700"},  # 示例国债代码，仅作兜底尝试
            )
            if output is None:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="网关 bond-zh-hs-daily 调用失败",
                )

            data = self._parse_json_result(output)
            if data is None:
                return DataResult(
                    data_type="cd_rate_1y",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="网关返回数据无法解析",
                )

            # 网关无直接收益率曲线数据，返回"不支持"让上层降级
            return DataResult(
                data_type="cd_rate_1y",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="网关无同业存单收益率曲线接口，建议使用AKShare层",
            )

        except Exception as e:
            return DataResult(
                data_type="cd_rate_1y",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"网关同业存单利率获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 期权数据（统一入口，被 OptionDataCollector 调用）
    # ============================================================

    def fetch_option_data(
        self,
        exchange: str = "SSE",
        underlying: str = "510300",
        trade_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取期权数据（统一入口）。
        注意：非交易时段合约列表可能为空，属正常现象。
        """
        try:
            exchange = exchange.upper()
            result_data = {
                "contracts": pd.DataFrame(),
                "daily": pd.DataFrame(),
                "underlying_price": None,
            }

            if exchange == "SSE":
                list_result = self.fetch_option_sse_list(symbol=underlying)
                if list_result.success:
                    # 解析合约列表
                    raw = list_result.data
                    contracts_up = []
                    contracts_down = []
                    if isinstance(raw, dict):
                        for key, val in raw.items():
                            if "UP" in key.upper() and isinstance(val, list):
                                contracts_up = val
                            elif "DOWN" in key.upper() and isinstance(val, list):
                                contracts_down = val
                    all_contracts = contracts_up + contracts_down
                    if all_contracts:
                        result_data["contracts"] = pd.DataFrame({"code": all_contracts})

            elif exchange == "CFFEX":
                list_result = self.fetch_option_cffex_list(underlying=underlying)
                if list_result.success and isinstance(list_result.data, list):
                    result_data["contracts"] = pd.DataFrame({"code": list_result.data})

            # 判断是否有数据
            has_contracts = not result_data["contracts"].empty
            if not has_contracts:
                return DataResult(
                    data=result_data,
                    data_type="option_data",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="期权合约列表为空（可能非交易时段）",
                )

            return DataResult(
                data=result_data,
                data_type="option_data",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"exchange": exchange, "underlying": underlying},
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
    # 期货基差（被 FuturesBasisCollector 调用）
    # ============================================================

    def fetch_futures_basis(
        self,
        index_code: str = "000300.SH",
        trade_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        获取股指期货实时行情（用于基差计算）。
        返回格式与Tushare基差数据尽量兼容。
        """
        try:
            # 指数代码到期货合约的映射
            index_to_futures = {
                "000300.SH": "nf_IF0",
                "000905.SH": "nf_IC0",
                "000852.SH": "nf_IM0",
                "000016.SH": "nf_IH0",
            }
            futures_code = index_to_futures.get(index_code, "nf_IF0")

            spot_result = self.fetch_futures_spot(symbols=[futures_code])
            if not spot_result.success:
                return DataResult(
                    data_type="futures_basis",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=spot_result.error,
                )

            # 转换为基差采集器能理解的格式
            data = spot_result.data
            records = []
            for code, info in data.items():
                records.append({
                    "ts_code": code,
                    "close": info.get("last"),
                    "pre_settle": info.get("pre_settlement"),
                    "open": info.get("open"),
                    "high": info.get("high"),
                    "low": info.get("low"),
                    "vol": info.get("volume"),
                    "oi": info.get("open_interest"),
                    "trade_date": info.get("date", "").replace("-", "") if info.get("date") else "",
                })

            df = pd.DataFrame(records)
            return DataResult(
                data=df,
                data_type="futures_basis",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"index_code": index_code, "count": len(df)},
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
    # 单个期货合约历史日线（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_futures_contract_daily(self, **kwargs) -> DataResult:
        """
        单个具体期货合约日线（Coze网关层）。
        当前暂无对应技能CLI，直接返回降级信号，由上层降级到 Web 层。
        """
        return DataResult(
            data_type="futures_contract_daily",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无期货合约日线技能CLI，降级到Web层",
        )

    # ============================================================
    # ETF 份额数据（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_etf_share(self, **kwargs) -> DataResult:
        """
        ETF份额数据（技能层兜底）。
        目前没有专门的ETF份额技能CLI，返回失败让系统降级到Web层。
        """
        return DataResult(
            data_type="etf_share",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无ETF份额数据技能，降级到Web层",
        )

    # ============================================================
    # 两融数据（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_margin_trading(self, **kwargs) -> DataResult:
        """
        两融数据（技能层兜底）。
        目前没有专门的两融数据技能CLI，返回失败让系统降级到Web层。
        """
        return DataResult(
            data_type="margin_trading",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无两融数据技能，降级到Web层",
        )

    # ============================================================
    # 板块资金流数据（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_sector_flow(self, **kwargs) -> DataResult:
        """
        板块资金流数据（技能层兜底）。
        目前没有专门的板块资金流技能CLI，返回失败让系统降级到Web层。
        """
        return DataResult(
            data_type="sector_flow",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无板块资金流技能，降级到Web层",
        )

    # ============================================================
    # 央行OMO操作数据（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_central_bank_omo(self, **kwargs) -> DataResult:
        """央行OMO数据（技能层兜底）。暂无专门技能CLI，降级到Web层。"""
        return DataResult(
            data_type="central_bank_omo",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无央行OMO技能，降级到Web层",
        )

    # ============================================================
    # 海外宏观数据（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_global_macro(self, **kwargs) -> DataResult:
        """海外宏观数据（技能层兜底）。暂无专门技能CLI，降级到Web层。"""
        return DataResult(
            data_type="global_macro",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无海外宏观技能，降级到Web层",
        )

    # ============================================================
    # 交易所回购利率（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_repo_exchange(self, **kwargs) -> DataResult:
        """交易所回购利率（技能层兜底）。暂无专门技能CLI，降级到Web层。"""
        return DataResult(
            data_type="repo_exchange",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无交易所回购利率技能，降级到Web层",
        )

    # ============================================================
    # 国债期货历史数据（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_treasury_futures(self, **kwargs) -> DataResult:
        """国债期货历史数据（技能层兜底）。暂无专门技能CLI，降级到Web层。"""
        return DataResult(
            data_type="treasury_futures",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无国债期货技能，降级到Web层",
        )

    # ============================================================
    # 大小盘对比指标（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_size_style(self, **kwargs) -> DataResult:
        """大小盘对比指标（技能层兜底）。暂无专门技能CLI，降级到Web层。"""
        return DataResult(
            data_type="size_style",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无大小盘指标技能，降级到Web层",
        )

    # ============================================================
    # A股实时行情（stock_spot）—— 技能层兜底
    # ============================================================

    def fetch_stock_spot(self, market: str = "all", **kwargs) -> DataResult:
        """
        A股实时行情（Coze网关层）。
        尝试通过 china-market-gateway / stock-data-skill 技能CLI获取。
        当前暂未配置对应技能CLI，直接返回降级信号。
        """
        return DataResult(
            data_type="stock_spot",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无A股实时行情技能CLI，降级到Web层",
        )

    # ============================================================
    # 指数日线行情（index_daily）—— 技能层兜底
    # ============================================================

    def fetch_index_daily(self, ts_code=None, **kwargs) -> DataResult:
        """
        指数日线行情（Coze网关层）。
        当前暂未配置对应技能CLI，直接返回降级信号。
        """
        return DataResult(
            data_type="index_daily",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无指数日线技能CLI，降级到Web层",
        )

    # ============================================================
    # 指数实时行情（index_spot）—— 技能层兜底
    # ============================================================

    def fetch_index_spot(self, index_codes=None, **kwargs) -> DataResult:
        """
        指数实时行情（Coze网关层）。
        当前暂未配置对应技能CLI，直接返回降级信号。
        """
        return DataResult(
            data_type="index_spot",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无指数实时行情技能CLI，降级到Web层",
        )

    # ============================================================
    # 期权IV历史序列（option_iv_history）—— 技能层兜底
    # ============================================================

    def fetch_option_iv_history(
        self,
        underlying_code: str,
        start_date: str,
        end_date: str,
        **kwargs,
    ) -> DataResult:
        """
        期权IV历史序列（Coze网关层）。
        当前暂未配置对应技能CLI，直接返回降级信号。
        """
        return DataResult(
            data_type="option_iv_history",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无期权IV历史技能CLI，降级到Web层",
        )

    # ============================================================
    # 北向资金数据（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_northbound_flow(self, **kwargs) -> DataResult:
        """北向资金数据（技能层兜底）。暂无专门技能CLI，降级到Web层。"""
        return DataResult(
            data_type="northbound_flow",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无北向资金技能，降级到Web层",
        )

    # ============================================================
    # 股权质押数据（技能层 - 目前未实现，走降级）
    # ============================================================

    def fetch_equity_pledge(self, **kwargs) -> DataResult:
        """股权质押数据（技能层兜底）。暂无专门技能CLI，降级到Web层。"""
        return DataResult(
            data_type="equity_pledge",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Coze网关暂无股权质押技能，降级到Web层",
        )
