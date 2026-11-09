"""T4：多指数股票腿 + 期权风险闸门（月度选样 + 日频风险门控）"""
import os, sys, json, numpy as np, pandas as pd
import engine3 as E, dyn3 as D3, dyn4 as M4

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = D3.panel, D3.rets
cols = list(panel.columns); idx = panel.index; R = rets.fillna(0).values
NAMES = E.NAMES
KC, kc = M4.KC, M4.kc

EQ = ["kc50", "cyb", "zz500", "zz1000", "hs300", "nasdaq", "nikkei", "sp500", "hstech",
      "semiconductor", "newenergy", "military"]
EQ = [c for c in EQ if c in cols]
BOND = "treasury10_etf"

def run(sleeve=0.45, ma=40, top=None, gate_hi=1.5, gate_mid=2.5, dd=0.03, band=0.05, cost=5.0,
        use_gate=True, monthly=True, breadth_lo=0.40, breadth_cut=0.35, use_breadth=False):
    n = len(cols)
    w = np.zeros(n); jb = cols.index(BOND); w[jb] = 1 - sleeve
    wa = {c: sleeve / len(EQ) for c in EQ}
    for c, x in wa.items(): w[cols.index(c)] = x
    nav = peak = 1.0; br = sc = 1.0
    rr = []; trades = []; trail = []; month = None
    # 提前算好月度选样
    sel_log = {}
    for i in range(D3.SI, len(idx) - 1):
        d = idx[i]
        if d.month != month:
            month = d.month
            elig = []
            for c in EQ:
                s = panel[c].loc[:d].dropna()
                if len(s) < ma + 5: continue
                p = float(s.iloc[-1]); ma_v = float(s.tail(ma).mean())
                r60 = float(s.iloc[-1] / s.iloc[-61] - 1) if len(s) > 61 else 0.0
                if p > ma_v and r60 > 0: elig.append((c, r60))
            if top: elig = sorted(elig, key=lambda x: -x[1])[:top]
            sel = [c for c, _ in elig]
            sel_log[str(d)[:10]] = sel
            base = np.zeros(n); base[jb] = 1 - sleeve
            if sel:
                for c in sel: base[cols.index(c)] = sleeve / len(sel)
            else:
                base[jb] = 1.0
            tgt_month = base
        tgt = tgt_month.copy()
        # 广度闸门：池内站上 MA20 的比例低于阈值 → 股票腿按比例收缩
        if use_breadth:
            above = 0; cnt = 0
            for c in EQ:
                ss = panel[c].loc[:d].dropna()
                if len(ss) < 25: continue
                cnt += 1
                if float(ss.iloc[-1]) > float(ss.tail(20).mean()): above += 1
            br_ratio = above / max(cnt, 1)
            if br_ratio < breadth_lo:
                k = breadth_cut
                for c in EQ: tgt[cols.index(c)] *= k
                tgt[jb] = 1 - sum(tgt[cols.index(c)] for c in EQ)
        if use_gate:
            ziv = float(kc.z_iv.loc[d]); zsk = float(kc.z_skew.loc[d])
            g = 1.0
            if ziv >= gate_mid: g = 0.25
            elif ziv >= gate_hi and zsk >= 1.0: g = 0.5
            for c in EQ: tgt[cols.index(c)] *= g
            tgt[jb] = 1 - sum(tgt[cols.index(c)] for c in EQ)
        for c in EQ: tgt[cols.index(c)] *= br
        tgt[jb] = 1 - sum(tgt[cols.index(c)] for c in EQ)
        trade = np.zeros(n)
        for j in range(n):
            if abs(w[j] - tgt[j]) > band: trade[j] = tgt[j] - w[j]
        c_ = 0.0
        if np.abs(trade).sum() > 1e-6:
            w = w + trade; c_ = float(np.abs(trade).sum()) * cost / 1e4
            trades.append(dict(date=str(d)[:10], action="、".join(
                f"{NAMES.get(cols[j],cols[j])}{'买入' if trade[j]>0 else '卖出'}{abs(trade[j]):.1%}"
                for j in range(n) if abs(trade[j]) > 0.005)))
        if br != sc:
            eqw = sum(w[cols.index(c)] for c in EQ)
            w[cols.index(BOND)] += eqw * (1 - br / sc)
            for c in EQ: w[cols.index(c)] *= br / sc
            sc = br
        pnl = float(np.nansum(w * R[i + 1])); ret = pnl - c_
        nav *= 1 + ret; peak = max(peak, nav); dcur = nav / peak - 1
        if dcur <= -dd: br = 0.5
        elif dcur >= -dd / 2: br = 1.0
        if pnl > -1: w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
        trail.append(dict(date=str(d)[:10], eq=float(sum(w[cols.index(c)] for c in EQ)),
                          bond=float(w[jb]), tot=float(w.sum()), br=br, gate=(ziv if use_gate else np.nan)))
    r = np.array(rr); nv = np.cumprod(1 + r); ddv = nv / np.maximum.accumulate(nv) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    m = dict(total=float(nv[-1] - 1), maxdd=float(ddv.min()), vol=float(vol),
             sharpe=float((nv[-1] ** (252 / len(r)) - 1) / vol), n_trades=len(trades))
    return m, trades, pd.DataFrame(trail), sel_log

if __name__ == "__main__":
    out = {}
    for k, kw in {"等权12只+闸门": dict(),
                  "动量选3+闸门": dict(top=3),
                  "等权+无闸门": dict(use_gate=False),
                  "动量选3+无闸门": dict(top=3, use_gate=False),
                  "股票50%": dict(sleeve=0.50),
                  "股票55%动量3": dict(sleeve=0.55, top=3),
                  "闸门收紧(1.2)": dict(gate_hi=1.2)}.items():
        m, tr, tl, sl = run(**kw)
        out[k] = m
        print(f"{k:16s} 收益 {m['total']:+.1%} 回撤 {m['maxdd']:.2%} 波动 {m['vol']:.1%} 夏普 {m['sharpe']:.2f} 笔数 {m['n_trades']}")
    json.dump(out, open(os.path.join(BASE, "eq6_variants.json"), "w"), ensure_ascii=False, indent=2, default=float)
