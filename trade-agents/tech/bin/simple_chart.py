# -*- coding: utf-8 -*-
"""单子图：缠论分型版 + VWAP 中线/上下轨
=========================================
一只股票一张图，不画小时线/均线版/MACD，只画：
  K线 + 缠论笔(分型版) + 中枢 ZG/ZD + VWAP 中线(±1σ/±2σ)
"""
import os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as _fm
for _f in ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]:
    if os.path.exists(_f):
        try:
            _fm.fontManager.addfont(_f)
            matplotlib.rcParams["font.sans-serif"] = [_fm.FontProperties(fname=_f).get_name(), "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            pass
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))
sys.path.insert(0, os.path.join(WS, "smc"))

import chan_smc_index as C
import smc_core as S
from chan_chart import chan_geometry

RED, GRN, BLUE = "#d62728", "#2ca02c", "#1f77b4"
CHART_DIR = os.path.join(WS, ".cache", "charts")
os.makedirs(CHART_DIR, exist_ok=True)


def draw_stock(code, name, d, n_show=120, out=None):
    """单子图：缠论分型版 + VWAP。d = DataFrame[dt,open,high,low,close,volume] 升序"""
    if d is None or len(d) < 60:
        return None
    d = d.sort_values("dt").reset_index(drop=True)
    total = len(d)
    start = max(0, total - n_show)
    view = d.iloc[start:].reset_index(drop=True)
    nv = len(view)
    x = np.arange(nv)
    o = view["open"].astype(float).values
    h = view["high"].astype(float).values
    l = view["low"].astype(float).values
    c = view["close"].astype(float).values

    fig, ax = plt.subplots(figsize=(11, 6.5), dpi=125)

    # 涨跌上色
    prev = np.concatenate([[np.nan], c[:-1]])
    if start > 0:
        prev[0] = float(d["close"].iloc[start-1])
    colors = [RED if (np.isfinite(prev[i]) and c[i] >= prev[i]) else GRN for i in range(nv)]
    bw = 0.62
    for i in range(nv):
        ax.vlines(x[i], l[i], h[i], color=colors[i], lw=0.9, zorder=3)
        lo, hi = min(o[i], c[i]), max(o[i], c[i])
        if abs(hi-lo) < 1e-9:
            hi = lo + (h[i]-l[i])*0.001 + 1e-6
        ax.add_patch(Rectangle((x[i]-bw/2, lo), bw, hi-lo,
                               facecolor=colors[i], edgecolor=colors[i], lw=0.5, zorder=4))

    # 缠论笔 + 中枢（全量算，按索引裁切）
    geo = chan_geometry(d)
    bx, by = [], []
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -2 <= xi <= nv+2:
            bx.append(xi); by.append(p["price"])
    if len(bx) >= 2:
        ax.plot(bx, by, color=BLUE, lw=1.5, zorder=6, label="缠论笔")
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -1 <= xi <= nv:
            ax.scatter([xi], [p["price"]], s=20, zorder=7,
                       color=(RED if p["type"]==1 else GRN), marker=("v" if p["type"]==1 else "^"))
    if geo.get("zs") and geo.get("zs_x"):
        zs0, zs1 = geo["zs"]
        x0 = max(geo["zs_x"][0]-start, -0.5)
        x1 = min(geo["zs_x"][1]-start, nv)
        if x1 > x0 and x1 >= -1:
            ax.add_patch(Rectangle((x0, zs0), x1-x0, zs1-zs0, facecolor="#ffd24d",
                                   alpha=0.25, edgecolor="#b8860b", lw=1.4, zorder=3))
            ax.axhline(zs1, color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)
            ax.axhline(zs0, color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)

    # VWAP 中线 + 上下轨（±1σ/±2σ）
    try:
        vw = S.vwap_tracks(d)
        v = vw["vwap"]; sg = vw["sigma"]
        v_v = v[start:]
        ax.plot(x, v_v, color="#8e44ad", lw=1.8, zorder=8, label="VWAP中线")
        for k, lab, col, ls in [(1, "+1σ", "#e67e22", "--"), (-1, "-1σ", "#e67e22", "--"),
                                (2, "+2σ", "#c0392b", ":"), (-2, "-2σ", "#c0392b", ":")]:
            band = v[start:] + k * sg[start:]
            ax.plot(x, band, color=col, lw=0.9, ls=ls, alpha=0.75, zorder=5,
                    label=f"{lab} {band[-1]:.2f}" if abs(k) <= 1 else None)
    except Exception:
        pass

    close = float(c[-1])
    ax.set_title(f"{name} {code} ｜ 缠论分型版+VWAP ｜ 收 {close:.2f}", fontsize=12, fontweight="bold")
    ax.set_ylabel("价格", fontsize=9)
    ax.legend(loc="upper left", fontsize=7, framealpha=0.85, ncol=2)
    ax.grid(True, alpha=0.25)
    # x轴日期刻度
    dates = view["dt"].astype(str).tolist()
    step = max(1, nv // 8)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([dt[5:] for dt in dates[::step]], fontsize=7, rotation=30)

    out = out or os.path.join(CHART_DIR, f"simple_{code}_{pd.Timestamp.now().strftime('%Y%m%d')}.png")
    fig.tight_layout()
    fig.savefig(out, dpi=125, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


if __name__ == "__main__":
    import tech_buypoint as TB
    d = TB.load_daily("688696.SH", TB._today_hist())
    p = draw_stock("688696.SH", "极米科技", d)
    print("出图:", p)
