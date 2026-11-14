"""新约束集与流动性容量口径（替代人为的 35% 上限）"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
D26 = os.path.join(BASE, "data2026")
cov = pd.read_csv(os.path.join(D26, "_coverage.csv"))
cov = cov[cov["name"] != "nasdaq2"]

NAME_CN = E.NAMES
rows = []
for _, r in cov.iterrows():
    d = pd.read_csv(os.path.join(D26, r["name"] + "_full.csv"), parse_dates=["date"]).set_index("date")
    d = d[~d.index.duplicated(keep="last")].sort_index()
    amt = pd.to_numeric(d["amount"], errors="coerce")
    a20 = float(amt.tail(20).mean())          # 20 日均成交额（元）
    a60 = float(amt.tail(60).mean())
    a5 = float(amt.tail(5).mean())
    rows.append(dict(key=r["name"], name=NAME_CN.get(r["name"], r["name"]),
                     amt20_yi=a20 / 1e8, ratio_5_60=a5 / a60 if a60 > 0 else np.nan))
liq = pd.DataFrame(rows).sort_values("amt20_yi", ascending=False)

# 容量规则：单日交易不超过当日成交额的 10%，建仓周期 3 个交易日 → 可承载仓位 = 20日均成交额 × 10% × 3
# 对 100 万 / 1000 万 / 1 亿三种资金规模，换算成"该资产能占组合的最大比例"
CAP_RATE, DAYS = 0.10, 3
for cap in (1e6, 1e7, 1e8):
    liq[f"max_w_{int(cap/1e4)}万"] = (liq["amt20_yi"] * 1e8 * CAP_RATE * DAYS / cap).clip(upper=1.0)

spec = {
    "objective": "每个状态 s：max 状态内累计收益",
    "constraints": ["w >= 0", "sum(w) = 1", "状态内组合路径最大回撤 >= -5%"],
    "removed_caps": ["单资产 35% 上限（已取消）", "商品/债券/海外分组合计上限（已取消）", "杠杆（仍不启用：ETF 现货多头）"],
    "liquidity_rule": {
        "metric": "20 日均成交额（元）",
        "capacity": "单日成交不超过当日成交额 10%，按 3 个交易日建仓 → 可承载仓位 = 20日均成交额 × 30%",
        "usage": "该数值换算成组合权重上限，作为求解器的 w_i 上界；资金规模越大，上限越紧",
        "data": "新浪财经 ETF 日成交额，2025-01-02 ~ 2026-11-06"
    },
    "mixing": "w_t = Σ_s p_s,t × w_s，再乘熔断系数；月度锁定目标，日频只查偏离与熔断",
}
json.dump(spec, open(os.path.join(BASE, "constraints_v2.json"), "w"), ensure_ascii=False, indent=2)
liq.to_csv(os.path.join(BASE, "liquidity_capacity.csv"), index=False)

print("流动性容量（20 日均成交额，单位亿元）与对应最大仓位：")
show = liq.head(12)[["name", "amt20_yi", "ratio_5_60", "max_w_100万", "max_w_1000万", "max_w_10000万"]]
print(show.round(3).to_string(index=False))
print("\n尾部（流动性最差）：")
print(liq.tail(6)[["name", "amt20_yi", "max_w_100万", "max_w_1000万"]].round(3).to_string(index=False))
print("\n1000 万规模下，受流动性硬约束（最大仓位<目标 20%）的资产：",
      liq[liq["max_w_1000万"] < 0.2]["name"].tolist())