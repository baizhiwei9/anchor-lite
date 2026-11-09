"""eq8：偏度优先的风险判据 + 趋势感知的均线规则 + 美国宏观驱动的原油"""
import os, sys, json, numpy as np, pandas as pd
import engine3 as E, dyn3 as D3

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = D3.panel, D3.rets
cols = list(panel.columns); idx = panel.index; R = rets.fillna(0).values
NAMES = E.NAMES; BOND = "treasury10_etf"
OPT = {"hs300": "510300", "zz500": "510500", "kc50": "588000", "zz1000": "510500"}  # zz1000 用 500 代理
P = pd.read_csv(os.path.join(BASE, "data_signals/opt_panel.csv"), parse_dates=["date"])
US = pd.read_csv(os.path.join(BASE, "data_signals/us_macro.csv"), parse_dates=["date"]).set_index("date")
def zs(s, w=60): return (s - s.rolling(w).mean()) / s.rolling(w).std()

SIG = {}
for a, code in OPT.items():
    x = P[P.code.astype(str) == code].set_index("date").sort_index()
    d = pd.DataFrame(index=x.index)
    d["skew"] = x["skew"]; d["iv"] = x.iv_atm; d["pcr_oi"] = x.pcr_oi; d["pcr"] = x.pcr_vol
    d["z_skew"] = zs(x["skew"]); d["z_iv"] = zs(x.iv_atm); d["z_pcr_oi"] = zs(x.pcr_oi)
    d["iv_chg20"] = x.iv_atm - x.iv_atm.shift(20)          # 隐含波动率上行幅度
    d["skew_chg5"] = x["skew"] - x["skew"].shift(5)
    SIG[a] = d.reindex(idx).ffill()

def risk_state(a, mode="skew", sk_th=1.2, poi_th=1.0, iv_chg_th=0.025, enter_n=2, exit_n=3):
    s = SIG[a]; out = []; on = False; ci = co = 0
    for dt in idx:
        if mode == "old":       # 旧：IV 高 或 偏度高
            ziv, zsk = s.z_iv.get(dt, np.nan), s.z_skew.get(dt, np.nan)
            bad = (ziv >= 1.0) or (zsk >= 1.0)
            ok = (ziv < 0.5) and (zsk < 0.5)
        else:                   # 新：偏度为主（put 变贵 或 put 持仓过多）且波动率上行确认
            zsk = s.z_skew.get(dt, np.nan); zpoi = s.z_pcr_oi.get(dt, np.nan)
            chg = s.iv_chg20.get(dt, np.nan)
            if np.isnan(zsk) or np.isnan(chg):
                out.append(False); continue
            fear = (zsk >= sk_th) or (zpoi >= poi_th)
            bad = fear and (chg >= iv_chg_th)
            ok = (zsk < 0.5) and (chg < 0.0)
        ci = ci + 1 if bad else 0
        if not on and ci >= enter_n: on = True; co = 0
        if on:
            co = co + 1 if ok else 0
            if co >= exit_n: on = False; ci = 0
        out.append(on)
    return pd.Series(out, index=idx)

RS_OLD = {a: risk_state(a, "old") for a in OPT}
RS_NEW = {a: risk_state(a, "skew") for a in OPT}

def segs(s):
    out = []; st = None; prev = None
    for d in s.index:
        if s.loc[d] and st is None: st = d
        if not s.loc[d] and st is not None: out.append((st, prev)); st = None
        prev = d
    if st is not None: out.append((st, prev))
    return out

if __name__ == "__main__":
    for a in ["hs300", "zz500", "kc50"]:
        so, sn = segs(RS_OLD[a]), segs(RS_NEW[a])
        print(f"\n{a} 旧判据 {int(RS_OLD[a].sum())} 天 {len(so)} 段:", "；".join(f"{str(x)[5:10]}~{str(y)[5:10]}" for x, y in so[:4]))
        print(f"{a} 新判据 {int(RS_NEW[a].sum())} 天 {len(sn)} 段:", "；".join(f"{str(x)[5:10]}~{str(y)[5:10]}" for x, y in sn[:4]))

MA = {a: (panel[a].rolling(10).mean(), panel[a].rolling(20).mean(), panel[a].rolling(60).mean()) for a in ["hs300","zz500","kc50","zz1000","nasdaq"]}
def ma_flags(a, d):
    f, s, l = MA[a]
    maf, mas = f.get(d, np.nan), s.get(d, np.nan)
    dead = (not np.isnan(maf)) and (not np.isnan(mas)) and maf < mas
    up = (not np.isnan(mas)) and (not np.isnan(l.get(d, np.nan))) and (panel[a].get(d, np.nan) > l.get(d, np.nan))
    return dead, up

def oil_macro_score(d):
    if d not in US.index: return 0.0
    r = US.loc[d]
    if pd.isna(r.cpi_yoy): return 0.0
    s = 0.0
    if r.cpi_yoy_chg3 > 0 and r.une_chg3 <= 0.0: s += 1.0      # 通胀上行 + 就业稳
    if r.cpi_yoy >= 0.035: s += 0.5                            # 通胀绝对水平偏高
    if r.une_chg3 >= 0.3: s -= 1.5                             # 就业恶化 → 衰退交易
    if r.une_chg6 >= 0.5: s -= 0.5
    return float(np.clip(s, -1.5, 1.5))

def run(base=None, risk_mode="skew", ma_mode="trend", oil_mode="us",
        risk_half=0.5, dd=0.025, band=0.03, cost=5.0, cd=5,
        oil_lo=0.05, oil_hi=0.25, oil_step=0.05, oil_stop=0.10, oil_win=5):
    base = base or {"nasdaq": 0.25, "kc50": 0.10, "hs300": 0.08, "zz500": 0.07, "zz1000": 0.05,
                    "treasury10_etf": 0.20, "oil": 0.15}
    EQ = [a for a in base if a not in (BOND, "oil")]
    RS = RS_OLD if risk_mode == "old" else RS_NEW
    w = np.zeros(len(cols))
    for a, x in base.items(): w[cols.index(a)] = x
    ST = {a: 1.0 for a in EQ}; CD = {a: 0 for a in EQ}
    nav = peak = 1.0; br = sc = 1.0; oil_hold = 0; oil_on = False
    rr = []; trades = []; trail = []
    for i in range(D3.SI, len(idx) - 1):
        d = idx[i]
        tgt = np.zeros(len(cols))
        for a in base:
            if a == BOND: continue
            j = cols.index(a); x = base[a]
            if a in EQ:
                risk = bool(RS[a].get(d, False)) if a in RS else False
                dead, up = ma_flags(a, d)
                if ST[a] == 0.0: CD[a] += 1
                if ma_mode == "old":
                    if dead and risk: ST[a] = 0.0; CD[a] = 0
                    elif ST[a] == 0.0 and CD[a] < cd: ST[a] = 0.0
                    elif ST[a] == 0.0 and not risk: ST[a] = 1.0
                    elif ST[a] == 0.0 and risk: ST[a] = 0.0
                    elif risk: ST[a] = min(ST[a], risk_half)
                    else: ST[a] = 1.0
                else:   # trend：死叉只在风险确认后动作，上升趋势中的死叉只减半不清仓
                    if dead and risk:
                        if up: ST[a] = min(ST[a], risk_half)
                        else: ST[a] = 0.0; CD[a] = 0
                    elif ST[a] == 0.0 and CD[a] < cd: ST[a] = 0.0
                    elif ST[a] == 0.0 and (not risk or up): ST[a] = 1.0
                    elif risk: ST[a] = min(ST[a], risk_half)
                    else: ST[a] = 1.0
                x *= ST[a]
            tgt[j] = x
        # 原油：美国 CPI + 就业率
        jo = cols.index("oil")
        if oil_mode == "dip":
            o20 = float(panel["oil"].loc[:d].iloc[-1] / panel["oil"].loc[:d].iloc[-21] - 1)
            if o20 <= -0.12: oil_on = True
            elif o20 >= -0.03: oil_on = False
            wo = min(base["oil"] + 0.10, oil_hi) if oil_on else base["oil"]
        elif oil_mode == "us":
            sc_o = oil_macro_score(d)
            wo = float(np.clip(base["oil"] + oil_step * sc_o, oil_lo, oil_hi))
        else:
            wo = base["oil"]
        tgt[jo] = wo * br
        r5o = float(panel["oil"].loc[d] / panel["oil"].loc[:d].iloc[-6] - 1)
        if oil_win > 0:
            if r5o <= -oil_stop: oil_hold = oil_win
            elif oil_hold > 0: oil_hold -= 1
        if oil_hold > 0: tgt[jo] = min(tgt[jo], oil_lo)
        for a in EQ: tgt[cols.index(a)] *= br
        tgt[cols.index(BOND)] = max(1.0 - sum(tgt[cols.index(a)] for a in EQ) - tgt[jo], 0.0)
        trade = np.zeros(len(cols))
        for j in range(len(cols)):
            if abs(w[j] - tgt[j]) > band: trade[j] = tgt[j] - w[j]
        c_ = 0.0
        if np.abs(trade).sum() > 1e-6:
            w = w + trade; c_ = float(np.abs(trade).sum()) * cost / 1e4
            trades.append(dict(date=str(d)[:10], action="、".join(
                f"{NAMES.get(cols[j],cols[j])}{'买入' if trade[j]>0 else '卖出'}{abs(trade[j]):.1%}"
                for j in range(len(cols)) if abs(trade[j]) > 0.005),
                risk="、".join(a for a in EQ if a in RS and RS[a].get(d, False))))
        if br != sc:
            for a in EQ:
                j = cols.index(a); delta = w[j] * (1 - br / sc); w[j] -= delta; w[cols.index(BOND)] += delta
            sc = br
        pnl = float(np.nansum(w * R[i + 1])); ret = pnl - c_
        nav *= 1 + ret; peak = max(peak, nav); dcur = nav / peak - 1
        if dcur <= -dd: br = 0.5
        elif dcur >= -dd / 2: br = 1.0
        if pnl > -1: w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
        trail.append(dict(date=str(d)[:10], eq=float(sum(w[cols.index(a)] for a in EQ)),
                          oil=float(w[jo]), tot=float(w.sum()), br=br, ret=ret,
                          w_kc50=float(w[cols.index("kc50")]), w_hs300=float(w[cols.index("hs300")]),
                          w_zz500=float(w[cols.index("zz500")]), w_zz1000=float(w[cols.index("zz1000")]),
                          w_nas=float(w[cols.index("nasdaq")]),
                          risk_n=int(sum(RS[a].get(d, False) for a in EQ if a in RS))))
    r = np.array(rr); nv = np.cumprod(1 + r); ddv = nv / np.maximum.accumulate(nv) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    m = dict(total=float(nv[-1] - 1), maxdd=float(ddv.min()), vol=float(vol),
             sharpe=float((nv[-1] ** (252 / len(r)) - 1) / vol), n_trades=len(trades))
    return m, trades, pd.DataFrame(trail)
