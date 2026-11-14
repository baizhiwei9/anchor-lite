"""2026 年内多资产引擎：动量轮动 + 波动预算 + 回撤熔断 + 期权单腿
只用 2026-01-02~2026-11-06 的数据，全部决策当日收盘、次日结算。"""
import os, sys, json
import numpy as np, pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
D26 = os.path.join(BASE, "data2026")

NAMES = {
    "oil": "原油ETF", "coal": "煤炭ETF", "nasdaq": "纳指ETF", "nikkei": "日经ETF",
    "soybean": "豆粕ETF", "bank": "银行ETF", "dividend": "红利ETF", "lowvol": "低波ETF",
    "sp500": "标普500ETF", "treasury_active": "活跃国债ETF", "citybond": "城投债ETF",
    "corp_bond": "公司债ETF", "treasury10_etf": "十年国债ETF", "treasury_etf": "国债ETF",
    "pharma": "医药ETF", "kc50": "科创50ETF", "consumer": "消费ETF", "gold": "黄金ETF",
    "hs300": "沪深300ETF", "zz500": "中证500ETF", "zz1000": "中证1000ETF",
    "liquor": "酒ETF", "broker": "券商ETF", "solar": "光伏ETF", "cyb": "创业板ETF",
    "steel": "钢铁ETF", "nonferrous": "有色ETF", "military": "军工ETF",
    "newenergy": "新能源ETF", "hstech": "恒生科技ETF", "china_internet": "中概互联ETF",
    "semiconductor": "半导体ETF",
}
BONDS = ["treasury_etf", "treasury10_etf", "treasury_active", "citybond", "corp_bond"]

def load_panel(full=False):
    cov = pd.read_csv(os.path.join(D26, "_coverage.csv"))
    keep = [n for n in cov["name"] if n in NAMES]
    px = {}
    for n in keep:
        fn = n + ("_full.csv" if full else ".csv")
        d = pd.read_csv(os.path.join(D26, fn), parse_dates=["date"]).set_index("date")["close"]
        px[n] = d[~d.index.duplicated()].astype(float)
    panel = pd.DataFrame(px).sort_index()
    panel = panel[~panel.index.duplicated(keep="last")]
    panel = panel.loc[("2025-01-02" if full else "2026-01-02"):"2026-11-06"]
    panel = panel.dropna(how="all").ffill()
    # 剔除上市不足或长期缺失的列
    panel = panel.loc[:, panel.notna().mean() > 0.95]
    rets = panel.pct_change()
    return panel, rets

def zs(x):
    s = pd.Series(x)
    sd = s.std(ddof=1)
    return (s - s.mean()) / sd if sd and sd > 0 else s * 0

def run(panel, rets, start="2026-01-02", end="2026-11-06", top_k=3, vol_target=0.08,
        dd_trigger=0.02, dd_cut=0.5, cost_bp=5.0, use_ma=True, ws=(0.5, 0.3, 0.2),
        bond_fallback=True, max_asset=0.5, overlay="none", overlay_size=0.2, qvix=None):
    idx = panel.loc[start:end].index
    R = rets.loc[idx].fillna(0.0)
    P = panel.loc[idx]
    assets = list(P.columns)
    vol20 = R.rolling(20).std() * np.sqrt(252)
    r20 = (P / P.shift(20) - 1); r60 = (P / P.shift(60) - 1); r120 = (P / P.shift(120) - 1)
    ma60 = P.rolling(60).mean()
    nav, peak, br = 1.0, 1.0, 1.0
    w_prev = pd.Series(0.0, index=assets)
    rows = []
    warm = 61
    for i in range(warm, len(idx) - 1):
        d = idx[i]; nd = idx[i + 1]
        sc = ws[0] * zs(r20.iloc[i]) + ws[1] * zs(r60.iloc[i]) + ws[2] * zs(r120.iloc[i])
        elig = [a for a in assets if (not use_ma or P[a].iloc[i] > ma60[a].iloc[i]) and not np.isnan(sc[a])]
        pick = sorted(elig, key=lambda a: sc[a], reverse=True)[:top_k]
        w = pd.Series(0.0, index=assets)
        if pick:
            iv = 1.0 / vol20.iloc[i][pick].clip(lower=0.05)
            w[pick] = iv / iv.sum()
        elif bond_fallback:
            w["treasury10_etf"] = 1.0
        w = w.clip(upper=max_asset)
        if w.sum() > 0: w = w / w.sum()
        # 波动预算
        cov = R.iloc[max(0, i - 59):i + 1].cov().values * 252
        sig = float(np.sqrt(max(w.values @ cov @ w.values, 1e-8)))
        scale = np.clip(vol_target / sig, 0.15, 1.0)
        w = w * scale * br
        r_next = R.iloc[i + 1].values
        pnl = float(np.nansum(w.values * r_next))
        turn = float((w - w_prev).abs().sum())
        cost = turn * cost_bp / 10000.0
        # 期权单腿
        ov_pnl = 0.0; ov_note = ""
        if overlay == "short_vol" and qvix is not None:
            iv = float(qvix.reindex(idx).ffill().iloc[i]) / 100.0
            rv = float(R["hs300"].iloc[max(0, i - 19):i + 1].std() * np.sqrt(252))
            move = abs(float(R["hs300"].iloc[i + 1]))
            carry = 0.5 * (iv ** 2 - rv ** 2) / 252.0
            jump = -4.0 * max(0.0, move - 0.015)
            ov_pnl = overlay_size * (carry + jump)
        elif overlay == "long_put":
            # 月度买入 3%-10% 宽跨保护，成本按名义 0.45%/月摊销
            ov_pnl = -overlay_size * 0.0045 / 21.0
        ret = pnl - cost + ov_pnl
        nav *= (1 + ret); peak = max(peak, nav); dd = nav / peak - 1
        if dd_trigger is not None:
            if dd <= -dd_trigger: br = 1.0 - dd_cut
            elif dd >= -dd_trigger / 2: br = 1.0
        rows.append((nd, ret, nav, dd, *w.values, sig, scale, br))
        w_prev = w
    cols = ["date", "ret", "nav", "dd"] + assets + ["sig", "scale", "breaker"]
    df = pd.DataFrame(rows, columns=cols).set_index("date")
    return df

def metrics(df):
    r = df["ret"].values; nav = (1 + r).cumprod(); dd = nav / np.maximum.accumulate(nav) - 1
    n = len(r); ann = nav[-1] ** (252 / n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    return dict(total=float(nav[-1] - 1), cagr=float(ann), vol=float(vol), maxdd=float(dd.min()),
                sharpe=float(ann / vol) if vol > 0 else 0.0, calmar=float(ann / abs(dd.min())) if dd.min() < 0 else np.nan,
                n=int(n), dd_date=str(df.index[int(np.argmin(dd))].date()),
                avg_net=float(df[list(df.columns[3:-3])].abs().sum(axis=1).mean()))

if __name__ == "__main__":
    panel, rets = load_panel()
    print("资产数:", panel.shape[1], "交易日:", len(panel), panel.index[0].date(), panel.index[-1].date())
    qv = pd.read_csv(os.path.join(D26, "qvix_300etf.csv"), parse_dates=["date"]).set_index("date")["close"]
    d = run(panel, rets, qvix=qv)
    print(json.dumps(metrics(d), ensure_ascii=False, indent=1))