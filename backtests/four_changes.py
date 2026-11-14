"""四项改动（可切换）：①类别预算 ②组合层波动预算+类内等权 ③商品腿下限 ④极端档6%"""
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
liq = pd.read_csv(os.path.join(BASE, "liquidity_capacity.csv")).set_index("key")
v20m = (rets.rolling(20).std() * np.sqrt(252)).median(axis=1)

# 类别预算（占净值 %）
BUDGET = {
    "低波趋势":   {"a_share": 30, "overseas": 25, "commodity": 10, "bond": 35},
    "温和通胀":   {"a_share": 10, "overseas": 25, "commodity": 30, "bond": 35},
    "商品强通胀": {"a_share": 0,  "overseas": 15, "commodity": 40, "bond": 45},
    "紧缩利率上行": {"a_share": 0, "overseas": 10, "commodity": 15, "bond": 75},
    "增长放缓":   {"a_share": 0,  "overseas": 10, "commodity": 10, "bond": 80},
    "流动性危机": {"a_share": 0,  "overseas": 0,  "commodity": 5,  "bond": 95},
    "衰退通缩":   {"a_share": 0,  "overseas": 5,  "commodity": 10, "bond": 85},
    "震荡未知":   {"a_share": 10, "overseas": 20, "commodity": 15, "bond": 55},
}
# 旧规则：组别“个数”配额
COUNT = D.POOL
INFLATION = ("温和通胀", "商品强通胀")

def target_vol(d, extreme6):
    h = v20m.loc[:d].tail(252).dropna()
    q = float((h < h.iloc[-1]).mean())
    if q < 0.25: return 0.12, q
    if q < 0.60: return 0.09, q
    if q < 0.85: return 0.06, q
    return (0.06 if extreme6 else 0.03), q

def pick(d, group, k):
    cand = [c for c in cols if D.GROUP[c] == group and liq.loc[c, "amt20_yi"] >= 1.0]
    mom60 = (panel.loc[d] / panel.iloc[max(0, list(idx).index(d) - 60)] - 1)
    return sorted(cand, key=lambda c: mom60.get(c, -9), reverse=True)[:max(0, k)]

def build_target(d, f1, f2, f3, f4):
    st = p8.loc[d].idxmax()
    tv, q = target_vol(d, f4)
    t = np.zeros(len(cols))
    if f1:      # ① 类别预算
        b = dict(BUDGET[st])
        if f3 and st in INFLATION and b["commodity"] < 25:
            need = 25 - b["commodity"]; b["commodity"] = 25
            b["bond"] = max(0, b["bond"] - need)
        for g, pct in b.items():
            if pct <= 0: continue
            k = max(1, min(3, int(round(pct / 15))))
            sel = pick(d, g, k)
            if not sel: continue
            if f2:   # ② 类内等权
                w = np.ones(len(sel)) / len(sel)
            else:    # 类内逆波动
                vol = (rets[sel].loc[:d].tail(60).std() * np.sqrt(252)).clip(lower=0.01)
                w = (1 / vol).values; w = w / w.sum()
            for a, ww in zip(sel, w):
                t[cols.index(a)] += pct / 100 * ww
    else:       # 旧规则：个数配额 + 全池逆波动
        cnt = dict(COUNT[st])
        if f3 and st in INFLATION and cnt.get("commodity", 0) < 2:   # ③ 在个数口径下也生效
            cnt["commodity"] = 2
            if cnt.get("bond", 0) > 1:
                cnt["bond"] -= 1
        pool = []
        for g, k in cnt.items():
            pool += pick(d, g, k) if k else []
        if not pool: return None
        if f2:
            w = np.ones(len(pool)) / len(pool)
            for a, ww in zip(pool, w): t[cols.index(a)] = ww
        else:
            vol = (rets[pool].loc[:d].tail(60).std() * np.sqrt(252)).clip(lower=0.01)
            w = (1 / vol).values; w = w / w.sum()
            for a, ww in zip(pool, w): t[cols.index(a)] = ww
    # 组合层波动预算
    nz = np.where(t > 0)[0]
    if len(nz) == 0: return None
    sub = rets.iloc[:, nz].loc[:d].tail(60).fillna(0).values
    w = t[nz]
    sig = float(np.sqrt(w @ (np.cov(sub.T) * 252) @ w))
    scale = float(np.clip(tv / sig, 0.10, 1.0))
    t = t * scale
    comm = float(sum(t[cols.index(c)] for c in cols if D.GROUP[c] == "commodity"))
    return t, st, q, tv, scale, comm

def run(f1, f2, f3, f4, trig=0.03, cost=5.0):
    w = np.zeros(len(cols)); nav = peak = 1.0; br = prev_br = 1.0
    rr = []; turns = 0.0; cur = None; monthly = []; comms = []
    for i in range(SI, len(idx) - 1):
        d = idx[i]
        if (idx[i + 1].month != idx[i].month) or i == SI:
            cur = build_target(d, f1, f2, f3, f4)
            if cur:
                t, st, q, tv, scale, comm = cur
                monthly.append(dict(month=str(idx[i + 1])[:7], state=st, target_vol=tv,
                                    q=round(q, 3), scale=round(scale, 3), comm=round(comm, 3),
                                    top={E.NAMES.get(cols[j], cols[j]): round(float(t[j]), 3)
                                         for j in np.argsort(-t)[:3] if t[j] > 0.01}))
                comms.append(comm)
        do = (idx[i + 1].month != idx[i].month) or (br != prev_br) or i == SI
        c = 0.0
        if do and cur is not None:
            tgt = cur[0] * br
            trade = tgt - w
            turns += float(np.abs(trade).sum())
            c = float(np.abs(trade).sum()) * cost / 1e4
            w = tgt.copy()
        pnl = float(np.nansum(w * R[i + 1]))
        ret = pnl - c
        nav *= 1 + ret; peak = max(peak, nav); dd = nav / peak - 1
        if dd <= -trig: br = 1 - 0.5
        elif dd >= -trig / 2: br = 1.0
        prev_br = br
        if pnl > -1: w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
    r = np.array(rr); n = np.cumprod(1 + r); dd = n / np.maximum.accumulate(n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    return dict(total=float(n[-1] - 1), maxdd=float(dd.min()), vol=float(vol),
                sharpe=float((n[-1] ** (252 / len(r)) - 1) / vol),
                turnover=float(turns / len(r) * 252),
                avg_comm=float(np.mean(comms)) if comms else 0.0), monthly

if __name__ == "__main__":
    combos = {"旧规则（全关）": (False, False, False, False),
              "仅①类别预算": (True, False, False, False),
              "仅②组合层+类内等权": (False, True, False, False),
              "仅③商品下限25%": (False, False, True, False),
              "仅④极端档6%": (False, False, False, True),
              "四项全改": (True, True, True, True)}
    out = {}
    for nm, f in combos.items():
        m, monthly = run(*f)
        out[nm] = dict(metrics=m, monthly=monthly)
        print(f"{nm}: 收益 {m['total']:+.1%} DD {m['maxdd']:.2%} vol {m['vol']:.1%} "
              f"夏普 {m['sharpe']:.2f} 换手 {m['turnover']*100:.0f}% 平均商品权重 {m['avg_comm']:.0%}")
    json.dump(out, open(os.path.join(BASE, "four_changes.json"), "w"), ensure_ascii=False, indent=2, default=str)