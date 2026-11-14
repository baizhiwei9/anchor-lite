"""2022-2026 全区间引擎：中美宏观锚 + 债券衰退切换 + 风格选股 + 期权风控"""
import os, sys, json, numpy as np, pandas as pd
BASE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(BASE, "data_long")

ANOMALY = {}
def load_px(name):
    d = pd.read_csv(os.path.join(D, f"{name}.csv"), parse_dates=["date"])
    s = d.drop_duplicates("date").set_index("date")["close"].astype(float).sort_index()
    r = s.pct_change()
    jump = r.abs() > 0.30          # 视为拆分/单位错误，按公司行动处理
    if jump.any():
        adj = np.ones(len(s)); cum = 1.0
        for i, j in enumerate(jump.values):
            if j and i > 0:
                cum *= (1 + r.iloc[i]); adj[i:] = cum
        ANOMALY[name] = [str(x.date()) for x in s.index[jump]]
        s = s / pd.Series(adj, index=s.index)
    return s

PX = {n: load_px(n) for n in ["hs300", "zz500", "cyb", "kc50", "nasdaq", "dividend",
                              "oil", "cn_bond10", "us_bond", "gold"]}
IDX = PX["hs300"].index[PX["hs300"].index >= "2022-01-04"]
RET = pd.DataFrame({k: v.pct_change() for k, v in PX.items()}).reindex(IDX).fillna(0.0)
MAC = pd.read_csv(os.path.join(D, "macro_long.csv"), parse_dates=["date"]).set_index("date").reindex(IDX).ffill()
BOND = pd.read_csv(os.path.join(D, "bond_alloc.csv"), parse_dates=["date"]).set_index("date").reindex(IDX).ffill()

def qvix(name):
    m = {"qvix300": "index_option_300etf_qvix", "qvix500": "index_option_500etf_qvix",
         "qvixcyb": "index_option_cyb_qvix", "qvixkcb": "index_option_kcb_qvix",
         "qvix50": "index_option_50etf_qvix"}
    p = os.path.join(D, f"{name}.csv")
    if os.path.exists(p):
        d = pd.read_csv(p, parse_dates=["date"]).drop_duplicates("date")
        return d.set_index("date")["close"].astype(float).reindex(IDX).ffill()
    import akshare as ak
    d = getattr(ak, m[name])(); d["date"] = pd.to_datetime(d.date)
    s = d.dropna(subset=["close"]).set_index("date")["close"].astype(float)
    pd.DataFrame({"date": s.index, "close": s.values}).to_csv(p, index=False)
    return s.reindex(IDX).ffill()

SKEW = pd.read_csv(os.path.join(D, "opt_skew_long.csv"), parse_dates=["date"]) if os.path.exists(os.path.join(D, "opt_skew_long.csv")) else pd.DataFrame()
SKEW["code"] = SKEW["code"].astype(str) if len(SKEW) else None

# 风格资产 → 期权映射
OPT_MAP = {"hs300": ("510300", "qvix300"), "zz500": ("510500", "qvix500"),
           "cyb": ("588000", "qvixcyb"), "kc50": ("588000", "qvixkcb"),
           "dividend": ("510300", "qvix300")}

def zs(s, w=60): return (s - s.rolling(w).mean()) / s.rolling(w).std()

FALLBACK_SKEW = "510300"
def asset_signals():
    sig = {}
    fb = SKEW[SKEW.code == FALLBACK_SKEW].drop_duplicates("date").set_index("date")["skew"] if len(SKEW) else pd.Series(dtype=float)
    fb = fb.reindex(IDX)
    iv_fb = qvix("qvix300")
    for a, (code, qv) in OPT_MAP.items():
        d = pd.DataFrame(index=IDX)
        s = SKEW[SKEW.code == code].drop_duplicates("date").set_index("date") if len(SKEW) else pd.DataFrame()
        base = s["skew"].reindex(IDX) if "skew" in s else pd.Series(index=IDX, dtype=float)
        d["skew"] = base.combine_first(fb)     # 早期无对应期权时用 300ETF 期权代理
        d["z_skew"] = zs(d["skew"])
        iv = qvix(qv).combine_first(iv_fb)
        d["iv"] = iv
        d["iv_chg20"] = iv.diff(20) / 100.0     # QVIX 为百分数，换成波动点小数
        sig[a] = d
    return sig

SIG = asset_signals()

def risk_state(a, sk_th=1.2, iv_th=0.025, enter_n=2, exit_n=3):
    s = SIG[a]; out = []; on = False; ci = co = 0
    for dt in IDX:
        z, c = s.z_skew.get(dt, np.nan), s.iv_chg20.get(dt, np.nan)
        if np.isnan(z) or np.isnan(c): out.append(False); continue
        bad = (z >= sk_th) and (c >= iv_th)
        ok = (z < 0.5) and (c < 0.0)
        ci = ci + 1 if bad else 0
        if not on and ci >= enter_n: on = True; co = 0
        if on:
            co = co + 1 if ok else 0
            if co >= exit_n: on = False; ci = 0
        out.append(on)
    return pd.Series(out, index=IDX)

RS = {a: risk_state(a) for a in OPT_MAP}

def ma_pair(a):
    p = PX[a]; return p.rolling(10).mean(), p.rolling(20).mean()

MA = {a: ma_pair(a) for a in OPT_MAP}
GROWTH = {"cyb", "kc50"}; VALUE = {"hs300", "zz500"}
POOL = ["kc50", "cyb", "zz500", "hs300"]

def oill_score(dt):
    """原油：美国通胀上行 + 就业稳 → 加；就业恶化 → 减"""
    r = MAC.loc[dt] if dt in MAC.index else None
    if r is None or pd.isna(r.cpi_yoy): return 0.0
    s = 0.0
    if r.cpi_yoy > 0.03: s += 1.0
    if r.une_chg3 <= 0.0: s += 0.5
    if r.une_chg3 >= 0.3: s -= 1.5
    return float(np.clip(s, -1.5, 1.5))

def run(stock_cap=0.60, stock_base=0.50, mom_win=60, top_n=2, dd=0.015, dd2=0.030, band=0.03, cost=5.0,
        oil_base=0.12, oil_cap=0.18, breaker=True):
    cols = list(RET.columns); n = len(cols)
    w = np.zeros(n)
    for a, x in {"hs300": 0.15, "kc50": 0.10, "nasdaq": 0.15, "oil": oil_base,
                 "cn_bond10": 0.40, "us_bond": 0.20}.items():
        if a in cols: w[cols.index(a)] = x
    w = w / w.sum()
    ST = {a: 1.0 for a in OPT_MAP}
    nav = peak = 1.0; br = 1.0; month = None; nav_hist = [1.0]
    sel_log = {}; trades = []; rr = []; trail = []
    for i, dt in enumerate(IDX[:-1]):
        if dt.month != month:
            month = dt.month
            # 风格动量
            mom = {}
            for a in POOL:
                s = PX[a].loc[:dt]
                mom[a] = float(s.iloc[-1] / s.iloc[-1 - mom_win] - 1) if len(s) > mom_win else 0.0
            top = sorted([a for a in POOL if mom[a] > 0], key=lambda x: -mom[x])[:top_n]
            if not top: top = ["dividend"]
            sel_log[str(dt)[:10]] = (top, {k: round(v, 3) for k, v in mom.items()})
            g = MAC.loc[dt].growth_cn if dt in MAC.index and not pd.isna(MAC.loc[dt].growth_cn) else 0.0
            gus = MAC.loc[dt].growth_us if dt in MAC.index and not pd.isna(MAC.loc[dt].growth_us) else 0.0
            S = float(np.clip(stock_base + 0.10 * (0.5 * g + 0.5 * gus), 0.30, stock_cap))
            # 广度闸门：站上 200 日线的宽基比例
            above = 0
            for a in POOL:
                p_ = PX[a].loc[:dt]
                if len(p_) > 200 and float(p_.iloc[-1]) > float(p_.tail(200).mean()): above += 1
            breadth = above / len(POOL)
            if breadth <= 0.25: S *= 0.25
            elif breadth <= 0.50: S *= 0.55
            o = float(np.clip(oil_base + 0.03 * oill_score(dt), 0.05, oil_cap))
            tgt_m = {a: 0.0 for a in cols}
            for a in top:
                tgt_m[a] = S / len(top)
            tgt_m["nasdaq"] = min(0.15, stock_cap - S) if S < stock_cap else 0.0
            tgt_m["oil"] = o
            bond_rem = max(1.0 - sum(tgt_m.values()), 0.0)
            us_share = float(BOND.loc[dt, "us_bond"]) if dt in BOND.index and not pd.isna(BOND.loc[dt, "us_bond"]) else 0.3
            # 债券内部动量择优：美债 60 日弱于中债则不配美债；美债占比上限 40%
            try:
                r_us = float(PX["us_bond"].loc[:dt].iloc[-1] / PX["us_bond"].loc[:dt].iloc[-61] - 1)
                r_cn = float(PX["cn_bond10"].loc[:dt].iloc[-1] / PX["cn_bond10"].loc[:dt].iloc[-61] - 1)
                if r_us < r_cn: us_share = 0.0
            except Exception:
                pass
            us_share = min(us_share, 0.40)
            tgt_m["us_bond"] = bond_rem * us_share
            tgt_m["cn_bond10"] = bond_rem * (1 - us_share)
        tgt = np.array([tgt_m.get(c, 0.0) for c in cols])
        # 期权风控四象限
        for a in OPT_MAP:
            maf, mas = MA[a][0].get(dt, np.nan), MA[a][1].get(dt, np.nan)
            dead = (not np.isnan(maf)) and (not np.isnan(mas)) and maf < mas
            risk = bool(RS[a].get(dt, False))
            ST[a] = (0.0 if risk else 0.5) if dead else (0.5 if risk else 1.0)
            j = cols.index(a)
            tgt[j] *= ST[a]
        # 股票腿上限
        eq_idx = [cols.index(a) for a in (POOL + ["nasdaq", "dividend"]) if a in cols]
        es = sum(tgt[j] for j in eq_idx)
        if es > stock_cap:
            k = stock_cap / es
            for j in eq_idx: tgt[j] *= k
        if breaker: tgt[eq_idx + [cols.index("oil")]] *= br
        # 债券吸收残差（含熔断让出的额度），避免现金拖累
        risk_sum = sum(tgt[j] for j in eq_idx) + tgt[cols.index("oil")]
        rem = max(1.0 - risk_sum, 0.0)
        us_share = float(BOND.loc[dt, "us_bond"]) if dt in BOND.index and not pd.isna(BOND.loc[dt, "us_bond"]) else 0.3
        # 债券内部动量择优：美债 60 日弱于中债则不配美债；美债占比上限 40%
        try:
            r_us = float(PX["us_bond"].loc[:dt].iloc[-1] / PX["us_bond"].loc[:dt].iloc[-61] - 1)
            r_cn = float(PX["cn_bond10"].loc[:dt].iloc[-1] / PX["cn_bond10"].loc[:dt].iloc[-61] - 1)
            if r_us < r_cn: us_share = 0.0
        except Exception:
            pass
        us_share = min(us_share, 0.40)
        tgt[cols.index("us_bond")] = rem * us_share
        tgt[cols.index("cn_bond10")] = rem * (1 - us_share)
        trade = np.zeros(n)
        for j in range(n):
            if abs(w[j] - tgt[j]) > band: trade[j] = tgt[j] - w[j]
        c_ = 0.0
        if np.abs(trade).sum() > 1e-6:
            w = w + trade; c_ = float(np.abs(trade).sum()) * cost / 1e4
            trades.append((str(dt)[:10], [(cols[j], round(trade[j], 3)) for j in range(n) if abs(trade[j]) > 0.005]))
        pnl = float((w * RET.loc[dt].values).sum()); ret = pnl - c_
        nav *= 1 + ret; peak = max(peak, nav); dcur = nav / peak - 1
        if breaker:
            if dcur <= -dd2: br = 0.4
            elif dcur <= -dd: br = min(br, 0.4)
            elif dcur >= -dd / 2: br = 1.0
            # 重置条件：净值创 60 日新高即解除熔断（避免长期锁死）
            if br < 1.0 and len(nav_hist) >= 60 and nav >= max(nav_hist[-60:]):
                br = 1.0
        if pnl > -1: w = w * (1 + RET.loc[dt].values) / (1 + pnl)
        if w.sum() > 1: w = w / w.sum()
        _es = sum(w[j] for j in eq_idx)
        if _es > stock_cap:                     # 股票腿硬上限
            k_ = stock_cap / _es
            for j in eq_idx:
                cut = w[j] * (1 - k_); w[j] -= cut; w[cols.index("cn_bond10")] += cut
        rr.append(ret); nav_hist.append(nav)
        trail.append(dict(date=str(dt)[:10], ret=ret, eq=float(sum(w[j] for j in eq_idx)),
                          oil=float(w[cols.index("oil")]), cn=float(w[cols.index("cn_bond10")]),
                          us=float(w[cols.index("us_bond")]), tot=float(w.sum()), br=br))
    r_ = np.array(rr); nv = np.cumprod(1 + r_); ddv = nv / np.maximum.accumulate(nv) - 1
    vol = r_.std(ddof=1) * np.sqrt(252)
    yrs = len(r_) / 252
    m = dict(total=float(nv[-1] - 1), cagr=float(nv[-1] ** (1 / yrs) - 1), maxdd=float(ddv.min()),
             vol=vol, sharpe=float((nv[-1] ** (1 / yrs) - 1) / vol), n_trades=len(trades))
    return m, pd.DataFrame(trail), sel_log, trades
