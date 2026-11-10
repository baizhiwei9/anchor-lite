"""
ETF/LOF 日线行情采集器（前复权）
==============================
采集场内 ETF/LOF 历史日线（前复权 qfq），补齐 stock_daily 对 ETF 代码的覆盖缺口。

背景: stock_daily 的 akshare 层用 stock_zh_a_hist（面向个股），对 ETF 代码
      会返回空数据或未复权数据（基金份额折算产生 -80% 假跳空）；web 层对部分
      跨境 ETF 也返回空。本采集器专用于 ETF/LOF，统一前复权。

两级降级:
  Tier 1: AKShare fund_etf_hist_em(adjust='qfq')   —— 前复权，覆盖基金折算
  Tier 2: 东方财富 push2his kline(fqt=1)           —— 前复权兜底

用法:
    collector = EtfDailyCollector()
    result = collector.collect(code="510300", start_date="20120101", end_date="20261110")
    df = result.data  # DataFrame: trade_date/open/close/high/low/vol/amount/ts_code
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

import pandas as pd

from ..base import BaseCollector, DataResult
from ..config import logger


class EtfDailyCollector(BaseCollector):
    """ETF/LOF 日线（前复权）采集器。"""

    data_type: str = "etf_daily"
    min_data_count: int = 10

    def is_data_sufficient(self, result: DataResult) -> bool:
        if not result.success or result.data is None:
            return False
        return getattr(result.data, "shape", (0,))[0] >= self.min_data_count

    @staticmethod
    def _norm_code(code: str) -> str:
        """6位纯代码 or ts_code(510300.SH) -> 6位纯代码"""
        code = str(code).strip().upper()
        if "." in code:
            code = code.split(".")[0]
        return code

    @staticmethod
    def _secid(code: str) -> str:
        """6位代码 -> 东方财富 secid (沪=1, 深=0)"""
        if code.startswith(("5", "6", "9")):
            return f"1.{code}"   # 上交所 (ETF/LOF 多为 5 开头)
        return f"0.{code}"       # 深交所 (ETF/LOF 多为 1 开头)

    def _fetch_akshare(self, code: str, start_date: str, end_date: str):
        try:
            import akshare as ak
            df = ak.fund_etf_hist_em(
                symbol=code, period="daily",
                start_date=start_date, end_date=end_date, adjust="qfq")
            if df is None or df.empty:
                return None
            out = pd.DataFrame({
                "trade_date": df["日期"].astype(str).str.replace("-", ""),
                "open": df["开盘"].astype(float),
                "close": df["收盘"].astype(float),
                "high": df["最高"].astype(float),
                "low": df["最低"].astype(float),
                "vol": df["成交量"].astype(float),
                "amount": df.get("成交额", pd.Series([0.0] * len(df))).astype(float),
                "ts_code": code,
            })
            return out
        except Exception as e:
            logger.debug(f"[etf_daily] akshare 拉取 {code} 失败: {e}")
            return None

    def _fetch_web(self, code: str, start_date: str, end_date: str):
        try:
            secid = self._secid(code)
            beg = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
            end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
            url = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
                f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
                f"&klt=101&fqt=1&beg={beg}&end={end}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            d = json.load(urllib.request.urlopen(req, timeout=20))
            klines = d.get("data", {}).get("klines", [])
            if not klines:
                return None
            rows = []
            for line in klines:
                p = line.split(",")
                rows.append({
                    "trade_date": p[0].replace("-", ""),
                    "open": float(p[1]), "close": float(p[2]),
                    "high": float(p[3]), "low": float(p[4]),
                    "vol": float(p[5]), "amount": float(p[6]),
                    "ts_code": code,
                })
            return pd.DataFrame(rows)
        except Exception as e:
            logger.debug(f"[etf_daily] web 拉取 {code} 失败: {e}")
            return None

    def collect(
        self,
        code: Optional[str] = None,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """获取 ETF/LOF 前复权日线。

        Args:
            code / ts_code: 6位纯代码 或 ts_code(510300.SH)
            start_date / end_date: YYYYMMDD
        """
        code = self._norm_code(code or ts_code or "")
        if not code:
            return DataResult(data_type=self.data_type, success=False,
                              error="缺少 code/ts_code")
        start_date = start_date or "20120101"
        end_date = end_date or "20261231"

        df = self._fetch_akshare(code, start_date, end_date)
        if df is None or df.empty:
            df = self._fetch_web(code, start_date, end_date)

        if df is None or df.empty:
            return DataResult(data_type=self.data_type, success=False,
                              error=f"ETF {code} 日线拉取失败 (akshare+web 均空)")

        return DataResult(data=df, data_type=self.data_type, success=True,
                          meta={"code": code, "records": len(df)})


def get_etf_daily(
    code: Optional[str] = None,
    ts_code: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    default: Optional[pd.DataFrame] = None,
) -> Optional[pd.DataFrame]:
    """便捷函数：获取 ETF/LOF 前复权日线。"""
    try:
        collector = EtfDailyCollector()
        result = collector.collect(code=code, ts_code=ts_code,
                                   start_date=start_date, end_date=end_date)
        return result.data if result.success else default
    except Exception as e:
        logger.error(f"[get_etf_daily] 获取失败: {e}")
        return default
