"""
data_hub.sources 数据源适配器包
================================
四级数据源：
- Tier 1: AKShare (akshare_source.py)
- Tier 2: Tushare (tushare_source.py)
- Tier 3: Coze 数据网关 (coze_gateway_source.py) — 通过技能CLI调用AKShare
- Tier 4: Web 爬取 (web_source.py)
"""

from .akshare_source import AKShareSource
from .tushare_source import TushareSource
from .coze_gateway_source import CozeGatewaySource
from .web_source import WebSource

__all__ = [
    "AKShareSource",
    "TushareSource",
    "CozeGatewaySource",
    "WebSource",
]
