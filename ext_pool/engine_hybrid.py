#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扩展池 v2：核心 MLP/CNN 信号（13 池）+ 卫星趋势（纳指/原油/黄金/中债/低波/红利/科创）+ 宏观闸门，
在账户级最大回撤 <=5% 约束下最大化收益。

口径同日频、T+1、5bp、1x、只多+空仓、regime MA200、波动率目标。
"""
import os
import json
import numpy as np
import pandas as pd

WS = "/home/work/dumate/5eaa88f7d2d5403f9ae855f5fcfd7f34/workspace"
A = os.path.join(WS, "projects/anchor-lite/tsmom/assets")
L = os.path.join(WS, "ses_gffe5e44ae14d3ffe2aO6fvWHPz1bbL/anchor_lite/data_long")
OUT = os.path.join(WS, "ses_gffe5e424df3c0ffepkEhgMkuPETfQj/ext_pool/out")
os.makedirs(OUT, exist_ok=True)

POOL13 = ["sh510300", "sh510500", "sh510050", "sz159915", "sz159949", "sh512880", "sh512660",
          "sh512010", "sh513100", "sh513500", "sz159941", "sh513030", "sz159920"]
SATELLITE = ["sh501018", "sh518880", "sh511260", "sh512890", "sh510880", "sh588000"]
COST_BP, CASH = 5.0, 0.02
START, END = "2022-01-04", "2026-09-22"


def load_close(path):
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    col = "close" if "close" in df.columns else df.columns[0]
    s = pd.to_numeric(df[col], errors="coerce")
    return s[~s.index.duplicated(keep="last")]


def build_panel():
    pool = pd.read_csv(os.path.join(A, "pool_model.csv"), index_col=0, parse_dates=True).sort_index()
    pool = pool.apply(pd.to_numeric, errors="coerce")
    ext = {"sh501018": load_close(os.path.join(L, "oil.csv")),
           "sh518880": load_close(os.path.join(L, "gold.csv")),
           "sh511260": load_close(os.path.join(L, "cn_bond10.csv")),
           "sh512890": load_close(os.path.join(L, "lowvol.csv")),
           "sh588000": load_close(os.path.join(L, "kc50.csv")),
           "sh511010": load_close(os.path.join(L, "cn_bond.csv"))}
    panel = pd.concat([pool, pd.DataFrame(ext)], axis=1).sort_index()
    panel = panel[~panel.index.duplicated(keep="last")].ffill(limit=3)
    panel = panel.loc[(panel.index >= START) & (panel.index <= END)]
    return panel.loc[:, panel.notna().mean() > 0.6]


def load_macro(idx):
    m = pd.read_csv(os.path.join(L, "macro_long.csv"), parse_dates=["date"]).set_index("date").sort_index()
    m = m[~m.index.duplicated(keep="last")].reindex(idx).ffill(limit=40)
    qv = load_close(os.path.join(L, "qvix300.csv")).reindex(idx).ffill(limit=5)
    return pd.DataFrame({"pmi": m.get("pmi"), "cfnai": m.get("cfnai"),
                         "us_rec": m.get("us_rec"), "qvix": qv}, index=idx)


def ewm_std(s, span=60):
    s = np.asarray(pd.Series(s).ewm(span=span, adjust=False).std().values).copy()
    rs = pd.Series(s).rolling(20, min_periods=5).mean().values
    nan = ~np.isfinite(s)
    s[nan] = rs[nan]
    s[~np.isfinite(s)] = np.nanstd(s) if np.isfinite(np.nanstd(s)) else 0.0
    return s


def core_signal(idx):
    z = np.load(os.path.join(A, "sig_SLP.npz"), allow_pickle=True)
    dates = pd.DatetimeIndex(pd.to_datetime([str(d) for d in z["dates"]]))
    sig = {}
    for m in ("MLP", "CNN"):
        sig[m] = np.load(os.path.join(A, f"sig_{m}.npz"), allow_pickle=True)["s"]
    mix = 0.7 * sig["MLP"] + 0.3 * sig["CNN"]
    w = pd.DataFrame(np.maximum(mix, 0), index=dates, columns=POOL13).reindex(idx).fillna(0)
    return w


def sat_weights(panel, ma=60):
    """卫星：价格 > MA(60) 时按波动率倒数加权，否则空仓。"""
    sat = [c for c in SATELLITE if c in panel.columns]
    px = panel[sat]
    above = (px > px.rolling(ma, min_periods=ma // 2).mean()).astype(float)
    vol = px.pct_change().rolling(60, min_periods=20).std()
    inv = (1.0 / vol.replace(0, np.nan)).clip(upper=50)
    w = (above * inv).fillna(0)
    return w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)


def run(panel, macro, core_w, sat_w, alpha=0.6, vol_target=0.10, cap=1.0,
        dd_trig=0.02, dd_cut=0.0, regime_ma=200, use_macro=True):
    idx = panel.index
    r = panel.pct_change().fillna(0.0)
    cols = list(panel.columns)
    core = core_w.reindex(columns=[c for c in POOL13 if c in cols]).fillna(0)
    core = core.div(core.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    sat = sat_w.reindex(columns=cols).fillna(0)

    w = pd.DataFrame(index=idx, columns=cols, dtype=float).fillna(0.0)
    w.loc[:, core.columns] = alpha * core.values
    w.loc[:, sat.columns] += (1 - alpha) * sat.values
    w = w.fillna(0.0)

    core_ret = r[[c for c in core.columns]].mean(axis=1)
    ew = (1 + core_ret).cumprod()
    regime = (ew > ew.rolling(regime_ma, min_periods=100).mean()).astype(float).shift(1).fillna(1.0)
    gate = pd.Series(1.0, index=idx)
    if use_macro:
        pmi, cfnai, us_rec, qv = macro["pmi"], macro["cfnai"], macro["us_rec"], macro["qvix"]
        qthr = np.nanpercentile(qv.values, 90) if np.isfinite(qv.values).any() else np.inf
        gate = pd.Series(1.0, index=idx)
        gate = gate * np.where(pmi < 50, 0.80, 1.0)
        gate = gate * np.where(cfnai < 0, 0.85, 1.0)
        gate = gate * np.where(us_rec > 0, 0.85, 1.0)
        gate = gate * np.where(qv > qthr, 0.75, 1.0)
        gate = pd.Series(gate, index=idx).shift(1).fillna(1.0)

    w_sig = w.shift(1).fillna(0.0)          # T+1 生效
    p = (w_sig * r).sum(axis=1) * regime * gate
    sd = ewm_std(p, 60)
    vscale = pd.Series(np.minimum(vol_target / np.sqrt(252) / (sd + 1e-9), cap), index=idx)
    p = p * vscale

    nav, peak, halted = 1.0, 1.0, False
    out = np.zeros(len(p))
    for i, v in enumerate(p.values):
        if i > 0:
            dd = nav / peak - 1
            if (not halted) and dd <= -dd_trig:
                halted = True
            elif halted and nav > peak * (1 - 0.5 * dd_trig):
                halted = False
            peak = max(peak, nav)
        out[i] = v * (dd_cut if halted else 1.0)
        nav *= (1 + out[i])

    wf = (w_sig.multiply(regime, axis=0).multiply(gate, axis=0).multiply(vscale, axis=0)).fillna(0)
    turn = wf.diff().abs().sum(axis=1).fillna(wf.iloc[0].abs().sum())
    net = pd.Series(out, index=idx) - turn * COST_BP / 10000.0
    return net, wf


def metrics(p):
    nav = (1 + p).cumprod()
    total = nav.iloc[-1] - 1
    ann = (1 + total) ** (252.0 / len(p)) - 1
    sharpe = p.mean() / p.std(ddof=1) * np.sqrt(252) if p.std(ddof=1) > 0 else 0
    dd = float((nav / nav.cummax() - 1).min())
    yearly = {str(y): float((1 + p[p.index.year == y]).prod() - 1) for y in sorted(set(p.index.year))}
    return dict(total=float(total), ann=float(ann), sharpe=float(sharpe), dd=dd,
                win=float((p > 0).mean()), yearly=yearly), nav


def main():
    panel = build_panel()
    idx = panel.index
    macro = load_macro(idx)
    cw = core_signal(idx)
    sw = sat_weights(panel)
    print(f"扩展池 {panel.shape[1]} 只 区间 {idx[0].date()}~{idx[-1].date()} {len(idx)} 日")
    print(f"核心信号覆盖 {cw.shape[1]} 只；卫星 {sw.shape[1]} 只：{list(sw.columns)}")

    grid, best = [], None
    for alpha in (0.0, 0.5, 0.8, 1.0):
        for vol in (0.05, 0.08, 0.10, 0.12, 0.15):
            for cap in (1.0, 1.5):
              for ddtrig in (0.02, 0.03, 0.05):
                net, wf = run(panel, macro, cw, sw, alpha=alpha, vol_target=vol, cap=cap, dd_trig=ddtrig)
                m, nav = metrics(net)
                m.update(dict(alpha=alpha, vol=vol, cap=cap, ddtrig=ddtrig, ok=bool(m["dd"] >= -0.05)))
                grid.append(m)
                if m["ok"] and (best is None or m["total"] > best["total"]):
                    best, best_net, best_nav = m, net, nav
    ok = [g for g in grid if g["ok"]]
    print(f"\n过 5% 约束: {len(ok)}/{len(grid)}；回撤范围 {min(g['dd'] for g in grid)*100:.1f}% ~ {max(g['dd'] for g in grid)*100:.1f}%")
    print("\n=== 5% 约束内 Top5 ===")
    for m in sorted(ok, key=lambda x: -x["total"])[:5]:
        print(f"  alpha={m['alpha']} vol={m['vol']} cap={m['cap']} ddtrig={m['ddtrig']} | 总{m['total']*100:+7.1f}% 年化{m['ann']*100:+6.1f}% "
              f"回撤{m['dd']*100:6.2f}% 夏普{m['sharpe']:+.2f} 胜率{m['win']*100:.0f}%")
    if best:
        print(f"\n最优: alpha={best['alpha']} vol={best['vol']} cap={best['cap']}")
        print(f"  总 {best['total']*100:+.1f}% 年化 {best['ann']*100:+.1f}% 回撤 {best['dd']*100:.2f}% 夏普 {best['sharpe']:.2f}")
        print("  逐年:", {k: round(v * 100, 1) for k, v in best["yearly"].items()})
        best_nav.to_frame("equity").to_csv(os.path.join(OUT, "nav_hybrid.csv"))
        best_net.to_frame("ret").to_csv(os.path.join(OUT, "ret_hybrid.csv"))
    json.dump({"n_assets": int(panel.shape[1]), "assets": list(panel.columns),
               "grid": sorted(grid, key=lambda x: -x["total"])[:60], "best": best},
              open(os.path.join(OUT, "results_hybrid.json"), "w"), indent=1, ensure_ascii=False)
    print("[done] HYBRID_DONE")


if __name__ == "__main__":
    main()