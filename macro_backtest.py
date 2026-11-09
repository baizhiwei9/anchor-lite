"""按真实宏观标签重配：国债/红利/低波为主，叠加反向波动与国债多头；2026 年回测对照"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E
import macro_state as M

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel(full=True)
cols = list(panel.columns)
idx = panel.index
R = rets.fillna(0.0).values
SI = int(np.searchsorted(idx, np.datetime64("2026-01-05")))
qv = pd.read_csv(os.path.join(BASE, "data2026", "qvix_300etf.csv"), parse_dates=["date"]).set_index("date")["close"]
qv = qv[~qv.index.duplicated(keep="last")].reindex(idx).ffill()
i300 = cols.index("hs300")

# 状态 → 目标权重（防御优先）
ALLOC = {
    "成本推动": {"treasury10_etf": 0.40, "corp_bond": 0.15, "dividend": 0.20, "lowvol": 0.10, "gold": 0.05, "oil": 0.10},
    "需求拉动通胀": {"treasury10_etf": 0.30, "dividend": 0.15, "lowvol": 0.10, "nasdaq": 0.15, "gold": 0.10, "oil": 0.20},
    "低通胀": {"treasury10_etf": 0.45, "dividend": 0.20, "lowvol": 0.15, "nasdaq": 0.10, "gold": 0.10},
    "数据不足": {"treasury10_etf": 0.45, "dividend": 0.20, "lowvol": 0.15, "nasdaq": 0.10, "gold": 0.10},
}
RISK_OFF = {"treasury10_etf": 0.50, "gold": 0.15, "dividend": 0.10, "treasury_active": 0.25}

def label_at(d):
    r = M.classify(pd.Timestamp(d))
    base = r["infl"]
    w = dict(ALLOC.get(base, ALLOC["低通胀"]))
    if r["risk"] == "风险偏高":
        w = dict(RISK_OFF)
    return r, w

def run(overlay="none", trig=0.03, cost=5.0):
    w = np.zeros(len(cols)); nav = peak = 1.0; br = prev_br = 1.0
    rr = []; turns = 0.0; cur = None; monthly = []
    for i in range(SI, len(idx) - 1):
        d = idx[i]
        if (idx[i + 1].month != idx[i].month) or i == SI:
            r, tgt = label_at(d)
            cur = np.zeros(len(cols))
            for k, v in tgt.items():
                cur[cols.index(k)] = v
            cur /= cur.sum()
            monthly.append(dict(month=str(idx[i + 1])[:7], label=r["label"], cpi=r["cpi"], ppi=r["ppi"],
                                basis=r["basis20"], qvix_pct=r["qvix_pct"],
                                weights={E.NAMES.get(k, k): v for k, v in tgt.items()}))
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
        # 反向波动叠加
        if overlay != "none":
            q = float(qv.iloc[i])
            pct = float((qv.iloc[max(0, i - 251):i + 1] < q).mean())
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
                sharpe=float((n[-1] ** (252 / len(r)) - 1) / vol), turnover=float(turns / len(r) * 252)), monthly

if __name__ == "__main__":
    out = {}
    for nm, kw in [("宏观防御配置", dict()), ("防御+卖波动(低波时)", dict(overlay="sell_vol")),
                   ("防御+买保护(高波时)", dict(overlay="buy_put"))]:
        m, monthly = run(**kw); out[nm] = dict(metrics=m, monthly=monthly)
        print(f"{nm}: 收益 {m['total']:+.1%} DD {m['maxdd']:.2%} vol {m['vol']:.1%} 夏普 {m['sharpe']:.2f} 换手 {m['turnover']*100:.0f}%")
    print("\n逐月标签与配置：")
    for r in out["宏观防御配置"]["monthly"]:
        print(f"  {r['month']} {r['label']} CPI {r['cpi']} PPI {r['ppi']} 基差{r['basis']}% 权重:" +
              ", ".join(f"{k} {v:.0%}" for k, v in r["weights"].items()))
    json.dump(out, open(os.path.join(BASE, "macro_backtest.json"), "w"), ensure_ascii=False, indent=2, default=str)