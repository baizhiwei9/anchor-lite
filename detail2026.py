"""最优方案明细：月度收益、净值曲线、手数换算"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import sim2026b as S
import engine3 as E

font_manager.fontManager.addfont(os.path.expanduser("~/.fonts/NotoSansSC-Regular.ttf"))
plt.rcParams["font.family"] = ["Noto Sans SC", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
BASE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(BASE, "figs"); os.makedirs(FIG, exist_ok=True)

o = json.load(open(os.path.join(BASE, "opt2026.json")))
mix = S.MIXES[o["best"]["name"]]
res, sr = S.sim(mix, trig=float(o["best"]["trigger"]), rets_out=True)
nav = (1 + sr).cumprod()
mret = (1 + sr).resample("ME").prod() - 1
print("月度收益:\n", (mret * 100).round(2).to_string())

# 基准曲线
panel = S.panel; idx = sr.index
hs = panel["hs300"].reindex(idx).ffill(); hs = hs / hs.iloc[0]
oil = panel["oil"].reindex(idx).ffill(); oil = oil / oil.iloc[0]
bond = panel["treasury10_etf"].reindex(idx).ffill(); bond = bond / bond.iloc[0]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.2), dpi=160, sharex=True,
                               gridspec_kw={"height_ratios": [2.4, 1]})
ax1.plot(nav.index, nav.values, color="#1F3A5F", lw=1.9,
         label=f"推荐配置（末值 {nav.iloc[-1]:.2f}，回撤 {o['best']['metrics']['maxdd']*100:.1f}%）")
ax1.plot(hs.index, hs.values, color="#9AA5B1", lw=1.1, ls="--", label=f"沪深300ETF（{hs.iloc[-1]:.2f}）")
ax1.plot(oil.index, oil.values, color="#E8833A", lw=1.0, alpha=.75, label=f"原油ETF（{oil.iloc[-1]:.2f}）")
ax1.set_yscale("log"); ax1.legend(frameon=False, fontsize=8, loc="upper left")
ax1.set_title("2026 年内净值对照（2026-01-05 = 1.00，含 5bp 单边成本，对数坐标）", fontsize=10)
for s_ in ("top", "right"): ax1.spines[s_].set_visible(False)
dd = nav / nav.cummax() - 1
ax2.fill_between(dd.index, dd.values, 0, color="#1F3A5F", alpha=.35)
ax2.axhline(-0.05, color="#B45309", ls=":", lw=1); ax2.text(dd.index[5], -0.053, "5% 约束线", color="#B45309", fontsize=8)
ax2.set_ylabel("回撤")
for s_ in ("top", "right"): ax2.spines[s_].set_visible(False)
fig.tight_layout(); fig.savefig(os.path.join(FIG, "nav2026.png")); plt.close(fig)

# 手数换算
cap = 1_000_000
px = {a: float(panel[a].iloc[-1]) for a in panel.columns}
lots = []
name_cn = {"treasury10_etf": "十年国债ETF 511260", "citybond": "城投债ETF 511220", "oil": "原油ETF 501018",
           "nasdaq": "纳指ETF 513100", "sp500": "标普500ETF 513500", "soybean": "豆粕ETF 159985"}
for a, w in mix.items():
    amt = cap * w
    lots.append(dict(asset=name_cn.get(a, a), weight=w, amount=amt, price=px[a], lots=int(amt / px[a] / 100)))
print("\n手数:", json.dumps(lots, ensure_ascii=False, indent=1))

json.dump(dict(monthly={str(k)[:7]: float(v) for k, v in (mret * 100).round(2).items()}, lots=lots,
               nav_last=float(nav.iloc[-1]), start=str(nav.index[0].date()), end=str(nav.index[-1].date())),
          open(os.path.join(BASE, "detail2026.json"), "w"), ensure_ascii=False, indent=2, default=str)
print("saved")