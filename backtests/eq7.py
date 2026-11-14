"""宽基股票池 + 期权风险开关 + 均线死叉清仓"""
import os, sys, json, numpy as np, pandas as pd
import engine3 as E, dyn3 as D3, dyn4 as M4

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = D3.panel, D3.rets
cols = list(panel.columns); idx = panel.index; R = rets.fillna(0).values
NAMES = E.NAMES
BOND = "treasury10_etf"

# 宽基 → 期权代码映射（中证1000 无 ETF 期权，用中证500 代理）
OPT = {"hs300": "510300", "zz500": "510500", "kc50": "588000", "zz1000": "510500"}
P = pd.read_csv(os.path.join(BASE, "data_signals/opt_panel.csv"), parse_dates=["date"])
def zs(s, w=60): return (s - s.rolling(w).mean()) / s.rolling(w).std()
SIG = {}
for asset, code in OPT.items():
    x = P[P.code.astype(str) == code].set_index("date").sort_index()
    d = pd.DataFrame(index=x.index)
    d["z_iv"] = zs(x.iv_atm); d["z_sk"] = zs(x["skew"]); d["z_pcr"] = zs(x.pcr_vol)
    SIG[asset] = d.reindex(idx).ffill()

def risk_states(asset, enter_iv=1.0, enter_sk=1.0, exit_iv=0.5, exit_sk=0.5, enter_n=2, exit_n=3):
    s = SIG[asset]; st = [] ; on = False; cnt_in = cnt_out = 0
    for dt in idx:
        ziv, zsk = s.z_iv.get(dt, np.nan), s.z_sk.get(dt, np.nan)
        if np.isnan(ziv): st.append(False); continue
        bad = (ziv >= enter_iv) or (zsk >= enter_sk)
        ok = (ziv < exit_iv) and (zsk < exit_sk)
        if not on:
            cnt_in = cnt_in + 1 if bad else 0
            if cnt_in >= enter_n: on = True; cnt_out = 0
        else:
            cnt_out = cnt_out + 1 if ok else 0
            if cnt_out >= exit_n: on = False; cnt_in = 0
        st.append(on)
    return pd.Series(st, index=idx)

RS = {a: risk_states(a) for a in OPT}
import eq8 as M8
RS_NEW = M8.RS_NEW
USM = M8.US.reindex(idx, method="ffill")

def ma_state(asset, fast=10, slow=20):
    p = panel[asset]
    return (p.rolling(fast).mean(), p.rolling(slow).mean())

def run(base=None, use_opt=True, use_ma=True, sm=True, ma_mode='old', eq_cap=0.45, risk_mode='old', ma_trend=False, oil_us=False, dd=0.03, band=0.03, cost=5.0,
        risk_cut=0.0, risk_half=0.5, recover_need_ma=True, cd=0, exit_need_risk=False, oil_dip=True, oil_add=0.10, oil_cap=0.30, oil_stop=0.10, oil_win=5):
    base = base or {"nasdaq": 0.20, "kc50": 0.10, "hs300": 0.08, "zz500": 0.07, "zz1000": 0.05,
                    "treasury10_etf": 0.35, "oil": 0.15}
    EQ = [a for a in base if a not in (BOND, "oil")]
    w = np.zeros(len(cols))
    for a, x in base.items(): w[cols.index(a)] = x
    nav = peak = 1.0; br = sc = 1.0; oil_on = False; oil_hold = 0; oil_month = None; wo_m = 0.15
    rr = []; trades = []; trail = []
    MA = {a: ma_state(a) for a in EQ}
    ST = {a: 1.0 for a in EQ}; CD = {a: 0 for a in EQ}   # 1=满仓 0.5=半仓 0=清仓
    RSR = RS_NEW if risk_mode == "skew" else RS
    for i in range(D3.SI, len(idx) - 1):
        d = idx[i]
        tgt = np.zeros(len(cols))
        for a in base:
            if a == BOND: continue
            j = cols.index(a); x = base[a]
            if a in EQ:
                maf, mas = MA[a][0].get(d, np.nan), MA[a][1].get(d, np.nan)
                dead = (not np.isnan(maf)) and (not np.isnan(mas)) and maf < mas
                risk = bool(RSR[a].get(d, False)) if (use_opt and a in RSR) else False
                dead = dead if use_ma else False
                if sm and (use_ma or use_opt):
                    # 状态机：死叉清仓；风险中不恢复；风险解除且未死叉才恢复
                    if ST[a] == 0.0: CD[a] += 1
                    up = (not np.isnan(mas)) and float(panel[a].loc[:d].iloc[-1]) > float(panel[a].loc[:d].tail(60).mean())
                    if ma_mode == "user":
                        # 你的规则：预警+死叉清仓；无预警+死叉减半；金叉且风险解除满仓；金叉但风险未解半仓
                        if dead:
                            ST[a] = 0.0 if risk else risk_half
                        else:
                            ST[a] = 1.0 if not risk else risk_half
                    elif ma_trend:
                        if dead and risk:
                            if up: ST[a] = min(ST[a], risk_half)
                            else: ST[a] = 0.0; CD[a] = 0
                        elif ST[a] == 0.0 and CD[a] < cd: ST[a] = 0.0
                        elif ST[a] == 0.0 and (not risk or up): ST[a] = 1.0
                        elif risk: ST[a] = min(ST[a], risk_half)
                        else: ST[a] = 1.0
                    elif dead and (risk or not exit_need_risk): ST[a] = 0.0; CD[a] = 0
                    elif ST[a] == 0.0 and CD[a] < cd: ST[a] = 0.0
                    elif ST[a] == 0.0 and not risk: ST[a] = 1.0
                    elif ST[a] == 0.0 and risk: ST[a] = 0.0
                    elif risk: ST[a] = min(ST[a], risk_half)
                    else: ST[a] = 1.0
                    x *= ST[a]
                else:
                    if use_ma and dead and (risk or not use_opt): x *= risk_cut
                    elif use_ma and risk: x *= risk_half
                    elif use_opt and risk: x *= risk_half
            tgt[j] = x
        eqsum = sum(tgt[cols.index(a)] for a in EQ)
        tgt[cols.index(BOND)] = 1.0 - eqsum - tgt[cols.index("oil")]
        tgt[cols.index("oil")] *= br
        if oil_us:
            if d.month != oil_month:
                oil_month = d.month
                wo_m = float(np.clip(0.15 + 0.05 * M8.oil_macro_score(d), 0.05, oil_cap))
            tgt[cols.index("oil")] = wo_m
        if oil_dip and not oil_us:
            o20 = float(panel["oil"].loc[:d].iloc[-1] / panel["oil"].loc[:d].iloc[-21] - 1)
            if o20 <= -0.12: oil_on = True
            elif o20 >= -0.03: oil_on = False
            if oil_on: tgt[cols.index("oil")] = min(tgt[cols.index("oil")] + oil_add, oil_cap)
        r5o = float(panel["oil"].loc[d] / panel["oil"].loc[:d].iloc[-6] - 1)
        if oil_win > 0:
            if r5o <= -oil_stop: oil_hold = oil_win
            elif oil_hold > 0: oil_hold -= 1
        if oil_hold > 0: tgt[cols.index("oil")] = min(tgt[cols.index("oil")], 0.05)
        for a in EQ: tgt[cols.index(a)] *= br
        _eqs = sum(tgt[cols.index(a)] for a in EQ)
        if _eqs > eq_cap:
            _k = eq_cap / _eqs
            for a in EQ: tgt[cols.index(a)] *= _k
        tgt[cols.index(BOND)] = max(1.0 - sum(tgt[cols.index(a)] for a in EQ) - tgt[cols.index("oil")], 0.0)
        trade = np.zeros(len(cols))
        for j in range(len(cols)):
            if abs(w[j] - tgt[j]) > band: trade[j] = tgt[j] - w[j]
        c_ = 0.0
        if np.abs(trade).sum() > 1e-6:
            w = w + trade; c_ = float(np.abs(trade).sum()) * cost / 1e4
            trades.append(dict(date=str(d)[:10], action="、".join(
                f"{NAMES.get(cols[j],cols[j])}{'买入' if trade[j]>0 else '卖出'}{abs(trade[j]):.1%}"
                for j in range(len(cols)) if abs(trade[j]) > 0.005),
                risk="、".join(a for a in EQ if a in RS and RS[a].get(d, False)) if use_opt else ""))
        sc = br
        pnl = float(np.nansum(w * R[i + 1])); ret = pnl - c_
        nav *= 1 + ret; peak = max(peak, nav); dcur = nav / peak - 1
        if dcur <= -dd: br = 0.5
        elif dcur >= -dd / 2: br = 1.0
        if pnl > -1: w = w * (1 + R[i + 1]) / (1 + pnl)
        if w.sum() > 1.0: w = w / w.sum()   # 总仓位不得超 100%
        if w[cols.index("oil")] > oil_cap:      # 原油硬上限
            _cut = w[cols.index("oil")] - oil_cap
            w[cols.index("oil")] = oil_cap; w[cols.index(BOND)] += _cut
        _eqw = sum(w[cols.index(a)] for a in EQ)
        if _eqw > eq_cap:                    # 股票腿合计不得超上限
            _k = eq_cap / _eqw
            for a in EQ:
                j = cols.index(a); cut = w[j] * (1 - _k)
                w[j] -= cut; w[cols.index(BOND)] += cut
        rr.append(ret)
        trail.append(dict(date=str(d)[:10], eq=float(sum(w[cols.index(a)] for a in EQ)), tot=float(w.sum()),
                          br=br, ret=ret,
                          w_kc50=float(w[cols.index("kc50")]), w_hs300=float(w[cols.index("hs300")]),
                          w_zz500=float(w[cols.index("zz500")]), w_zz1000=float(w[cols.index("zz1000")]),
                          w_nas=float(w[cols.index("nasdaq")]), w_oil=float(w[cols.index("oil")]),
                          risk_n=int(sum(RS[a].get(d, False) for a in EQ if a in RS)) if use_opt else 0))
    r = np.array(rr); nv = np.cumprod(1 + r); ddv = nv / np.maximum.accumulate(nv) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    m = dict(total=float(nv[-1] - 1), maxdd=float(ddv.min()), vol=float(vol),
             sharpe=float((nv[-1] ** (252 / len(r)) - 1) / vol), n_trades=len(trades))
    return m, trades, pd.DataFrame(trail)

if __name__ == "__main__":
    V = {"无风控(固定权重)": dict(use_opt=False, use_ma=False),
         "仅均线死叉": dict(use_opt=False),
         "仅期权风险减半": dict(use_ma=False),
         "期权+均线(用户规则)": dict(),
         "纳指25%+期权均线": dict(base={"nasdaq": 0.25, "kc50": 0.10, "hs300": 0.08, "zz500": 0.07,
                                        "zz1000": 0.05, "treasury10_etf": 0.30, "oil": 0.15}),
         "纳指30%+期权均线": dict(base={"nasdaq": 0.30, "kc50": 0.10, "hs300": 0.08, "zz500": 0.07,
                                        "zz1000": 0.05, "treasury10_etf": 0.25, "oil": 0.15})}
    out = {}
    for k, kw in V.items():
        m, tr, tl = run(**kw); out[k] = m
        print(f"{k:20s} 收益 {m['total']:+.1%} 回撤 {m['maxdd']:.2%} 波动 {m['vol']:.1%} 夏普 {m['sharpe']:.2f} 笔数 {m['n_trades']}")
    json.dump(out, open(os.path.join(BASE, "eq7_variants.json"), "w"), ensure_ascii=False, indent=2, default=float)
