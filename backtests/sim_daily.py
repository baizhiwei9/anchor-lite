"""日频判断版：每天算目标仓位（固定配比 / 宏观状态调整），按阈值或每日再平衡"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel(full=True)
R = rets.fillna(0.0).values
cols = list(panel.columns)
idx = panel.index
START = "2026-01-05"
SI = int(np.searchsorted(idx, np.datetime64(START)))
WARM = max(SI - 1, 60)

probs = pd.read_csv(os.path.join(BASE, "probs2026.csv"), index_col=0, parse_dates=True)
probs = probs[~probs.index.duplicated(keep="last")].reindex(idx).ffill()
P_LOW, P_INF, P_CRI, P_UNK = [probs[c].values for c in ["低波趋势", "通胀交易", "高波危机", "震荡未知"]]

BASE_MIX = {"treasury10_etf": 0.55, "citybond": 0.15, "oil": 0.15, "nasdaq": 0.10, "soybean": 0.05}
RISK_ASSETS = ["oil", "nasdaq", "soybean"]
BOND_ASSETS = ["treasury10_etf", "citybond"]

def run(mode="fixed", rebal="month", thr=0.02, cost=5.0, trig=0.03, cut=0.5, regime_gain=0.8):
    w0 = np.zeros(len(cols))
    for k, v in BASE_MIX.items():
        w0[cols.index(k)] = v
    w = w0.copy()
    nav = peak = 1.0
    br = prev_br = 1.0
    rr, turns = [], 0.0
    for i in range(WARM, len(idx) - 1):
        # 每日目标
        if mode == "fixed":
            target = w0.copy()
        else:
            risk_on = P_INF[i] + P_LOW[i]
            mult = np.clip(0.6 + regime_gain * risk_on, 0.3, 1.4)
            t = w0.copy()
            for a in RISK_ASSETS:
                t[cols.index(a)] *= mult
            for a in BOND_ASSETS:
                t[cols.index(a)] *= (1 + 0.4 * P_CRI[i])
            t = t / t.sum()
            target = t
        target = target * br
        # 是否调仓
        if rebal == "day":
            do = True
        elif rebal == "month":
            do = (idx[i + 1].month != idx[i].month) or (br != prev_br)
        else:  # 阈值触发
            do = float(np.abs(target - w).max()) > thr or (br != prev_br)
        c = 0.0
        if do:
            trade = target - w
            turns += float(np.abs(trade).sum())
            c = float(np.abs(trade).sum()) * cost / 1e4
            w = target.copy()
        pnl = float(np.nansum(w * R[i + 1]))
        ret = pnl - c
        nav *= 1 + ret; peak = max(peak, nav); dd = nav / peak - 1
        if trig is not None:
            if dd <= -trig: br = 1 - cut
            elif dd >= -trig / 2: br = 1.0
        prev_br = br
        if pnl > -1:
            w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
    r = np.array(rr); n = np.cumprod(1 + r)
    dd = n / np.maximum.accumulate(n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    return dict(total=float(n[-1] - 1), maxdd=float(dd.min()), vol=float(vol),
                sharpe=float((n[-1] ** (252 / len(r)) - 1) / vol), turnover=float(turns / len(r) * 252))

if __name__ == "__main__":
    rows = []
    cfgs = [
        ("月度再平衡 · 固定配比", dict(mode="fixed", rebal="month")),
        ("日频判断 · 阈值 2% 触发 · 固定配比", dict(mode="fixed", rebal="thr", thr=0.02)),
        ("日频判断 · 每日再平衡 · 固定配比", dict(mode="fixed", rebal="day")),
        ("日频判断 · 宏观状态调整 · 阈值 1%", dict(mode="regime", rebal="thr", thr=0.01)),
        ("日频判断 · 宏观状态调整 · 阈值 2%", dict(mode="regime", rebal="thr", thr=0.02)),
        ("日频判断 · 宏观状态调整 · 阈值 3%", dict(mode="regime", rebal="thr", thr=0.03)),
        ("日频判断 · 宏观状态调整 · 每日再平衡", dict(mode="regime", rebal="day")),
    ]
    for nm, kw in cfgs:
        r = run(**kw); r["name"] = nm; rows.append(r)
        print(f"{nm}: 收益 {r['total']:+.1%} DD {r['maxdd']:.2%} vol {r['vol']:.1%} 夏普 {r['sharpe']:.2f} 年换手 {r['turnover']*100:.0f}%")
    df = pd.DataFrame(rows); df.to_csv(os.path.join(BASE, "daily_cmp.csv"), index=False)
    # 宏观状态调整的强度敏感性（阈值 2%）
    print("\n状态调整强度敏感性（阈值 2%）：")
    for g in (0.0, 0.4, 0.8, 1.2):
        r = run(mode="regime", rebal="thr", thr=0.02, regime_gain=g)
        print(f"  gain={g}: 收益 {r['total']:+.1%} DD {r['maxdd']:.2%} 换手 {r['turnover']*100:.0f}%")
    json.dump(rows, open(os.path.join(BASE, "daily_cmp.json"), "w"), ensure_ascii=False, indent=2, default=str)