#!/usr/bin/env python3
"""
Tushare 代理版初始化（统一入口）

后续所有需要 Tushare 的脚本，统一引用本文件初始化，不要在各处重复写 token/代理地址：

    from tushare_init import pro, ts, get_pro, get_pro_bar

用法：
    pro = get_pro()
    df = pro.index_basic(limit=5)

    df2 = get_pro_bar(ts_code="000001.SZ", limit=3)

注意：
    ⭐️ 代理版必须设置 pro._DataApi__http_url = "https://ttx.dailyfetch.top/"
    ⭐️ 如果显示 Token 不对，检查代码是不是少了上面这行代理地址。
"""

import os
import tushare as ts

# 代理版 Token（高积分，走代理地址绕过 IP 限制）
TUSHARE_PROXY_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# 代理地址（必须设置，否则走官方地址会 Token 不对 / 限频）
TUSHARE_PROXY_HTTP_URL = "https://ttx.dailyfetch.top/"


def get_pro():
    """返回已配置好代理地址的 pro 接口实例。"""
    pro = ts.pro_api(TUSHARE_PROXY_TOKEN)
    # 关键：代理版必须指向代理地址
    pro._DataApi__http_url = TUSHARE_PROXY_HTTP_URL
    return pro


def get_pro_bar(ts_code, **kwargs):
    """pro_bar 接口封装，自动带上已初始化的 pro 实例。"""
    pro = get_pro()
    return ts.pro_bar(api=pro, ts_code=ts_code, **kwargs)


# 模块级默认实例，方便 `from tushare_init import pro` 直接使用
pro = get_pro()


if __name__ == "__main__":
    print("=== 自检：index_basic ===")
    df = pro.index_basic(limit=5)
    print(df)

    print("\n=== 自检：pro_bar ===")
    df2 = get_pro_bar(ts_code="000001.SZ", limit=3)
    print(df2)
