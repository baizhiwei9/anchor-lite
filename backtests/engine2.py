"""Anchor-Lite v2：可调波动预算 + 日频回撤熔断 + 商品集中度上限 + 期权尾部对冲
所有决策只用当日及以前信息，权重作用于次日收益。"""
import os, sys, json
import numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS = ["kc50", "hs300", "oil", "nonferrous", "t10"]
LABELS = ["低波趋势", "通胀交易", "高波危机", "震荡未知"]
TGT = np.array([
    [0.35, 0.30, 0.10, 0.10, 0.00],   # 低波趋势
    [0.08, 0.10, 0.30, 0.28, -0.35],  # 通胀交易
    [-0.10, -0.10, 0.05, 0.05, 0.55], # 高波危机
    [0.08, 0.10, 0.08, 0.05, 0.15],   # 震荡未知
])
TGT_SV = np.array([-0.15, -0.05, 0.0, 0.0])

def load():
    probs = pd.read_csv(os.path.join(BASE, "probs_filtered.csv"), index_col=0, parse_dates=True)
    rets = pd.read_csv(os.path.join(BASE, "returns.csv"), index_col=0, parse_dates=True)
    rets["kc50"] = rets["kc50"].fillna(rets["hs300"])
    rets["_kc50_px"] = rets["_kc50_px"].fillna(rets["_hs300_px"])
    rets["_rv20"] = rets["_rv20"].fillna(rets["_rv20_cn"] / 100.0)
    return probs, rets

class Ctx:
    """预先算好所有逐日输入，循环内只做矩阵运算"""
    def __init__(self, probs, rets):
        idx = [d for d in rets.index if d in probs.index]
        self.dates = pd.DatetimeIndex(idx)
        r = rets.loc[self.dates]
        self.P = probs.loc[self.dates][LABELS].values
        self.R = r[ASSETS].values
        self.n = len(idx)
        # 次日收益：ret_next[t] = R[t+1]
        self.Rn = np.vstack([self.R[1:], np.full((1, len(ASSETS)), np.nan)])
        # 60 日滚动年化协方差
        cov = np.full((self.n, len(ASSETS), len(ASSETS)), np.nan)
        Rf = pd.DataFrame(self.R, columns=ASSETS)
        for t in range(20, self.n):
            lo = max(0, t - 59)
            cov[t] = Rf.iloc[lo:t + 1].cov().values * 252
        for t in range(0, 20):
            cov[t] = cov[20]
        self.cov = cov
        # 趋势确认度
        px = {a: r["_" + {"kc50": "kc50", "hs300": "hs300", "oil": "oil",
                          "nonferrous": "nf", "t10": "t10"}[a] + "_px"].values for a in ASSETS}
        self.trend = np.zeros((self.n, len(ASSETS)))
        for j, a in enumerate(ASSETS):
            s = px[a]
            for t in range(120, self.n):
                sig = []
                for w in (20, 60, 120):
                    base = s[t - w]
                    sig.append(np.sign(s[t] / base - 1) if base and not np.isnan(base) else 0)
                self.trend[t, j] = np.mean(sig)
        self.ma60_ok = {}
        for j, a in enumerate(ASSETS):
            s_ = px[a]
            ma = pd.Series(s_).rolling(60).mean().values
            self.ma60_ok[a] = (s_ > ma)  # True = 价在 60 日均线上方
        self.qvix = r["_qvix"].values
        self.rv20 = r["_rv20"].values
        # 空波动率近似收益（次日）
        sv = np.zeros(self.n)
        hs_next = np.abs(self.Rn[:, 1])
        for t in range(self.n - 1):
            iv = self.qvix[t] / 100.0 if not np.isnan(self.qvix[t]) else 0.20
            rv = self.rv20[t] if not np.isnan(self.rv20[t]) else 0.18
            carry = 0.5 * (iv ** 2 - rv ** 2) / 252.0
            jump = -3.0 * max(0.0, (hs_next[t] if not np.isnan(hs_next[t]) else 0) - 0.02)
            sv[t] = carry + jump
        self.sv = sv
        self.month = self.dates.to_period("M")

def run(ctx, start, end, vol_target=0.09, max_lev=1.0, dd_trigger=None, dd_cut=0.5,
        comm_cap=None, hedge_ratio=0.0, hedge_cost_lo=0.0045, hedge_cost_hi=0.009,
        net_cap=0.55, gross_cap=1.10, sv_max=0.15, cost_bp=5.0, mult=1.0,
        comm_ma_gate=False, fast_x=None, fast_cut=0.5):
    i0 = ctx.dates.searchsorted(pd.Timestamp(start))
    i1 = ctx.dates.searchsorted(pd.Timestamp(end))
    w = np.zeros(len(ASSETS)); w_sv = 0.0
    prev_w = np.zeros(len(ASSETS)); prev_sv = 0.0
    nav = 1.0; peak = 1.0; breaker = 1.0
    rows = []
    month_ret = 1.0; month_start = ctx.month[i0]
    for t in range(i0, i1):
        if ctx.month[t] != month_start:
            month_start = ctx.month[t]; month_ret = 1.0
        p = ctx.P[t]
        tgt = p @ TGT
        tgt_sv = float(p @ TGT_SV)
        # 空波动率闸门
        qv = ctx.qvix[t] if not np.isnan(ctx.qvix[t]) else 20.0
        p_low = p[0]
        sv_cap = 0.0
        if p_low >= 0.45 and qv <= 20:
            sv_cap = -sv_max * min(1.0, (22.0 - qv) / 6.0) * min(1.0, p_low / 0.5)
        tgt_sv = max(tgt_sv, sv_cap) if sv_cap < 0 else 0.0
        # 商品 60 日均线闸门（只用当日及以前价格）
        if comm_ma_gate:
            for j, a in ((2, "oil"), (3, "nonferrous")):
                if not bool(ctx.ma60_ok[a][t]) and tgt[j] > 0:
                    tgt[j] *= 0.25
            if bool(ctx.ma60_ok["t10"][t]) and tgt[4] < 0:
                tgt[4] *= 0.25
        # 渐进建仓 / 快速减仓
        tr = ctx.trend[t]
        for j in range(len(ASSETS)):
            same = (np.sign(tgt[j]) == np.sign(tr[j])) and abs(tr[j]) > 0
            cutting = abs(tgt[j]) < abs(w[j]) or np.sign(tgt[j]) != np.sign(w[j])
            k = 0.45 if cutting else (0.10 + 0.25 * abs(tr[j]) if same else 0.15)
            w[j] += k * (tgt[j] - w[j])
        w_sv += (0.45 if abs(tgt_sv) < abs(w_sv) else 0.20) * (tgt_sv - w_sv)
        # 商品集中度上限
        if comm_cap is not None:
            c = w[2] + w[3]
            if abs(c) > comm_cap:
                f = comm_cap / abs(c); w[2] *= f; w[3] *= f
        # 风险预算
        sig = float(np.sqrt(max(w @ ctx.cov[t] @ w, 1e-8)))
        scale = np.clip((vol_target * mult) / sig, 0.20, max_lev)
        net = w[0] + w[1] + w[2] + w[3]
        gross = np.abs(w).sum() + abs(w_sv)
        if net > net_cap: scale *= net_cap / net
        if gross > gross_cap: scale *= gross_cap / gross
        wf = w * scale * breaker
        wsv = w_sv * scale * breaker
        # 快速熔断：近 5 日累计亏损超阈值立即降仓
        if fast_x is not None:
            past = [rows[i][1] for i in range(max(0, len(rows) - 5), len(rows))]
            if len(past) == 5 and sum(past) < -fast_x:
                breaker = min(breaker, 1.0 - fast_cut)
            elif len(past) >= 5 and sum(past) > 0:
                breaker = 1.0 if dd_trigger is None else breaker
        # 回撤熔断（用当日净值状态）
        if dd_trigger is not None:
            dd_now = nav / peak - 1
            if dd_now <= -dd_trigger:
                breaker = 1.0 - dd_cut
            elif dd_now >= -dd_trigger / 2:
                breaker = 1.0
        # 次日损益
        rn = ctx.Rn[t]
        pnl = float(np.nansum(wf * rn)) + wsv * ctx.sv[t]
        turn = np.abs(wf - prev_w).sum() + abs(wsv - prev_sv)
        cost = turn * cost_bp / 10000.0
        hcost = 0.0; hpay = 0.0
        if hedge_ratio > 0:
            days_in_month = 21.0
            rate = hedge_cost_lo if qv < 20 else hedge_cost_hi
            hcost = hedge_ratio * rate / days_in_month
        ret = pnl - cost - hcost
        month_ret *= (1 + ret)
        nav *= (1 + ret)
        peak = max(peak, nav)
        # 月度尾部对冲赔付（月末结算）
        if hedge_ratio > 0 and (t == i1 - 1 or ctx.month[t] != ctx.month[min(t + 1, ctx.n - 1)]):
            mdd = month_ret - 1
            if mdd < -0.03:
                hpay = hedge_ratio * min(-mdd - 0.03, 0.10)
                nav *= (1 + hpay); peak = max(peak, nav); ret += hpay
            month_ret = 1.0
        rows.append((ctx.dates[t], ret, nav, wf[0], wf[1], wf[2], wf[3], wf[4], wsv, sig, scale, breaker, p_low, p[1], p[2], qv, hcost, hpay))
        prev_w = wf; prev_sv = wsv
    df = pd.DataFrame(rows, columns=["date", "ret", "nav", "w_kc50", "w_hs300", "w_oil", "w_nf",
                                     "w_t10", "w_sv", "sig", "scale", "breaker", "p_low", "p_inf",
                                     "p_cri", "qvix", "hedge_cost", "hedge_pay"]).set_index("date")
    return df

def metrics(df):
    if df is None or len(df) == 0:
        return None
    r = df["ret"].values
    nav = np.cumprod(1 + r)
    dd = nav / np.maximum.accumulate(nav) - 1
    n = len(r)
    ann = nav[-1] ** (252 / n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    mret = (1 + pd.Series(r, index=df.index)).resample("ME").prod() - 1
    mdd = (1 + mret).cumprod(); mdd = mdd / mdd.cummax() - 1
    return dict(total=float(nav[-1] - 1), cagr=float(ann), vol=float(vol),
                maxdd=float(dd.min()), sharpe=float(ann / vol) if vol > 0 else 0.0,
                calmar=float(ann / abs(dd.min())) if dd.min() < 0 else np.nan,
                worst_month=float(mret.min()), month_dd=float(mdd.min()),
                n=int(n), maxdd_date=str(df.index[int(np.argmin(dd))].date()),
                avg_net=float((df[["w_kc50", "w_hs300", "w_oil", "w_nf"]].sum(axis=1)).mean()),
                avg_gross=float(df[["w_kc50", "w_hs300", "w_oil", "w_nf", "w_t10", "w_sv"]].abs().sum(axis=1).mean()),
                turnover=float(df[["w_kc50", "w_hs300", "w_oil", "w_nf", "w_t10", "w_sv"]].diff().abs().sum(axis=1).mean()),
                sv_days=int((df["w_sv"] < -0.001).sum()),
                hedge_cost=float(df["hedge_cost"].sum()), hedge_pay=float(df["hedge_pay"].sum()))

if __name__ == "__main__":
    probs, rets = load()
    ctx = Ctx(probs, rets)
    d = run(ctx, "2024-01-02", "2026-04-14")
    print(json.dumps(metrics(d), ensure_ascii=False, indent=1))
    print(d.tail(3).round(3).to_string())