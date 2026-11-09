"""Barra 风格因子库（ETF 层面）：价量类逐项落地，基本面类如实标注代理/不可得"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
D26 = os.path.join(BASE, "data2026")
cov = pd.read_csv(os.path.join(D26, "_coverage.csv"))
cov = cov[cov["name"] != "nasdaq2"]

data = {}
for _, r in cov.iterrows():
    d = pd.read_csv(os.path.join(D26, r["name"] + "_full.csv"), parse_dates=["date"]).set_index("date")
    d = d[~d.index.duplicated(keep="last")].sort_index()
    data[r["name"]] = d

close = pd.DataFrame({k: v["close"] for k, v in data.items()}).sort_index().ffill()
amount = pd.DataFrame({k: v["amount"] for k, v in data.items()}).sort_index().ffill()
volume = pd.DataFrame({k: v["volume"] for k, v in data.items()}).sort_index().ffill()
high = pd.DataFrame({k: v["high"] for k, v in data.items()}).sort_index().ffill()
low = pd.DataFrame({k: v["low"] for k, v in data.items()}).sort_index().ffill()
ret = close.pct_change()
hs = ret["hs300"]

def roll_std(x, n): return x.rolling(n).std() * np.sqrt(252)
def mom(n): return close / close.shift(n) - 1

F = {}
# 动量
F["MOM_5D"] = mom(5); F["MOM_20D"] = mom(20); F["MOM_60D"] = mom(60)
F["MOM_120D"] = mom(120); F["MOM_250D"] = mom(250)
F["MOM_20D_EX1"] = close.shift(1) / close.shift(21) - 1
# 趋势
F["TREND_MA20"] = close / close.rolling(20).mean() - 1
F["TREND_MA60"] = close / close.rolling(60).mean() - 1
F["TREND_MA120"] = close / close.rolling(120).mean() - 1
F["MA_SLOPE_60"] = close.rolling(60).mean() / close.rolling(60).mean().shift(20) - 1
ema12 = close.ewm(span=12).mean(); ema26 = close.ewm(span=26).mean()
dif = ema12 - ema26; dea = dif.ewm(span=9).mean()
F["MACD_HIST"] = (dif - dea) / close
delta = close.diff(); up = delta.clip(lower=0); dn = (-delta).clip(lower=0)
rs = up.rolling(14).mean() / dn.rolling(14).mean().replace(0, np.nan)
F["RSI_14"] = 100 - 100 / (1 + rs)
ma20 = close.rolling(20).mean(); sd20 = close.rolling(20).std()
F["BOLL_POS"] = (close - ma20) / (2 * sd20)
# 反转
F["REV_1D"] = -ret; F["REV_5D"] = -mom(5)
# 波动
F["VOL_20"] = roll_std(ret, 20); F["VOL_60"] = roll_std(ret, 60); F["VOL_120"] = roll_std(ret, 120)
F["DOWNSIDE_VOL_60"] = ret.where(ret < 0).rolling(60).std() * np.sqrt(252)
F["VOL_OF_VOL_60"] = roll_std(ret, 20).rolling(60).std()
tr = pd.DataFrame({c: np.maximum(high[c] - low[c], np.maximum((high[c] - close[c].shift(1)).abs(),
                                                                (low[c] - close[c].shift(1)).abs())) for c in close.columns})
F["ATR14_PCT"] = tr.rolling(14).mean() / close
roll_max = close.rolling(120).max()
F["MAXDD_120"] = (close / roll_max - 1)
F["SKEW_60"] = ret.rolling(60).skew(); F["KURT_60"] = ret.rolling(60).kurt()
# Beta / 相关
for n in (60, 120):
    cov_n = ret.rolling(n).cov(hs)
    var_n = hs.rolling(n).var()
    F[f"BETA_{n}_HS300"] = cov_n.div(var_n, axis=0)
F["CORR_60_HS300"] = ret.rolling(60).corr(hs)
F["IDIO_VOL_60"] = (ret.sub(hs * F["BETA_60_HS300"], axis=0)).rolling(60).std() * np.sqrt(252)
for ref, nm in (("oil", "OIL"), ("treasury10_etf", "BOND"), ("gold", "GOLD")):
    F[f"BETA_{nm}_60"] = ret.rolling(60).cov(ret[ref]).div(ret[ref].rolling(60).var(), axis=0)
# 流动性 / 规模代理
F["AMT_20_LOG"] = np.log(amount.rolling(20).mean())
F["AMT_RATIO_5_60"] = amount.rolling(5).mean() / amount.rolling(60).mean()
F["VOLUME_TREND_20_60"] = volume.rolling(20).mean() / volume.rolling(60).mean()
F["UP_DAYS_60"] = (ret > 0).rolling(60).mean()
F["GAP_AVG_20"] = ((close / close.shift(1) - 1) - (close - close.shift(1)) / close.shift(1)).abs().rolling(20).mean()

# 统一形状：把 rolling.cov 等返回的 Series/MultiIndex 还原成 日期 × 资产
for _k, _v in list(F.items()):
    if not isinstance(_v, pd.DataFrame):
        if isinstance(_v, pd.Series) and isinstance(_v.index, pd.MultiIndex):
            F[_k] = _v.unstack()
        else:
            print("[drop]", _k, type(_v).__name__)
            F.pop(_k)

TIER = {}
for k in F:
    n = k
    if any(s in n for s in ["AMT", "VOLUME"]): TIER[k] = "代理（成交额/成交量代理规模与流动性）"
    elif "BETA" in n or "CORR" in n or "IDIO" in n: TIER[k] = "真实（价量回归）"
    else: TIER[k] = "真实（价量衍生）"

UNAVAILABLE = {
    "SIZE_MKT_CAP": "ETF 无市值口径，规模只能用成交额代理",
    "BETA_FUNDAMENTAL": "无成分股财务报表，无法做基本面 beta",
    "BOOK_TO_PRICE": "无成分股 PB",
    "EARNINGS_YIELD": "无成分股 PE",
    "ROE": "无成分股 ROE",
    "LEVERAGE": "无成分股资产负债率",
    "DIVIDEND_YIELD": "ETF 分红数据不全，无法稳定计算",
    "ROLL_YIELD": "商品 ETF 无期货曲线，无法计算展期收益",
    "CREDIT_SPREAD_SENS": "无信用利差序列，无法回归信用敏感度",
}

# 取 2026-11-06 暴露（横截面 z 分数）
last = close.index[-1]
expo = pd.DataFrame({k: v.loc[last].to_dict() for k, v in F.items()}).T
expo = expo[[c for c in close.columns if c in expo.columns]]   # 只保留资产列，剔除误入的日期键
raw = expo.copy()
z = expo.sub(expo.mean(axis=1), axis=0).div(expo.std(axis=1).replace(0, np.nan), axis=0)
z.index.name = "factor"
z.to_csv(os.path.join(BASE, "factor_exposure_20261106.csv"))
raw.to_csv(os.path.join(BASE, "factor_raw_20261106.csv"))

summary = dict(date=str(last.date()), n_factors=len(F) + len(UNAVAILABLE),
               n_computable=len(F), n_unavailable=len(UNAVAILABLE),
               tiers=pd.Series(TIER).value_counts().to_dict(),
               unavailable=UNAVAILABLE,
               assets=int(close.shape[1]))
json.dump(summary, open(os.path.join(BASE, "factor_summary.json"), "w"), ensure_ascii=False, indent=2)

print("资产数:", close.shape[1], "可算因子:", len(F), "不可得:", len(UNAVAILABLE),
      "合计:", len(F) + len(UNAVAILABLE))
print("数据起点:", close.index[0].date(), "终点:", last.date())
print("\n因子暴露（z 分数）抽样：")
show = ["MOM_20D", "MOM_60D", "TREND_MA60", "VOL_60", "BETA_60_HS300", "AMT_20_LOG", "MAXDD_120"]
print(z.loc[show, ["oil", "nasdaq", "sp500", "treasury10_etf", "citybond", "hs300", "kc50", "gold"]].round(2).to_string())
print("\n层级别:", summary["tiers"])
# 因子面板存盘，供后续打分与 IC 使用
pd.to_pickle(F, os.path.join(BASE, "factor_panels.pkl"))