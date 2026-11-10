"""
Web 爬取数据源适配器
====================
四级数据源：从官方网站/财经网站直接爬取数据。
作为 AKShare、Tushare 和 Coze 网关都失败时的最后兜底方案。

优先级：Tier 4（最后兜底）
限制：需遵守网站 robots.txt，爬取频率低，仅作备用
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import List, Optional

import numpy as np
import pandas as pd

from ..base import BaseDataSource, DataResult
from ..config import TIMEOUT_CONFIG, logger

class WebSource(BaseDataSource):
    """Web 爬取数据源适配器（四级数据源）。"""

    name: str = "web"
    tier: int = 4

    def __init__(self):
        super().__init__()
        self._session = None

    def _get_session(self):
        """获取 requests session。"""
        if self._session is None:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            self._session = requests.Session()
            retry_strategy = Retry(
                total=TIMEOUT_CONFIG.max_retries,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
            self._session.headers.update({
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
            })
        return self._session

    def _get(self, url: str, **kwargs) -> Optional[str]:
        """发送 HTTP GET 请求。"""
        try:
            session = self._get_session()
            timeout = kwargs.pop('timeout', TIMEOUT_CONFIG.http_timeout)
            resp = session.get(url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.debug(f"[web] GET {url} 失败: {e}")
            return None

    def _post(self, url: str, data=None, json_data=None, **kwargs) -> Optional[str]:
        """发送 HTTP POST 请求。"""
        try:
            session = self._get_session()
            timeout = kwargs.pop('timeout', TIMEOUT_CONFIG.http_timeout)
            resp = session.post(url, data=data, json=json_data, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.debug(f"[web] POST {url} 失败: {e}")
            return None

    def is_available(self) -> bool:
        """检查网络是否可用（简单探活）。"""
        try:
            text = self._get("https://www.baidu.com", timeout=5)
            return text is not None
        except Exception:
            return False

    # ============================================================
    # 指数成分股
    # ============================================================

    def fetch_index_components(self, index_code: str, **kwargs) -> DataResult:
        """
        从东方财富/中证指数官网爬取指数成分股（兜底方案）。

        Args:
            index_code: 指数代码

        Returns:
            DataResult.data: list[str] — tushare格式代码列表
        """
        codes = []
        source_used = ""

        # 方案1：东方财富指数成分股页面
        try:
            symbol = index_code.split('.')[0]
            url = (
                f"https://push2.eastmoney.com/api/qt/clist/get?"
                f"pn=1&pz=1000&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:BK{symbol}"
                f"&fields=f12,f14"
            )
            text = self._get(url)
            if text:
                data = json.loads(text)
                diff = data.get("data", {}).get("diff", [])
                if diff:
                    for item in diff:
                        code = item.get("f12", "")
                        if code and len(str(code)) == 6:
                            code = str(code).zfill(6)
                            if code.startswith('6'):
                                codes.append(code + '.SH')
                            elif code.startswith('0') or code.startswith('3'):
                                codes.append(code + '.SZ')
                            elif code.startswith('688'):
                                codes.append(code + '.SH')
                            else:
                                if index_code.endswith('.SH'):
                                    codes.append(code + '.SH')
                                else:
                                    codes.append(code + '.SZ')
                    source_used = "eastmoney"
        except Exception as e:
            logger.debug(f"[web] 东方财富指数成分股爬取失败: {e}")

        # 方案2：新浪财经（如果方案1失败或数据太少）
        if len(codes) < 20:
            try:
                symbol = index_code.split('.')[0]
                url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vII_NewestComponent/indexid/{symbol}.phtml"
                text = self._get(url)
                if text:
                    # 用正则提取6位股票代码
                    found = re.findall(r'\b(\d{6})\b', text)
                    if found:
                        for code in found:
                            if code.startswith('6'):
                                codes.append(code + '.SH')
                            elif code.startswith('0') or code.startswith('3'):
                                codes.append(code + '.SZ')
                            elif code.startswith('688'):
                                codes.append(code + '.SH')
                            else:
                                if index_code.endswith('.SH'):
                                    codes.append(code + '.SH')
                                else:
                                    codes.append(code + '.SZ')
                        source_used = "sina" if not source_used else source_used + "+sina"
            except Exception as e:
                logger.debug(f"[web] 新浪指数成分股爬取失败: {e}")

        # 去重
        codes = list(set(codes))

        if len(codes) < 10:
            return DataResult(
                data_type="index_components",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web爬取指数成分股数据不足，仅获取 {len(codes)} 只",
            )

        meta = {
            "index_code": index_code,
            "source_site": source_used,
        }

        return DataResult(
            data=codes,
            data_type="index_components",
            source=self.name,
            source_tier=self.tier,
            success=True,
            meta=meta,
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
        从交易所官网/新浪财经爬取期权数据（兜底方案）。

        Args:
            exchange: 交易所代码（SSE/SZSE/CFFEX）
            trade_date: 交易日期（YYYYMMDD格式）
            underlying: 标的代码

        Returns:
            DataResult.data: dict with contracts and daily data
        """
        try:
            exchange_upper = exchange.upper()
            result = {
                "contracts": pd.DataFrame(),
                "daily": pd.DataFrame(),
                "underlying_price": None,
            }

            source_used = ""

            # 上交所：官网每日行情
            if exchange_upper == "SSE":
                try:
                    td = trade_date or ""
                    if td:
                        # 上交所每日期权行情列表
                        url = (
                            f"http://query.sse.com.cn/commonQuery.do?"
                            f"jsonCallBack=jsonpCallback"
                            f"&sqlId=SSE_ZQPZ_YSP_QQHQYYY_PROD_XXPL_JYXXPL_L"
                            f"&tradeDate={td[:4]}-{td[4:6]}-{td[6:8]}"
                            f"&isPagination=true&pageHelp.pageSize=10000"
                        )
                        headers = {
                            'Referer': 'http://www.sse.com.cn/assortment/options/price/',
                        }
                        text = self._get(url, headers=headers)
                        if text:
                            # 解析 JSONP
                            json_match = re.search(r'jsonpCallback\((.+)\)', text)
                            if json_match:
                                data = json.loads(json_match.group(1))
                                rows = data.get("result", [])
                                if rows:
                                    result["contracts"] = pd.DataFrame(rows)
                                    source_used = "sse_official"
                except Exception as e:
                    logger.debug(f"[web] 上交所期权官网爬取失败: {e}")

            # 深交所：官网每日行情
            if exchange_upper == "SZSE" and result["contracts"].empty:
                try:
                    td = trade_date or ""
                    if td:
                        td_fmt = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
                        url = (
                            f"http://www.szse.cn/api/report/ShowReport?"
                            f"SHOWTYPE=JSON&CATALOGID=1734&TABKEY=tab1"
                            f"&txtTrddate={td_fmt}"
                        )
                        text = self._get(url)
                        if text:
                            data = json.loads(text)
                            rows = data.get("data", [])
                            if rows:
                                result["contracts"] = pd.DataFrame(rows)
                                source_used = "szse_official"
                except Exception as e:
                    logger.debug(f"[web] 深交所期权官网爬取失败: {e}")

            # 新浪财经（通用兜底）
            if result["contracts"].empty and underlying:
                try:
                    url = (
                        f"https://stock.finance.sina.com.cn/futures/view/options.php"
                        f"?j_code=OP{underlying}"
                    )
                    text = self._get(url)
                    if text:
                        # 从页面提取期权合约代码和价格
                        codes = re.findall(r'\b(\d{8}[CP]\d{8})\b', text)
                        if codes:
                            result["contracts"] = pd.DataFrame({"ts_code": codes})
                            source_used = "sina_options"
                except Exception as e:
                    logger.debug(f"[web] 新浪期权爬取失败: {e}")

            has_data = (
                not result["contracts"].empty or not result["daily"].empty
            )

            if not has_data:
                return DataResult(
                    data_type="option_data",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"Web爬取 {exchange_upper} 期权数据为空",
                )

            meta = {
                "exchange": exchange_upper,
                "underlying": underlying or "",
                "trade_date": trade_date or "",
                "source_site": source_used,
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

    def fetch_stock_daily(
        self,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        trade_date: Optional[str] = None,
        symbol: Optional[str] = None,
        **kwargs,
    ) -> DataResult:
        """
        从东方财富/新浪财经爬取A股日线行情（兜底方案）。

        Args:
            ts_code: tushare格式股票代码
            symbol: 6位数字股票代码
            trade_date: 单日日期
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataResult.data: pd.DataFrame — 日线行情数据
        """
        try:
            code = symbol or (ts_code.split('.')[0] if ts_code else None)

            if not code:
                from datetime import datetime as _dt_now
                _today_str = _dt_now.now().strftime("%Y%m%d")
                _is_today = (trade_date is None) or (str(trade_date) == _today_str)

                # ---- 全市场历史日期：push2 是实时行情接口，不支持历史日期 ----
                # 历史日期直接返回失败，避免把当日数据伪装成历史数据
                if not _is_today:
                    return DataResult(
                        data_type="stock_daily",
                        source=self.name,
                        source_tier=self.tier,
                        success=False,
                        error=(
                            f"Web源全市场模式仅支持当日实时行情 "
                            f"(trade_date={trade_date}, 今日={_today_str})；"
                            f"历史日期请使用单只K线模式或其他数据源"
                        ),
                    )

                # ---- 全市场实时行情（当日）：东方财富优先，失败降级新浪 ----
                df = None
                spot_source = "eastmoney_web"
                
                # 1) 东方财富 push2 接口
                url = (
                    "https://push2.eastmoney.com/api/qt/clist/get?"
                    "pn=1&pz=6000&po=1&np=1&fltt=2&invt=2&fid=f3"
                    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
                    "&fields=f12,f14,f2,f3,f4,f5,f6,f7,f15,f16,f17,f18"
                )
                text = self._get(url)
                if text:
                    try:
                        data = json.loads(text)
                        diff = data.get("data", {}).get("diff", [])
                        if diff:
                            df = pd.DataFrame(diff)
                            col_map = {
                                'f12': 'ts_code_raw',
                                'f14': 'name',
                                'f2': 'close',
                                'f3': 'pct_chg',
                                'f4': 'change',
                                'f5': 'vol',
                                'f6': 'amount',
                                'f7': 'amplitude',
                                'f15': 'high',
                                'f16': 'low',
                                'f17': 'open',
                                'f18': 'pre_close',
                            }
                            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
                            if 'ts_code_raw' in df.columns:
                                df['ts_code'] = df['ts_code_raw'].apply(
                                    lambda x: f"{x}.SH" if str(x).startswith('6') else f"{x}.SZ"
                                )
                    except Exception as em_e:
                        logger.debug(f"[web] 东方财富全市场行情解析失败: {em_e}")
                        df = None

                # 2) 新浪行情接口兜底（Market_Center.getHQNodeData）
                if df is None or df.empty:
                    spot_source = "sina_web"
                    try:
                        df = self._fetch_sina_spot_all()
                    except Exception as sina_e:
                        logger.debug(f"[web] 新浪全市场行情获取失败: {sina_e}")
                        df = None

                if df is None or df.empty:
                    return DataResult(
                        data_type="stock_daily",
                        source=self.name,
                        source_tier=self.tier,
                        success=False,
                        error="全市场行情爬取失败 (东方财富+新浪均失败)",
                    )

                # 补充 trade_date 列（实时行情的日期 = 当日）
                from datetime import datetime as _dt
                _today = _dt.now().strftime("%Y%m%d")
                df["trade_date"] = trade_date or _today

                meta = {
                    "mode": "spot_all",
                    "spot_source": spot_source,
                    "record_count": len(df),
                    "trade_date": trade_date or _today,
                }

                return DataResult(
                    data=df,
                    data_type="stock_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=True,
                    meta=meta,
                )

            # 单只股票历史K线
            sd = start_date or trade_date
            ed = end_date or trade_date
            sd_fmt = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}" if sd else ""
            ed_fmt = f"{ed[:4]}-{ed[4:6]}-{ed[6:8]}" if ed else ""

            # ---- 方案1: 东方财富 K线接口（短超时，快速失败降级到新浪） ----
            em_success = False
            df = None
            try:
                secid = self._code_to_secid(code, ts_code)
                url = (
                    f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
                    f"secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
                    f"&fields1=f1,f2,f3,f4,f5,f6"
                    f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                    f"&klt=101&fqt=1"
                    f"&beg={sd_fmt if sd_fmt else '0'}&end={ed_fmt if ed_fmt else '20500101'}"
                )
                # 东方财富K线用更短的超时（5s），快速失败降级到新浪
                text = self._get(url, timeout=5)
                if text:
                    data = json.loads(text)
                    klines = data.get("data", {}).get("klines", [])
                    if klines:
                        rows = []
                        for line in klines:
                            parts = line.split(',')
                            if len(parts) >= 11:
                                rows.append({
                                    'trade_date': parts[0].replace('-', ''),
                                    'open': float(parts[1]),
                                    'close': float(parts[2]),
                                    'high': float(parts[3]),
                                    'low': float(parts[4]),
                                    'vol': float(parts[5]),
                                    'amount': float(parts[6]),
                                    'amplitude': float(parts[7]) if parts[7] else 0,
                                    'pct_chg': float(parts[8]) if parts[8] else 0,
                                    'change': float(parts[9]) if parts[9] else 0,
                                    'turnover': float(parts[10]) if len(parts) > 10 and parts[10] else 0,
                                })
                        if rows:
                            df = pd.DataFrame(rows)
                            df['ts_code'] = ts_code or code
                            em_success = True
                            source_site_used = "eastmoney"
            except Exception as em_e:
                logger.debug(f"[web] 东方财富K线 {code} 失败: {em_e}")

            # ---- 方案2: 新浪 K线接口（东方财富挂了时降级） ----
            if not em_success or df is None or df.empty:
                try:
                    # 新浪代码前缀
                    if ts_code and ts_code.endswith('.SH'):
                        sina_symbol = f'sh{code}'
                    elif ts_code and ts_code.endswith('.SZ'):
                        sina_symbol = f'sz{code}'
                    elif ts_code and ts_code.endswith('.BJ'):
                        sina_symbol = f'bj{code}'
                    else:
                        # 没有后缀时按首字母猜
                        if code.startswith('6'):
                            sina_symbol = f'sh{code}'
                        elif code.startswith('0') or code.startswith('3'):
                            sina_symbol = f'sz{code}'
                        else:
                            sina_symbol = f'sz{code}'

                    # datalen 方式：从最新往回取 N 条
                    # 先估算需要多少天
                    import datetime as _dt_mod
                    if sd and ed:
                        d1 = _dt_mod.datetime.strptime(sd, '%Y%m%d')
                        d2 = _dt_mod.datetime.strptime(ed, '%Y%m%d')
                        datalen = max(10, int((d2 - d1).days * 1.5) + 10)
                    else:
                        datalen = 250

                    sina_url = (
                        f"https://money.finance.sina.com.cn/quotes_service/api/"
                        f"json_v2.php/CN_MarketData.getKLineData"
                        f"?symbol={sina_symbol}&scale=240&ma=no&datalen={datalen}"
                    )
                    sina_text = self._get(sina_url)
                    if sina_text:
                        sina_data = json.loads(sina_text)
                        if isinstance(sina_data, list) and len(sina_data) > 0:
                            rows = []
                            for item in sina_data:
                                day_str = str(item.get('day', '')).replace('-', '')
                                if not day_str:
                                    continue
                                # 日期过滤
                                if sd and day_str < sd:
                                    continue
                                if ed and day_str > ed:
                                    continue
                                rows.append({
                                    'trade_date': day_str,
                                    'open': float(item.get('open', 0)),
                                    'close': float(item.get('close', 0)),
                                    'high': float(item.get('high', 0)),
                                    'low': float(item.get('low', 0)),
                                    'vol': float(item.get('volume', 0)),
                                    # 新浪单只K线没有 amount 字段，用 close*vol 近似
                                    'amount': float(item.get('close', 0)) * float(item.get('volume', 0)),
                                })
                            if rows:
                                df = pd.DataFrame(rows)
                                df['ts_code'] = ts_code or code
                                source_site_used = "sina"
                                em_success = True
                except Exception as sina_e:
                    logger.debug(f"[web] 新浪K线 {code} 失败: {sina_e}")

            if not em_success or df is None or df.empty:
                return DataResult(
                    data_type="stock_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"股票 {code} 日线爬取失败 (东方财富+新浪均失败)",
                )

            meta = {
                "code": code,
                "ts_code": ts_code or code,
                "start_date": sd or "",
                "end_date": ed or "",
                "record_count": len(df),
                "source_site": source_site_used,
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

    def _fetch_sina_spot_all(self) -> pd.DataFrame:
        """从新浪财经获取全市场A股实时行情（分页拉取）。
        
        使用 Market_Center.getHQNodeData 接口，节点 hs_a 覆盖沪深京A股。
        返回列统一为: ts_code, name, close, change, pct_chg, open, high, low, vol, amount, pre_close
        """
        import requests as _requests
        
        base_url = (
            "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData"
        )
        headers = {
            'Referer': 'https://finance.sina.com.cn/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
        
        # 先获取总数
        count_url = (
            "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeStockCount?node=hs_a"
        )
        try:
            r = _requests.get(count_url, headers=headers, timeout=10)
            total = int(r.text.strip('"'))
        except Exception:
            total = 6000  # 估计值
        
        page_size = 100  # 新浪接口单页最大100条
        all_rows = []
        page = 1
        max_pages = (total // page_size) + 2
        
        while page <= max_pages:
            params = {
                'page': page,
                'num': page_size,
                'sort': 'changepercent',
                'asc': 0,
                'node': 'hs_a',
                '_s_r_a': 'auto',
            }
            try:
                r = _requests.get(base_url, params=params, headers=headers, timeout=15)
                data = json.loads(r.text)
            except Exception:
                break
            
            if not data or not isinstance(data, list) or len(data) == 0:
                break
            
            all_rows.extend(data)
            if len(data) < page_size:
                break
            page += 1
        
        if not all_rows:
            raise ValueError("新浪全市场行情数据为空")
        
        df = pd.DataFrame(all_rows)
        
        # 列名映射: symbol, code, name, trade, pricechange, changepercent, 
        #           settlement(昨收), open, high, low, volume, amount
        col_map = {
            'symbol': 'ts_code_raw',
            'name': 'name',
            'trade': 'close',
            'pricechange': 'change',
            'changepercent': 'pct_chg',
            'settlement': 'pre_close',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'volume': 'vol',
            'amount': 'amount',
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        
        # ts_code 统一格式
        if 'ts_code_raw' in df.columns:
            def _to_ts_code(x):
                s = str(x).strip().lower()
                if s.startswith('bj'):
                    return f"{s[2:]}.BJ"
                elif s.startswith('sh'):
                    return f"{s[2:]}.SH"
                elif s.startswith('sz'):
                    return f"{s[2:]}.SZ"
                if s.startswith('6') or s.startswith('9'):
                    return f"{s}.SH"
                return f"{s}.SZ"
            df['ts_code'] = df['ts_code_raw'].apply(_to_ts_code)
            df = df.drop(columns=['ts_code_raw'], errors='ignore')
        
        return df

    @staticmethod
    def _code_to_secid(code: str, ts_code: Optional[str] = None) -> str:
        """股票代码转东方财富 secid 格式。"""
        if ts_code:
            if ts_code.endswith('.SH'):
                return f"1.{code}"
            elif ts_code.endswith('.SZ'):
                return f"0.{code}"
            elif ts_code.endswith('.BJ'):
                return f"0.{code}"
        # 根据代码判断
        if code.startswith('6') or code.startswith('688'):
            return f"1.{code}"  # 上交所
        else:
            return f"0.{code}"  # 深交所


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
        从新浪财经爬取股指期货基差数据（兜底方案）。

        Args:
            trade_date: 交易日期（YYYYMMDD）
            products: 期货品种列表，默认 ['IF', 'IC', 'IM']

        Returns:
            DataResult.data: list[dict] — 基差数据列表
        """
        try:
            if products is None:
                products = ['IF', 'IC', 'IM']

            # 指数代码映射（新浪格式）
            index_map = {
                'IF': {'sina_code': 'sh000300', 'name': '沪深300'},
                'IC': {'sina_code': 'sh000905', 'name': '中证500'},
                'IM': {'sina_code': 'sh000852', 'name': '中证1000'},
            }

            # 期货代码映射（新浪期货格式：nf_IF0 主力连续）
            futures_map = {
                'IF': 'nf_IF0',
                'IC': 'nf_IC0',
                'IM': 'nf_IM0',
            }

            result_list = []

            # 1. 批量获取指数现货价格
            index_codes = [index_map[p]['sina_code'] for p in products if p in index_map]
            spot_prices = self._fetch_sina_quotes(index_codes)

            # 2. 批量获取期货价格
            fut_codes = [futures_map[p] for p in products if p in futures_map]
            fut_prices = self._fetch_sina_futures(fut_codes)

            # 3. 计算基差
            for product in products:
                if product not in index_map or product not in futures_map:
                    continue

                sina_idx = index_map[product]['sina_code']
                sina_fut = futures_map[product]

                spot_price = spot_prices.get(sina_idx)
                futures_price = fut_prices.get(sina_fut)

                if spot_price is None or futures_price is None:
                    continue
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
                    'contract_type': '主力连续',
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
                    error="Web爬取股指期货基差数据为空",
                )

            meta = {
                "trade_date": trade_date or "",
                "products": products,
                "count": len(result_list),
                "source_site": "sina",
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
        从新浪财经爬取**单个具体期货合约**的历史日线（兜底方案，纯 requests）。

        接口（JSONP）:
            https://stock2.finance.sina.com.cn/futures/api/jsonp.php/
                var _<SYMBOL>=/InnerFuturesNewService.getDailyKLine?symbol=<SYMBOL>
        合约代码如 IF2609（具体合约，非 IF0 主力连续）。
        返回每条 K 线: d(日期), o/h/l/c, v(成交量), p(持仓量), s(结算价)

        Returns:
            DataResult.data: pd.DataFrame
                标准列 date(YYYYMMDD), open, high, low, close, volume, oi，按 date 升序
        """
        try:
            import requests
            import json as _json
            from ..collectors.futures_contract_daily import normalize_futures_contract_df

            symbol = sina_symbol or (str(contract).split('.')[0] if contract else None)
            if not symbol:
                return DataResult(
                    data_type="futures_contract_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="合约代码为空",
                )

            url = (
                "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
                f"var%20_{symbol}=/InnerFuturesNewService.getDailyKLine?symbol={symbol}"
            )
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            }
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            text = resp.text.strip()

            # 形如: /*<script>...</script>*/\nvar _IF2609=([{...}]);
            lb = text.find('([')
            rb = text.rfind('])')
            if lb < 0 or rb < 0 or rb <= lb:
                return DataResult(
                    data_type="futures_contract_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"新浪期货K线响应无法解析 ({symbol})",
                )

            data = _json.loads(text[lb + 1:rb + 1])
            if not data:
                return DataResult(
                    data_type="futures_contract_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"新浪期货K线数据为空 ({symbol})",
                )

            # 原始字段 d/o/h/l/c/v/p/s，交由统一标准化函数映射
            raw = pd.DataFrame(data)
            df = normalize_futures_contract_df(raw)

            # 按日期区间过滤
            sd = str(start_date) if start_date else None
            ed = str(end_date) if end_date else None
            if sd and not df.empty:
                df = df[df['date'] >= sd]
            if ed and not df.empty:
                df = df[df['date'] <= ed]

            if df.empty:
                return DataResult(
                    data_type="futures_contract_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"合约 {symbol} 在 {sd or '-'}~{ed or '-'} 区间无数据",
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
                    "start_date": sd or "",
                    "end_date": ed or "",
                    "api": "sina_InnerFuturesNewService.getDailyKLine",
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

    def _fetch_sina_quotes(self, codes: list) -> dict:
        """
        从新浪财经获取股票/指数实时报价。

        Args:
            codes: 新浪格式代码列表，如 ['sh000300', 'sh510300']

        Returns:
            dict: {code: price}
        """
        result = {}
        if not codes:
            return result

        try:
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            text = self._get(url, headers={'Referer': 'https://finance.sina.com.cn'})
            if not text:
                return result

            # 解析新浪行情
            lines = text.strip().split('\n')
            for line in lines:
                line = line.strip()
                if not line or '=' not in line:
                    continue

                # 格式: var hq_str_sh000300="名称,今开,昨收,最新价,最高,最低,...";
                eq_idx = line.find('=')
                code_part = line[:eq_idx]
                val_part = line[eq_idx + 1:]

                # 提取代码
                code = code_part.replace('var hq_str_', '').strip()

                # 提取引号内内容
                val_part = val_part.strip().strip(';').strip('"')
                if not val_part:
                    continue

                fields = val_part.split(',')
                if len(fields) >= 4:
                    try:
                        # 字段: 0名称, 1今开, 2昨收, 3最新价, 4最高, 5最低
                        price = float(fields[3])
                        result[code] = price
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            logger.debug(f"[web] 新浪行情获取失败: {e}")

        return result

    def _fetch_sina_futures(self, codes: list) -> dict:
        """
        从新浪财经获取期货实时报价。

        Args:
            codes: 新浪期货代码列表，如 ['nf_IF0', 'nf_IC0']

        Returns:
            dict: {code: price}
        """
        result = {}
        if not codes:
            return result

        try:
            url = f"https://hq.sinajs.cn/list={','.join(codes)}"
            text = self._get(url, headers={'Referer': 'https://finance.sina.com.cn'})
            if not text:
                return result

            lines = text.strip().split('\n')
            for line in lines:
                line = line.strip()
                if not line or '=' not in line:
                    continue

                eq_idx = line.find('=')
                code_part = line[:eq_idx]
                val_part = line[eq_idx + 1:]

                code = code_part.replace('var hq_str_', '').strip()

                val_part = val_part.strip().strip(';').strip('"')
                if not val_part:
                    continue

                fields = val_part.split(',')
                if len(fields) >= 5:
                    try:
                        # 期货字段: 0今开, 1昨结, 2最低, 3最新价, 4最高, ...
                        price = float(fields[3])
                        result[code] = price
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            logger.debug(f"[web] 新浪期货获取失败: {e}")

        return result


    # ============================================================
    # 利率数据
    # ============================================================

    def fetch_interest_rate(
        self,
        rate_type: str = "shibor",
        **kwargs,
    ) -> DataResult:
        """
        获取利率数据（Web 爬取兜底）。

        Args:
            rate_type: 利率类型（shibor / lpr / hibor / bond_yield）

        Returns:
            DataResult.data — 利率数据
        """
        rate_type = rate_type.lower()

        try:
            if rate_type == "shibor":
                return self._fetch_shibor_web()
            elif rate_type == "lpr":
                return self._fetch_lpr_web()
            elif rate_type == "hibor":
                return self._fetch_hibor_web()
            elif rate_type == "bond_yield":
                return self._fetch_bond_yield_web()
            elif rate_type == "cd_rate_1y":
                return self._fetch_cd_rate_1y_web()
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

    def _fetch_shibor_web(self) -> DataResult:
        """从 Shibor 官网爬取 SHIBOR 利率。"""
        try:
            # Shibor 官网数据接口
            url = "https://www.shibor.org/shibor/web/html/shibor.html"
            text = self._get(url)
            if not text:
                # 备用：从新浪财经获取
                url2 = "https://hq.sinajs.cn/list=shibor_on,shibor_1w,shibor_2w,shibor_1m,shibor_3m,shibor_6m,shibor_9m,shibor_1y"
                text2 = self._get(url2, headers={'Referer': 'https://finance.sina.com.cn'})
                if not text2:
                    return DataResult(
                        data_type="shibor",
                        source=self.name,
                        source_tier=self.tier,
                        success=False,
                        error="Web爬取 SHIBOR 数据失败",
                    )
                text = text2

            # 简单解析新浪格式
            latest_rates = {}
            lines = text.strip().split('\n')
            for line in lines:
                line = line.strip()
                if not line or '=' not in line:
                    continue
                match = re.match(r'var hq_str_shibor_([^=]+)="([^"]*)"', line)
                if match:
                    term = match.group(1).upper()
                    value_str = match.group(2)
                    if value_str:
                        fields = value_str.split(',')
                        if fields and fields[0]:
                            try:
                                # 标准化期限名称
                                term_map = {
                                    'ON': 'O/N', '1W': '1W', '2W': '2W',
                                    '1M': '1M', '3M': '3M', '6M': '6M',
                                    '9M': '9M', '1Y': '1Y',
                                }
                                display_term = term_map.get(term.upper(), term)
                                latest_rates[display_term] = float(fields[0])
                            except (ValueError, IndexError):
                                pass

            if not latest_rates:
                return DataResult(
                    data_type="shibor",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Web爬取 SHIBOR 数据为空",
                )

            result = {
                "latest": latest_rates,
                "latest_date": "",
                "history": pd.DataFrame(),
                "terms": list(latest_rates.keys()),
            }

            meta = {
                "source_site": "sina_shibor",
                "term_count": len(latest_rates),
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
                error=f"Web爬取 SHIBOR 失败: {type(e).__name__}: {e}",
            )

    def _fetch_lpr_web(self) -> DataResult:
        """从央行/财经网站爬取 LPR 利率。"""
        try:
            # 从金十数据或类似网站爬取
            url = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_WEB_RATE_LPR&columns=ALL&pageNumber=1&pageSize=10"
            text = self._get(url)
            if not text:
                return DataResult(
                    data_type="lpr",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Web爬取 LPR 数据失败",
                )

            raw = json.loads(text) if text else {}
            data_list = raw.get("result", {}).get("data", []) if isinstance(raw, dict) else []

            if not data_list:
                return DataResult(
                    data_type="lpr",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Web爬取 LPR 数据为空",
                )

            latest = data_list[0]
            latest_rates = {}
            latest_date = ""

            # 尝试提取 LPR1Y 和 LPR5Y
            for key in latest:
                key_lower = str(key).lower()
                if '1年' in str(key) or '1y' in key_lower or '1年期' in str(key):
                    try:
                        latest_rates['LPR1Y'] = float(latest[key])
                    except (ValueError, TypeError):
                        pass
                elif '5年' in str(key) or '5y' in key_lower or '5年期' in str(key):
                    try:
                        latest_rates['LPR5Y'] = float(latest[key])
                    except (ValueError, TypeError):
                        pass
                elif '日期' in str(key) or 'date' in key_lower:
                    latest_date = str(latest[key]).replace('-', '')[:8]

            result = {
                "latest": latest_rates,
                "latest_date": latest_date,
                "history": pd.DataFrame(data_list),
            }

            meta = {
                "latest_date": latest_date,
                "source_site": "eastmoney",
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
                error=f"Web爬取 LPR 失败: {type(e).__name__}: {e}",
            )

    def _fetch_hibor_web(self) -> DataResult:
        """从财经网站爬取 HIBOR 利率。"""
        try:
            # HIBOR 数据可以从 HKMA 或财经网站获取
            # 这里用一个简化的实现，尝试从新浪/东方财富获取
            url = "https://hq.sinajs.cn/list=hibor_on,hibor_1w,hibor_1m,hibor_3m,hibor_6m,hibor_1y"
            text = self._get(url, headers={'Referer': 'https://finance.sina.com.cn'})

            if not text:
                return DataResult(
                    data_type="hibor",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Web爬取 HIBOR 数据失败",
                )

            latest_rates = {}
            lines = text.strip().split('\n')
            for line in lines:
                line = line.strip()
                if not line or '=' not in line:
                    continue
                match = re.match(r'var hq_str_hibor_([^=]+)="([^"]*)"', line)
                if match:
                    term = match.group(1).upper()
                    value_str = match.group(2)
                    if value_str:
                        fields = value_str.split(',')
                        if fields and fields[0]:
                            try:
                                term_map = {
                                    'ON': 'O/N', '1W': '1W', '2W': '2W',
                                    '1M': '1M', '3M': '3M', '6M': '6M',
                                    '9M': '9M', '1Y': '1Y',
                                }
                                display_term = term_map.get(term.upper(), term)
                                latest_rates[display_term] = float(fields[0])
                            except (ValueError, IndexError):
                                pass

            if not latest_rates:
                return DataResult(
                    data_type="hibor",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Web爬取 HIBOR 数据为空",
                )

            result = {
                "latest": latest_rates,
                "latest_date": "",
                "history": pd.DataFrame(),
                "terms": list(latest_rates.keys()),
            }

            meta = {
                "source_site": "sina_hibor",
                "term_count": len(latest_rates),
            }

            return DataResult(
                data=result,
                data_type="hibor",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta=meta,
            )

        except Exception as e:
            return DataResult(
                data_type="hibor",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web爬取 HIBOR 失败: {type(e).__name__}: {e}",
            )

    def _fetch_bond_yield_web(self) -> DataResult:
        """从FRED/中国债券信息网爬取国债收益率曲线。"""
        try:
            from datetime import datetime, timedelta
            from io import StringIO

            result_frames = []

            # 方案1：中国债券信息网 chinabond
            # 通过 AKShare 的 bond_china_yield 接口获取（作为Web层的可靠替代）
            # 如果AKShare层已成功，Web层通常不会到达这里
            # 这里用FRED美债+中债国债代理构建

            # 方案2：从东方财富获取中债国债收益率
            # 使用正确的报表名：中债收益率曲线
            try:
                url = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_BOND_CHINA_YIELD&columns=ALL&pageNumber=1&pageSize=50&sortColumns=TRADE_DATE&sortTypes=-1"
                text = self._get(url)
                if text:
                    raw = json.loads(text)
                    data_list = raw.get("result", {}).get("data", []) if isinstance(raw, dict) else []
                    if data_list:
                        df = pd.DataFrame(data_list)
                        meta = {
                            "record_count": len(df),
                            "source_site": "eastmoney_bond_yield",
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
                logger.debug(f"[web] 东方财富中债收益率爬取失败: {e}")

            # 方案3：从chinabond官网直接获取
            try:
                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                url = f"https://yield.chinabond.com.cn/cbweb-mn/yc/search?zc=&lx=0&tb=nated&dxbm=0&qx=1&bz=0&wk=&zq=&flag=1&searchFlag=2&startDate={start_date}&endDate={end_date}&isIt=N&showYcqp=N"
                text = self._get(url)
                if text and len(text) > 200:
                    # 尝试从页面解析国债收益率数据
                    import re
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(text, 'html.parser')
                    # 查找包含利率数据的表格
                    tables = soup.find_all('table')
                    for table in tables:
                        rows = table.find_all('tr')
                        if len(rows) > 2:
                            records = []
                            for row in rows[1:]:
                                cells = row.find_all('td')
                                if len(cells) >= 3:
                                    record = {}
                                    for i, cell in enumerate(cells):
                                        record[f'col_{i}'] = cell.get_text(strip=True)
                                    records.append(record)
                            if records:
                                df = pd.DataFrame(records)
                                meta = {
                                    "record_count": len(df),
                                    "source_site": "chinabond",
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
                logger.debug(f"[web] chinabond 爬取失败: {e}")

            return DataResult(
                data_type="bond_yield",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="Web爬取国债收益率数据失败（所有方案均未获取到有效数据）",
            )

        except Exception as e:
            return DataResult(
                data_type="bond_yield",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web爬取国债收益率失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 同业存单利率（cd_rate_1y）- Web层
    # ============================================================

    def _fetch_cd_rate_1y_web(self) -> DataResult:
        """
        从财经网站爬取1年期同业存单利率（最后兜底）。

        尝试从中国债券信息网或东方财富等数据源获取。
        代理标的：中债商业银行普通债收益率曲线(AAA) 1年期
        """
        try:
            # 方案1：东方财富债券收益率数据
            # 尝试东方财富数据中心的债券收益率接口
            url = (
                "https://datacenter-web.eastmoney.com/api/data/v1/get?"
                "reportName=RPT_BOND_CB_LIST&columns=ALL&pageNumber=1&pageSize=50"
            )
            text = self._get(url)
            if text:
                try:
                    raw = json.loads(text)
                    data_list = raw.get("result", {}).get("data", []) if isinstance(raw, dict) else []
                    if data_list:
                        df = pd.DataFrame(data_list)
                        # 尝试从中提取1年期商业银行债收益率
                        # 列名可能是中文，模糊匹配
                        rate_1y = None
                        for _, row in df.iterrows():
                            for col in df.columns:
                                col_str = str(col)
                                if ('1年' in col_str or '1Y' in col_str or '1年期' in col_str) and \
                                   ('商业银行' in str(row.get('债券类型', '')) or 
                                    '商业银行' in str(row.get('名称', '')) or
                                    'AAA' in str(row.get('评级', ''))):
                                    try:
                                        val = row[col]
                                        if val and str(val) not in ['', 'nan', 'None']:
                                            rate_1y = float(val)
                                            break
                                    except (ValueError, TypeError):
                                        continue
                            if rate_1y:
                                break

                        if rate_1y and 0 < rate_1y < 20:
                            result = {
                                "latest": {"1Y": rate_1y},
                                "latest_date": "",
                                "curve_name": "东方财富-商业银行债收益率(AAA)",
                                "period": "1年",
                                "history": df,
                            }
                            return DataResult(
                                data=result,
                                data_type="cd_rate_1y",
                                source=self.name,
                                source_tier=self.tier,
                                success=True,
                                meta={"source_site": "eastmoney", "latest_rate": rate_1y},
                            )
                except Exception as e:
                    logger.debug(f"[web] 东方财富CD利率解析失败: {e}")

            # 方案2：中国债券信息网直接爬取
            try:
                from datetime import datetime
                today = datetime.now().strftime("%Y-%m-%d")
                # 中债收益率曲线查询接口
                url2 = (
                    f"https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery?"
                    f"startDate={today}&endDate={today}&gjqx=0&qxId=ycqx&locale=cn_ZH"
                )
                text2 = self._get(url2)
                if text2 and len(text2) > 100:
                    # 用正则提取收益率数据
                    # 中债页面是动态渲染的，直接HTML爬取可能拿不到数据
                    # 这里只是简单尝试
                    import re
                    # 匹配"中债商业银行普通债收益率曲线(AAA)"附近的1年利率
                    pattern = r'商业银行.*?AAA.*?(\d+\.?\d*).*?1年|1年.*?(\d+\.?\d*).*?商业银行.*?AAA'
                    matches = re.findall(pattern, text2, re.DOTALL)
                    if matches:
                        for m in matches:
                            val_str = m[0] if m[0] else m[1]
                            try:
                                rate_1y = float(val_str)
                                if 0 < rate_1y < 20:
                                    result = {
                                        "latest": {"1Y": rate_1y},
                                        "latest_date": today.replace('-', ''),
                                        "curve_name": "中债商业银行普通债收益率曲线(AAA)",
                                        "period": "1年",
                                        "history": pd.DataFrame(),
                                    }
                                    return DataResult(
                                        data=result,
                                        data_type="cd_rate_1y",
                                        source=self.name,
                                        source_tier=self.tier,
                                        success=True,
                                        meta={"source_site": "chinabond", "latest_rate": rate_1y},
                                    )
                            except (ValueError, TypeError):
                                continue
            except Exception as e:
                logger.debug(f"[web] 中债信息网CD利率爬取失败: {e}")

            # 所有方案均失败
            return DataResult(
                data_type="cd_rate_1y",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="Web爬取1年期同业存单利率失败（所有兜底方案均未获取到有效数据）",
            )

        except Exception as e:
            return DataResult(
                data_type="cd_rate_1y",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web爬取同业存单利率失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # ETF 份额数据（Web层 - 最后兜底）
    # ============================================================

    def fetch_etf_share(self, ts_code: str = None, symbol: str = None,
                        trade_date: str = None, **kwargs) -> DataResult:
        """
        ETF份额数据（Web爬取 - 最后兜底）。
        从交易所官网或东方财富爬取ETF份额数据。
        目前实现简易版，优先使用AKShare/Tushare。
        """
        try:
            import requests
            from bs4 import BeautifulSoup

            code = symbol or (ts_code.split('.')[0] if ts_code else None)
            if not code:
                return DataResult(
                    data_type="etf_share",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="缺少ETF代码参数",
                )

            # 简易兜底：从东方财富网页爬取
            # 注意：Web层作为最后兜底，实现从简，主要依赖AKShare/Tushare
            return DataResult(
                data_type="etf_share",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="Web层ETF份额数据暂未实现，请使用AKShare/Tushare数据源",
            )
        except Exception as e:
            return DataResult(
                data_type="etf_share",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web爬取ETF份额失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 两融数据（Web层 - 最后兜底）
    # ============================================================

    def fetch_margin_trading(self, trade_date: str = None, **kwargs) -> DataResult:
        """
        两融数据（Web爬取 - 最后兜底）。
        从交易所官网爬取两融数据。
        """
        try:
            # Web层作为最后兜底，实现从简
            return DataResult(
                data_type="margin_trading",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="Web层两融数据暂未实现，请使用AKShare/Tushare数据源",
            )
        except Exception as e:
            return DataResult(
                data_type="margin_trading",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web爬取两融数据失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 板块资金流数据（Web层 - 最后兜底）
    # ============================================================

    def fetch_sector_flow(self, sector_type: str = 'industry', trade_date: str = None,
                          **kwargs) -> DataResult:
        """
        板块资金流数据（Web爬取 - 最后兜底）。

        数据源：同花顺行业/概念资金流页面（HTTP）
        - 行业板块：http://data.10jqka.com.cn/funds/hyzjl/
        - 概念板块：http://data.10jqka.com.cn/funds/gnzjl/

        Args:
            sector_type: 板块类型（industry 行业 / concept 概念）
            trade_date: 交易日期（YYYYMMDD），同花顺只提供当日数据

        Returns:
            DataResult.data: pd.DataFrame — 板块资金流数据
                列: sector_name, main_net_inflow, main_net_inflow_pct,
                     change_pct, company_count, leading_stock, flow_pct_of_total
        """
        try:
            import re as _re

            # 选择URL
            if sector_type in ['industry', '行业', '行业资金流']:
                url = 'http://data.10jqka.com.cn/funds/hyzjl/'
                type_name = '行业'
            elif sector_type in ['concept', '概念', '概念资金流']:
                url = 'http://data.10jqka.com.cn/funds/gnzjl/'
                type_name = '概念'
            else:
                url = 'http://data.10jqka.com.cn/funds/hyzjl/'
                type_name = '行业'

            headers = {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
            }

            text = self._get(url, headers=headers)
            # 同花顺用GBK编码
            if text:
                try:
                    # self._get 返回的是 str，可能已经用默认编码解码过了
                    # 如果乱码，需要重新编码
                    # 这里我们用 session 重新获取原始 bytes
                    pass
                except Exception:
                    pass

            # 如果获取失败或为空，用 session 直接获取原始响应再解码
            if not text or len(text) < 1000:
                try:
                    session = self._get_session()
                    resp = session.get(url, headers=headers, timeout=TIMEOUT_CONFIG.http_timeout)
                    resp.encoding = 'gbk'
                    text = resp.text
                except Exception as e:
                    logger.debug(f"[web] 同花顺{type_name}资金流获取失败: {e}")

            if not text or len(text) < 2000:
                return DataResult(
                    data_type="sector_flow",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"Web爬取{type_name}板块资金流数据为空或获取失败",
                )

            # 解析表格
            table_match = _re.search(r'<table[^>]*>(.*?)</table>', text, _re.DOTALL)
            if not table_match:
                return DataResult(
                    data_type="sector_flow",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"Web爬取{type_name}板块资金流：未找到表格数据",
                )

            table_html = table_match.group(1)
            rows = _re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, _re.DOTALL)

            if len(rows) < 2:
                return DataResult(
                    data_type="sector_flow",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"Web爬取{type_name}板块资金流：表格数据不足 ({len(rows)}行)",
                )

            # 解析每一行数据（跳过表头）
            records = []
            for row_html in rows[1:]:
                cells = _re.findall(r'<td[^>]*>(.*?)</td>', row_html, _re.DOTALL)
                if len(cells) < 11:
                    continue

                # 清理HTML标签
                clean = []
                for c in cells:
                    txt = _re.sub(r'<[^>]+>', '', c).strip()
                    txt = txt.replace('&nbsp;', '').replace('\u3000', '')
                    clean.append(txt)

                # 列索引（同花顺表格）:
                # 0序号, 1行业名称, 2行业指数, 3涨跌幅, 4流入资金(亿), 5流出资金(亿),
                # 6净额(亿), 7公司家数, 8领涨股, 9领涨股涨跌幅, 10领涨股当前价
                try:
                    sector_name = clean[1]
                    change_pct_str = clean[3].replace('%', '').replace('+', '')
                    inflow_str = clean[4]  # 亿元
                    outflow_str = clean[5]  # 亿元
                    net_str = clean[6]  # 亿元
                    company_count_str = clean[7]
                    leading_stock = clean[8]

                    change_pct = float(change_pct_str) if change_pct_str and change_pct_str not in ('-', '--') else 0.0
                    main_net_inflow = float(net_str) * 1e8 if net_str and net_str not in ('-', '--') else 0.0  # 转元
                    inflow = float(inflow_str) * 1e8 if inflow_str and inflow_str not in ('-', '--') else 0.0
                    outflow = float(outflow_str) * 1e8 if outflow_str and outflow_str not in ('-', '--') else 0.0

                    # 计算主力净流入占比（占成交额比例）
                    total_turnover = inflow + outflow
                    main_net_inflow_pct = (main_net_inflow / total_turnover * 100) if total_turnover > 0 else 0.0

                    company_count = int(company_count_str) if company_count_str.isdigit() else 0

                    records.append({
                        'sector_name': sector_name,
                        'main_net_inflow': main_net_inflow,
                        'main_net_inflow_pct': round(main_net_inflow_pct, 2),
                        'change_pct': change_pct,
                        'company_count': company_count,
                        'leading_stock': leading_stock,
                        'inflow_amount': inflow,
                        'outflow_amount': outflow,
                    })
                except (ValueError, IndexError) as e:
                    logger.debug(f"[web] 解析板块行失败: {e}, 数据: {clean[:5]}")
                    continue

            if not records:
                return DataResult(
                    data_type="sector_flow",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"Web爬取{type_name}板块资金流：解析后无有效数据",
                )

            df = pd.DataFrame(records)

            # 计算占全市场比例（按主力净流入绝对值）
            if 'main_net_inflow' in df.columns:
                total_abs = df['main_net_inflow'].abs().sum()
                if total_abs > 0:
                    df['flow_pct_of_total'] = df['main_net_inflow'].abs() / total_abs
                else:
                    df['flow_pct_of_total'] = 0.0

            meta = {
                "sector_type": sector_type,
                "sector_count": len(df),
                "source_site": "同花顺(10jqka)",
                "trade_date": trade_date or "",
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
                error=f"Web爬取板块资金流失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 央行OMO操作数据
    # ============================================================

    def fetch_central_bank_omo(self, trade_date: str = None, lookback_days: int = 30,
                               **kwargs) -> DataResult:
        """
        从PBOC官网爬取央行OMO公开市场操作数据。

        数据来源：http://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html
        官网编码：UTF-8（非GBK，2025年改版后已切换）

        覆盖字段：
        - 各期限逆回购操作量（7天/14天/28天/63天/隔夜）
        - 各期限投标量
        - MLF操作量
        - 到期量（逆回购到期 / MLF到期）
        - 当日净投放 = 总投放 - 总到期
        - 历史明细（近N日）
        """
        try:
            from datetime import datetime, timedelta
            import re
            from bs4 import BeautifulSoup

            if trade_date is None:
                trade_date = datetime.now().strftime("%Y%m%d")

            td_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
            session = self._get_session()

            # ============================================================
            # Step 1: 列表页获取公告链接
            # ============================================================
            list_url = ("http://www.pbc.gov.cn/zhengcehuobisi/125207/"
                        "125213/125431/125475/index.html")
            resp = session.get(list_url, timeout=TIMEOUT_CONFIG.http_timeout)
            resp.encoding = 'utf-8'  # PBOC官网2025年后已切换为UTF-8
            list_html = resp.text

            soup = BeautifulSoup(list_html, 'html.parser')

            # 提取所有公告 (标题, 相对链接)
            announcements = []
            for a in soup.find_all('a', href=True):
                title = a.get_text(strip=True)
                href = a['href']
                if '公开市场业务交易公告' in title and '第' in title and href.endswith('.html'):
                    announcements.append((title, href))

            if not announcements:
                return DataResult(
                    data_type="central_bank_omo",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="PBOC公告列表页未找到任何公告",
                )

            # ============================================================
            # Step 2: 定位指定日期的公告
            # ============================================================
            target_href = None
            for title, href in announcements:
                # 从URL中的日期戳判断 或 从公告编号推断
                # URL格式: /2026081808573129759/index.html -> 前8位是日期
                m = re.search(r'/(\d{8})\d+/', href)
                if m:
                    url_date = m.group(1)
                    if url_date == trade_date:
                        target_href = href
                        break

            # 如果列表第一页没找到目标日期，尝试取最新的一条（可能当日公告已发）
            if target_href is None:
                # 看最新公告的日期
                m = re.search(r'/(\d{8})\d+/', announcements[0][1])
                if m:
                    latest_date = m.group(1)
                    # 如果最新日期比目标日期新或相等，可能是当日公告
                    if latest_date >= trade_date:
                        target_href = announcements[0][1]
                        trade_date = latest_date
                        td_dash = f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:]}"

            if target_href is None:
                return DataResult(
                    data_type="central_bank_omo",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"未找到 {trade_date} 的OMO公告",
                )

            detail_url = ('http://www.pbc.gov.cn' + target_href
                          if target_href.startswith('/') else target_href)

            # ============================================================
            # Step 3: 解析公告详情
            # ============================================================
            resp = session.get(detail_url, timeout=TIMEOUT_CONFIG.http_timeout)
            resp.encoding = 'utf-8'
            detail_html = resp.text

            detail_soup = BeautifulSoup(detail_html, 'html.parser')
            full_text = detail_soup.get_text('\n', strip=True)

            operations = {}  # 期限 -> 中标量(亿元)
            bids = {}        # 期限 -> 投标量(亿元)
            rates = {}       # 期限 -> 中标利率(%)
            maturity = {}    # 类型 -> 到期量(亿元)
            maturity_total = 0.0

            # --- 3a. 解析操作表格（逆回购） ---
            tables = detail_soup.find_all('table')
            for table in tables:
                rows = table.find_all('tr')
                if len(rows) < 2:
                    continue
                header_cells = [c.get_text(strip=True) for c in rows[0].find_all(['td', 'th'])]
                # 识别逆回购操作表: 有"期限"且有"中标量"或"交易量"
                has_term = any('期限' in h for h in header_cells)
                has_amount = any('中标量' in h or '交易量' in h for h in header_cells)
                if not (has_term and has_amount):
                    continue

                for row in rows[1:]:
                    cells = [c.get_text(strip=True) for c in row.find_all(['td', 'th'])]
                    if len(cells) < 3:
                        continue
                    term_raw = cells[0]
                    bid_raw = cells[1] if len(cells) > 1 else ''
                    win_raw = cells[2] if len(cells) > 2 else ''

                    # 严格过滤：期限单元格必须短（<10字）且中标量含"亿元"
                    # 避免把整页拼接文本的大单元格误识别
                    if len(term_raw) > 15:
                        continue
                    if '亿元' not in win_raw and '亿元' not in bid_raw:
                        continue

                    # 标准化期限名称
                    term_key = self._normalize_omo_term(term_raw)
                    if not term_key:
                        continue

                    # 提取金额数字
                    bid_num = re.search(r'([\d.]+)', bid_raw)
                    win_num = re.search(r'([\d.]+)', win_raw)

                    if win_num:
                        operations[term_key] = float(win_num.group(1))
                    if bid_num:
                        bids[term_key] = float(bid_num.group(1))
                break  # 只处理第一个匹配的操作表

            # --- 3b. 从文本提取隔夜逆回购 ---
            # 模式："开展了XXXX亿元隔夜逆回购操作"
            overnight_match = re.search(
                r'开展[了]?\s*([\d.]+)\s*亿元\s*隔夜\s*逆回购', full_text)
            if overnight_match:
                operations['隔夜逆回购'] = float(overnight_match.group(1))

            # --- 3c. 提取MLF操作量 ---
            mlf_match = re.search(
                r'([\d.]+)\s*亿元.*?中期借贷便利|MLF', full_text[:2000])
            if mlf_match and mlf_match.group(1):
                amt = float(mlf_match.group(1))
                if amt > 0:
                    operations['MLF'] = amt

            # --- 3d. 提取MLF利率 ---
            rate_match = re.search(r'利率[为是：:]\s*([\d.]+)\s*%', full_text[:2000])
            if rate_match:
                rates['MLF'] = float(rate_match.group(1))

            # --- 3e. 提取到期量 ---
            # 常见表述："今日有XXXX亿元逆回购到期"
            # 也可能："公开市场有XXXX亿元逆回购到期"
            mat_patterns = [
                r'([\d.]+)\s*亿元\s*(\d+天期|隔夜)?\s*逆回购\s*到期',
                r'有\s*([\d.]+)\s*亿元\s*(逆回购|MLF)\s*到期',
                r'逆回购到期\s*([\d.]+)\s*亿元',
                r'MLF到期\s*([\d.]+)\s*亿元',
            ]
            for pat in mat_patterns:
                for m in re.finditer(pat, full_text[:3000]):
                    amt = float(m.group(1))
                    if amt <= 0:
                        continue
                    # 判断类型
                    if 'MLF' in m.group(0):
                        mat_key = 'MLF到期'
                    else:
                        mat_key = '逆回购到期'
                    maturity[mat_key] = amt

            maturity_total = sum(maturity.values())

            # --- 3f. 计算净投放 ---
            total_injection = sum(operations.values())
            net_injection = total_injection - maturity_total

            # ============================================================
            # Step 4: 历史数据（最近N条公告）
            # ============================================================
            history_records = []
            hist_count = min(lookback_days, len(announcements))
            for title, href in announcements[:hist_count]:
                m = re.search(r'/(\d{8})\d+/', href)
                if not m:
                    continue
                hist_date = m.group(1)
                # 简单解析历史记录（只提取主要字段，避免发太多请求）
                # 先从标题获取公告号，日期从URL取
                history_records.append({
                    'date': hist_date,
                    'title': title,
                    'url': ('http://www.pbc.gov.cn' + href
                            if href.startswith('/') else href),
                })

            history_df = pd.DataFrame(history_records)

            result_data = {
                "date": trade_date,
                "total_injection": total_injection,
                "maturity_total": maturity_total,
                "net_injection": net_injection,
                "operations": operations,
                "bids": bids,
                "rates": rates,
                "maturity": maturity,
                "history": history_df,
                "source_url": detail_url,
            }

            return DataResult(
                data=result_data,
                data_type="central_bank_omo",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "trade_date": trade_date,
                    "lookback_days": lookback_days,
                    "source_url": detail_url,
                },
            )
        except Exception as e:
            logger.error(f"[web] OMO爬取异常: {type(e).__name__}: {e}")
            return DataResult(
                data_type="central_bank_omo",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web OMO爬取失败: {type(e).__name__}: {e}",
            )

    @staticmethod
    def _normalize_omo_term(term_raw: str) -> Optional[str]:
        """标准化OMO期限名称。"""
        term_raw = term_raw.strip()
        if '隔夜' in term_raw or '1天' in term_raw or '1日' in term_raw:
            return '隔夜逆回购'
        elif '7天' in term_raw or '7日' in term_raw or '7 天' in term_raw:
            return '7天逆回购'
        elif '14天' in term_raw or '14日' in term_raw:
            return '14天逆回购'
        elif '28天' in term_raw or '28日' in term_raw:
            return '28天逆回购'
        elif '63天' in term_raw or '63日' in term_raw:
            return '63天逆回购'
        elif 'MLF' in term_raw or '中期借贷便利' in term_raw:
            return 'MLF'
        elif '正回购' in term_raw:
            return '正回购'
        return None


        # ============================================================
    # 海外宏观数据
    # ============================================================

    def fetch_global_macro(self, trade_date: str = None, us_treasury_lookback_days: int = 20,
                           **kwargs) -> DataResult:
        """
        从Web获取海外宏观数据。

        数据源:
        - 美债利率: FRED CSV 接口
        - DXY/原油/CNH: TradingEconomics 等
        """
        try:
            from datetime import datetime, timedelta
            from io import StringIO

            if trade_date is None:
                trade_date = datetime.now().strftime("%Y%m%d")

            dxy = None
            us_2y = None
            us_10y = None
            brent = None
            cnh = None
            us_2y_history = pd.DataFrame()
            us_10y_history = pd.DataFrame()
            us_10y_up_ratio_20d = None

            # 1. FRED 美债2年期 CSV
            try:
                url_2y = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2"
                csv_text = self._get(url_2y)
                if csv_text:
                    df_2y = pd.read_csv(StringIO(csv_text))
                    df_2y = df_2y.dropna()
                    if not df_2y.empty:
                        df_2y.columns = ['date', 'us_2y']
                        df_2y['date'] = pd.to_datetime(df_2y['date']).dt.strftime('%Y-%m-%d')
                        df_2y['us_2y'] = df_2y['us_2y'].astype(float)
                        df_2y = df_2y.sort_values('date', ascending=False)
                        us_2y = float(df_2y.iloc[0]['us_2y'])
                        us_2y_history = df_2y.head(us_treasury_lookback_days).sort_values('date', ascending=True).reset_index(drop=True)
            except Exception as e:
                logger.debug(f"[web] FRED DGS2 失败: {e}")

            # 2. FRED 美债10年期 CSV
            try:
                url_10y = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
                csv_text = self._get(url_10y)
                if csv_text:
                    df_10y = pd.read_csv(StringIO(csv_text))
                    df_10y = df_10y.dropna()
                    if not df_10y.empty:
                        df_10y.columns = ['date', 'us_10y']
                        df_10y['date'] = pd.to_datetime(df_10y['date']).dt.strftime('%Y-%m-%d')
                        df_10y['us_10y'] = df_10y['us_10y'].astype(float)
                        df_10y = df_10y.sort_values('date', ascending=False)
                        us_10y = float(df_10y.iloc[0]['us_10y'])
                        us_10y_history = df_10y.head(us_treasury_lookback_days).sort_values('date', ascending=True).reset_index(drop=True)

                        # 20日上涨天数占比
                        if len(us_10y_history) >= 2:
                            vals = us_10y_history['us_10y'].values
                            diffs = vals[1:] - vals[:-1]
                            us_10y_up_ratio_20d = float((diffs > 0).sum()) / len(diffs)
            except Exception as e:
                logger.debug(f"[web] FRED DGS10 失败: {e}")

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
                error=f"Web 海外宏观获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 交易所回购利率
    # ============================================================

    def fetch_repo_exchange(self, trade_date: str = None, lookback_days: int = 20,
                            **kwargs) -> DataResult:
        """从Web获取交易所回购利率（上交所GC001/GC007）。"""
        try:
            from datetime import datetime

            if trade_date is None:
                trade_date = datetime.now().strftime("%Y%m%d")

            # 上交所逆回购实时数据
            # GC001 = 204001, GC007 = 204007
            gc001 = None
            gc007 = None

            # 尝试从东方财富获取GC001/GC007
            for code, name in [('204001', 'gc001'), ('204007', 'gc007')]:
                try:
                    url = f"http://push2.eastmoney.com/api/qt/stock/get?secid=1.{code}&fields=f43,f44,f45,f46,f47,f170"
                    text = self._get(url)
                    if text:
                        import json
                        data = json.loads(text)
                        if data and 'data' in data and data['data']:
                            close = data['data'].get('f43')
                            if close:
                                val = float(close) / 100.0  # 东财返回单位是%
                                if name == 'gc001':
                                    gc001 = val
                                else:
                                    gc007 = val
                except Exception as e:
                    logger.debug(f"[web] GC{code}获取失败: {e}")

            if gc001 is None and gc007 is None:
                return DataResult(
                    data_type="repo_exchange",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="Web 回购利率获取失败",
                )

            result_data = {
                "date": trade_date,
                "latest": {"FR001": gc001, "FR007": gc007},
                "gc001": gc001,
                "gc007": gc007,
                "history": pd.DataFrame(),
            }

            return DataResult(
                data=result_data,
                data_type="repo_exchange",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"trade_date": trade_date},
            )
        except Exception as e:
            return DataResult(
                data_type="repo_exchange",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web 回购利率获取失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 国债期货历史数据
    # ============================================================

    def fetch_treasury_futures(self, symbol: str = "TL", lookback_days: int = 30,
                               **kwargs) -> DataResult:
        """从Web获取国债期货数据（新浪期货接口兜底）。"""
        # AKShare的futures_main_sina已经使用新浪，Web层通常无需重复
        return DataResult(
            data_type="treasury_futures",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Web层国债期货由AKShare新浪接口覆盖，无需重复爬取",
        )

    # ============================================================
    # 大小盘对比指标
    # ============================================================

    def fetch_size_style(self, lookback_days: int = 60, base_date: str = "20211231",
                         **kwargs) -> DataResult:
        """从Web获取大小盘对比指标（中证指数官网兜底）。"""
        # AKShare的csindex接口已覆盖，Web层通常无需重复
        return DataResult(
            data_type="size_style",
            source=self.name,
            source_tier=self.tier,
            success=False,
            error="Web层大小盘指标由AKShare csindex接口覆盖，无需重复爬取",
        )

    # ============================================================
    # A股实时行情（stock_spot）—— 新浪分页接口
    # ============================================================

    def fetch_stock_spot(self, market: str = "all", **kwargs) -> DataResult:
        """
        从新浪 hq.sinajs.cn 批量接口获取A股实时行情（Web层兜底）。

        策略：
        1. 先从上一个交易日的日线数据获取全市场股票代码列表
           （优先用 Tushare daily，失败则用 AKShare stock_zh_a_spot 的代码）
        2. 转换为新浪格式（sh600000 / sz000001 / bj430001）
        3. 每批最多 200 只股票，调用 https://hq.sinajs.cn/list=code1,code2,...
        4. 解析返回文本，合并成 DataFrame

        Args:
            market: 市场类型（all）

        Returns:
            DataResult.data: pd.DataFrame — 实时行情数据
                列: ts_code, symbol, name, price, change, change_pct,
                    open, high, low, prev_close, volume, amount
        """
        try:
            import re
            from datetime import datetime, timedelta

            # ===== 步骤1：获取全市场股票代码列表 =====
            ts_codes = self._get_all_a_stock_codes()
            if not ts_codes:
                return DataResult(
                    data_type="stock_spot",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="无法获取全市场股票代码列表",
                )

            logger.debug(f"[web] 全市场股票代码: {len(ts_codes)} 只")

            # 转换为新浪格式
            sina_codes = []
            for code in ts_codes:
                if '.' not in code:
                    continue
                num, market_suffix = code.split('.', 1)
                market_lower = market_suffix.lower()
                if market_lower == 'sh':
                    sina_codes.append(f'sh{num}')
                elif market_lower == 'sz':
                    sina_codes.append(f'sz{num}')
                elif market_lower == 'bj':
                    sina_codes.append(f'bj{num}')
                else:
                    sina_codes.append(f'sh{num}')

            # ===== 步骤2：分批调用新浪 hq.sinajs.cn 接口 =====
            session = self._get_session()
            headers = {
                'Referer': 'https://finance.sina.com.cn',
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
            }

            records = []
            batch_size = 200  # 每批200只
            total_batches = (len(sina_codes) + batch_size - 1) // batch_size

            for batch_idx in range(total_batches):
                batch_start = batch_idx * batch_size
                batch_end = min(batch_start + batch_size, len(sina_codes))
                batch_codes = sina_codes[batch_start:batch_end]

                try:
                    url = f"https://hq.sinajs.cn/list={','.join(batch_codes)}"
                    resp = session.get(url, headers=headers,
                                       timeout=TIMEOUT_CONFIG.http_timeout)
                    resp.encoding = 'gbk'

                    pattern = r'var hq_str_(\w+)="([^"]*)";'
                    matches = re.findall(pattern, resp.text)

                    for symbol, fields_str in matches:
                        fields = fields_str.split(',')
                        if len(fields) < 10:
                            continue
                        try:
                            name = fields[0]
                            open_p = float(fields[1]) if fields[1] else 0
                            prev_close = float(fields[2]) if fields[2] else 0
                            price = float(fields[3]) if fields[3] else 0
                            high = float(fields[4]) if fields[4] else 0
                            low = float(fields[5]) if fields[5] else 0
                            volume = float(fields[8]) if len(fields) > 8 and fields[8] else 0
                            amount = float(fields[9]) if len(fields) > 9 and fields[9] else 0

                            # 过滤停牌/无数据（昨收或最新价为0）
                            if prev_close <= 0 or price <= 0:
                                continue

                            change = price - prev_close
                            change_pct = (change / prev_close * 100) if prev_close > 0 else 0

                            date_str = fields[30] if len(fields) > 30 else ''
                            time_str = fields[31] if len(fields) > 31 else ''

                            records.append({
                                'symbol': symbol.lower(),
                                'name': name,
                                'price': price,
                                'change': round(change, 4),
                                'change_pct': round(change_pct, 4),
                                'open': open_p,
                                'high': high,
                                'low': low,
                                'prev_close': prev_close,
                                'volume': volume,
                                'amount': amount,
                                'tick_time': f"{date_str} {time_str}".strip(),
                            })
                        except (ValueError, IndexError):
                            continue

                    # 适当节流
                    time.sleep(0.05)

                except Exception as e:
                    logger.debug(f"[web] stock_spot batch {batch_idx+1}/{total_batches} 失败: {e}")
                    continue

                # 每20批打印一次进度
                if (batch_idx + 1) % 20 == 0:
                    logger.debug(
                        f"[web] stock_spot 进度: {batch_idx+1}/{total_batches} 批, "
                        f"已获取 {len(records)} 只"
                    )

            if not records:
                return DataResult(
                    data_type="stock_spot",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="新浪hq.sinajs.cn批量接口未返回有效数据",
                )

            df = pd.DataFrame(records)

            # 生成 ts_code
            ts_codes_out = []
            for s in df['symbol']:
                s_lower = s.lower()
                if s_lower.startswith('sh'):
                    ts_codes_out.append(s_lower[2:] + '.SH')
                elif s_lower.startswith('sz'):
                    ts_codes_out.append(s_lower[2:] + '.SZ')
                elif s_lower.startswith('bj'):
                    ts_codes_out.append(s_lower[2:] + '.BJ')
                else:
                    ts_codes_out.append(s_lower)
            df['ts_code'] = ts_codes_out

            return DataResult(
                data=df,
                data_type="stock_spot",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "api": "sina_hq_batch",
                    "record_count": len(df),
                    "total_batches": total_batches,
                    "input_codes": len(sina_codes),
                },
            )

        except Exception as e:
            return DataResult(
                data_type="stock_spot",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )

    def _get_all_a_stock_codes(self):
        """
        获取全市场A股股票代码列表。
        优先从 Tushare daily 数据获取（更全），失败则降级到其他方式。

        Returns:
            list[str] — tushare格式股票代码列表，如 ['000001.SZ', '600000.SH', ...]
        """
        from datetime import datetime, timedelta

        codes = []

        # 方法1：从 Tushare 拿最近一个交易日的日线数据
        try:
            from ..sources import TushareSource
            ts_source = TushareSource()
            if ts_source.is_available():
                pro = ts_source._get_pro()
                # 往前查最多 10 天，找到有数据的交易日
                today = datetime.now()
                for i in range(1, 15):
                    check_date = (today - timedelta(days=i)).strftime('%Y%m%d')
                    ts_source._rate_limit()
                    try:
                        df = pro.daily(trade_date=check_date)
                        if df is not None and not df.empty and len(df) > 1000:
                            codes = df['ts_code'].astype(str).tolist()
                            logger.debug(
                                f"[web] 从Tushare({check_date})获取股票代码: {len(codes)} 只"
                            )
                            return codes
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"[web] Tushare获取股票代码失败: {e}")

        # 方法2：从 AKShare stock_info_a_code_name 获取（东财源，可能被封）
        try:
            from ..sources import AKShareSource
            ak_src = AKShareSource()
            if ak_src.is_available():
                ak = ak_src._get_ak()
                ak_src._rate_limit()
                try:
                    df = ak.stock_info_a_code_name()
                    if df is not None and not df.empty:
                        # 列名可能是 'code' / 'ts_code' / '代码'
                        code_col = None
                        for c in ['ts_code', 'code', '代码', '证券代码']:
                            if c in df.columns:
                                code_col = c
                                break
                        if code_col:
                            raw_codes = df[code_col].astype(str).tolist()
                            for code in raw_codes:
                                code = code.strip()
                                if '.' in code:
                                    codes.append(code)
                                elif len(code) == 6 and code.isdigit():
                                    if code.startswith('6'):
                                        codes.append(code + '.SH')
                                    elif code.startswith(('0', '3')):
                                        codes.append(code + '.SZ')
                                    else:
                                        codes.append(code + '.BJ')
                            if len(codes) > 1000:
                                logger.debug(
                                    f"[web] 从AKShare stock_info 获取股票代码: {len(codes)} 只"
                                )
                                return codes
                except Exception as e:
                    logger.debug(f"[web] AKShare stock_info 获取失败: {e}")
        except Exception as e:
            logger.debug(f"[web] AKShare获取股票代码失败: {e}")

        # 方法2b：从 AKShare stock_zh_a_spot（新浪源）获取代码列表
        # 当东财源被封时，新浪源仍可用作代码列表来源
        codes = []
        try:
            from ..sources import AKShareSource
            ak_src2 = AKShareSource()
            if ak_src2.is_available():
                ak2 = ak_src2._get_ak()
                ak_src2._rate_limit()
                try:
                    df_sina = ak2.stock_zh_a_spot()
                    if df_sina is not None and not df_sina.empty:
                        # 新浪源代码列名为 '代码'，格式如 sh600000 / sz000001 / bj430047
                        code_col = None
                        for c in ['代码', 'code', 'symbol']:
                            if c in df_sina.columns:
                                code_col = c
                                break
                        if code_col:
                            raw_codes = df_sina[code_col].astype(str).tolist()
                            for raw in raw_codes:
                                raw = raw.strip().lower()
                                if raw.startswith('sh'):
                                    codes.append(raw[2:] + '.SH')
                                elif raw.startswith('sz'):
                                    codes.append(raw[2:] + '.SZ')
                                elif raw.startswith('bj'):
                                    codes.append(raw[2:] + '.BJ')
                                elif len(raw) == 6 and raw.isdigit():
                                    if raw.startswith('6') or raw.startswith('9'):
                                        codes.append(raw + '.SH')
                                    elif raw.startswith(('0', '3')):
                                        codes.append(raw + '.SZ')
                                    else:
                                        codes.append(raw + '.BJ')
                            if len(codes) > 1000:
                                logger.debug(
                                    f"[web] 从AKShare sina spot 获取股票代码: {len(codes)} 只"
                                )
                                return codes
                except Exception as e:
                    logger.debug(f"[web] AKShare sina spot 获取股票代码失败: {e}")
        except Exception as e:
            logger.debug(f"[web] AKShare sina 获取股票代码失败: {e}")

        # 方法3：最终兜底 — 硬编码主要指数成分股（200+只代表性大盘股）
        logger.warning("[web] 所有动态代码获取方式均失败，使用硬编码主要股票列表")
        codes = [
            '000001.SZ', '000002.SZ', '000063.SZ', '000100.SZ', '000157.SZ',
            '000333.SZ', '000538.SZ', '000568.SZ', '000596.SZ', '000625.SZ',
            '000651.SZ', '000661.SZ', '000725.SZ', '000768.SZ', '000776.SZ',
            '000858.SZ', '000895.SZ', '000938.SZ', '000963.SZ', '000977.SZ',
            '002001.SZ', '002027.SZ', '002049.SZ', '002120.SZ', '002142.SZ',
            '002202.SZ', '002230.SZ', '002236.SZ', '002271.SZ', '002304.SZ',
            '002311.SZ', '002352.SZ', '002415.SZ', '002460.SZ', '002466.SZ',
            '002475.SZ', '002493.SZ', '002555.SZ', '002601.SZ', '002607.SZ',
            '002714.SZ', '002736.SZ', '002812.SZ', '002916.SZ', '002938.SZ',
            '003816.SZ', '300003.SZ', '300014.SZ', '300015.SZ', '300059.SZ',
            '300122.SZ', '300124.SZ', '300142.SZ', '300144.SZ', '300347.SZ',
            '300408.SZ', '300413.SZ', '300433.SZ', '300498.SZ', '300529.SZ',
            '300601.SZ', '300628.SZ', '300750.SZ', '300760.SZ', '300782.SZ',
            '600000.SH', '600009.SH', '600010.SH', '600011.SH', '600015.SH',
            '600016.SH', '600018.SH', '600019.SH', '600028.SH', '600029.SH',
            '600030.SH', '600031.SH', '600036.SH', '600048.SH', '600050.SH',
            '600061.SH', '600085.SH', '600089.SH', '600104.SH', '600111.SH',
            '600115.SH', '600176.SH', '600183.SH', '600188.SH', '600196.SH',
            '600276.SH', '600309.SH', '600332.SH', '600346.SH', '600406.SH',
            '600436.SH', '600438.SH', '600519.SH', '600570.SH', '600585.SH',
            '600588.SH', '600690.SH', '600745.SH', '600837.SH', '600887.SH',
            '600900.SH', '600918.SH', '600919.SH', '601006.SH', '601009.SH',
            '601012.SH', '601066.SH', '601088.SH', '601100.SH', '601111.SH',
            '601117.SH', '601138.SH', '601155.SH', '601166.SH', '601169.SH',
            '601186.SH', '601211.SH', '601225.SH', '601229.SH', '601231.SH',
            '601236.SH', '601238.SH', '601288.SH', '601318.SH', '601328.SH',
            '601336.SH', '601360.SH', '601377.SH', '601390.SH', '601398.SH',
            '601601.SH', '601628.SH', '601633.SH', '601668.SH', '601669.SH',
            '601688.SH', '601698.SH', '601728.SH', '601766.SH', '601788.SH',
            '601800.SH', '601808.SH', '601816.SH', '601818.SH', '601828.SH',
            '601838.SH', '601857.SH', '601877.SH', '601878.SH', '601881.SH',
            '601888.SH', '601898.SH', '601899.SH', '601901.SH', '601919.SH',
            '601933.SH', '601939.SH', '601958.SH', '601985.SH', '601988.SH',
            '601989.SH', '601998.SH', '603019.SH', '603156.SH', '603160.SH',
            '603185.SH', '603195.SH', '603259.SH', '603260.SH', '603288.SH',
            '603369.SH', '603444.SH', '603486.SH', '603501.SH', '603515.SH',
            '603517.SH', '603659.SH', '603799.SH', '603806.SH', '603833.SH',
            '603858.SH', '603885.SH', '603899.SH', '603986.SH', '603993.SH',
        ]
        return codes

    # ============================================================
    # 指数日线行情（index_daily）—— 新浪K线接口
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
        从新浪财经获取指数日线K线数据。

        接口: https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData
        参数: symbol=sh000001&scale=240&ma=no&datalen=500

        Args:
            ts_code: 指数代码（tushare格式）
            start_date: 开始日期
            end_date: 结束日期
            trade_date: 单日日期

        Returns:
            DataResult.data: pd.DataFrame — 指数日线数据
        """
        try:
            import requests
            import json as _json

            if not ts_code:
                return DataResult(
                    data_type="index_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="指数代码为空",
                )

            # 构造新浪格式代码
            code_part = ts_code.split('.')[0]
            if ts_code.endswith('.SH') or code_part.startswith('000'):
                sina_code = f"sh{code_part}"
            elif ts_code.endswith('.SZ') or code_part.startswith('399'):
                sina_code = f"sz{code_part}"
            else:
                sina_code = f"sh{code_part}"

            # 计算需要的天数
            sd = start_date or trade_date
            ed = end_date or trade_date
            datalen = 500  # 最多取500天

            url = (
                "https://money.finance.sina.com.cn/quotes_service/"
                f"api/json_v2.php/CN_MarketData.getKLineData?"
                f"symbol={sina_code}&scale=240&ma=no&datalen={datalen}"
            )
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            }

            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            text = resp.text.strip()

            # 返回的是 JSON 数组
            if not text or text == 'null':
                return DataResult(
                    data_type="index_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"新浪指数日线返回空 ({sina_code})",
                )

            data = _json.loads(text)
            if not data:
                return DataResult(
                    data_type="index_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"新浪指数日线数据为空 ({sina_code})",
                )

            df = pd.DataFrame(data)
            # 新浪返回列: day, open, high, low, close, volume
            # 标准化列名
            rename_map = {
                'day': 'trade_date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'vol',
            }
            df = df.rename(columns=rename_map)
            if 'trade_date' in df.columns:
                df['trade_date'] = df['trade_date'].str.replace('-', '')

            # 数值列转换
            for col in ['open', 'high', 'low', 'close', 'vol']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # 按日期过滤
            if sd and 'trade_date' in df.columns:
                df = df[df['trade_date'] >= sd]
            if ed and 'trade_date' in df.columns:
                df = df[df['trade_date'] <= ed]

            # 添加 ts_code 列
            df['ts_code'] = ts_code

            # 按日期排序
            df = df.sort_values('trade_date').reset_index(drop=True)

            if df.empty:
                return DataResult(
                    data_type="index_daily",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"日期过滤后无数据 ({sina_code})",
                )

            return DataResult(
                data=df,
                data_type="index_daily",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "ts_code": ts_code,
                    "sina_code": sina_code,
                    "start_date": start_date or trade_date or "",
                    "end_date": end_date or trade_date or "",
                    "api": "sina_getKLineData",
                },
            )
        except Exception as e:
            logger.error(f"[web] index_daily 获取异常: {e}")
            return DataResult(
                data_type="index_daily",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=str(e),
            )

    # ============================================================
    # 指数实时行情（index_spot）—— 新浪hq.sinajs.cn接口
    # ============================================================

    def fetch_index_spot(self, index_codes: Optional[List[str]] = None, **kwargs) -> DataResult:
        """
        从新浪 hq.sinajs.cn 接口获取指数实时行情。

        接口: https://hq.sinajs.cn/list=sh000001,sz399006,...
        编码: GBK
        Referer: https://finance.sina.com.cn

        Args:
            index_codes: 指数代码列表（新浪格式如 sh000001 或 tushare格式）

        Returns:
            DataResult.data: pd.DataFrame — 指数实时行情
        """
        try:
            session = self._get_session()

            # 默认主要指数
            if not index_codes:
                index_codes = [
                    'sh000001', 'sh000300', 'sh000905', 'sh000852',
                    'sh000016', 'sh000688',
                    'sz399001', 'sz399006', 'sz399106',
                ]
            else:
                # 转换 tushare 格式为新浪格式
                sina_codes = []
                for c in index_codes:
                    c = str(c).strip()
                    if c.lower().startswith(('sh', 'sz', 'bj')):
                        sina_codes.append(c.lower())
                    elif '.' in c:
                        code, market = c.split('.', 1)
                        sina_codes.append(market.lower() + code)
                    elif len(c) == 6 and c.isdigit():
                        if c.startswith('399'):
                            sina_codes.append('sz' + c)
                        else:
                            sina_codes.append('sh' + c)
                    else:
                        sina_codes.append(c.lower())
                index_codes = sina_codes

            url = f"https://hq.sinajs.cn/list={','.join(index_codes)}"
            headers = {
                'Referer': 'https://finance.sina.com.cn',
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
            }

            resp = session.get(url, headers=headers, timeout=TIMEOUT_CONFIG.http_timeout)
            resp.encoding = 'gbk'

            # 解析 var hq_str_xxx="..."
            import re
            pattern = r'var hq_str_(\w+)="([^"]*)";'
            matches = re.findall(pattern, resp.text)

            records = []
            for symbol, fields_str in matches:
                fields = fields_str.split(',')
                if len(fields) < 10:
                    continue
                # 指数字段: 名称,今开,昨收,最新价,最高,最低,...,成交量,成交额,日期,时间
                try:
                    name = fields[0]
                    open_p = float(fields[1]) if fields[1] else 0
                    prev_close = float(fields[2]) if fields[2] else 0
                    price = float(fields[3]) if fields[3] else 0
                    high = float(fields[4]) if fields[4] else 0
                    low = float(fields[5]) if fields[5] else 0
                    volume = float(fields[8]) if len(fields) > 8 and fields[8] else 0
                    amount = float(fields[9]) if len(fields) > 9 and fields[9] else 0

                    change = price - prev_close if prev_close > 0 else 0
                    change_pct = (change / prev_close * 100) if prev_close > 0 else 0

                    # 日期时间
                    date_str = fields[30] if len(fields) > 30 else ''
                    time_str = fields[31] if len(fields) > 31 else ''

                    records.append({
                        'symbol': symbol.lower(),
                        'name': name,
                        'price': price,
                        'change': round(change, 4),
                        'change_pct': round(change_pct, 4),
                        'open': open_p,
                        'high': high,
                        'low': low,
                        'prev_close': prev_close,
                        'volume': volume,
                        'amount': amount,
                        'tick_time': f"{date_str} {time_str}".strip(),
                    })
                except (ValueError, IndexError):
                    continue

            if not records:
                return DataResult(
                    data_type="index_spot",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="新浪指数实时行情解析失败",
                )

            df = pd.DataFrame(records)

            # 生成 ts_code
            ts_codes = []
            for s in df['symbol']:
                s_lower = s.lower()
                if s_lower.startswith('sh'):
                    ts_codes.append(s_lower[2:] + '.SH')
                elif s_lower.startswith('sz'):
                    ts_codes.append(s_lower[2:] + '.SZ')
                else:
                    ts_codes.append(s_lower)
            df['ts_code'] = ts_codes

            return DataResult(
                data=df,
                data_type="index_spot",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={
                    "api": "sina_hq",
                    "record_count": len(df),
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
    # 期权IV历史序列（Tier 4：RV降级，用指数已实现波动率代替IV）

    # ============================================================
    # 北向资金数据（Web层 - 最后兜底）
    # ============================================================

    def fetch_northbound_flow(
        self,
        start_date: str = None,
        end_date: str = None,
        lookback_days: int = 60,
        **kwargs,
    ) -> DataResult:
        """
        北向资金数据（Web爬取 - 最后兜底）。
        Web层作为最后兜底，优先使用AKShare数据源。
        """
        try:
            # 尝试从东方财富页面爬取（作为兜底）
            return DataResult(
                data_type="northbound_flow",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="Web层北向资金暂未实现，请使用AKShare/Tushare数据源",
            )
        except Exception as e:
            return DataResult(
                data_type="northbound_flow",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web爬取北向资金失败: {type(e).__name__}: {e}",
            )

    # ============================================================
    # 股权质押数据（Web层 - 最后兜底）
    # ============================================================

    def fetch_equity_pledge(
        self,
        end_date: str = None,
        **kwargs,
    ) -> DataResult:
        """
        股权质押数据（Web爬取 - 最后兜底）。
        Web层作为最后兜底，优先使用AKShare数据源。
        """
        try:
            return DataResult(
                data_type="equity_pledge",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error="Web层股权质押暂未实现，请使用AKShare/Tushare数据源",
            )
        except Exception as e:
            return DataResult(
                data_type="equity_pledge",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"Web爬取股权质押失败: {type(e).__name__}: {e}",
            )


    # ============================================================
    # 期权原始行情兜底（T型报价 / 标的当日行情 / 现货快照）
    # ------------------------------------------------------------
    # 为「市场情绪监控」等业务脚本的 akshare 链路提供 Tier 4 兜底，
    # 输出列名严格对齐 akshare 原生函数，保证上层解析逻辑零改动。
    # ============================================================

    @staticmethod
    def _parse_sina_hq_fields(val_part: str) -> Optional[dict]:
        """解析新浪 hq.sinajs.cn 单条行情字段。

        格式: 名称,今开,昨收,最新价,最高,最低,买一,卖一,成交量,成交额,...
        """
        fields = val_part.split(',')
        if len(fields) < 10:
            return None
        try:
            name = fields[0]
            open_ = float(fields[1] or 0)
            prev_close = float(fields[2] or 0)
            price = float(fields[3] or 0)
            high = float(fields[4] or 0)
            low = float(fields[5] or 0)
            volume = float(fields[8] or 0)
            amount = float(fields[9] or 0)
        except (ValueError, IndexError):
            return None
        if price <= 0 and prev_close <= 0:
            return None
        change = price - prev_close if prev_close > 0 else 0.0
        change_pct = (price / prev_close - 1) * 100 if prev_close > 0 else 0.0
        return {
            "名称": name,
            "最新价": price,
            "今开": open_,
            "昨收": prev_close,
            "最高": high,
            "最低": low,
            "成交量": volume,
            "成交额": amount,
            "涨跌额": round(change, 4),
            "涨跌幅": round(change_pct, 4),
        }

    def _fetch_sina_hq_rows(self, sina_codes: List[str], code_style: str = "sina") -> List[dict]:
        """批量抓取新浪 hq 行情，返回行列表。

        Args:
            sina_codes: 新浪代码列表，如 ['sh000852', 'sh510300']
            code_style: 'sina'=保留 sh/sz 前缀（指数口径）；'plain'=去掉前缀（ETF 口径）
        """
        rows: List[dict] = []
        if not sina_codes:
            return rows
        url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
        text = self._get(url, headers={'Referer': 'https://finance.sina.com.cn'})
        if not text:
            return rows
        for line in text.strip().split('\n'):
            line = line.strip()
            if not line or '=' not in line:
                continue
            eq_idx = line.find('=')
            raw_code = line[:eq_idx].replace('var hq_str_', '').strip()
            val_part = line[eq_idx + 1:].strip().strip(';').strip('"')
            if not val_part:
                continue
            parsed = self._parse_sina_hq_fields(val_part)
            if not parsed:
                continue
            code = raw_code if code_style == "sina" else raw_code[2:]
            row = {"代码": code}
            row.update(parsed)
            rows.append(row)
        return rows

    def fetch_index_spot_sina(self, index_codes: Optional[List[str]] = None, **kwargs) -> DataResult:
        """新浪指数实时行情兜底（列名对齐 akshare stock_zh_index_spot_sina）。"""
        try:
            from ..option_maps import DEFAULT_INDEX_CODES, index_to_sina_symbol

            codes = [index_to_sina_symbol(c) for c in (index_codes or DEFAULT_INDEX_CODES)]
            rows = self._fetch_sina_hq_rows(codes, code_style="sina")
            if not rows:
                return DataResult(
                    data_type="index_spot_sina",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="新浪指数行情兜底返回空",
                )
            df = pd.DataFrame(rows)
            return DataResult(
                data=df,
                data_type="index_spot_sina",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"rows": len(df), "codes": codes},
            )
        except Exception as e:
            return DataResult(
                data_type="index_spot_sina",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"新浪指数行情兜底失败: {type(e).__name__}: {e}",
            )

    def fetch_etf_spot_em(self, etf_codes: Optional[List[str]] = None, **kwargs) -> DataResult:
        """ETF 实时行情兜底（列名对齐 akshare fund_etf_spot_em，「代码」列不带交易所前缀）。"""
        try:
            from ..option_maps import DEFAULT_ETF_CODES, etf_to_sina_symbol

            codes = [etf_to_sina_symbol(c) for c in (etf_codes or DEFAULT_ETF_CODES)]
            rows = self._fetch_sina_hq_rows(codes, code_style="plain")
            if not rows:
                return DataResult(
                    data_type="etf_spot_em",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error="新浪ETF行情兜底返回空",
                )
            df = pd.DataFrame(rows)
            return DataResult(
                data=df,
                data_type="etf_spot_em",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"rows": len(df), "codes": codes},
            )
        except Exception as e:
            return DataResult(
                data_type="etf_spot_em",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"新浪ETF行情兜底失败: {type(e).__name__}: {e}",
            )

    def fetch_option_underlying_quote(self, symbol: str, **kwargs) -> DataResult:
        """期权标的当日行情兜底（列名对齐 akshare option_finance_sse_underlying）。

        akshare 该接口在 pandas 3.x 下会抛 TypeError，此处用新浪 hq 兜底，
        通过看板名称反查标的 ETF 代码。
        """
        try:
            from ..option_maps import SSE_BOARD_TO_ETF, SZSE_BOARD_TO_ETF, etf_to_sina_symbol

            etf_code = SSE_BOARD_TO_ETF.get(symbol) or SZSE_BOARD_TO_ETF.get(symbol)
            if not etf_code:
                return DataResult(
                    data_type="option_underlying_quote",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"未知期权看板名称: {symbol}",
                )
            rows = self._fetch_sina_hq_rows([etf_to_sina_symbol(etf_code)], code_style="plain")
            if not rows:
                return DataResult(
                    data_type="option_underlying_quote",
                    source=self.name,
                    source_tier=self.tier,
                    success=False,
                    error=f"标的行情兜底返回空: {symbol}",
                )
            row = rows[0]
            df = pd.DataFrame([{
                "代码": row.get("代码", etf_code),
                "名称": row.get("名称", ""),
                "当前价": row.get("最新价", 0.0),
                "涨跌": row.get("涨跌额", 0.0),
                "涨跌幅": row.get("涨跌幅", 0.0),
                "振幅": 0.0,
                "成交量(手)": row.get("成交量", 0.0),
                "成交额(万元)": round(float(row.get("成交额", 0.0)) / 10000, 4),
                "更新日期": pd.Timestamp.now(),
            }])
            return DataResult(
                data=df,
                data_type="option_underlying_quote",
                source=self.name,
                source_tier=self.tier,
                success=True,
                meta={"symbol": symbol, "etf_code": etf_code, "rows": len(df)},
            )
        except Exception as e:
            return DataResult(
                data_type="option_underlying_quote",
                source=self.name,
                source_tier=self.tier,
                success=False,
                error=f"标的行情兜底失败: {type(e).__name__}: {e}",
            )
