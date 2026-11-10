#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tushare 统一初始化（代理模式）
股债利差项目使用：ttx.dailyfetch.top 代理网关

token 取值优先级（避免把密钥写死在仓库里）：
  1. 环境变量 TUSHARE_PROXY_TOKEN
  2. 环境变量 TUSHARE_TOKEN
  3. 本地文件 ~/.config/tushare/proxy_token（单行）
  4. 内置默认值（向后兼容，便于首次部署）

兼容层（2026-10-19 补回，供 market_fetch / build_momentum / tda_60* 等复用）：
  TOKEN / DIRECT_TOKEN / USE_DIRECT_FALLBACK / get_bar() / ts_call()
  get_pro(token=..., http_url=...) 显式参数仍可用。
"""
import os
import sys
import datetime
import tushare as ts

# ⚠️ 全局硬规矩：容器 IPv6 不通，域名解析出 IPv6 会静默挂到超时 → 强制 IPv4
import socket as _socket
_orig_gai = _socket.getaddrinfo
_socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == _socket.AF_INET]

PROXY_URL = os.environ.get('TUSHARE_PROXY_URL', 'https://ttx.dailyfetch.top/')

_DEFAULT_PROXY_TOKEN = ''   # 公开版不内置密钥，请用环境变量或 ~/.config/tushare/proxy_token

# 旧直连 token（走 TuShare 官网，无代理）
DIRECT_TOKEN = os.environ.get('TUSHARE_DIRECT_TOKEN', '')

# 直连兜底开关（默认关闭：只用代理 token）
USE_DIRECT_FALLBACK = os.environ.get('TUSHARE_USE_DIRECT', '0').strip() in ('1', 'true', 'True')

_pro = None


def _load_token():
    """按优先级读取代理 token。"""
    for k in ('TUSHARE_PROXY_TOKEN', 'TUSHARE_TOKEN'):
        v = os.environ.get(k)
        if v and v.strip():
            return v.strip()
    fp = os.path.expanduser('~/.config/tushare/proxy_token')
    try:
        with open(fp, encoding='utf-8') as f:
            v = f.read().strip()
        if v:
            return v
    except Exception:
        pass
    if not _DEFAULT_PROXY_TOKEN:
        print('[tushare_init] 未配置 token：请设 TUSHARE_PROXY_TOKEN '
              '或写 ~/.config/tushare/proxy_token', file=sys.stderr)
    return _DEFAULT_PROXY_TOKEN


TOKEN = _load_token()


def get_pro(token=None, http_url=None):
    """返回 tushare pro 接口。

    - 不传参数（默认）：代理 token + 代理端点，单例缓存；
    - 显式传参：按参数构造新实例（不污染单例），供直连兜底使用。
    """
    global _pro
    if token is None and http_url is None:
        if callable(_pro) or _pro is None:
            pass  # 见下方：单例变量只放 pro 实例
        if _pro is not None and not callable(_pro):
            return _pro
        pro = ts.pro_api(TOKEN)
        pro._DataApi__http_url = PROXY_URL
        _pro = pro
        return pro
    tok = token or TOKEN
    url = PROXY_URL if http_url is None else http_url
    pro = ts.pro_api(tok)
    if url:
        pro._DataApi__http_url = url
    return pro


def get_bar(pro, **kwargs):
    """包装 ts.pro_bar，自动带上 api=pro"""
    return ts.pro_bar(api=pro, **kwargs)


# ------------------------------------------------- 双 token（代理优先 → 直连兜底）
_PRO_CACHE = {}


def _pro_api(kind='proxy'):
    """返回 pro 实例（缓存）：'proxy' = 代理 token，'direct' = 旧直连 token。

    直连受 USE_DIRECT_FALLBACK 开关控制；关闭时返回 None（调用方跳过）。
    """
    if kind == 'direct' and not USE_DIRECT_FALLBACK:
        return None
    if kind not in _PRO_CACHE:
        _PRO_CACHE[kind] = (get_pro() if kind == 'proxy'
                            else get_pro(token=DIRECT_TOKEN, http_url=''))
    return _PRO_CACHE[kind]


def _age(df, asof):
    """数据日期距 asof 相差天数；无 trade_date 列则 0（不判定）"""
    if df is None or 'trade_date' not in df.columns or not asof:
        return 0
    try:
        mx = str(df['trade_date'].astype(str).max())
        a = datetime.datetime.strptime(str(asof), '%Y%m%d')
        m = datetime.datetime.strptime(mx, '%Y%m%d')
        return (a - m).days
    except Exception:
        return 0


# ⚠️ 2026-11-07 大佬：「这个接口获取失败，你走下一个接口啊。你不能一直等着他」
#   原来 ts_call 里 `getattr(pro, api)(**kw)` **没有超时**：
#   网络卡住就无限阻塞（代理 + 直连各卡一次 = 两个都等死），上层只能干等。
#   → 每个调用包一层线程 + join(timeout)，超时立刻放弃，交给上层降级到下一层 API。
TS_TIMEOUT = float(os.environ.get("TS_TIMEOUT", "8"))


def _call_with_timeout(fn, kw, timeout):
    """在独立线程里跑 fn(**kw)，超时立即返回 (None, 'timeout')

    ⚠️ 线程无法强杀 → 用 daemon 线程，超时就丢弃（不会阻塞进程退出）
    """
    import threading
    box = {}

    def _run():
        try:
            box["df"] = fn(**kw)
        except Exception as e:
            box["err"] = e

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return None, "timeout"
    if "err" in box:
        return None, box["err"]
    return box.get("df"), None


def ts_call(api, **kw):
    """tushare 调用：**代理 token 优先 → 直连 token 兜底**，每层带硬超时。

    - 每层最多等 TS_TIMEOUT 秒（默认 8s），超时立刻切下一层
    - 轮 1：优先返回「数据日期够新」的结果（代理 → 直连）；
    - 轮 2：两级都只能拿到旧数据（如代理当日滞后）→ 回退用旧数据；
    - 两级都空/都超时 → 返回 None（上层立即可降级到 akshare / 网页）

    用法： df = ts_call('moneyflow', trade_date='20260923')
    """
    want = str(kw.get('trade_date') or kw.get('end_date') or '')
    stale = None
    for kind in ('proxy', 'direct'):
        try:
            pro = _pro_api(kind)
        except Exception:
            continue
        if pro is None:
            continue
        fn = getattr(pro, api, None)
        if fn is None:
            continue
        df, err = _call_with_timeout(fn, kw, TS_TIMEOUT)
        if err == "timeout":
            try:
                print("[tushare] %s(%s) 超时 %.0fs → 切下一层" % (api, kind, TS_TIMEOUT),
                      file=sys.stderr, flush=True)
            except Exception:
                pass
            continue
        if df is None or len(df) == 0:
            continue
        if want.isdigit() and _age(df, want) > 0:
            if stale is None:
                stale = df
            continue
        return df
    return stale


if __name__ == '__main__':
    pro = get_pro()
    df = pro.trade_cal(exchange='SSE', start_date='20261015', end_date='20261019')
    print(df)
