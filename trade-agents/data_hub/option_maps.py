"""
期权标的映射表（data_hub 统一维护）
==================================
集中维护「ETF/指数代码 <-> 期权看板名称 <-> 新浪行情代码」的映射关系，
供 data_hub 内部（sources / collectors）使用，避免各业务脚本各自维护一份。

注意：本模块不依赖 data_hub 其它模块，可被 sources 与 collectors 共同引用。
"""

from __future__ import annotations

from typing import Dict, List

# ============================================================
# 上交所 ETF 期权
# ============================================================

# ETF 代码 -> 看板名称（option_finance_board 的 symbol 参数）
SSE_OPTION_BOARD_MAP: Dict[str, str] = {
    "510300": "华泰柏瑞沪深300ETF期权",
    "510500": "南方中证500ETF期权",
    "588000": "华夏科创50ETF期权",
    "588080": "易方达科创50ETF期权",
}

# 看板名称 -> ETF 代码
SSE_BOARD_TO_ETF: Dict[str, str] = {v: k for k, v in SSE_OPTION_BOARD_MAP.items()}

# ETF 代码 -> option_current_day_sse 合约简称前缀
SSE_NAME_PREFIX_MAP: Dict[str, str] = {
    "510300": "300ETF",
    "510500": "500ETF",
    "588000": "科创50",
    "588080": "科创50",
}

# ============================================================
# 深交所 ETF 期权
# ============================================================

SZSE_OPTION_BOARD_MAP: Dict[str, str] = {
    "159915": "易方达创业板ETF期权",
    "159919": "嘉实沪深300ETF期权",
    "159922": "嘉实中证500ETF期权",
}

SZSE_BOARD_TO_ETF: Dict[str, str] = {v: k for k, v in SZSE_OPTION_BOARD_MAP.items()}

SZSE_NAME_PREFIX_MAP: Dict[str, str] = {
    "159915": "创业板ETF",
    "159919": "沪深300ETF",
    "159922": "中证500ETF",
}

# ============================================================
# 中金所股指期权
# ============================================================

# 品种代码 -> (看板名称, 指数代码)
CFFEX_OPTION_BOARD_MAP: Dict[str, Dict[str, str]] = {
    "MO": {"board": "中证1000股指期权", "index": "000852"},
    "HO": {"board": "上证50股指期权", "index": "000016"},
    "IO": {"board": "沪深300股指期权", "index": "000300"},
}

# ============================================================
# 兜底行情快照默认代码集
# ============================================================

# 默认指数代码（新浪格式）
DEFAULT_INDEX_CODES: List[str] = [
    "sh000001",  # 上证指数
    "sh000300",  # 沪深300
    "sh000905",  # 中证500
    "sh000852",  # 中证1000
    "sh000016",  # 上证50
    "sh000688",  # 科创50
    "sz399001",  # 深证成指
    "sz399006",  # 创业板指
    "sz399106",  # 深证综指
]

# 默认 ETF 代码（不含交易所前缀，与 akshare fund_etf_spot_em 的「代码」列一致）
DEFAULT_ETF_CODES: List[str] = [
    "510050",  # 上证50ETF
    "510300",  # 沪深300ETF
    "510500",  # 中证500ETF
    "588000",  # 科创50ETF
    "588080",  # 科创板50ETF
    "159915",  # 创业板ETF
    "159919",  # 沪深300ETF(深)
    "159922",  # 中证500ETF(深)
    "512100",  # 中证1000ETF
    "512880",  # 证券ETF
    "512480",  # 半导体ETF
    "518880",  # 黄金ETF
    "511380",  # 转债ETF
    "513100",  # 纳指ETF
]


def etf_to_sina_symbol(code: str) -> str:
    """ETF 代码 -> 新浪行情代码（5/6/9 开头为沪市，1/3 开头为深市）。"""
    code = str(code).strip()
    if code.lower().startswith(("sh", "sz")):
        return code.lower()
    if code.startswith(("5", "6", "9")):
        return "sh" + code
    return "sz" + code


def index_to_sina_symbol(code: str) -> str:
    """指数代码 -> 新浪行情代码（399xxx 为深市，其余为沪市）。"""
    code = str(code).strip()
    if code.lower().startswith(("sh", "sz")):
        return code.lower()
    if code.startswith("399"):
        return "sz" + code
    return "sh" + code
