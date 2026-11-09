"""多因子打分 + 八态宏观叠加，2026 年内 5% 回撤约束回测"""
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

F = pd.read_pickle(os.path.join(BASE, "factor_panels.pkl"))
p8 = pd.read_csv(os.path.join(BASE, "probs2026_8.csv"), index_col=0, parse_dates=True)
p8 = p8[~p8.index.duplicated(keep="last")].reindex(idx).ffill()

BONDS = ["treasury10_etf", "citybond"]
RISK = [c for c in cols if c not in BONDS]
WIN = ["MOM_20D", "MOM_60D", "TREND_MA60", "VOL_60", "MAXDD_120"]
WTS = {"MOM_20D": 0.25, "MOM_60D": 0.35, "TREND_MA60": 0.25, "VOL_60": -0.15, "MAXDD_120": 0.10}

def zscore(s):
    sd = s.std(ddof=1)
    return (s - s.mean()) / sd if sd and sd > 0 else s * 0

def score_at(d):
    sc = pd.Series(0.0, index=RISK)
    for f, w in WTS.items():
        v = F[f].loc[d].reindex(RISK)
        sc += w * zscore(v)
    return sc.dropna()

def state_mult(d, up=("商品强通胀", "温和通胀", "低波趋势"), down=("流动性危机", "衰退通缩")):
    p = p8.loc[d]
    pu = float(p[[c for c in up]].sum()); pd_ = float(p[[c for c in down]].sum())
    return float(np.clip(0.5 + 1.5 * (pu - pd_), 0.3, 1.2))

def run(mode="factor8", top_k=2, base_risk=0.30, trig=0.03, cut=0.5, cost=5.0,
        bond_split=(0.55, 0.15), use_state=True):
    w = np.zeros(len(cols)); br = prev_br = 1.0
    nav = peak = 1.0; turns = 0.0; rr = []
    target = np.zeros(len(cols))
    for i in range(WARM, len(idx) - 1):
        d = idx[i]
        new_m = (idx[i + 1].month != idx[i].month)
        if new_m or i == WARM:
            if mode == "fixed":
                t = np.zeros(len(cols)); t[cols.index("treasury10_etf")] = 0.55
                t[cols.index("citybond")] = 0.15; t[cols.index("oil")] = 0.15
                t[cols.index("nasdaq")] = 0.10; t[cols.index("soybean")] = 0.05
                target = t
            else:
                sc = score_at(d)
                pick = list(sc.sort_values(ascending=False).head(top_k).index)
                rw = base_risk * (state_mult(d) if use_state else 1.0)
                t = np.zeros(len(cols))
                t[cols.index("treasury10_etf")] = bond_split[0] / sum(bond_split) * (1 - rw)
                t[cols.index("citybond")] = bond_split[1] / sum(bond_split) * (1 - rw)
                for a in pick:
                    t[cols.index(a)] += rw / len(pick)
                target = t
        do = new_m or (br != prev_br) or (i == WARM)
        c = 0.0
        if do:
            tgt = target * br
            trade = tgt - w
            turns += float(np.abs(trade).sum())
            c = float(np.abs(trade).sum()) * cost / 1e4
            w = tgt.copy()
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
    r = np.array(rr); n = np.cumprod(1 + r); dd = n / np.maximum.accumulate(n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    return dict(total=float(n[-1] - 1), maxdd=float(dd.min()), vol=float(vol),
                sharpe=float((n[-1] ** (252 / len(r)) - 1) / vol), turnover=float(turns / len(r) * 252))

if __name__ == "__main__":
    res = {}
    res["固定配比（上一版）"] = run(mode="fixed")
    res["多因子选2只（八态不叠加）"] = run(mode="factor", use_state=False)
    res["多因子选2只 + 八态叠加"] = run(mode="factor", use_state=True)
    res["多因子选3只 + 八态叠加"] = run(mode="factor", top_k=3, use_state=True)
    for k, v in res.items():
        print(f"{k}: 收益 {v['total']:+.1%} DD {v['maxdd']:.2%} vol {v['vol']:.1%} 夏普 {v['sharpe']:.2f} 换手 {v['turnover']*100:.0f}%")
    # 每月选出的资产
    picks = []
    for i in range(WARM, len(idx) - 1):
        if idx[i + 1].month != idx[i].month:
            sc = score_at(idx[i])
            picks.append((str(idx[i + 1])[:7], ", ".join(E.NAMES.get(a, a) for a in sc.sort_values(ascending=False).head(2).index),
                          round(state_mult(idx[i]), 2)))
    print("\n每月选中的风险资产与前两名的状态乘数：")
    for p_ in picks: print("  ", p_)
    json.dump(dict(results=res, picks=picks), open(os.path.join(BASE, "multifactor.json"), "w"),
              ensure_ascii=False, indent=2, default=str)