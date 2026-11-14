"""期权/期货信号直接定仓引擎（dyn5）：
科创50 权重由「趋势 + 期权平静度 + 恐慌 + 过热」四类信号决定（3%~25%）；
原油由「期货增仓方向 + 跌幅 + 趋势」决定（5%~30%）；
纳指按动量；债券按剩余额度分配；熔断 3%，只作用于风险腿。"""
import os, sys, json, numpy as np, pandas as pd
import engine3 as E, dyn3 as D3, dyn4 as M4

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = D3.panel, D3.rets
cols = list(panel.columns); idx = panel.index; R = rets.fillna(0.0).values
NAMES = E.NAMES
KC, SC, kc = M4.KC, M4.SC, M4.kc

def run(kc_lo=0.03, kc_hi=0.25, kc_base=0.10, kc_step=0.05,
        oil_lo=0.05, oil_hi=0.30, oil_base=0.15, oil_step=0.05,
        nas_base=0.10, nas_hi=0.20, dd=0.03, cost=5.0, band=0.05,
        stop_thr=0.10, stop_win=5, oil_stop=0.10,
        hot_r20=0.25, hot_ziv=1.5, panic_z=1.5, calm_z=0.5, record=True):
    sm = pd.DataFrame(index=idx)
    for c in ["r20", "r60", "z_iv", "z_skew", "z_pcr"]:
        sm[c] = kc[c].rolling(3).mean()
    w = np.zeros(len(cols)); w[cols.index("treasury10_etf")] = 0.40
    w[cols.index("dividend")] = 0.15; w[cols.index("lowvol")] = 0.10
    w[cols.index("nasdaq")] = nas_base; w[cols.index("kc50")] = kc_base; w[cols.index("oil")] = oil_base
    nav = peak = 1.0; br = sc = 1.0
    hot_state = 0; hot_hold = 0; hot_cnt = 0; stop_hold = 0; oil_hold = 0
    rr = []; trades = []; trail = []
    for i in range(D3.SI, len(idx) - 1):
        d = idx[i]
        r20 = float(sm.r20.loc[d]); r60 = float(sm.r60.loc[d])
        ziv = float(sm.z_iv.loc[d]); zsk = float(sm.z_skew.loc[d]); zpcr = float(sm.z_pcr.loc[d])
        hi = bool(kc.hi20.loc[d])
        # ---- 科创50 打分 ----
        s = 0.0
        if r20 > 0 and r60 > 0: s += 1.0                     # 趋势确认
        if ziv < calm_z: s += 1.0                            # 期权定价平静
        if zpcr < calm_z: s += 1.0                           # 对冲需求低（偏多）
        if zsk >= panic_z and r20 <= -0.10: s += 1.0          # 恐慌极端，逆向加
        # 过热状态机：连续 2 日成立才进入，最少维持 10 日，r20<5% 且 ziv<0.5 才退出
        cond = (r20 >= hot_r20 and ziv >= hot_ziv)
        hot_cnt = hot_cnt + 1 if cond else 0
        if hot_state == 0 and hot_cnt >= 2: hot_state = 1; hot_hold = 10
        elif hot_state == 1:
            hot_hold -= 1
            if hot_hold <= 0 and r20 < 0.05 and ziv < 0.5: hot_state = 0
        overheat = hot_state
        if hot_state == 1: s -= 2.0
        if hi and ziv >= hot_ziv + 1.0 and r20 >= hot_r20: s -= 1.0; overheat = 2
        # 短期急跌止损：5 日跌幅超阈值则压到下限，最少维持 5 日
        r5k = float(kc.px5.loc[d]) if "px5" in kc.columns else 0.0
        if stop_win > 0:
            if r5k <= -stop_thr: stop_hold = stop_win
            elif stop_hold > 0: stop_hold -= 1
        w_kc = np.clip(kc_base + kc_step * s, kc_lo, kc_hi)
        if stop_hold > 0: w_kc = min(w_kc, kc_lo)
        # ---- 原油打分 ----
        r5o = float(SC.r5.loc[d]); doi = float(SC.d_oi5.loc[d])
        oil20 = float(panel["oil"].loc[:d].iloc[-1] / panel["oil"].loc[:d].iloc[-21] - 1)
        so = 0.0
        if r5o > 0 and doi > 0: so += 1.0                     # 增仓上行
        if r5o < 0 and doi > 0: so -= 1.0                    # 增仓下行
        if oil20 <= -0.12: so += 1.0                         # 跌幅够深，逆势
        if oil20 <= -0.25: so += 1.0
        if float(SC.r20.loc[d] or 0) > 0: so += 0.5
        w_oil = np.clip(oil_base + oil_step * so, oil_lo, oil_hi)
        # 原油急跌止损：5 日跌幅超阈值压到下限，最少维持 5 日
        r5o5 = float(panel["oil"].loc[d] / panel["oil"].loc[:d].iloc[-6] - 1)
        if stop_win > 0:
            if r5o5 <= -oil_stop: oil_hold = stop_win
            elif oil_hold > 0: oil_hold -= 1
        if oil_hold > 0: w_oil = min(w_oil, oil_lo)
        # ---- 纳指 ----
        rn = float(panel["nasdaq"].loc[d] / panel["nasdaq"].loc[:d].iloc[-21] - 1)
        vn = float(rets["nasdaq"].loc[:d].tail(20).std() * np.sqrt(252))
        w_nas = nas_base + (0.05 if (rn > 0 and vn < 0.30) else (-0.03 if rn < -0.05 else 0))
        w_nas = float(np.clip(w_nas, 0.05, nas_hi))
        # ---- 债券/红利/低波按剩余额度 ----
        risk_sum = w_kc + w_oil + w_nas
        rem = max(1.0 - risk_sum, 0.0)
        core = {"treasury10_etf": 0.40, "dividend": 0.15, "lowvol": 0.10}
        core_sum = sum(core.values())
        tgt = np.zeros(len(cols))
        for k, v in core.items(): tgt[cols.index(k)] = rem * v / core_sum
        tgt[cols.index("kc50")] = w_kc; tgt[cols.index("oil")] = w_oil; tgt[cols.index("nasdaq")] = w_nas
        # ---- 熔断只削减风险腿 ----
        for a in ["kc50", "oil", "nasdaq"]:
            j = cols.index(a); tgt[j] *= br
        trade = np.zeros(len(cols))
        for j in range(len(cols)):
            if abs(w[j] - tgt[j]) > band: trade[j] = tgt[j] - w[j]
        c = 0.0
        if np.abs(trade).sum() > 1e-6:
            w = w + trade; c = float(np.abs(trade).sum()) * cost / 1e4
            if record:
                trades.append(dict(date=str(d)[:10], r20=round(r20, 3), ziv=round(ziv, 2),
                                   zsk=round(zsk, 2), w_kc=round(w_kc, 3), w_oil=round(w_oil, 3),
                                   overheat=overheat, br=br,
                                   action="、".join(f"{NAMES.get(cols[j],cols[j])}{'买入' if trade[j]>0 else '卖出'}{abs(trade[j]):.1%}"
                                                    for j in range(len(cols)) if abs(trade[j]) > 0.005)))
        pnl = float(np.nansum(w * R[i + 1])); ret = pnl - c
        nav *= 1 + ret; peak = max(peak, nav); dcur = nav / peak - 1
        if dcur <= -dd: br = 0.5
        elif dcur >= -dd / 2: br = 1.0
        if pnl > -1: w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
        trail.append(dict(date=str(d)[:10], kc50=float(w[cols.index("kc50")]), oil=float(w[cols.index("oil")]),
                          nas=float(w[cols.index("nasdaq")]), tot=float(w.sum()), br=br, overheat=overheat,
                          nav=nav, dd=nav / peak - 1,
                          div=float(w[cols.index("dividend")]), lowvol=float(w[cols.index("lowvol")]),
                          treas=float(w[cols.index("treasury10_etf")])))
    r = np.array(rr); n = np.cumprod(1 + r); ddv = n / np.maximum.accumulate(n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    m = dict(total=float(n[-1] - 1), maxdd=float(ddv.min()), vol=float(vol),
             sharpe=float((n[-1] ** (252 / len(r)) - 1) / vol), n_trades=len(trades))
    return m, trades, pd.DataFrame(trail)

if __name__ == "__main__":
    for k, kw in {"默认": dict(), "科创上限20%": dict(kc_hi=0.20), "科创上限30%": dict(kc_hi=0.30),
                  "熔断2%": dict(dd=0.02), "熔断4%": dict(dd=0.04), "热阈20%": dict(hot_r20=0.20)}.items():
        m, tr, tl = run(**kw)
        print(f"{k:12s} 收益 {m['total']:+.1%} 回撤 {m['maxdd']:.2%} 波动 {m['vol']:.1%} 夏普 {m['sharpe']:.2f} 笔数 {m['n_trades']}")
