# -*- coding: utf-8 -*-
"""
阶段一：全行业周线买卖点扫描（统一用 techAnly 官方判定链）
======================================================
严格复用官方链：chan_read(周线分型版) → ma20_state → rank_buys → best_buy
参数口径与 tech_buypoint / tech_summary 完全一致，不另写判定。

优先级（官方已定稿）：三买 > 一买（须MA20向上） > 二买
层级：缠论分型版（本脚本）为第一优先；均线版/SMC/VWAP 在阶段二汇总时并列参考。
"""
import os, sys, json
import numpy as np
import pandas as pd

WS = "/tmp/techAnly"
sys.path.insert(0, os.path.join(WS, "bin"))
sys.path.insert(0, os.path.join(WS, "smc"))

import chan_smc_index as C
import datahub as DH
import tech_summary as TS   # 复用官方 rank_buys / best_buy / ma20_state

def main():
    swmap = json.load(open(os.path.join(WS, ".cache", "sw_index_map.json")))
    member = json.load(open(os.path.join(WS, ".cache", "sw_member_all.json")))
    l1 = sorted({v["l1"] for v in member.values() if v.get("l1")})
    print("="*64)
    print("阶段一：全行业周线买卖点扫描（统一官方判定链）")
    print(f"时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    print("口径：缠论分型版(周线) → rank_buys → best_buy ｜ 三买>一买>二买")
    print("="*64)
    results = []
    for name in l1:
        ic = swmap.get(name)
        if not ic:
            continue
        try:
            rows = DH.sw_daily(ic, days=800)
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=["dt","open","high","low","close","vol"])
            df["dt"] = pd.to_datetime(df["dt"].str[:8])
            if len(df) < 60:
                continue
            # 周线缠论分型版
            ww = C.resample_w(df)
            ch = C.chan_read(C.bars_of(ww), "week")
            if not ch.get("ok"):
                continue
            buy_list = ch.get("buy_list", [])
            if not buy_list:
                continue
            # 统一判定链：rank_buys + best_buy
            ma = TS.ma20_state(df)   # MA20 用日线算（官方口径）
            close = float(df["close"].iloc[-1])
            ranked = TS.rank_buys(buy_list, ma, close=close)
            bb = TS.best_buy(ranked)
            if bb and bb.get("accept"):
                results.append(dict(name=name, code=ic, best=bb, ranked=ranked,
                                    close=close, stage=ch.get("stage","")))
        except Exception as e:
            print(f"  [skip] {name}: {str(e)[:50]}", flush=True)
    # 按官方优先级排序（best_buy 的 pri: 1=三买 2=一买 3=二买）
    results.sort(key=lambda r: (r["best"]["pri"], r["best"]["age"]))
    print(f"\n扫 {len(l1)} 个申万一级行业，{len(results)} 个有可用买点：\n")
    PRI_MARK = {1:"①三买", 2:"②一买", 3:"③二买"}
    for r in results:
        bb = r["best"]
        print(f"  🌙 {r['name']} ｜ {PRI_MARK.get(bb['pri'],bb['tag'])} @{bb['level']:.0f} "
              f"（{bb['age']}根前）｜ {bb['why']}")
    print("\n⚠️ 技术结构分析，不构成投资建议。")
    json.dump([{"name":r["name"],"pri":r["best"]["pri"],"level":r["best"]["level"],
                "age":r["best"]["age"]} for r in results],
              open("/tmp/stage1_candidates.json","w"), ensure_ascii=False, default=str)

if __name__ == "__main__":
    main()
