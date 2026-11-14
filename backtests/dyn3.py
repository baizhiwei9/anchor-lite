"""三条动态规则引擎：①偏离阈值再平衡 ②原油逆势加仓 ③波动率目标定仓"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine3 as E

BASE = os.path.dirname(os.path.abspath(__file__))
panel, rets = E.load_panel(full=True)
cols = list(panel.columns)
idx = panel.index
R = rets.fillna(0.0).values
SI = int(np.searchsorted(idx, np.datetime64("2026-01-05")))

BASE_W = {"treasury10_etf": 0.40, "dividend": 0.15, "lowvol": 0.10,
          "nasdaq": 0.10, "kc50": 0.10, "oil": 0.15}
RISK = ["nasdaq", "kc50", "oil"]
ASSET_TARGET_VOL = {"nasdaq": 0.25, "kc50": 0.30, "oil": 0.35}

def base_vec():
    v = np.zeros(len(cols))
    for k, w in BASE_W.items():
        v[cols.index(k)] = w
    return v

def run(band=0.08, oil_drop=-0.12, oil_add=0.10, oil_cap=0.25,
        vol_scale=True, trig=0.03, cost=5.0, record=True, cooldown=5, vol_target=None):
    w = base_vec()
    target = base_vec()
    nav = peak = 1.0
    br = prev_br = 1.0
    rr = []
    trades = []
    month = None
    last_oil_trade = -999
    sc = 1.0   # 当前已应用的熔断缩放系数
    oil_boost = False   # 逆势加仓状态（滞回：跌到阈值开启，回到 -3% 关闭）
    for i in range(SI, len(idx) - 1):
        d = idx[i]
        # 月度：重设基准与波动率目标定仓
        if d.month != month:
            month = d.month
            target = base_vec()
            if vol_scale:
                for a in RISK:
                    j = cols.index(a)
                    vol20 = float(rets[a].loc[:d].tail(20).std() * np.sqrt(252))
                    k = np.clip(ASSET_TARGET_VOL[a] / max(vol20, 0.01), 0.5, 1.8)
                    target[j] = BASE_W[a] * k
            # 归一（现金部分吸收差额）
            s_ = target.sum()
            if s_ > 1.0:
                target = target / s_
            if vol_target is not None:
                sub = rets.iloc[:, :].loc[:d].tail(60).fillna(0)
                cov = sub.cov().values * 252
                sig = float(np.sqrt(max(target @ cov @ target, 1e-8)))
                target = target * min(1.0, vol_target / sig)
        # 原油逆势加仓信号（日频检查）
        oil20 = float(panel["oil"].loc[:d].iloc[-1] / panel["oil"].loc[:d].iloc[-21] - 1)
        j_oil = cols.index("oil")
        tgt = target.copy()
        if oil20 <= oil_drop:
            oil_boost = True
        elif oil20 >= -0.03:
            oil_boost = False
        if oil_boost:
            tgt[j_oil] = min(tgt[j_oil] + oil_add, oil_cap)
        tgt = tgt * br   # 熔断状态作用在目标上，避免再平衡把减仓加回去
        # 偏离阈值再平衡（持仓权重 vs 目标）
        cur = w   # 用实际权重比较，不能归一化，否则会把熔断减掉的仓位又加回来
        trade = np.zeros(len(cols))
        for j in range(len(cols)):
            if abs(cur[j] - tgt[j]) > band:
                trade[j] = tgt[j] - cur[j]
        c = 0.0
        if np.abs(trade).sum() > 1e-6:
            w = cur + trade
            c = float(np.abs(trade).sum()) * cost / 1e4
            if abs(trade[j_oil]) > 0.01:
                last_oil_trade = i
            if record:
                trades.append(dict(date=str(d)[:10], oil20=round(oil20, 3),
                                   action="、".join(f"{E.NAMES.get(cols[j], cols[j])}{'买入' if trade[j]>0 else '卖出'}{abs(trade[j]):.1%}"
                                                    for j in range(len(cols)) if abs(trade[j]) > 0.005)))
        # 熔断：状态切换时按比例缩放当前持仓
        if br != sc:
            w = w * (br / sc)
            sc = br
        pnl = float(np.nansum(w * R[i + 1]))
        ret = pnl - c
        nav *= 1 + ret
        peak = max(peak, nav)
        dd = nav / peak - 1
        if dd <= -trig:
            br = 0.5
        elif dd >= -trig / 2:
            br = 1.0
        if pnl > -1:
            w = w * (1 + R[i + 1]) / (1 + pnl)
        rr.append(ret)
    r = np.array(rr)
    n = np.cumprod(1 + r)
    dd = n / np.maximum.accumulate(n) - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    m = dict(total=float(n[-1] - 1), maxdd=float(dd.min()), vol=float(vol),
             sharpe=float((n[-1] ** (252 / len(r)) - 1) / vol), n_trades=len(trades))
    return m, trades, r

if __name__ == "__main__":
    m, trades, _ = run()
    print("三条动态规则：收益 %+.1f%% 回撤 %.2f%% 波动 %.1f%% 夏普 %.2f 调仓次数 %d"
          % (m['total'] * 100, m['maxdd'] * 100, m['vol'] * 100, m['sharpe'], m['n_trades']))
    print("\n调仓记录（前 12 条 + 最后 6 条）：")
    for t in trades[:12] + (["..."] if len(trades) > 18 else []) + trades[-6:]:
        print("  ", t if isinstance(t, str) else f"{t['date']} oil20={t['oil20']:+.2%} {t['action']}")
    json.dump(dict(metrics=m, trades=trades), open(os.path.join(BASE, "dyn3_backtest.json"), "w"),
              ensure_ascii=False, indent=2)