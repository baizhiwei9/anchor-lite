#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扩展池回测引擎：行业ETF + 纳指/原油/黄金/中债/美债因子 + 宏观闸门。

口径：日频、T 日信号 T+1 生效、单边 5bp、1x 无杠杆、只多+空仓、regime MA200、波动率目标。
约束：账户级全区间最大回撤 <= 5%（网格标定 vol_target / cap / K）。
数据：projects/anchor-lite/tsmom/assets/pool_model.csv + anchor_lite/data_long/*.csv + macro_long.csv
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

COST_BP = 5.0
CASH = 0.02
START, END = "2022-01-04", "2026-09-22"

# data_long 文件 -> 标的代码（close 列）
EXTRA = {"oil.csv": "sh501018", "gold.csv": "sh518880", "cn_bond10.csv": "sh511260",
         "lowvol.csv": "sh512890", "kc50.csv": "sh588000", "cn_bond.csv": "sh511010"}


def load_close(path, date_col="date"):
    df = pd.read_csv(path, parse_dates=[date_col]).set_index(date_col).sort_index()
    col = "close" if "close" in df.columns else df.columns[0]
    return pd.to_numeric(df[col], errors="coerce")


def build_panels():
    pool = pd.read_csv(os.path.join(A, "pool_model.csv"), index_col=0, parse_dates=True).sort_index()
    pool = pool.apply(pd.to_numeric, errors="coerce")
    ext = {}
    for fn, code in EXTRA.items():
        p = os.path.join(L, fn)
        if os.path.exists(p):
            ext[code] = load_close(p)
    if ext:
        extdf = pd.DataFrame(ext)
        panel = pd.concat([pool, extdf], axis=1).sort_index()
        panel = panel.loc[:, ~panel.columns.duplicated()]
    else:
        panel = pool
    panel = panel[~panel.index.duplicated(keep="last")]
    panel = panel.ffill(limit=3)
    panel = panel.loc[(panel.index >= START) & (panel.index <= END)]
    panel = panel.loc[:, panel.notna().mean() > 0.6]
    return panel


def load_macro(idx):
    m = pd.read_csv(os.path.join(L, "macro_long.csv"), parse_dates=["date"]).set_index("date").sort_index()
    m = m[~m.index.duplicated(keep="last")].reindex(idx).ffill(limit=40)
    qv = load_close(os.path.join(L, "qvix300.csv"))
    qv = qv[~qv.index.duplicated(keep="last")].reindex(idx).ffill(limit=5)
    out = pd.DataFrame(index=idx)
    out["pmi"] = m["pmi"] if "pmi" in m else np.nan
    out["cfnai"] = m["cfnai"] if "cfnai" in m else np.nan
    out["us_rec"] = m["us_rec"] if "us_rec" in m else np.nan
    out["qvix"] = qv
    return out


def ewm_std(s, span=60):
    s = np.asarray(pd.Series(s).ewm(span=span, adjust=False).std().values).copy()
    rs = pd.Series(s).rolling(20, min_periods=5).mean().values
    nan = ~np.isfinite(s)
    s[nan] = rs[nan]
    s[~np.isfinite(s)] = np.nanstd(s) if np.isfinite(np.nanstd(s)) else 0.0
    return s


def backtest(panel, macro, K=6, vol_target=0.12, cap=1.5, use_macro=True, dd_trig=0.03, dd_cut=0.4):
    idx = panel.index
    r = panel.pct_change().fillna(0.0).values
    N = r.shape[1]
    mom20 = panel.pct_change(20)
    mom60 = panel.pct_change(60)
    # 截面 z-score
    z = pd.DataFrame(index=idx)
    z20 = mom20.sub(mom20.mean(axis=1), axis=0).div(mom20.std(axis=1) + 1e-9, axis=0)
    z60 = mom60.sub(mom60.mean(axis=1), axis=0).div(mom60.std(axis=1) + 1e-9, axis=0)
    score = (0.5 * z20 + 0.5 * z60).fillna(-9)
    rank = np.argsort(-score.values, axis=1)
    w = np.zeros((len(idx), N))
    for i in range(len(idx)):
        w[i, rank[i, :K]] = 1.0 / K
    # T+1：信号移位
    w = np.vstack([np.zeros((1, N)), w[:-1]])

    ew = (1 + pd.DataFrame(r, index=idx).mean(axis=1)).cumprod()
    ma200 = pd.Series(ew).rolling(200, min_periods=100).mean()
    regime = (pd.Series(ew) > ma200).astype(float).shift(1).fillna(1.0).values

    gate = np.ones(len(idx))
    if use_macro:
        pmi = macro["pmi"].values
        cfnai = macro["cfnai"].values
        us_rec = macro["us_rec"].values
        qv = macro["qvix"].values
        qv_thr = np.nanpercentile(qv, 90) if np.isfinite(qv).any() else np.inf
        for i in range(len(idx)):
            g = 1.0
            if np.isfinite(pmi[i]) and pmi[i] < 50: g *= 0.75
            if np.isfinite(cfnai[i]) and cfnai[i] < 0: g *= 0.80
            if np.isfinite(us_rec[i]) and us_rec[i] > 0: g *= 0.80
            if np.isfinite(qv[i]) and qv[i] > qv_thr: g *= 0.70
            gate[i] = g
        gate = np.concatenate([[1.0], gate[:-1]])

    p = (w * r).sum(axis=1) * regime * gate
    sd = ewm_std(p, 60)
    vscale = np.minimum(vol_target / np.sqrt(252) / (sd + 1e-9), cap)
    p = p * vscale

    # 回撤熔断（基于 t-1 净值）
    nav, peak, halted = 1.0, 1.0, False
    out = np.zeros(len(p))
    for i in range(len(p)):
        if i > 0:
            dd = nav / peak - 1
            if (not halted) and dd <= -dd_trig:
                halted = True
            elif halted and nav > peak * (1 - dd_trig / 2):
                halted = False
            peak = max(peak, nav)
        out[i] = p[i] * (dd_cut if halted else 1.0)
        nav *= (1 + out[i])

    wf = w * regime[:, None] * gate[:, None] * vscale[:, None]
    turn = np.abs(np.diff(np.vstack([np.zeros((1, N)), wf]), axis=0)).sum(axis=1)
    net = out - turn * COST_BP / 10000.0
    return net, wf, pd.Series(net, index=idx)


def metrics(p, idx):
    nav = (1 + p).cumprod()
    total = nav.iloc[-1] - 1
    ann = (1 + total) ** (252.0 / len(p)) - 1
    sharpe = p.mean() / p.std(ddof=1) * np.sqrt(252) if p.std(ddof=1) > 0 else 0
    dd = float((nav / nav.cummax() - 1).min())
    win = float((p > 0).mean())
    yearly = {str(y): float((1 + p[p.index.year == y]).prod() - 1) for y in sorted(set(idx.year))}
    return dict(total=float(total), ann=float(ann), sharpe=float(sharpe), dd=dd, win=win, yearly=yearly), nav


def main():
    panel = build_panels()
    idx = panel.index
    macro = load_macro(idx)
    print(f"扩展池标的数={panel.shape[1]} 区间={idx[0].date()}~{idx[-1].date()} 天数={len(idx)}")
    print("标的:", list(panel.columns))

    rows, navs, best = [], {}, None
    # 基线：13 池 + MLP/CNN 信号（同引擎、同风控）
    grid = []
    for K in (4, 6, 8):
        for vol in (0.08, 0.10, 0.12, 0.15):
            for cap in (1.0, 1.5, 2.0):
                p, wf, ps = backtest(panel, macro, K=K, vol_target=vol, cap=cap, use_macro=True)
                m, nav = metrics(ps, idx)
                m.update(dict(K=K, vol=vol, cap=cap, ok=bool(m["dd"] >= -0.05)))
                grid.append(m)
                if m["ok"] and (best is None or m["total"] > best["total"]):
                    best = m; best_p = ps; best_nav = nav
    grid_sorted = sorted(grid, key=lambda x: -x["total"])
    print("\n=== 扩展池 5% 约束内 Top5 ===")
    for m in [g for g in grid_sorted if g["ok"]][:5]:
        print(f"  K={m['K']} vol={m['vol']} cap={m['cap']} | 总{m['total']*100:+7.1f}% 年化{m['ann']*100:+6.1f}% "
              f"回撤{m['dd']*100:6.1f}% 夏普{m['sharpe']:+.2f} 胜率{m['win']*100:.0f}%")
    print(f"  过约束档位: {sum(1 for g in grid if g['ok'])}/{len(grid)}")
    print(f"  回撤范围: {min(g['dd'] for g in grid)*100:.1f}% ~ {max(g['dd'] for g in grid)*100:.1f}%")
    print("\n=== 回撤最小 Top5（不筛约束） ===")
    for m in sorted(grid, key=lambda x: -x["dd"])[:5]:
        print(f"  K={m['K']} vol={m['vol']} cap={m['cap']} | 总{m['total']*100:+7.1f}% 年化{m['ann']*100:+6.1f}% "
              f"回撤{m['dd']*100:6.1f}% 夏普{m['sharpe']:+.2f}")

    json.dump({"n_assets": int(panel.shape[1]), "assets": list(panel.columns),
               "grid": grid_sorted[:40], "best": best},
              open(os.path.join(OUT, "results.json"), "w"), indent=1, ensure_ascii=False)
    if best is not None:
        best_nav.to_frame("equity").to_csv(os.path.join(OUT, "nav_ext.csv"))
        best_p.to_frame("ret").to_csv(os.path.join(OUT, "ret_ext.csv"))
        print(f"\n最优: {best['K']}/{best['vol']}/{best['cap']} 总{best['total']*100:+.1f}% 年化{best['ann']*100:+.1f}% "
              f"回撤{best['dd']*100:.2f}% 夏普{best['sharpe']:.2f}")
        print("逐年:", {k: round(v * 100, 1) for k, v in best["yearly"].items()})
    print("[done] EXT_POOL_DONE")


if __name__ == "__main__":
    main()