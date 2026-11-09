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
IDX = PX["hs300"].index[PX["hs300"].index >= "2020-01-02"]
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

def _release_series():
    o = SIG["hs300"]
    opt = (o["z_skew"] < 0.5) & (o["iv_chg20"] < 0.0)
    ups = []
    for a in POOL:
        p = PX[a]
        ups.append((p > p.rolling(20).mean()) & (p.rolling(20).mean() > p.rolling(20).mean().shift(5)))
    ma = pd.concat(ups, axis=1).sum(axis=1) >= 3
    p = PX["hs300"]; r = p.pct_change(); v20 = r.rolling(20).std() * np.sqrt(252)
    vol = (v20 < v20.rolling(60).median()) & (v20 < v20.shift(10))
    out = pd.DataFrame({"opt": opt.reindex(IDX).fillna(False), "ma": ma.reindex(IDX).fillna(False),
                        "vol": vol.reindex(IDX).fillna(False)})
    out["combo"] = out["opt"] | out["ma"] | out["vol"]      # 任一解除
    iv_pct252 = o["iv"].rolling(252).rank(pct=True)
    opt_p = (o["z_skew"] < 0.5) & (iv_pct252 < 0.5)
    out["optp"] = opt_p.reindex(IDX).fillna(False)
    out["all3p"] = opt_p.reindex(IDX).fillna(False) & out["ma"] & out["vol"]
    out["combo2"] = out["opt"] & out["ma"]                   # 期权+均线同日解除
    out["all3"] = out["opt"] & out["ma"] & out["vol"]        # 三者同时安全
    return out

def ma_pair(a):
    p = PX[a]; return p.rolling(10).mean(), p.rolling(20).mean()

MA = {a: ma_pair(a) for a in OPT_MAP}
GROWTH = {"cyb", "kc50"}; VALUE = {"hs300", "zz500"}
POOL = ["kc50", "cyb", "zz500", "hs300"]
REL = _release_series()

def oill_score(dt):
    """原油：美国通胀上行 + 就业稳 → 加；就业恶化 → 减"""
    r = MAC.loc[dt] if dt in MAC.index else None
    if r is None or pd.isna(r.cpi_yoy): return 0.0
    s = 0.0
    if r.cpi_yoy > 0.03: s += 1.0
    if r.une_chg3 <= 0.0: s += 0.5
    if r.une_chg3 >= 0.3: s -= 1.5
    return float(np.clip(s, -1.5, 1.5))

def run(stock_cap=0.60, stock_base=0.50, mom_win=60, top_n=2, dd=0.015, dd2=0.030, band=0.03, cost=5.0, start=None,
        use_breadth=True, nas_cap=0.15, nas_indep=False, nas_ma=20, nas_half=0.5, cyb_free=False, cyb_br_free=False, cyb_risk_free=False, br_floor=0.0, rearm_days=0, leg_gate=False, floor_when_breadth=False, reset_mode="nav", reset_confirm=5, check_freq="D", sel_freq="M", use_risk=True,
        oil_base=0.12, oil_cap=0.18, breaker=True):
    cols = list(RET.columns); n = len(cols)
    w = np.zeros(n)
    for a, x in {"hs300": 0.15, "kc50": 0.10, "nasdaq": 0.15, "oil": oil_base,
                 "cn_bond10": 0.40, "us_bond": 0.20}.items():
        if a in cols: w[cols.index(a)] = x
    w = w / w.sum()
    ST = {a: 1.0 for a in OPT_MAP}
    nav = peak = 1.0; br = 1.0; month = None; nav_hist = [1.0]; prev_year = None
    a_nav = a_peak = 1.0; br_a = 1.0; rel_cnt = 0; off_days = 0
    sel_log = {}; trades = []; rr = []; trail = []
    IDX_RUN = IDX if start is None else IDX[IDX >= pd.Timestamp(start)]
    last_wk = None; last_sel_wk = None
    for i0, dt in enumerate(IDX_RUN[:-1]):
        wk = tuple(dt.isocalendar()[:2])
        is_check = (check_freq != "W") or (wk != last_wk)
        if is_check: last_wk = wk
        wk_sel = tuple(dt.isocalendar()[:2])
        do_sel = (wk_sel != last_sel_wk) if sel_freq == "W" else (dt.month != month)
        if do_sel:
            if sel_freq == "W": last_sel_wk = wk_sel
            if month is not None and dt.year != prev_year:
                if reset_mode == "nav": br = 1.0     # 年度重置只在净值复位模式下开闸
                peak = nav; nav_hist[:] = [nav]
            prev_year = dt.year
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
            if use_breadth:
                if breadth <= 0.25: S *= 0.25
                elif breadth <= 0.50: S *= 0.55
            o = float(np.clip(oil_base + 0.03 * oill_score(dt), 0.05, oil_cap))
            tgt_m = {a: 0.0 for a in cols}
            for a in top:
                tgt_m[a] = S / len(top)
            if nas_indep:
                tgt_m["nasdaq"] = nas_cap
            else:
                tgt_m["nasdaq"] = min(nas_cap, stock_cap - S) if S < stock_cap else 0.0
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
            if (cyb_free or cyb_risk_free) and a == "cyb":
                continue
            maf, mas = MA[a][0].get(dt, np.nan), MA[a][1].get(dt, np.nan)
            dead = (not np.isnan(maf)) and (not np.isnan(mas)) and maf < mas
            risk = bool(RS[a].get(dt, False))
            if is_check:
                ST[a] = (0.0 if risk else 0.5) if dead else (0.5 if risk else 1.0) if use_risk else 1.0
            j = cols.index(a)
            tgt[j] *= ST[a]
        # 股票腿上限
        a_idx = [cols.index(a) for a in (POOL + ["dividend"]) if a in cols]
        n_idx = [cols.index("nasdaq")]
        if nas_indep:
            # 纳指独立风控：自身 20 日均线，跌破减半、站上恢复（境内无纳指期权）
            p_n = PX["nasdaq"].loc[:dt]
            ma_n = float(p_n.tail(nas_ma).mean()) if len(p_n) >= nas_ma else np.nan
            below = (not np.isnan(ma_n)) and float(p_n.iloc[-1]) < ma_n
            tgt[n_idx[0]] *= (nas_half if below else 1.0)
        eq_idx = a_idx + n_idx
        if breaker:
            if leg_gate:
                # A 股腿自身回撤闸门（不含纳指/原油/债券）
                ret_a = float(sum(w[j] * RET.loc[dt].values[j] for j in a_idx))
                a_nav *= 1 + ret_a; a_peak = max(a_peak, a_nav)
                a_dd = a_nav / a_peak - 1
                if a_dd <= -0.10: br_a = 0.0
                elif a_dd <= -0.06: br_a = min(br_a, 0.4)
                elif a_dd >= -0.03: br_a = 1.0
            else:
                # 仅在广度转多时允许半仓底回补，否则维持原始闸门
                if floor_when_breadth:
                    p_ = None; above_n = 0
                    for a_ in POOL:
                        pp = PX[a_].loc[:dt]
                        if len(pp) > 200 and float(pp.iloc[-1]) > float(pp.tail(200).mean()): above_n += 1
                    b_now = above_n / len(POOL)
                    br_a = max(br, br_floor) if b_now > 0.5 else br
                else:
                    br_a = max(br, br_floor)      # A 股腿半仓底：不清零
            for j in a_idx + [cols.index("oil")]:
                if (cyb_free or cyb_br_free) and cols[j] == "cyb":
                    continue
                tgt[j] *= (br_a if cols[j] != "oil" else br)
        es = sum(tgt[j] for j in eq_idx)
        if es > stock_cap:                      # 超上限先压 A 股，再压纳指
            over = es - stock_cap
            a_sum = sum(tgt[j] for j in a_idx)
            cut_a = min(over, a_sum)
            if a_sum > 0:
                k_a = (a_sum - cut_a) / a_sum
                for j in a_idx: tgt[j] *= k_a
            over -= cut_a
            if over > 0 and tgt[n_idx[0]] > 0:
                tgt[n_idx[0]] = max(tgt[n_idx[0]] - over, 0.0)
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
        band_eff = band if is_check else 1.0
        for j in range(n):
            if abs(w[j] - tgt[j]) > band_eff: trade[j] = tgt[j] - w[j]
        c_ = 0.0
        if np.abs(trade).sum() > 1e-6:
            w = w + trade; c_ = float(np.abs(trade).sum()) * cost / 1e4
            trades.append((str(dt)[:10], [(cols[j], round(trade[j], 3)) for j in range(n) if abs(trade[j]) > 0.005]))
        pnl = float((w * RET.loc[dt].values).sum()); ret = pnl - c_
        nav *= 1 + ret; peak = max(peak, nav); dcur = nav / peak - 1
        if breaker and is_check:
            if dcur <= -dd2: br = 0.0
            elif dcur <= -dd: br = min(br, 0.4)
            elif dcur >= -dd / 2 and reset_mode == "nav": br = 1.0
            # 重置条件：净值创 60 日新高即解除熔断（避免长期锁死）
            if reset_mode != "nav":
                sig = bool(REL[reset_mode].get(dt, False))
                if sig and br < 1.0:
                    rel_cnt += 1
                    br = 1.0 if rel_cnt >= reset_confirm else max(br, 0.4)
                elif not sig:
                    rel_cnt = 0
            if reset_mode == "nav" and br < 1.0 and len(nav_hist) >= 60 and nav >= max(nav_hist[-60:]):
                br = 1.0
            if br < 1.0 and rearm_days > 0:
                off_days += 1
                if off_days >= rearm_days: br = 1.0; off_days = 0
            elif br >= 1.0:
                off_days = 0
        if pnl > -1: w = w * (1 + RET.loc[dt].values) / (1 + pnl)
        if w.sum() > 1: w = w / w.sum()
        _es = sum(w[j] for j in eq_idx)
        if _es > stock_cap:                     # 股票腿硬上限：先压 A 股，再压纳指
            over = _es - stock_cap
            a_sum = sum(w[j] for j in a_idx)
            cut_a = min(over, a_sum)
            if a_sum > 0:
                k_a = (a_sum - cut_a) / a_sum
                for j in a_idx:
                    cut = w[j] * (1 - k_a); w[j] -= cut; w[cols.index("cn_bond10")] += cut
                over -= cut_a
            if over > 0 and w[n_idx[0]] > 0:
                cut = min(over, w[n_idx[0]]); w[n_idx[0]] -= cut; w[cols.index("cn_bond10")] += cut
        rr.append(ret); nav_hist.append(nav)
        trail.append(dict(date=str(dt)[:10], ret=ret, eq=float(sum(w[j] for j in eq_idx)),
                          oil=float(w[cols.index("oil")]), w_nas=float(w[cols.index("nasdaq")]), w_cyb=float(w[cols.index("cyb")]), w_kc50=float(w[cols.index("kc50")]), w_zz500=float(w[cols.index("zz500")]), w_hs300=float(w[cols.index("hs300")]), cn=float(w[cols.index("cn_bond10")]),
                          us=float(w[cols.index("us_bond")]), tot=float(w.sum()), br=br,
                          tgt_hs300=float(tgt[cols.index("hs300")]),
                          tgt_zz500=float(tgt[cols.index("zz500")]),
                          tgt_cyb=float(tgt[cols.index("cyb")]),
                          tgt_kc50=float(tgt[cols.index("kc50")]),
                          tgt_nasdaq=float(tgt[cols.index("nasdaq")]),
                          tgt_oil=float(tgt[cols.index("oil")]),
                          tgt_cn=float(tgt[cols.index("cn_bond10")]),
                          tgt_us=float(tgt[cols.index("us_bond")])))
    r_ = np.array(rr); nv = np.cumprod(1 + r_); ddv = nv / np.maximum.accumulate(nv) - 1
    vol = r_.std(ddof=1) * np.sqrt(252)
    yrs = len(r_) / 252
    m = dict(total=float(nv[-1] - 1), cagr=float(nv[-1] ** (1 / yrs) - 1), maxdd=float(ddv.min()),
             vol=vol, sharpe=float((nv[-1] ** (1 / yrs) - 1) / vol), n_trades=len(trades))
    return m, pd.DataFrame(trail), sel_log, trades
