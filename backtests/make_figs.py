"""出图：Regime 概率堆叠面积 + 净值/回撤对照"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import engine

from matplotlib import font_manager
for fp in [os.path.expanduser("~/.fonts/NotoSansSC-Regular.ttf")]:
    font_manager.fontManager.addfont(fp)
plt.rcParams["font.family"] = ["Noto Sans SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BASE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(BASE, "figs")
os.makedirs(FIG, exist_ok=True)

probs, rets = engine.load_inputs()
COLS = {"低波趋势": "#4C8BF5", "通胀交易": "#E8833A", "高波危机": "#C0392B", "震荡未知": "#9AA5B1"}

# 图1：Regime 概率
p = probs.loc["2024-01-02":"2026-04-14"]
fig, ax = plt.subplots(figsize=(11, 3.4), dpi=160)
ax.stackplot(p.index, [p[c] for c in COLS], labels=list(COLS), colors=list(COLS.values()), alpha=0.85)
ax.set_ylim(0, 1); ax.set_xlim(p.index[0], p.index[-1])
ax.set_ylabel("滤波概率")
ax.legend(loc="upper left", ncol=4, frameon=False, fontsize=8)
ax.set_title("Anchor-Lite Regime 滤波概率（2024-01 ~ 2026-04-14，仅用当日及以前信息）", fontsize=10)
for s in ("top", "right"): ax.spines[s].set_visible(False)
fig.tight_layout(); fig.savefig(os.path.join(FIG, "regime.png")); plt.close(fig)

# 图2：净值与回撤
a = engine.run_engine(probs, rets, "2024-01-02", "2026-04-14")
b = engine.run_engine(probs, rets, "2024-01-02", "2026-04-14", mode="switch")
idx = a.index.intersection(b.index)
hs = rets.loc[idx, "_hs300_px"]; kc = rets.loc[idx, "_kc50_px"]
curves = {"Anchor-Lite（概率加权+风险预算）": a.loc[idx, "nav"],
          "硬切换（argmax 一把到位）": b.loc[idx, "nav"],
          "沪深300ETF 持有": hs / hs.iloc[0],
          "科创50ETF 持有": kc / kc.iloc[0]}
colors = ["#1F3A5F", "#E8833A", "#4C8BF5", "#C0392B"]
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.2), dpi=160, sharex=True,
                               gridspec_kw={"height_ratios": [2.4, 1]})
for (k, v), c in zip(curves.items(), colors):
    ax1.plot(v.index, v.values, label=f"{k}（末值 {v.iloc[-1]:.2f}）", color=c, lw=1.6)
ax1.legend(frameon=False, fontsize=8, loc="upper left")
ax1.set_title("同区间净值对照（2024-01-02 = 1.00，含 5bp 单边成本）", fontsize=10)
for s in ("top", "right"): ax1.spines[s].set_visible(False)
for (k, v), c in zip(list(curves.items())[:2], colors[:2]):
    dd = v / v.cummax() - 1
    ax2.fill_between(dd.index, dd.values, 0, color=c, alpha=0.35, label=k)
ax2.legend(frameon=False, fontsize=8, loc="lower left")
ax2.set_ylabel("回撤")
for s in ("top", "right"): ax2.spines[s].set_visible(False)
fig.tight_layout(); fig.savefig(os.path.join(FIG, "nav.png")); plt.close(fig)
print("figs saved")