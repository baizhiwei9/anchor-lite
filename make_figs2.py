"""第二轮出图：全区间净值/回撤 + 波动预算前沿"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import engine2 as E

font_manager.fontManager.addfont(os.path.expanduser("~/.fonts/NotoSansSC-Regular.ttf"))
plt.rcParams["font.family"] = ["Noto Sans SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BASE = os.path.dirname(os.path.abspath(__file__)); FIG = os.path.join(BASE, "figs")
os.makedirs(FIG, exist_ok=True)
probs, rets = E.load(); ctx = E.Ctx(probs, rets)
FULL = ("2018-06-01", "2026-04-14")

cfgs = {
    "5% 档：波动预算 7% + 熔断 + 均线闸门": dict(vol_target=0.07, dd_trigger=0.02, dd_cut=0.5,
                                        comm_ma_gate=True, comm_cap=0.25),
    "8-11% 回撤档：波动预算 10%": dict(vol_target=0.10, comm_ma_gate=True, comm_cap=0.25),
    "样本内高收益档：波动预算 12%": dict(vol_target=0.12, comm_ma_gate=True, comm_cap=0.25),
}
res = {}
for k, p in cfgs.items():
    d = E.run(ctx, *FULL, **p); res[k] = d
    d.to_csv(os.path.join(BASE, "full_" + str(abs(hash(k)) % 100000) + ".csv"))

idx = res[list(cfgs)[0]].index
hs = rets.loc[idx, "_hs300_px"]; hs = hs / hs.iloc[0]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.4), dpi=160, sharex=True,
                               gridspec_kw={"height_ratios": [2.5, 1]})
colors = ["#1F3A5F", "#E8833A", "#C0392B"]
for (k, d), c in zip(res.items(), colors):
    ax1.plot(d.index, d["nav"], label=f"{k}（末值 {d['nav'].iloc[-1]:.2f}）", color=c, lw=1.6)
ax1.plot(idx, hs.values, label=f"沪深300ETF 持有（末值 {hs.iloc[-1]:.2f}）", color="#9AA5B1", lw=1.2, ls="--")
ax1.set_yscale("log"); ax1.legend(frameon=False, fontsize=8, loc="upper left")
ax1.set_title("全区间连续回测（2018-06 ~ 2026-04-14，含 5bp 单边成本，对数坐标）", fontsize=10)
for s in ("top", "right"): ax1.spines[s].set_visible(False)
for (k, d), c in zip(res.items(), colors):
    dd = d["nav"] / d["nav"].cummax() - 1
    ax2.plot(dd.index, dd.values, color=c, lw=1.2, label=k)
ax2.axhline(-0.05, color="#B45309", ls=":", lw=1)
ax2.text(idx[20], -0.052, "5% 约束线", color="#B45309", fontsize=8)
ax2.set_ylabel("回撤"); ax2.legend(frameon=False, fontsize=8, loc="lower left")
for s in ("top", "right"): ax2.spines[s].set_visible(False)
fig.tight_layout(); fig.savefig(os.path.join(FIG, "full_nav.png")); plt.close(fig)

# 前沿：横轴波动预算，纵轴样本内年化，颜色 = 全窗口最差回撤
fr = pd.read_csv(os.path.join(BASE, "grid_frontier.csv"))
best = fr.sort_values("is_cagr", ascending=False).groupby("vol_target").head(1)
fig, ax = plt.subplots(figsize=(8.6, 4.0), dpi=160)
sc = ax.scatter(best["vol_target"], best["is_cagr"] * 100, c=-best["worst_dd"] * 100,
                cmap="RdYlGn_r", s=95, edgecolor="#333", linewidth=.6)
for _, r in best.iterrows():
    ax.annotate(f"{r['is_cagr']*100:.1f}%／{r['worst_dd']*100:.1f}%", (r["vol_target"], r["is_cagr"] * 100),
                textcoords="offset points", xytext=(6, 6), fontsize=7.5, color="#333")
cb = fig.colorbar(sc, ax=ax); cb.set_label("全窗口最差回撤（%）", fontsize=8)
ax.set_xlabel("波动预算（年化）"); ax.set_ylabel("样本内（2024-01~2026-04）年化收益 %")
ax.set_title("波动预算 → 收益 / 回撤前沿：标注为「样本内年化／全窗口最差回撤」", fontsize=10)
for s in ("top", "right"): ax.spines[s].set_visible(False)
fig.tight_layout(); fig.savefig(os.path.join(FIG, "frontier.png")); plt.close(fig)
print("ok", list(res))
for k, d in res.items():
    m = E.metrics(d); print(k, round(m["cagr"], 4), round(m["maxdd"], 4))