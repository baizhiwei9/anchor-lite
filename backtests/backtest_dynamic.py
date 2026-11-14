"""动态池 × 波动预算回测（2026 年内，月度重设 + 3% 熔断，5bp 成本）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import dynamic_rule as D
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
idx, panel, p8 = D.idx, D.panel, D.p8
rets = panel.pct_change().fillna(0)
R = rets.values
cols = list(panel.columns)
SI = int(np.searchsorted(idx, np.datetime64("2026-01-05")))
v20 = (rets.rolling(20).std() * np.sqrt(252)).median(axis=1)

def bucket(d):
    h = v20.loc[:d].tail(252).dropna()
    q = float((h < h.iloc[-1]).mean())
    if q < 0.25: return 0.12
    if q < 0.60: return 0.09
    if q < 0.85: return 0.06
    return 0.03

def target_at(d):
    st = p8.loc[d].idxmax()
    pool = D.build_pool(d, st)
    if not pool:
        return None
    vol = (rets[pool].loc[:d].tail(60).std() * np.sqrt(252)).clip(lower=0.01)
    w0 = (1 / vol) / (1 / vol).sum()
    cov = rets[pool].loc[:d].tail(60).cov().values * 252
    sig = float(np.sqrt(max(w0.values @ cov @ w0.values, 1e-8)))
    tv = bucket(d)
    scale = float(np.clip(tv / sig, 0.10, 1.0))
    t = np.zeros(len(cols))
    for c, v in (w0 * scale).items():
        t[cols.index(c)] = v
    return t, st, len(pool), tv, scale

def run(trig=0.03, cut=0.5, cost=5.0, rebal="month"):
    w = np.zeros(len(cols)); nav = peak = 1.0; br = prev_br = 1.0
    rr = []; turns = 0.0; targets = {}
    cur = None
    for i in range(SI, len(idx) - 1):
        d = idx[i]
        if (idx[i + 1].month != idx[i].month) or i == SI:
            cur = target_at(d)
            targets[str(idx[i + 1])[:7]] = dict(state=cur[1], n=cur[2], target_vol=cur[3], scale=round(cur[4], 3))
        do = (idx[i + 1].month != idx[i].month) or (br != prev_br) or i == SI
        c = 0.0
        if do and cur is not None:
            t = cur[0] * br
            trade = t - w
            turns += float(np.abs(trade).sum())
            c = float(np.abs(trade).sum()) * cost / 1e4
            w = t.copy()
        pnl = float(np.nansum(w * R[i + 1]))
        ret = pnl - c
        nav *= 1 + ret; peak = max(peak, nav); dd = nav / peak - 1
        if trig is not None:
            if dd <= -trig: br = 1 - cut
            elif dd >= -trig / 2: br = 1.0
        prev_br = br
        if pnl > -1: w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
    r = np.array(rr); n = np.cumprod(1 + r); dd = n / np.maximum.accumulate(n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    return dict(total=float(n[-1] - 1), maxdd=float(dd.min()), vol=float(vol),
                sharpe=float((n[-1] ** (252 / len(r)) - 1) / vol), turnover=float(turns / len(r) * 252)), targets, r

if __name__ == "__main__":
    res, tg, r = run()
    print("动态池 × 波动预算（2026 年内）：")
    print(f"  收益 {res['total']:+.1%}  最大回撤 {res['maxdd']:.2%}  波动 {res['vol']:.1%}  夏普 {res['sharpe']:.2f}  换手 {res['turnover']*100:.0f}%")
    print("\n逐月目标：")
    for m, v in tg.items():
        print(f"  {m}: {v['state']}｜池内 {v['n']} 只｜目标波动 {v['target_vol']:.0%}｜缩放 {v['scale']}")
    # 稳健性
    r10, _, _ = run(cost=10.0)
    rn, _, _ = run(trig=None)
    half = len(r) // 2
    def seg(x):
        n = np.cumprod(1 + x); return float(n[-1] - 1), float((n / np.maximum.accumulate(n) - 1).min())
    h1, h2 = seg(r[:half]), seg(r[half:])
    print(f"\n稳健性：10bp 成本 {r10['total']:+.1%}/{r10['maxdd']:.2%}；无熔断 {rn['total']:+.1%}/{rn['maxdd']:.2%}")
    print(f"  上半年 {h1[0]:+.1%}/{h1[1]:.2%}；下半年 {h2[0]:+.1%}/{h2[1]:.2%}")
    print("\n对照：固定配比 +17.5%/-4.06%；有上限软混合 -10.4%/-20.7%；无上限 -13.7%/-29.5%")
    json.dump(dict(result=res, monthly=tg, robust=dict(cost10=r10, no_trig=rn, h1=h1, h2=h2)),
              open(os.path.join(BASE, "dynamic_backtest.json"), "w"), ensure_ascii=False, indent=2, default=str)