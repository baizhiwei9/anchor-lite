"""状态软混合回测：权重用 2025 数据拟合（含缺失状态的类比映射），在 2026 年做样本外检验"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E
from state_weights import solve, GROUP, cols, R, idx, p8

BASE = os.path.dirname(os.path.abspath(__file__))
SI = int(np.searchsorted(idx, np.datetime64("2026-01-05")))

def fit_on(year_start, year_end):
    out = {}
    for s in p8.columns:
        pos = [i for i in range(61, len(idx) - 1)
               if year_start <= str(idx[i])[:10] <= year_end and p8[s].values[i] >= 0.5]
        if len(pos) < 20:
            continue
        r = solve(R[pos])
        if r: out[s] = r[0]
    return out

w2025 = fit_on("2025-01-01", "2025-12-31")
print("2025 可拟合的状态:", list(w2025.keys()))

FALLBACK = {"商品强通胀": "紧缩利率上行", "增长放缓": "低波趋势", "衰退通缩": "流动性危机",
            "震荡未知": "温和通胀", "温和通胀": "温和通胀", "紧缩利率上行": "紧缩利率上行",
            "低波趋势": "低波趋势", "流动性危机": "流动性危机"}
W = {}
for s in p8.columns:
    src = FALLBACK[s]
    W[s] = w2025.get(src, w2025.get("温和通胀"))

def backtest(mode="soft", trig=0.05, cut=0.5, cost=5.0, rebal="month"):
    w = np.zeros(len(cols)); nav = peak = 1.0; br = prev_br = 1.0
    rr = []; turns = 0.0
    for i in range(SI, len(idx) - 1):
        if mode == "soft":
            tgt = sum(p8[s].values[i] * W[s] for s in p8.columns)
        elif mode == "hard":
            tgt = W[p8.iloc[i].idxmax()]
        else:  # fixed
            tgt = np.zeros(len(cols))
            for k, v in {"treasury10_etf": 0.55, "citybond": 0.15, "oil": 0.15,
                         "nasdaq": 0.10, "soybean": 0.05}.items():
                tgt[cols.index(k)] = v
        tgt = tgt / tgt.sum()
        do = (idx[i + 1].month != idx[i].month) or (br != prev_br) or i == SI
        if rebal == "day":
            do = True
        c = 0.0
        if do:
            t = tgt * br
            trade = t - w
            turns += float(np.abs(trade).sum())
            c = float(np.abs(trade).sum()) * cost / 1e4
            w = t.copy()
        pnl = float(np.nansum(w * R[i + 1]))
        ret = pnl - c
        nav *= 1 + ret; peak = max(peak, nav); dd = nav / peak - 1
        if dd <= -trig: br = 1 - cut
        elif dd >= -trig / 2: br = 1.0
        prev_br = br
        if pnl > -1: w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
    r = np.array(rr); n = np.cumprod(1 + r); dd = n / np.maximum.accumulate(n) - 1
    return dict(total=float(n[-1] - 1), maxdd=float(dd.min()), vol=float(r.std(ddof=1) * np.sqrt(252)),
                turnover=float(turns / len(r) * 252),
                avg_commodity=float(np.mean([0])) if False else None)

if __name__ == "__main__":
    res = {"固定配比": backtest("fixed"), "硬切换（argmax 状态）": backtest("hard"),
           "软混合（Σ p×w，月度执行）": backtest("soft"), "软混合（日频执行）": backtest("soft", rebal="day")}
    for k, v in res.items():
        print(f"{k}: 收益 {v['total']:+.1%} DD {v['maxdd']:.2%} vol {v['vol']:.1%} 换手 {v['turnover']*100:.0f}%")
    # 各状态权重（2025 拟合 / 类比映射）
    print("\n状态权重（用于 2026 回测，全部来自 2025 拟合或类比映射）：")
    for s in p8.columns:
        w = W[s]
        if w is None: continue
        ws = pd.Series(w, index=cols)
        top = ws.sort_values(ascending=False).head(4)
        print(f"  {s}（来源 {FALLBACK[s]}）：" + ", ".join(f"{E.NAMES.get(k,k)} {v:.0%}" for k, v in top.items()))
    json.dump({k: v for k, v in res.items()}, open(os.path.join(BASE, "softmix_backtest.json"), "w"),
              ensure_ascii=False, indent=2, default=str)