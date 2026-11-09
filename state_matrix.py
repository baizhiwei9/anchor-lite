"""状态 × 资产 条件收益/波动/回撤矩阵（2025 先验与 2026 检验两套）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel(full=True)
R = rets.fillna(0.0)
cols = list(panel.columns)
idx = panel.index
p8 = pd.read_csv(os.path.join(BASE, "probs2026_8.csv"), index_col=0, parse_dates=True)
p8 = p8[~p8.index.duplicated(keep="last")].reindex(idx).ffill().fillna(1 / 8)
STATES = list(p8.columns)
KEY = ["oil", "soybean", "nasdaq", "gold", "hs300", "kc50", "treasury10_etf", "citybond"]

def window_days(a, b, s, thr):
    m = (p8[s] >= thr).values
    return [i for i in range(len(idx) - 1) if m[i] and a <= str(idx[i])[:10] <= b]

def matrix(a, b, thr=0.5):
    rows = {}
    counts = {}
    for s in STATES:
        pos = window_days(a, b, s, thr)
        counts[s] = len(pos)
        if len(pos) < 10:
            continue
        sub = R.iloc[pos]
        nav = (1 + sub).cumprod()
        dd = (nav / nav.cummax() - 1).min()
        ann = nav.iloc[-1] - 1   # 状态内累计收益，避免 16 天样本年化后出现荒谬数字
        vol = sub.std(ddof=1) * np.sqrt(252)
        rows[s] = pd.DataFrame({"状态内累计": ann, "年化波动": vol, "状态内回撤": dd})
    return rows, counts

def flatten(mats, tag):
    recs = []
    for s, df in mats.items():
        for a in cols:
            recs.append(dict(window=tag, state=s, asset=E.NAMES.get(a, a), key=a,
                             ann=float(df.loc[a, "状态内累计"]), vol=float(df.loc[a, "年化波动"]),
                             dd=float(df.loc[a, "状态内回撤"])))
    return pd.DataFrame(recs)

m25, c25 = matrix("2025-01-01", "2025-12-31")
m26, c26 = matrix("2026-01-05", "2026-11-06")
print("状态天数（2025 / 2026）：")
for s in STATES:
    print(f"  {s}: {c25[s]} / {c26[s]}")

f25 = flatten(m25, "2025"); f26 = flatten(m26, "2026")
f25.to_csv(os.path.join(BASE, "state_matrix_2025.csv"), index=False)
f26.to_csv(os.path.join(BASE, "state_matrix_2026.csv"), index=False)

print("\n关键资产 × 状态（2026 年内，状态内累计收益 / 状态内回撤）：")
hdr = "资产".ljust(12) + "".join(s[:6].rjust(16) for s in m26)
print(hdr)
for a in KEY:
    line = E.NAMES.get(a, a).ljust(12)
    for s in m26:
        d = m26[s].loc[a]
        line += f"{d['状态内累计']*100:+7.1f}%{d['状态内回撤']*100:7.1f}%  "
    print(line)

print("\n关键资产 × 状态（2025 先验，状态内累计收益 / 状态内回撤）：")
print("资产".ljust(12) + "".join(s[:6].rjust(16) for s in m25))
for a in KEY:
    line = E.NAMES.get(a, a).ljust(12)
    for s in m25:
        d = m25[s].loc[a]
        line += f"{d['状态内累计']*100:+7.1f}%{d['状态内回撤']*100:7.1f}%  "
    print(line)

json.dump(dict(counts_2025=c25, counts_2026=c26,
               states_2025=list(m25.keys()), states_2026=list(m26.keys())),
          open(os.path.join(BASE, "state_matrix_meta.json"), "w"), ensure_ascii=False, indent=2)