"""2026 年内候选配比 + 期权单腿 + 稳健性（重写版：成本计入再平衡成交额）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel(full=True)
R = rets.fillna(0.0).values
cols = list(panel.columns)
idx = panel.index
qv = pd.read_csv(os.path.join(BASE, "data2026", "qvix_300etf.csv"), parse_dates=["date"]).set_index("date")["close"]
qv = qv[~qv.index.duplicated(keep="last")].reindex(idx).ffill()
qv = np.where(np.isnan(qv.values), 20.0, qv.values)
i300 = cols.index("hs300")
START = "2026-01-05"
START_I = int(np.searchsorted(idx, np.datetime64(START)))
WARM = max(START_I - 1, 60)

def sim(mix, trig=0.05, cut=0.5, cost=5.0, overlay="none", osize=0.10, rets_out=False, jump_coef=4.0):
    w0 = np.zeros(len(cols))
    for k, v in mix.items():
        w0[cols.index(k)] = v
    w = w0.copy()
    nav = peak = 1.0
    br = prev_br = 1.0
    rr = []
    turns = 0.0
    mret = 1.0
    for i in range(WARM, len(idx) - 1):
        target = w0 * br
        rebal = (idx[i + 1].month != idx[i].month) or (br != prev_br)
        c = 0.0
        if rebal:
            trade = target - w
            turns += float(np.abs(trade).sum())
            c = float(np.abs(trade).sum()) * cost / 1e4
            w = target.copy()
        pnl = float(np.nansum(w * R[i + 1]))
        ov = 0.0
        if overlay == "short_vol":
            iv = qv[i] / 100.0
            rv = float(np.std(R[max(0, i - 19):i + 1, i300]) * np.sqrt(252))
            carry = 0.5 * (iv ** 2 - rv ** 2) / 252.0
            jump = -jump_coef * max(0.0, abs(R[i + 1, i300]) - 0.015)
            ov = osize * (carry + jump)
        elif overlay == "long_put":
            ov = -osize * 0.0045 / 21.0
        ret = pnl - c + ov
        mret *= (1 + ret)
        if idx[i].month != idx[i + 1].month:
            if overlay == "long_put":
                md = mret - 1
                if md < -0.03:
                    ret += osize * min(-md - 0.03, 0.10)
            mret = 1.0
        nav *= 1 + ret
        peak = max(peak, nav)
        dd = nav / peak - 1
        if trig is not None:
            if dd <= -trig:
                br = 1 - cut
            elif dd >= -trig / 2:
                br = 1.0
        prev_br = br
        if pnl > -1:
            w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
    r = np.array(rr)
    n = np.cumprod(1 + r)
    dd = n / np.maximum.accumulate(n) - 1
    ann = n[-1] ** (252 / len(r)) - 1
    vol = float(r.std(ddof=1) * np.sqrt(252))
    res = dict(total=float(n[-1] - 1), maxdd=float(dd.min()), vol=vol, expo=float(np.sum(w0)),
               sharpe=float(ann / vol) if vol > 0 else 0.0, turnover_ann=float(turns / len(r) * 252))
    if rets_out:
        return res, pd.Series(r, index=idx[WARM + 1:WARM + 1 + len(r)])
    return res

MIXES = {
    "70% 十年国债 + 15% 原油 + 15% 纳指": {"treasury10_etf": 0.7, "oil": 0.15, "nasdaq": 0.15},
    "60% 国债 + 20% 原油 + 20% 纳指": {"treasury10_etf": 0.6, "oil": 0.2, "nasdaq": 0.2},
    "50% 国债 + 20% 城投 + 15% 原油 + 15% 纳指": {"treasury10_etf": 0.5, "citybond": 0.2, "oil": 0.15, "nasdaq": 0.15},
    "55% 国债 + 15% 城投 + 15% 原油 + 10% 纳指 + 5% 豆粕": {"treasury10_etf": 0.55, "citybond": 0.15, "oil": 0.15, "nasdaq": 0.10, "soybean": 0.05},
    "60% 国债 + 10% 原油 + 10% 纳指 + 10% 红利 + 10% 低波": {"treasury10_etf": 0.6, "oil": 0.10, "nasdaq": 0.10, "dividend": 0.10, "lowvol": 0.10},
    "40% 国债 + 30% 城投 + 10% 原油 + 10% 纳指 + 10% 标普": {"treasury10_etf": 0.40, "citybond": 0.30, "oil": 0.10, "nasdaq": 0.10, "sp500": 0.10},
    "80% 国债 + 20% 原油": {"treasury10_etf": 0.8, "oil": 0.2},
    "100% 十年国债ETF": {"treasury10_etf": 1.0},
}

if __name__ == "__main__":
    rows = []
    for nm, mx in MIXES.items():
        for trig in (0.02, 0.03, 0.05, None):
            r = sim(mx, trig=trig); r.update(name=nm, trigger=(trig if trig is not None else "无"))
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(BASE, "mixes_2026.csv"), index=False)
    print(df[["name", "trigger", "total", "maxdd", "vol"]].round(4).to_string(index=False))
    ok = df[df.maxdd >= -0.05].sort_values("total", ascending=False)
    best_name, best_trig = ok.iloc[0]["name"], ok.iloc[0]["trigger"]
    print("\n最优达标:", best_name, best_trig, round(ok.iloc[0]["total"], 4), round(ok.iloc[0]["maxdd"], 4))
    trig = 0.05 if best_trig == "无" else float(best_trig)
    ov_rows = []
    for ov in ("none", "short_vol", "long_put"):
        for sz in ([0.0] if ov == "none" else [0.05, 0.10, 0.20]):
            r = sim(MIXES[best_name], trig=trig, overlay=ov, osize=sz); r.update(overlay=ov, size=sz)
            ov_rows.append(r)
            print(f"期权 {ov} {sz:.0%}: {r['total']:+.1%} DD {r['maxdd']:.2%} vol {r['vol']:.1%}")
    res_best, sr = sim(MIXES[best_name], trig=trig, rets_out=True)
    def seg(s):
        n = (1 + s).cumprod(); return dict(total=float(n.iloc[-1] - 1), maxdd=float((n / n.cummax() - 1).min()))
    half = len(sr) // 2
    rob = dict(h1=seg(sr.iloc[:half]), h2=seg(sr.iloc[half:]),
               cost10=sim(MIXES[best_name], trig=trig, cost=10.0),
               cost20=sim(MIXES[best_name], trig=trig, cost=20.0),
               no_trigger=sim(MIXES[best_name], trig=None))
    print("\n稳健性:", json.dumps(rob, ensure_ascii=False, indent=1))
    bench = {}
    for a in ["hs300", "treasury10_etf", "oil", "nasdaq", "sp500"]:
        r = R[WARM:, cols.index(a)]
        n = np.cumprod(1 + r)
        bench[a] = dict(total=float(n[-1] - 1), maxdd=float((n / np.maximum.accumulate(n) - 1).min()),
                        vol=float(r.std(ddof=1) * np.sqrt(252)))
    json.dump(dict(mixes=rows, best=dict(name=best_name, trigger=trig, metrics=res_best),
                   overlays=ov_rows, robust=rob, bench=bench),
              open(os.path.join(BASE, "opt2026.json"), "w"), ensure_ascii=False, indent=2, default=str)