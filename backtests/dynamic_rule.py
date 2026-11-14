"""规则规格：宏观状态决定候选池（选什么），波动率状态决定波动预算（买多少）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel(full=True)
cols = list(panel.columns)
idx = panel.index
p8 = pd.read_csv(os.path.join(BASE, "probs2026_8.csv"), index_col=0, parse_dates=True)
p8 = p8[~p8.index.duplicated(keep="last")].reindex(idx).ffill().fillna(1 / 8)
liq = pd.read_csv(os.path.join(BASE, "liquidity_capacity.csv")).set_index("key")

# ---------- 1) 宏观状态 → 候选池（组级别） ----------
POOL = {
    "低波趋势":   {"a_share": 2, "overseas": 2, "bond": 1, "commodity": 0},
    "温和通胀":   {"a_share": 1, "overseas": 2, "bond": 1, "commodity": 1},
    "商品强通胀": {"a_share": 0, "overseas": 1, "bond": 1, "commodity": 3},
    "紧缩利率上行": {"a_share": 0, "overseas": 1, "bond": 3, "commodity": 1},
    "增长放缓":   {"a_share": 0, "overseas": 1, "bond": 3, "commodity": 1},
    "流动性危机": {"a_share": 0, "overseas": 0, "bond": 4, "commodity": 1},
    "衰退通缩":   {"a_share": 0, "overseas": 1, "bond": 3, "commodity": 1},
    "震荡未知":   {"a_share": 1, "overseas": 2, "bond": 2, "commodity": 1},
}
GROUP = {}
for c in ["oil", "soybean", "coal", "gold", "nonferrous", "steel", "newenergy", "solar"]:
    GROUP[c] = "commodity"
for c in ["treasury10_etf", "citybond", "treasury_etf", "treasury_active", "corp_bond"]:
    GROUP[c] = "bond"
for c in ["nasdaq", "sp500", "nikkei", "hstech", "china_internet"]:
    GROUP[c] = "overseas"
for c in cols:
    GROUP.setdefault(c, "a_share")

# ---------- 2) 波动率状态 → 波动预算 ----------
VOL_BUCKETS = [(0.0, 0.25, "低波", 0.12), (0.25, 0.60, "中波", 0.09),
               (0.60, 0.85, "高波", 0.06), (0.85, 1.01, "极端", 0.03)]

def vol_regime(d, lookback=252):
    """用全资产 20 日波动率的中位数，与其历史分位比较"""
    v20 = rets.rolling(20).std() * np.sqrt(252)
    hist = v20.loc[:d].tail(lookback).median(axis=1).dropna()
    cur = float(hist.iloc[-1])
    q = float((hist < cur).mean())
    for lo, hi, name, target in VOL_BUCKETS:
        if lo <= q < hi:
            return name, q, target
    return "极端", q, 0.03

def build_pool(d, state):
    cfg = POOL[state]
    mom60 = (panel.loc[d] / panel.iloc[max(0, list(idx).index(d) - 60)] - 1)
    ma60 = panel.loc[:d].tail(60).mean()
    pool = []
    for g, k in cfg.items():
        if k == 0:
            continue
        cand = [c for c in cols if GROUP[c] == g and liq.loc[c, "amt20_yi"] >= 1.0]
        cand = sorted(cand, key=lambda c: mom60.get(c, -9), reverse=True)
        pool += cand[:k]
    return pool

def weights_now(d, state, target_vol):
    pool = build_pool(d, state)
    if not pool:
        return None, None, None
    vol = (rets[pool].tail(60).std() * np.sqrt(252)).clip(lower=0.01)
    w = (1 / vol) / (1 / vol).sum()          # 池内逆波动
    R = rets[pool].tail(60).fillna(0).values
    sig = float(np.sqrt(w.values @ (np.cov(R.T) * 252) @ w.values))
    scale = float(np.clip(target_vol / sig, 0.10, 1.0))
    return pool, w * scale, scale

SPEC = {
    "macro_to_pool": POOL,
    "pool_member_pick": "组内按 60 日动量排序取前 k 个，要求 20 日均成交额 ≥ 1 亿元（流动性门槛）",
    "vol_regime": "全资产 20 日波动率中位数 vs 过去 252 日分位：<25% 低波→12%；25-60% 中波→9%；60-85% 高波→6%；>85% 极端→3%",
    "weight_rule": "池内逆波动加权 w0；再按 σ_target/σ(w0) 缩放，缩放上限 1.0（不加杠杆）",
    "switch_freq": "宏观状态与波动档位月度重设；熔断日频（回撤 3% 减半、1.5% 恢复）",
}

if __name__ == "__main__":
    d = idx[-1]
    st = p8.iloc[-1].idxmax(); p = float(p8.iloc[-1].max())
    vr, q, tv = vol_regime(d)
    pool, w, scale = weights_now(d, st, tv)
    print(f"当期（{str(d)[:10]}）核对：")
    print(f"  宏观状态：{st}（概率 {p:.1%}）")
    print(f"  波动档位：{vr}（分位 {q:.0%}）→ 目标波动 {tv:.0%}")
    print(f"  候选池（{len(pool)} 只）：" + ", ".join(E.NAMES.get(c, c) for c in pool))
    print(f"  缩放系数：{scale:.2f}，权重合计：{float(w.sum()):.2%}")
    print("  当期权重：" + ", ".join(f"{E.NAMES.get(c, c)} {v:.1%}" for c, v in w.sort_values(ascending=False).items() if v > 0.005))
    json.dump(SPEC, open(os.path.join(BASE, "dynamic_rule_spec.json"), "w"), ensure_ascii=False, indent=2)
    print("\nsaved dynamic_rule_spec.json")