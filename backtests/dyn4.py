"""期权/期货信号引擎（在 dyn3 三条动态规则之上叠加）：
A 过热止盈  B 恐慌加仓  C 期权方向定仓  D 期货持仓量增仓  E 原油跌深逆势加仓（dyn3 保留）
信号只用当日及以前数据，次一交易日结算。"""
import os, sys, json, numpy as np, pandas as pd
import engine3 as E, dyn3 as D3

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = D3.panel, D3.rets
cols = list(panel.columns); idx = panel.index; R = rets.fillna(0.0).values
BASE_W = D3.BASE_W; RISK = D3.RISK; ATV = D3.ASSET_TARGET_VOL
NAMES = E.NAMES
def zs(s, win=60):
    m = s.rolling(win).mean(); sd = s.rolling(win).std()
    return (s - m) / sd.replace(0, np.nan)

P = pd.read_csv(os.path.join(BASE, "data_signals/opt_panel.csv"), parse_dates=["date"])
def opt_sig(code):
    x = P[P.code.astype(str) == str(code)].set_index("date").sort_index()
    s = pd.DataFrame(index=x.index)
    s["pcr"] = x.pcr_vol; s["pcr_oi"] = x.pcr_oi; s["skew"] = x["skew"]; s["iv"] = x.iv_atm; s["oi_net"] = x.oi_net
    for c in ["pcr", "skew", "iv", "oi_net"]: s["z_" + c] = zs(s[c])
    s["d_skew5"] = s["skew"].diff(5); s["d_pcr5"] = s["pcr"].diff(5)
    return s.reindex(idx).ffill()
KC = opt_sig("588000")
F = pd.read_csv(os.path.join(BASE, "data_signals/futures_main.csv"))
F.columns = ["sym", "date", "open", "high", "low", "close", "vol", "oi", "settle"]
F["date"] = pd.to_datetime(F["date"])
def fut_sig(sym):
    x = F[F.sym == sym].set_index("date").sort_index()
    s = pd.DataFrame(index=x.index)
    s["r5"] = x.close.pct_change(5); s["r20"] = x.close.pct_change(20)
    s["d_oi5"] = x.oi.pct_change(5); s["d_vol5"] = x.vol.pct_change(5)
    return s.reindex(idx).ffill()
SC = fut_sig("SC0"); IF = fut_sig("IF0")

px_k = panel["kc50"]
kc = pd.DataFrame(index=idx)
kc["r20"] = px_k.pct_change(20); kc["r60"] = px_k.pct_change(60)
kc["vol20"] = px_k.pct_change().rolling(20).std() * np.sqrt(252)
kc["hi20"] = px_k >= px_k.rolling(20).max()
kc["ma20_up"] = px_k > px_k.rolling(20).mean()
kc = kc.join(KC)

def run(band=0.06, trig=0.02, vol_target=0.08, cost=5.0,
        hot_r20=0.25, hot_ziv=1.5, hot_exit=0.10, hot_days=5,
        fear_zskew=1.5, fear_r20=-0.10, fear_add=0.05,
        trend_cap=0.20, trend_add=0.03,
        oil_oi=0.05, oil_drop=-0.12, oil_add=0.10, oil_cap=0.25,
        use_hot=False, use_fear=False, use_trend=False, use_oiloi=False, use_oildip=True,
        kc_floor=0.0):
    w = D3.base_vec(); target = D3.base_vec()
    nav = peak = 1.0; br = sc = 1.0; month = None
    rr = []; trades = []; trail = []
    hot_on = False; hot_left = 0; oil_boost = False
    for i in range(D3.SI, len(idx) - 1):
        d = idx[i]
        if d.month != month:
            month = d.month; target = D3.base_vec()
            for a in RISK:
                j = cols.index(a)
                v20 = float(rets[a].loc[:d].tail(20).std() * np.sqrt(252))
                target[j] = BASE_W[a] * np.clip(ATV[a] / max(v20, 0.01), 0.5, 1.8)
            if target.sum() > 1: target = target / target.sum()
            sub = rets.loc[:d].tail(60).fillna(0); cov = sub.cov().values * 252
            sig = float(np.sqrt(max(target @ cov @ target, 1e-8)))
            target = target * min(1.0, vol_target / sig)
        tgt = target.copy()
        jk, jo = cols.index("kc50"), cols.index("oil")
        r20 = float(kc.r20.loc[d]); ziv = float(kc.z_iv.loc[d]); zsk = float(kc.z_skew.loc[d])
        zpcr = float(kc.z_pcr.loc[d]); hi = bool(kc.hi20.loc[d]); maup = bool(kc.ma20_up.loc[d])
        # A 过热止盈
        if use_hot:
            if (r20 >= hot_r20 and ziv >= hot_ziv) or (r20 >= hot_r20 + 0.05 and hi and ziv >= hot_ziv - 0.5):
                hot_on = True; hot_left = hot_days
            elif hot_on:
                hot_left -= 1
                if hot_left <= 0 and (r20 < hot_exit or ziv < 0.5): hot_on = False
            if hot_on: tgt[jk] = min(tgt[jk], 0.03)
        # B 恐慌加仓
        if use_fear and zsk >= fear_zskew and r20 <= fear_r20:
            tgt[jk] = min(tgt[jk] + fear_add, trend_cap)
        # C 期权方向定仓：平静+低对冲需求+趋势向上 → 加到 trend_cap；隐含波动极端 → 降至 3%
        if use_trend:
            if ziv <= 0.5 and zpcr <= 0.5 and r20 > 0 and maup:
                tgt[jk] = min(tgt[jk] + trend_add, trend_cap)
            elif ziv >= 1.5 and zsk >= 1.0 and r20 > 0:
                tgt[jk] = min(tgt[jk], 0.04)
        # E 原油跌深逆势加仓（dyn3 保留，滞回）
        oil20 = float(panel["oil"].loc[:d].iloc[-1] / panel["oil"].loc[:d].iloc[-21] - 1)
        if use_oildip:
            if oil20 <= oil_drop: oil_boost = True
            elif oil20 >= -0.03: oil_boost = False
            if oil_boost: tgt[jo] = min(tgt[jo] + oil_add, oil_cap)
        # D 期货持仓量：增仓上行加、增仓下行减
        if use_oiloi:
            r5 = float(SC.r5.loc[d]); doi = float(SC.d_oi5.loc[d])
            if r5 > 0 and doi > 0: tgt[jo] = min(tgt[jo] + oil_oi, oil_cap)
            elif r5 < 0 and doi > 0: tgt[jo] = max(tgt[jo] - oil_oi, 0.03)
        tgt = np.clip(tgt, 0, None) * br
        cur = w; trade = np.zeros(len(cols))
        for j in range(len(cols)):
            if abs(cur[j] - tgt[j]) > band: trade[j] = tgt[j] - cur[j]
        c = 0.0
        if np.abs(trade).sum() > 1e-6:
            w = cur + trade; c = float(np.abs(trade).sum()) * cost / 1e4
            trades.append(dict(date=str(d)[:10], r20=round(r20, 3), ziv=round(ziv, 2), zsk=round(zsk, 2),
                               hot=int(hot_on),
                               action="、".join(f"{NAMES.get(cols[j],cols[j])}{'买入' if trade[j]>0 else '卖出'}{abs(trade[j]):.1%}"
                                                for j in range(len(cols)) if abs(trade[j]) > 0.005)))
        if br != sc:
            w = w * (br / sc); sc = br
        pnl = float(np.nansum(w * R[i + 1])); ret = pnl - c
        nav *= 1 + ret; peak = max(peak, nav); dd = nav / peak - 1
        if dd <= -trig: br = 0.5
        elif dd >= -trig / 2: br = 1.0
        if pnl > -1: w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
        trail.append(dict(date=str(d)[:10], kc50=float(w[jk]), oil=float(w[jo]), tot=float(w.sum()),
                          br=br, hot=int(hot_on)))
    r = np.array(rr); n = np.cumprod(1 + r); dd = n / np.maximum.accumulate(n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    m = dict(total=float(n[-1] - 1), maxdd=float(dd.min()), vol=float(vol),
             sharpe=float((n[-1] ** (252 / len(r)) - 1) / vol), n_trades=len(trades))
    return m, trades, pd.DataFrame(trail)

if __name__ == "__main__":
    V = {
        "dyn3基准(仅油跌加仓)": dict(use_hot=False, use_fear=False, use_trend=False, use_oiloi=False),
        "+过热止盈": dict(use_hot=True),
        "+恐慌加仓": dict(use_fear=True),
        "+期权方向定仓": dict(use_trend=True),
        "+期货油仓": dict(use_oiloi=True),
        "全部叠加": dict(use_hot=True, use_fear=True, use_trend=True, use_oiloi=True),
        "止盈+方向+期货油": dict(use_hot=True, use_trend=True, use_oiloi=True),
        "方向+期货油": dict(use_trend=True, use_oiloi=True),
    }
    out = {}
    for k, kw in V.items():
        m, tr, tl = run(**kw)
        print(f"{k:16s} 收益 {m['total']:+.1%} 回撤 {m['maxdd']:.2%} 波动 {m['vol']:.1%} 夏普 {m['sharpe']:.2f} 笔数 {m['n_trades']}")
        out[k] = m
    json.dump(out, open(os.path.join(BASE, "dyn4_variants.json"), "w"), ensure_ascii=False, indent=2)
