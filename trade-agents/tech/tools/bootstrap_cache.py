#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次运行前的缓存初始化（需联网 + Tushare token）

生成 `tech_buypoint.py` 依赖的两个本地缓存：

  .cache/sw_member_all.json   个股 → 申万 L1/L2/L3（tushare index_member_all）
  .cache/sw_index_map.json    行业名 → 行业指数代码（tushare index_classify L1/L2/L3）
  .cache/tech_stock_names.json  个股代码 → 股票名称（报告里显示中文名，否则只剩 6 位代码）

用法：
  export TUSHARE_PROXY_TOKEN=你的token
  python3 tools/bootstrap_cache.py
"""
import os
import sys
import json
import socket

# 全局硬规矩：禁 IPv6（容器/部分网络 IPv6 不通会静默卡死）
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))

CACHE = os.path.join(WS, ".cache")
SW_MEMBER = os.path.join(CACHE, "sw_member_all.json")
SW_INDEX_MAP = os.path.join(CACHE, "sw_index_map.json")
STOCK_NAMES = os.path.join(CACHE, "tech_stock_names.json")


def build_member():
    """index_member_all 单次上限 2000 条 → 用 offset 分页拉全（约 5900 只）。"""
    from tushare_init import ts_call
    out = {}
    off, page = 0, 2000
    while True:
        df = ts_call("index_member_all", offset=off, limit=page)
        if df is None or not len(df):
            break
        for r in df.to_dict("records"):
            code = r.get("ts_code")
            if not code:
                continue
            out[code] = {"l1": r.get("l1_name"), "l2": r.get("l2_name"),
                         "l3": r.get("l3_name"), "name": r.get("name")}
        print("  已拉 %d 条（累计 %d 只）" % (off + len(df), len(out)))
        if len(df) < page:
            break
        off += page
    if not out:
        print("[ERR] index_member_all 拉取失败（检查 token / 积分）")
        return False
    os.makedirs(CACHE, exist_ok=True)
    json.dump(out, open(SW_MEMBER, "w", encoding="utf-8"), ensure_ascii=False)
    print("[OK] %s  %d 只个股" % (SW_MEMBER, len(out)))
    return True


def build_index_map():
    from tushare_init import ts_call
    out = {}
    for lv in ("L1", "L2", "L3"):
        df = ts_call("index_classify", level=lv)
        if df is None or not len(df):
            print("[WARN] index_classify %s 为空" % lv)
            continue
        for r in df.to_dict("records"):
            code, name = str(r.get("index_code") or ""), str(r.get("industry_name") or "")
            if code.startswith("80") and name:
                out[name] = code
        print("[OK] index_classify %s  %d 条" % (lv, len(df)))
    if out:
        os.makedirs(CACHE, exist_ok=True)
        json.dump(out, open(SW_INDEX_MAP, "w", encoding="utf-8"), ensure_ascii=False)
        print("[OK] %s  %d 个行业指数" % (SW_INDEX_MAP, len(out)))
    return bool(out)


def build_names():
    """个股代码 → 中文名（tech_buypoint.name_map 用；缺了报告只剩 6 位代码很难读）"""
    out = {}
    # 优先用 sw_member_all 里已带的 name（不用额外接口）
    if os.path.exists(SW_MEMBER):
        try:
            for code, v in json.load(open(SW_MEMBER, encoding="utf-8")).items():
                nm = (v or {}).get("name")
                if code and nm:
                    out[code] = nm
        except Exception:
            pass
    if len(out) < 1000:
        from tushare_init import ts_call
        df = ts_call("stock_basic", exchange="", list_status="L")
        if df is not None and len(df):
            for r in df.to_dict("records"):
                code, nm = r.get("ts_code"), r.get("name")
                if code and nm:
                    out[code] = nm
    if not out:
        print("[WARN] 股票名称缓存为空（tushare 无权限？报告将只显示代码）")
        return False
    os.makedirs(CACHE, exist_ok=True)
    json.dump(out, open(STOCK_NAMES, "w", encoding="utf-8"), ensure_ascii=False)
    print("[OK] %s  %d 个股票名称" % (STOCK_NAMES, len(out)))
    return True


if __name__ == "__main__":
    ok1 = build_member()
    ok2 = build_index_map()
    ok3 = build_names()
    print("\n完成" if (ok1 and ok2) else "\n部分失败，见上方日志")
