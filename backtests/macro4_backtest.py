"""按四维度标签配仓并回测（2026 年内，5% 回撤约束，含卖波动/买保护叠加）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E
import macro4 as M

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel(full=True)
cols = list(panel.columns)
idx = panel.index
R = rets.fillna(0.0).values
SI = int(np.searchsorted(idx, np.datetime64("2026-01-05")))
qv = M.qv
i300 = cols.index("hs300")

def build(label):
    w = {"treasury10_etf": 0.45, "treasury_active": 0.25, "dividend": 0.15, "lowvol": 0.10, "gold": 0.05}
    def add(k, v, src=None):
        w[k] = w.get(k, 0) + v
    oil_up = "(油↑)" in label["supply"]
    strong = "供给强冲击" in label["supply"]
    mild = "供给温和上行" in label["supply"]
    if strong and oil_up:
        for k, v in {"oil": 0.12, "soybean": 0.05, "coal": 0.03}.items(): add(k, v)
        w["treasury10_etf"] -= 0.10; w["treasury_active"] -= 0.10
    elif mild and oil_up:
        for k, v in {"oil": 0.06, "soybean": 0.02, "coal": 0.02}.items(): add(k, v)
        w["treasury10_etf"] -= 0.05; w["treasury_active"] -= 0.05
    elif strong:
        w["gold"] += 0.05; w["treasury10_etf"] -= 0.05
    if label["growth"] == "增长偏强":
        add("nasdaq", 0.08); add("hs300", 0.07)
        w["treasury10_etf"] -= 0.10; w["treasury_active"] -= 0.05
    if label["demand"] == "需求过热":
        w["gold"] += 0.05; w["treasury10_etf"] -= 0.05
    if "流动性紧" in label["liq"]:
        for k in ["oil", "soybean", "coal", "nasdaq", "hs300", "dividend", "lowvol"]:
            if k in w and w[k] > 0:
                cut = w[k] * 0.5; w[k] -= cut; w["treasury_active"] = w.get("treasury_active", 0) + cut
    w = {k: max(0.0, v) for k, v in w.items()}
    s = sum(w.values())
    return {k: v / s for k, v in w.items() if v > 0}

def run(overlay="none", trig=0.03, cost=5.0):
    w = np.zeros(len(cols)); nav = peak = 1.0; br = prev_br = 1.0
    rr = []; turns = 0.0; cur = None; monthly = []
    for i in range(SI, len(idx) - 1):
        d = idx[i]
        if (idx[i + 1].month != idx[i].month) or i == SI:
            lab = M.classify(d)
            tgt = build(lab)
            cur = np.zeros(len(cols))
            for k, v in tgt.items():
                cur[cols.index(k)] = v
            monthly.append(dict(month=str(idx[i + 1])[:7], label=lab["label"],
                                cpi=lab["cpi"], ppi=lab["ppi"], pmi=lab["pmi"], m1=lab["m1"],
                                oil60=lab["oil60"], basis=lab["basis20"], qvix_pct=lab["qvix_pct"],
                                weights={E.NAMES.get(k, k): round(v, 3) for k, v in tgt.items()}))
        do = (idx[i + 1].month != idx[i].month) or (br != prev_br) or i == SI
        c = 0.0
        if do and cur is not None:
            t = cur * br
            trade = t - w
            turns += float(np.abs(trade).sum())
            c = float(np.abs(trade).sum()) * cost / 1e4
            w = t.copy()
        pnl = float(np.nansum(w * R[i + 1]))
        ret = pnl - c
        if overlay != "none":
            q = float(qv.iloc[i]); hist = qv.iloc[max(0, i - 251):i + 1]
            pct = float((hist < q).mean())
            if overlay == "sell_vol" and pct < 0.30:
                iv, rv = q / 100.0, float(np.std(R[max(0, i - 19):i + 1, i300]) * np.sqrt(252))
                ret += 0.05 * (0.5 * (iv ** 2 - rv ** 2) / 252 - 4 * max(0.0, abs(R[i + 1, i300]) - 0.015))
            elif overlay == "buy_put" and pct > 0.85:
                ret -= 0.20 * 0.0045 / 21.0
        nav *= 1 + ret; peak = max(peak, nav); dd = nav / peak - 1
        if dd <= -trig: br = 0.5
        elif dd >= -trig / 2: br = 1.0
        prev_br = br
        if pnl > -1: w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
    r = np.array(rr); n = np.cumprod(1 + r); dd = n / np.maximum.accumulate(n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    return dict(total=float(n[-1] - 1), maxdd=float(dd.min()), vol=float(vol),
                sharpe=float((n[-1] ** (252 / len(r)) - 1) / vol), turnover=float(turns / len(r) * 252)), monthly, r

if __name__ == "__main__":
    out = {}
    for nm, kw in [("四维度配仓", dict()), ("+卖波动(低波时)", dict(overlay="sell_vol")),
                   ("+买保护(高波时)", dict(overlay="buy_put"))]:
        m, monthly, r = run(**kw); out[nm] = dict(metrics=m, monthly=monthly)
        print(f"{nm}: 收益 {m['total']:+.1%} DD {m['maxdd']:.2%} vol {m['vol']:.1%} 夏普 {m['sharpe']:.2f} 换手 {m['turnover']*100:.0f}%")
    print("\n逐月标签与配置：")
    for x in out["四维度配仓"]["monthly"]:
        print(f"  {x['month']} {x['label']}")
        print(f"      持仓: " + ", ".join(f"{k} {v:.0%}" for k, v in x["weights"].items()))
    json.dump(out, open(os.path.join(BASE, "macro4_backtest.json"), "w"), ensure_ascii=False, indent=2, default=str)