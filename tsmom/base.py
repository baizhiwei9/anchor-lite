# -*- coding: utf-8 -*-
"""tsmom 分支：13 只 ETF 横截面动量信号 + regime / 波动率目标 / 成本 的统一回测底座。

口径（全分支统一）：
- 标的：13 只 ETF（见 POOL13），1x 无杠杆，只多 + 空仓
- 信号：T 日收盘生成、T+1 生效（np.roll 1），无前视
- regime：等权净值 > 其 MA200（参数在 2013–2021 训练段选定），用 t-1 信息
- 波动率目标：EWM60 估计已实现波动，按 vol_target 缩放，cap 上限
- 成本：单边 cost_bp × 日度仓位变动
"""
import os
import json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")

POOL13 = ["sh510300", "sh510500", "sh510050", "sz159915", "sz159949", "sh512880", "sh512660", "sh512010",
          "sh513100", "sh513500", "sz159941", "sh513030", "sz159920"]
MODELS = ["SLP", "MLP", "CNN", "LSTM"]


def load_panel():
    raw = pd.read_csv(os.path.join(ASSETS, "panel13.csv"), index_col=0, parse_dates=True)
    return raw[POOL13].sort_index()


def load_signals():
    z0 = np.load(os.path.join(ASSETS, "sig_SLP.npz"), allow_pickle=True)
    dates = pd.DatetimeIndex(pd.to_datetime([str(d) for d in z0["dates"]]))
    sigs = {}
    for m in MODELS:
        sigs[m] = np.load(os.path.join(ASSETS, f"sig_{m}.npz"), allow_pickle=True)["s"]
    sigs["ENS"] = np.mean([sigs[m] for m in MODELS], axis=0)
    return dates, sigs


def load_tucker(dates):
    tk = pd.read_csv(os.path.join(ASSETS, "tucker_distance.csv"), parse_dates=["date"]).set_index("date")
    tz = tk[["z_time", "z_factor", "z_asset"]].max(axis=1)
    return tz.reindex(dates).ffill().shift(1).fillna(0.0).values


def ewm_std(s, span=60):
    s = np.asarray(pd.Series(s).ewm(span=span, adjust=False).std().values).copy()
    rs = pd.Series(np.asarray(s)).rolling(20, min_periods=5).mean().values
    nan = ~np.isfinite(s)
    s[nan] = rs[nan]
    s[~np.isfinite(s)] = np.nanstd(s) if np.isfinite(np.nanstd(s)) else 0.0
    return s


def trend_switch(px, k):
    m = pd.Series(px).rolling(k, min_periods=k).mean()
    s = (pd.Series(px) > m).astype(float)
    return s.shift(1).fillna(0).values


class TsmomEngine:
    """组合层引擎。子类只需覆盖类属性，不改计算逻辑。"""

    name = "base"
    signal = "MLP"          # MLP / mix(0.7MLP+0.3CNN) / ruleA_mix
    vol_target = 0.15
    cap = 2.0
    cost_bp = 5.0
    cash = 0.02
    thr = 0.0
    use_tucker = False
    tucker_cut, tucker_scale = 1.5, 0.5
    tucker_cut2, tucker_scale2 = 2.5, 0.0
    ma_window = 200
    mom_window = 21
    top_n = 8

    # ---------- 数据 ----------
    def prepare(self):
        self.df = load_panel()
        self.returns = self.df.pct_change()
        self.dates, self.sigs = load_signals()
        self.r = self.returns[POOL13].reindex(self.dates).values
        self.T, self.N = self.r.shape
        ew = (1 + self.returns[POOL13].mean(axis=1)).cumprod()
        self.ew_nav = ew
        self.regime = trend_switch(ew, self.ma_window)[-self.T:]
        self.tz = load_tucker(self.dates) if self.use_tucker else np.zeros(self.T)
        self.ruleA = np.ones((self.T, self.N))
        mom = self.df.pct_change(self.mom_window).reindex(self.dates).values
        rank = np.argsort(-np.nan_to_num(mom, nan=-9e9), axis=1)
        self.ruleB = np.zeros((self.T, self.N))
        for i in range(self.T):
            self.ruleB[i, rank[i, : self.top_n]] = 1.0
        return self

    def build_signal(self):
        if self.signal == "mix":
            return 0.7 * self.sigs["MLP"] + 0.3 * self.sigs["CNN"]
        if self.signal == "ruleA_mix":
            return 0.5 * np.maximum(self.sigs["MLP"], 0) + 0.5 * self.ruleA
        return self.sigs[self.signal]

    # ---------- 回测 ----------
    def run(self):
        sig = self.build_signal()
        sp = np.roll(sig, 1, axis=0)
        sp[0] = 0
        w = np.maximum(sp, 0.0)
        if self.thr > 0:
            w[np.abs(sp) < self.thr] = 0.0
        p = (w * self.r).mean(axis=1)

        tscale = np.ones(self.T)
        if self.use_tucker:
            tscale = np.where(self.tz >= self.tucker_cut2, self.tucker_scale2,
                              np.where(self.tz >= self.tucker_cut, self.tucker_scale, 1.0))
        p = p * tscale * self.regime + (1 - self.regime) * (self.cash / 252.0)

        vscale = np.ones(self.T)
        sd = ewm_std(p, 60)
        vscale = np.minimum(self.vol_target / np.sqrt(252) / (sd + 1e-9), self.cap)
        p = p * vscale

        if self.cost_bp > 0:
            wf = w * vscale[:, None] * tscale[:, None] * np.repeat(self.regime[:, None], self.N, axis=1)
            turn = np.abs(np.diff(np.vstack([np.zeros((1, self.N)), wf]), axis=0)).sum(axis=1) / self.N
            p = p - turn * (self.cost_bp / 10000.0)
        self.p = p
        return p

    def summarize(self, p=None):
        p = self.p if p is None else p
        nav = (1 + p).cumprod()
        total = float(nav[-1] - 1)
        ann = float((1 + total) ** (252.0 / len(p)) - 1)
        sharpe = float(p.mean() / p.std(ddof=1) * np.sqrt(252)) if p.std(ddof=1) > 0 else 0.0
        dd = float((nav / np.maximum.accumulate(nav) - 1).min())
        win = float((p > 0).mean())
        yearly = {}
        for y in sorted(set(self.dates.year)):
            m = self.dates.year == y
            yearly[str(y)] = float((np.prod(1 + p[m]) - 1) * 100)
        return dict(name=self.name, signal=self.signal, vol_target=self.vol_target,
                    cost_bp=self.cost_bp, use_tucker=self.use_tucker,
                    total=total, ann=ann, sharpe=sharpe, dd=dd, win=win, yearly=yearly), nav