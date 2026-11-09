"""Anchor-Lite 组合引擎：概率加权 → 趋势确认渐进建仓 → 风险预算/净敞口/空波动率闸门 → 压力测试反馈"""
import os, sys, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS = ["kc50", "hs300", "oil", "nonferrous", "t10", "sv"]

# 各 Regime 下的目标敞口（占净值，空头为负；sv=做空波动率的 vega 名义敞口）
TGT = {
    "低波趋势": dict(kc50=0.35, hs300=0.30, oil=0.10, nonferrous=0.10, t10=0.00, sv=-0.15),
    "通胀交易": dict(kc50=0.08, hs300=0.10, oil=0.30, nonferrous=0.28, t10=-0.35, sv=-0.05),
    "高波危机": dict(kc50=-0.10, hs300=-0.10, oil=0.05, nonferrous=0.05, t10=0.55, sv=0.00),
    "震荡未知": dict(kc50=0.08, hs300=0.10, oil=0.08, nonferrous=0.05, t10=0.15, sv=0.00),
}
LABELS = list(TGT.keys())
TARGET_VOL = 0.09
COST = 0.0005  # 单边 5bp（ETF/期货混合近似）

def load_inputs():
    probs = pd.read_csv(os.path.join(BASE, "probs_filtered.csv"), index_col=0, parse_dates=True)
    rets = pd.read_csv(os.path.join(BASE, "returns.csv"), index_col=0, parse_dates=True)
    # 科创50ETF 2020-11 上市：此前用沪深300ETF 代理（工程假设，压力测试窗口需要）
    rets["kc50"] = rets["kc50"].fillna(rets["hs300"])
    rets["_kc50_px"] = rets["_kc50_px"].fillna(rets["_hs300_px"])
    rets["_rv20"] = rets["_rv20"].fillna(rets["_rv20_cn"] / 100.0)
    return probs, rets

def trend_conf(px, t):
    """多周期动量方向一致度：三个窗口同向 = 1，全反向 = -1"""
    s = px.loc[:t]
    if len(s) < 125:
        return 0.0
    signs = []
    for n in (20, 60, 120):
        signs.append(np.sign(s.iloc[-1] / s.iloc[-1 - n] - 1))
    return float(np.mean(signs))

def run_engine(probs, rets, start, end, mode="anchor", mult=1.0, vol_target=TARGET_VOL):
    dates = rets.loc[start:end].index
    dates = [d for d in dates if d in probs.index]
    w = {a: 0.0 for a in ASSETS}
    hist = []
    # 资产价格（用于趋势确认）
    px = {"kc50": rets["_kc50_px"], "hs300": rets["_hs300_px"], "oil": rets["_oil_px"],
          "nonferrous": rets["_nf_px"], "t10": rets["_t10_px"]}
    for i, d in enumerate(dates[:-1]):
        nxt = dates[i + 1]
        p = probs.loc[d]
        tgt = {a: float(sum(p[l] * TGT[l][a] for l in LABELS)) for a in ASSETS}

        # 空波动率闸门
        qv = float(rets.loc[d, "_qvix"]) if not np.isnan(rets.loc[d, "_qvix"]) else 20.0
        p_low = float(p["低波趋势"])
        sv_cap = 0.0
        if p_low >= 0.45 and qv <= 20:
            sv_cap = -0.15 * min(1.0, (22.0 - qv) / 6.0) * min(1.0, p_low / 0.5)
        tgt["sv"] = min(tgt.get("sv", 0.0), sv_cap) if sv_cap < 0 else 0.0

        # 渐进建仓 / 快速减仓
        for a in ASSETS:
            if mode == "switch":  # 硬切换对照：直接跳到 argmax regime 的目标
                continue
            c = trend_conf(px[a], d) if a in px else 0.0
            same = (np.sign(tgt[a]) == np.sign(c)) and abs(c) > 0
            cutting = abs(tgt[a]) < abs(w[a]) or np.sign(tgt[a]) != np.sign(w[a])
            k = 0.45 if cutting else (0.10 + 0.25 * abs(c) if same else 0.15)
            w[a] = w[a] + k * (tgt[a] - w[a])
        if mode == "switch":
            am = p.idxmax()
            w = {a: TGT[am][a] for a in ASSETS}

        # 风险预算：波动率目标 + 净/总敞口上限
        r_hist = rets[[a for a in ASSETS if a != "sv"]].loc[:d].tail(60)
        wv = np.array([w[a] for a in r_hist.columns])
        cov = r_hist.cov().values * 252
        sig = float(np.sqrt(max(wv @ cov @ wv, 1e-8)))
        scale = np.clip((vol_target * mult) / sig, 0.25, 1.0) if mode == "anchor" else 1.0
        net = w["kc50"] + w["hs300"] + w["oil"] + w["nonferrous"]
        gross = sum(abs(w[a]) for a in ASSETS)
        if mode == "anchor":
            if net > 0.55:
                scale *= 0.55 / net
            if gross > 1.10:
                scale *= 1.10 / gross
        w = {a: w[a] * scale for a in ASSETS}

        # 次日损益
        pnl = {a: 0.0 for a in ASSETS}
        for a in ASSETS:
            if a == "sv":
                iv = float(rets.loc[d, "_qvix"]) / 100.0
                rv = float(rets.loc[d, "_rv20"])
                carry = 0.5 * (iv ** 2 - rv ** 2) / 252.0
                move = abs(float(rets.loc[nxt, "hs300"])) if not np.isnan(rets.loc[nxt, "hs300"]) else 0.0
                jump = -3.0 * max(0.0, move - 0.02)
                r_sv = carry + jump
                pnl[a] = w[a] * r_sv
            else:
                r = float(rets.loc[nxt, a]) if not np.isnan(rets.loc[nxt, a]) else 0.0
                pnl[a] = w[a] * r
        port = sum(pnl.values())
        hist.append(dict(date=nxt, ret=port, **{f"w_{a}": w[a] for a in ASSETS},
                         **{f"c_{a}": pnl[a] for a in ASSETS}, p_low=p_low, p_inf=float(p["通胀交易"]),
                         p_cri=float(p["高波危机"]), qvix=qv, sig=sig, scale=scale))
    df = pd.DataFrame(hist).set_index("date")
    df["nav"] = (1 + df["ret"]).cumprod()
    return df

def metrics(df):
    r = df["ret"]
    nav = (1 + r).cumprod()
    dd = nav / nav.cummax() - 1
    n = len(r)
    ann = nav.iloc[-1] ** (252 / max(n, 1)) - 1 if n else 0
    vol = r.std() * np.sqrt(252)
    return dict(total=float(nav.iloc[-1] - 1), annual=float(ann), vol=float(vol),
                maxdd=float(dd.min()), sharpe=float(ann / vol) if vol > 0 else 0.0,
                maxdd_date=str(dd.idxmin().date()) if n else "", n=int(n))

def buy_hold(rets, start, end, col):
    r = rets.loc[start:end, col].dropna()
    nav = (1 + r).cumprod()
    dd = nav / nav.cummax() - 1
    return dict(total=float(nav.iloc[-1] - 1), vol=float(r.std() * np.sqrt(252)),
                maxdd=float(dd.min()), n=int(len(r)),
                annual=float(nav.iloc[-1] ** (252 / len(r)) - 1))

def main():
    probs, rets = load_inputs()
    out = {}
    # 压力测试（先用 base 引擎跑，得到回撤，再决定降仓系数）
    stress = {"2020-03": ("2020-02-03", "2020-04-30"), "2022": ("2022-01-04", "2022-10-31"),
              "2025-04": ("2025-03-03", "2025-05-30")}
    base = {}
    for k, (s, e) in stress.items():
        d = run_engine(probs, rets, s, e, mult=1.0)
        base[k] = metrics(d)
    worst = min(v["maxdd"] for v in base.values())
    mult = 0.8 if worst < -0.08 else 1.0
    out["stress_base"] = base
    out["risk_mult"] = mult

    # 主区间
    main_w = ("2024-01-02", "2026-04-14")
    res = {}
    for name, mode in [("anchor", "anchor"), ("switch", "switch"), ("norisk", "norisk")]:
        d = run_engine(probs, rets, *main_w, mode=mode, mult=mult)
        res[name] = metrics(d)
        if name == "anchor":
            d.to_csv(os.path.join(BASE, "engine_main.csv"))
            anchor_df = d
    out["main"] = res
    out["buyhold_hs300"] = buy_hold(rets, *main_w, "hs300")
    out["buyhold_kc50"] = buy_hold(rets, *main_w, "kc50")
    out["stress_with_mult"] = {k: metrics(run_engine(probs, rets, s, e, mult=mult)) for k, (s, e) in stress.items()}

    # 2026 年至今（模拟日所在年）
    d26 = run_engine(probs, rets, "2026-01-02", "2026-04-14", mult=mult)
    out["ytd2026"] = metrics(d26)
    d26.to_csv(os.path.join(BASE, "engine_2026.csv"))

    # 当日目标仓位与风险贡献（2026-04-14）
    p = probs.loc["2026-04-14"]
    tgt = {a: float(sum(p[l] * TGT[l][a] for l in LABELS)) for a in ASSETS}
    last = anchor_df.iloc[-1]
    w_now = {a: float(last[f"w_{a}"]) for a in ASSETS}
    rets_ = rets[["kc50", "hs300", "oil", "nonferrous", "t10"]].loc[:"2026-04-14"].tail(120)
    cov = rets_.cov().values * 252
    wv = np.array([w_now[a] for a in rets_.columns])
    sig_p = float(np.sqrt(max(wv @ cov @ wv, 1e-9)))
    mrc = cov @ wv / sig_p
    rc = {a: float(w_now[a] * mrc[i] / sig_p) for i, a in enumerate(rets_.columns)}
    out["snapshot"] = dict(date="2026-04-14", probs={k: float(v) for k, v in p.items()},
                           target_blend={k: round(v, 4) for k, v in tgt.items()},
                           weights={k: round(v, 4) for k, v in w_now.items()},
                           port_vol=sig_p, risk_contrib={k: round(v, 4) for k, v in rc.items()})

    # 原油渐进路径（周度）
    oil = anchor_df[["w_oil", "w_kc50", "w_hs300", "w_nonferrous", "w_t10", "w_sv", "p_inf", "qvix"]]
    oil_w = oil.resample("W").last().round(4)
    out["oil_path"] = [dict(date=str(i.date()), **{c: float(v) for c, v in row.items()})
                       for i, row in oil_w.iterrows()]
    # 归因
    contrib = {c: float(anchor_df[f"c_{c}"].sum()) for c in ASSETS}
    out["attribution_main"] = contrib
    out["attribution_2026"] = {c: float(d26[f"c_{c}"].sum()) for c in ASSETS}
    out["engine_stats"] = dict(n_days=int(len(anchor_df)),
                               avg_net=float((anchor_df[["w_kc50", "w_hs300", "w_oil", "w_nonferrous"]].sum(1)).mean()),
                               avg_turnover=float(anchor_df[[f"w_{a}" for a in ASSETS]].diff().abs().sum(1).mean()),
                               avg_sv=float(anchor_df["w_sv"].mean()),
                               sv_days_on=int((anchor_df["w_sv"] < -0.001).sum()))
    with open(os.path.join(BASE, "engine_out.json"), "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: out[k] for k in ["main", "buyhold_hs300", "ytd2026", "snapshot", "risk_mult", "engine_stats"]},
                     ensure_ascii=False, indent=1, default=str))
    print("\nstress base:", json.dumps(base, ensure_ascii=False, default=str))
    print("\nattribution 2026:", out["attribution_2026"])

if __name__ == "__main__":
    main()