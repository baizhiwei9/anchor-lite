"""
Tushare 统一初始化配置
所有需要使用 Tushare 的地方都从这里导入 pro 和 pro_bar，确保代理地址一致。

代理说明：通过 ttx.dailyfetch.top 代理访问 Tushare 接口，可绕过 IP 限制。
如果代理不可用，降级到官方地址，再不行就走 data_hub 后续层级降级。

重要：本模块维护全局单例 pro 实例，所有采集器/数据源应复用同一个实例，
      以减少出口 IP 分散导致的 IP 超限问题。
"""

import os
import time

import tushare as ts

# 代理版 Tushare Token（15000 分，走代理地址可绕过 IP 限制）
TUSHARE_PROXY_TOKEN = os.environ.get('TUSHARE_PROXY_TOKEN', '***')
# 官方版 Tushare Token（2000 分，直连官方地址）
TUSHARE_OFFICIAL_TOKEN = os.environ.get('TUSHARE_OFFICIAL_TOKEN', '***')

# 代理地址（优先使用，可绕过 IP 限制）
TUSHARE_PROXY_URL = 'https://ttx.dailyfetch.top/'

# 官方地址（备用）
TUSHARE_OFFICIAL_URL = 'http://api.tushare.pro'

# 探活调用的最小间隔（防止多个 TushareSource 实例同时探活）
_PROBE_MIN_INTERVAL = 5.0  # 秒

# 全局单例状态
_pro_singleton = None
_pro_mode = None          # 'proxy' / 'official' / None（未初始化）
_pro_probed = False       # 是否已完成探活
_last_probe_ts = 0.0      # 上次探活时间戳


def _create_pro_instance(use_proxy: bool):
    """创建一个新的 pro 实例（不涉及单例）。"""
    if use_proxy:
        pro = ts.pro_api(TUSHARE_PROXY_TOKEN)
    else:
        pro = ts.pro_api(TUSHARE_OFFICIAL_TOKEN)
    if use_proxy:
        pro._DataApi__http_url = TUSHARE_PROXY_URL
    else:
        # 显式设置官方地址，避免使用 tushare 库内部的默认地址
        pro._DataApi__http_url = TUSHARE_OFFICIAL_URL
    return pro


def _probe_pro_available(pro) -> bool:
    """探活：用一个轻量接口验证 pro 实例是否可用。
    
    检测逻辑：
    1. trade_cal 返回非空 DataFrame → 可用
    2. 抛出异常（如 token已过期）→ 不可用
    3. 返回空 DataFrame（如官方 token 失效）→ 不可用
    """
    try:
        df = pro.trade_cal(
            exchange='SSE',
            start_date='20240101',
            end_date='20240102',
        )
        if df is None or df.empty:
            return False
        # 额外检查：确保返回的数据确实包含交易日
        if 'is_open' in df.columns:
            return int(df['is_open'].sum()) > 0
        return True
    except Exception as e:
        # 记录具体错误，便于排查（如 token已过期）
        import logging
        logging.getLogger('data_hub').debug(f"[tushare] 探活失败: {e}")
        return False


def _ensure_pro_ready():
    """
    确保全局单例 pro 已初始化且经探活可用。

    策略：
    1. 首次调用：创建代理地址单例 → 探活 → 成功则使用，失败则切官方地址
    2. 后续调用：直接返回已就绪的单例，不再探活
    3. 同一进程内只探活一次（代理失败才再探一次官方）
    """
    global _pro_singleton, _pro_mode, _pro_probed, _last_probe_ts

    # 已探活且可用，直接返回
    if _pro_probed and _pro_singleton is not None:
        return _pro_singleton

    # 防抖：短时间内重复调用不重复探活
    now = time.time()
    if _last_probe_ts > 0 and (now - _last_probe_ts) < _PROBE_MIN_INTERVAL:
        # 还在防抖期内，直接返回当前单例（即使可能刚失败）
        if _pro_singleton is not None:
            return _pro_singleton

    _last_probe_ts = now

    # 第一步：尝试代理地址
    if _pro_mode is None:
        try:
            _pro_singleton = _create_pro_instance(use_proxy=True)
            if _probe_pro_available(_pro_singleton):
                _pro_mode = 'proxy'
                _pro_probed = True
                return _pro_singleton
            # 代理探活失败，继续往下尝试官方
        except Exception:
            pass

    # 第二步：尝试官方地址
    try:
        _pro_singleton = _create_pro_instance(use_proxy=False)
        if _probe_pro_available(_pro_singleton):
            _pro_mode = 'official'
            _pro_probed = True
            return _pro_singleton
    except Exception:
        pass

    # 都不行也保留最后一个实例（让调用方去处理异常）
    if _pro_singleton is None:
        _pro_singleton = _create_pro_instance(use_proxy=False)
    # 即使探活失败，也标记为已探活，避免每次都重试
    # mode 设为 'dead' 表示两个 token 都不可用，调用方可快速跳过
    if _pro_mode is None:
        _pro_mode = 'dead'  # 代理和官方都不可用
    _pro_probed = True
    return _pro_singleton


def get_tushare_pro(use_proxy=True, force_new=False):
    """
    获取 Tushare pro 实例（全局单例，已探活）。
    
    同一进程内所有调用默认复用同一个 pro 实例，以避免不同实例各自维护连接、
    造成出口 IP 分散导致的"IP数量超限"。

    首次调用会自动探活：代理可用则用代理，否则降级到官方地址。
    
    Args:
        use_proxy: 是否优先使用代理地址（默认 True）
                   注意：这只是"优先"，实际使用哪种地址取决于探活结果。
                   所有实例共享同一个已探活的单例。
        force_new: 强制新建实例（不推荐，仅在特殊场景下使用，不复用单例）
    
    Returns:
        tushare.pro_api 实例
    """
    if force_new:
        return _create_pro_instance(use_proxy=use_proxy)

    return _ensure_pro_ready()


def get_pro_mode() -> str:
    """获取当前全局单例工作模式：'proxy' / 'official' / 'dead' / 'unknown'。
    
    - proxy: 代理地址可用
    - official: 官方地址可用（代理不可用时降级）
    - dead: 代理和官方都不可用（token过期/失效）
    - unknown: 尚未探活
    """
    return _pro_mode or 'unknown'


def is_tushare_dead() -> bool:
    """快速判断 Tushare 是否完全不可用（两个 token 都失效）。
    
    调用方可先用此函数判断，避免在不可用时仍浪费时间调用 API。
    """
    _ensure_pro_ready()
    return _pro_mode == 'dead'


def reset_tushare_pro():
    """
    重置全局单例状态（仅在测试或需要强制重建连接时使用）。
    """
    global _pro_singleton, _pro_mode, _pro_probed, _last_probe_ts
    _pro_singleton = None
    _pro_mode = None
    _pro_probed = False
    _last_probe_ts = 0.0


def tushare_pro_bar(use_proxy=True, **kwargs):
    """
    调用 ts.pro_bar，自动注入 pro 实例（复用全局单例）。
    
    用法：from data_hub.tushare_config import tushare_pro_bar
          df = tushare_pro_bar(ts_code='000001.SZ', limit=3)
    """
    pro = get_tushare_pro(use_proxy=use_proxy)
    return ts.pro_bar(api=pro, **kwargs)


# 兼容旧代码：直接访问 pro 属性
# 使用延迟初始化，首次访问时才触发探活
pro = None


def __getattr__(name):
    """模块级延迟初始化：访问 pro 属性时才创建单例。"""
    if name == 'pro':
        return get_tushare_pro(use_proxy=True)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
