# -*- coding: utf-8 -*-
"""
阶段二：候选行业下钻个股（MA40 + VWAP 中线过滤后，输出可买个股）
=================================================================
输入：阶段一筛选出的行业（周线/日线买点过闸）
输出：每个行业下钻出的可买个股（三买 > 一买 > 二买）
过滤：个股必须 站上MA40且MA40向上 且 站上VWAP中线
"""
import os, sys, json
import numpy as np
import pandas as pd

WS = "/tmp/techAnly"
sys.path.insert(0, os.path.join(WS, "bin"))
sys.path.insert(0, os.path.join(WS, "smc"))
os.environ.setdefault("TECH_WS", WS)

import chan_smc_index as C
import datahub as DH
import tech_summary as TS
import smc_core as S

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

def load_sw_member():
    return json.load(open(os.path.join(WS, ".cache", "sw_member_all.json")))

def load_sw_index_map():
    return json.load(open(os.path.join(WS, ".cache", "sw_index_map.json")))

def fetch_stock_daily(code, hist=None):
    """个股日线，返回 DataFrame[dt,open,high,low,close,volume]"""
    # 用 tech_buypoint 的 load_daily（复用缓存逻辑）
    import tech_buypoint as TB
    return TB.load_daily(code, hist)

def ma40_vwap_pass(d):
    """个股预过滤：站上MA40且MA40向上 且 站上VWAP中线"""
    cl = d["close"].astype(float)
    if len(cl) < 45:
        return False
    ma40 = cl.rolling(40).mean()
    close = float(cl.iloc[-1])
    ma40_now = float(ma40.iloc[-1])
    ma40_prev = float(ma40.iloc[-6]) if len(ma40) >= 6 else ma40_now
    if not (close > ma40_now and ma40_now > ma40_prev):
        return False
    try:
        vw = S.vwap_tracks(d)
        return close > float(vw["vwap"][-1])
    except Exception:
        return True  # VWAP 算不出不拦截

def analyse_stock(code, nm, d):
    """个股买点判定（复用官方链）"""
    bars = C.bars_of(d)
    fx = C.chan_read(bars, "day")
    if not fx.get("ok"):
        return None
    ma20 = TS.ma20_state(d)
    close = float(d["close"].iloc[-1])
    # 一买/二买从 fx.buy_list 提取（与官方 analyse_one 一致）
    buy_list = fx.get("buy_list", [])
    from tech_buypoint import find_third_buys, _gate_y1, _gate_y2
    cl = d["close"].astype(float).values
    tb = find_third_buys(d, cl)
    yb, eb = [], []
    for b in buy_list:
        if b.get("tag") == "一买":
            yb.append(_gate_y1(b, close, ma20))
        elif b.get("tag") == "二买":
            eb.append(_gate_y2(b, close, ma20))
    cands = tb + yb + eb
    acc = [x for x in cands if x["accept"]]
    acc.sort(key=lambda x: (x["pri"], x.get("age", 0)))
    if not acc:
        return None
    best = acc[0]
    return dict(code=code, name=nm, best=best, close=close)

def main(industries):
    member = load_sw_member()
    # 行业 -> 成分股
    l1_map = {}
    for code, v in member.items():
        l1_map.setdefault(v.get("l1"), []).append(code)
    print("="*64)
    print("阶段二：候选行业下钻个股（MA40 + VWAP中线过滤）")
    print(f"时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*64)
    import tech_buypoint as TB
    hist = TB._today_hist()
    all_hits = []
    for ind in industries:
        codes = l1_map.get(ind, [])
        print(f"\n【{ind}】成分 {len(codes)} 只，开始下钻...")
        hits = []
        for i, code in enumerate(codes):
            try:
                d = fetch_stock_daily(code, hist)
                if d is None or len(d) < 45:
                    continue
                if not ma40_vwap_pass(d):
                    continue
                r = analyse_stock(code, "", d)
                if r:
                    r["industry"] = ind
                    hits.append(r)
            except Exception:
                pass
        if hits:
            hits.sort(key=lambda r: (r["best"]["pri"], r["best"].get("age",0)))
            all_hits.extend(hits)
            print(f"  可买个股 {len(hits)} 只：")
            for r in hits:
                b = r["best"]
                mark = {1:"①三买",2:"②一买",3:"③二买"}.get(b["pri"], b["tag"])
                print(f"    {mark} {r['name'] or r['code']} {r['code']} 收{r['close']:.2f}")
        else:
            print(f"  无可买个股")
    # 汇总
    print("\n" + "="*64)
    print(f"【汇总】共 {len(all_hits)} 只可买个股")
    for r in all_hits:
        b = r["best"]
        mark = {1:"①三买",2:"②一买",3:"③二买"}.get(b["pri"], b["tag"])
        print(f"  {mark} {r['industry']}·{r['name'] or r['code']} {r['code']} 收{r['close']:.2f}")
    print("⚠️ 技术结构分析，不构成投资建议。")
    return all_hits

if __name__ == "__main__":
    # 阶段一输出的候选行业（当前手工指定，后续读 stage1 输出）
    cands = ["家用电器", "医药生物"]
    main(cands)
