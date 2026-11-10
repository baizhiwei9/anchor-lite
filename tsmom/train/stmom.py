#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
时空动量 (Spatio-Temporal Momentum, STMOM) 复现
参考: Tan, Roberts & Zohren (2023) JFDS "Spatio-Temporal Momentum"
复现口径:
  - 资产池: 9个国际指数 (对齐面板, 2000-01 ~ 2026-09)
  - 特征: 波动率归一化收益率(lag 1/21/63/126/252) + 波动率归一化MACD(8_24/16_48/32_96)
  - 时空张量: (N, L=5, F=8), 展平输入
  - 模型: SLP (单层感知器), 输出 tanh 信号
  - 损失: 等权各资产 Sharpe + L1 收缩 + 换手率正则 (局部小批量/顺序批)
  - 波动率目标化: 60日EWM波动率, 年化目标 15%
  - 训练: 2000~2021 前90%训练后10%验证, Adam, 早停 patience=25, 随机搜索超参
  - 回测: 2022-01-01 ~ 2026-09-18 完全样本外, 固定模型权重
"""
import numpy as np
import pandas as pd
import json
import os
import time
from datetime import datetime

# ---------------- 配置 ----------------
DATA_CSV = "data/aligned_close.csv"
TRAIN_END = "2021-12-31"   # 训练/验证截止(2022年之前)
EVAL_START = "2022-01-01"  # 样本外回测起点
TARGET_VOL = 0.15          # 年化目标波动率
VOL_SPAN = 60              # 波动率EWM span
L_WINDOW = 5               # 时间窗口
LAGS = [1, 21, 63, 126, 252]           # 收益率滞后
MACD_PAIRS = [(8, 24), (16, 48), (32, 96)]  # MACD 短/长半衰期
MACD_STD = 63             # MACD 归一化滚动std
SEED_BASE = 42
OUT_DIR = "stmom_out"

os.makedirs(OUT_DIR, exist_ok=True)

# ---------------- 数据加载与基础量 ----------------
def load_data():
    df = pd.read_csv(DATA_CSV, index_col=0, parse_dates=True)
    df = df.sort_index()
    return df

def ewm_std(series, span):
    """指数加权移动标准差(样本, ddof=1), 与pandas ewm().std()一致"""
    return series.ewm(span=span, adjust=False).std()

def build_asset_features(close):
    """单资产: 返回特征矩阵 T x (5+3), MACD与收益特征已波动率归一化"""
    price = close
    ret = price.pct_change()
    sigma_d = ewm_std(ret, VOL_SPAN)  # 日波动率, 指数加权
    feats = []
    # 1) 波动率归一化收益率 lag
    for k in LAGS:
        r_k = price / price.shift(k) - 1.0
        feats.append(r_k / sigma_d)
    # 2) 波动率归一化 MACD
    for sht, lng in MACD_PAIRS:
        alpha_s = 1.0 - 0.5 ** (1.0 / sht)
        alpha_l = 1.0 - 0.5 ** (1.0 / lng)
        ema_s = price.ewm(alpha=alpha_s, adjust=False).mean()
        ema_l = price.ewm(alpha=alpha_l, adjust=False).mean()
        macd = ema_s - ema_l
        macd_std = price.rolling(MACD_STD).std()
        feats.append(macd / macd_std)
    F = pd.concat(feats, axis=1)
    F.columns = [f"ret_{k}" for k in LAGS] + [f"macd_{s}_{l}" for s, l in MACD_PAIRS]
    return F, sigma_d

def build_tensor(df):
    """对所有资产构建 特征表格 (按日期索引的资产x特征多列) 与 sigma表"""
    feat_parts, sigma_parts = [], {}
    names = df.columns
    for c in names:
        F, sig = build_asset_features(df[c])
        F.columns = [f"{c}__{col}" for col in F.columns]
        feat_parts.append(F)
        sigma_parts[c] = sig
    Xdf = pd.concat(feat_parts, axis=1)
    Sigmadf = pd.DataFrame(sigma_parts)
    return Xdf, Sigmadf, list(names)

def make_samples(Xdf, Sigmadf, returns, names):
    """
    时空张量样本: 对每个日期 t (已有 t 及之前 L 期特征),
    X: (N, L, F) 展平为 D 向量; y: 次日收益向量 (N,) 用于 Sharpe
    返回 X (T, D), y (T, N), 日期索引, vol_scale 表 (T, N) [target/sigma_ann]
    """
    D_feat = 5 + len(MACD_PAIRS)  # 8
    N = len(names)
    # 特征值矩阵
    Xmat = Xdf.values            # (T, N*F)
    dates = Xdf.index
    # 次日收益
    ret_all = returns[names]     # 与 Xdf 同索引
    # 波动率缩放因子 (t 时刻事前波动率)
    sigma_d = Sigmadf
    vol_scale = TARGET_VOL / (sigma_d * np.sqrt(252))  # 年化目标/年化波动率

    Xs, ys, vscales, idxs = [], [], [], []
    Fmap = list(range(D_feat))
    for i in range(L_WINDOW - 1, len(Xmat)):
        # X: 取 i-L+1..i 共 L 个时间步
        block = Xmat[i - L_WINDOW + 1: i + 1]           # (L, N*F)
        # 重排为 (N, L, F) -> 展平 (N*L*F,)
        block = block.reshape(L_WINDOW, N, D_feat)       # (L, N, F)
        x = block.transpose(1, 0, 2).reshape(-1)          # (N*L*F,)
        # y: 次日收益 (t -> t+1)
        if i + 1 >= len(Xmat):
            break
        y = ret_all.iloc[i + 1].values                    # (N,)
        vs = vol_scale.iloc[i].values                     # (N,) 事前
        Xs.append(x)
        ys.append(y)
        vscales.append(vs)
        idxs.append(dates[i])
    Xs = np.array(Xs, dtype=np.float64)
    ys = np.array(ys, dtype=np.float64)
    vscales = np.array(vscales, dtype=np.float64)
    # 过滤含 NaN 的样本 (起始段滚动窗口不足)
    fin = np.isfinite(Xs).all(axis=1) & np.isfinite(ys).all(axis=1) & np.isfinite(vscales).all(axis=1)
    if not fin.all():
        print(f"  过滤 {int((~fin).sum())} 个含NaN样本 (起始段滚动窗口)")
    return Xs[fin], ys[fin], vscales[fin], pd.DatetimeIndex(np.array(idxs)[fin])

# ---------------- SLP 模型与损失 ----------------
def init_slp(D_in, N_out, seed):
    rng = np.random.default_rng(seed)
    scale = 1.0 / np.sqrt(D_in)
    W = rng.uniform(-scale, scale, size=(N_out, D_in))
    b = np.zeros(N_out)
    return W, b

def forward(X, W, b):
    z = X @ W.T + b
    s = np.tanh(z)
    return z, s

def sharpe_loss_grad(s, g, T):
    """
    s: (T, N) 信号; g: (T, N) 波动率缩放后次日收益 g = vol_scale * r_next
    π = s*g 每资产; Sharpe_i = mean/std*sqrt(252)
    返回 loss(标量) 与 dloss/ds (T, N)
    """
    pi = s * g
    mu = pi.mean(axis=0, keepdims=True)
    var = pi.var(axis=0, keepdims=True)
    sd = np.sqrt(var + 1e-12)
    sharpe_i = mu / sd  # 忽略 sqrt(252) 常数, 之后乘
    loss = -sharpe_i.mean()  # 等权负 Sharpe
    # dL/ds = (1/T) * [ mu*(pi-mu)/sd^3 - 1/sd ] * g   (推导: d(-mu/sd)/dpi)
    dpi = (1.0 / T) * (mu * (pi - mu) / (sd ** 3) - 1.0 / sd)
    ds = dpi * g
    return loss, ds

def turnover_reg(seq_s):
    """顺序批内相邻样本信号差 L2: mean((s_t - s_{t-1})^2)"""
    if len(seq_s) < 2:
        return 0.0, np.zeros_like(seq_s)
    d = np.diff(seq_s, axis=0)
    loss = np.mean(d ** 2)
    grad = np.zeros_like(seq_s)
    grad[1:] += 2 * d
    grad[:-1] -= 2 * d
    grad = grad / (len(seq_s))
    return loss, grad

def l1_reg(W):
    return np.mean(np.abs(W)), np.sign(W) / W.size

def train_model(Xtr, ytr, vtr, Xva, yva, vva, W, b, hparams, rng, verbose=False):
    """Adam 训练, 早停(validation 负Sharpe均值)"""
    lr = hparams["lr"]
    l1 = hparams["l1"]
    trn = hparams["turn"]
    batch = hparams.get("batch", 64)
    max_epochs = hparams.get("epochs", 120)
    patience = 25

    N = ytr.shape[1]
    T = len(Xtr)

    mW = np.zeros_like(W); vW = np.zeros_like(W)
    mb = np.zeros_like(b); vb = np.zeros_like(b)
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    tstep = 0

    best_sharpe = -np.inf
    best_weights = (W.copy(), b.copy())
    bad_epochs = 0

    def eval_sharpe(X, y, v, W, b):
        _, s = forward(X, W, b)
        g = v * y
        pi = s * g
        mu = pi.mean(axis=0); sd = pi.std(axis=0) + 1e-12
        shp = np.mean(mu / sd) * np.sqrt(252)
        return shp, s

    for ep in range(max_epochs):
        # 全量梯度: 数据集 ~4000x360x9, 可行且稳定
        z, s = forward(Xtr, W, b)
        g = vtr * ytr
        loss_shp, ds = sharpe_loss_grad(s, g, T)
        loss = loss_shp
        grad_W = ds.T @ Xtr  # (N, D)
        grad_b = ds.sum(axis=0)

        if l1 > 0:
            l_l1, g_l1 = l1_reg(W)
            loss = loss + l1 * l_l1
            grad_W = grad_W + l1 * g_l1

        if trn > 0:
            l_trn, g_trn = turnover_reg(s)
            loss = loss + trn * l_trn
            grad_W = grad_W + trn * (g_trn.T @ Xtr)
            grad_b = grad_b + trn * g_trn.sum(axis=0)

        # Adam
        tstep += 1
        mW = beta1 * mW + (1 - beta1) * grad_W
        vW = beta2 * vW + (1 - beta2) * grad_W ** 2
        mb = beta1 * mb + (1 - beta1) * grad_b
        vb = beta2 * vb + (1 - beta2) * grad_b ** 2
        mW_hat = mW / (1 - beta1 ** tstep); vW_hat = vW / (1 - beta2 ** tstep)
        mb_hat = mb / (1 - beta1 ** tstep); vb_hat = vb / (1 - beta2 ** tstep)
        W -= lr * mW_hat / (np.sqrt(vW_hat) + eps)
        b -= lr * mb_hat / (np.sqrt(vb_hat) + eps)

        # 逐 epoch 验证 + 早停 (patience 个 epoch 无提升)
        vs, _ = eval_sharpe(Xva, yva, vva, W, b)
        if vs > best_sharpe:
            best_sharpe = vs
            best_weights = (W.copy(), b.copy())
            bad_epochs = 0
        else:
            bad_epochs += 1
        if (ep + 1) % 20 == 0 and verbose:
            print(f"    epoch {ep+1}: trn_loss={loss:.4f} val_sharpe={vs:.3f} best={best_sharpe:.3f}")
        if bad_epochs >= patience:
            if verbose:
                print(f"    早停于 epoch {ep+1} (patience={patience}), best_val_sharpe={best_sharpe:.3f}")
            break
    return best_weights, best_sharpe

def random_search(Xtr, ytr, vtr, Xva, yva, vva, names, n_trials=24):
    D_in = Xtr.shape[1]
    N = ytr.shape[1]
    rng = np.random.default_rng(SEED_BASE)
    results = []
    for t in range(n_trials):
        hparams = {
            "lr": float(10 ** rng.uniform(-4, -2)),
            "l1": float(10 ** rng.uniform(-6, -3)),
            "turn": float(10 ** rng.uniform(-6, -3)),
            "epochs": int(rng.integers(60, 150)),
            "batch": 64,
        }
        seed = int(rng.integers(0, 10000))
        W, b = init_slp(D_in, N, seed)
        (Wb, bb), va_sh = train_model(Xtr, ytr, vtr, Xva, yva, vva,
                                              W, b, hparams, np.random.default_rng(seed))
        results.append({"trial": t, "val_sharpe": va_sh,
                        "hparams": hparams, "seed": seed, "W": Wb, "b": bb})
        print(f"  trial {t}: val_sharpe={va_sh:.3f} hparams={hparams}")
    results.sort(key=lambda r: -r["val_sharpe"])
    return results

# ---------------- 回测指标 ----------------
def backtest_metrics(dates, port_ret, annual_ret_dates=None):
    """dates: DatetimeIndex 与 port_ret 对齐 (组合收益 t 为信号 t-1 到 t 的实现日)"""
    df = pd.DataFrame({"ret": port_ret}, index=dates)
    nav = (1 + df["ret"]).cumprod()
    dd = nav / nav.cummax() - 1.0
    max_dd = dd.min()
    total_ret = nav.iloc[-1] - 1.0
    n = len(df)
    years = (df.index[-1] - df.index[0]).days / 365.25
    ann_ret = (nav.iloc[-1]) ** (1 / years) - 1.0 if years > 0 else np.nan
    ann_vol = df["ret"].std() * np.sqrt(252)
    sharpe = (df["ret"].mean() / (df["ret"].std() + 1e-12)) * np.sqrt(252)
    win_rate = (df["ret"] > 0).mean()
    # 年度收益
    yearly = df.groupby(df.index.year)["ret"].apply(lambda r: (1 + r).prod() - 1)
    return {
        "total_ret": total_ret, "ann_ret": ann_ret, "ann_vol": ann_vol,
        "sharpe": sharpe, "max_dd": max_dd, "win_rate": win_rate,
        "years": years, "n_days": n, "yearly": yearly, "nav": nav, "dd": dd,
    }


def main():
    t0 = time.time()
    df = load_data()
    names = list(df.columns)
    returns = df.pct_change()

    Xdf, Sigmadf, names2 = build_tensor(df)
    assert names == names2

    X, y, V, dates = make_samples(Xdf, Sigmadf, returns, names)
    print(f"样本数: {len(X)}, 输入维度: {X.shape[1]}, 资产数: {len(names)}")

    # 训练/验证/回测切分 (按时间)
    train_mask = dates <= pd.Timestamp(TRAIN_END)
    eval_mask = dates >= pd.Timestamp(EVAL_START)
    Xtr_all, ytr_all, Vtr_all = X[train_mask], y[train_mask], V[train_mask]
    Xeval, yeval, Veval = X[eval_mask], y[eval_mask], V[eval_mask]
    print(f"训练(含验证): {Xtr_all.shape[0]} 样本, 回测: {Xeval.shape[0]} 样本")
    # 训练: 前90%, 验证: 后10%
    split = int(len(Xtr_all) * 0.9)
    Xtr, ytr, Vtr = Xtr_all[:split], ytr_all[:split], Vtr_all[:split]
    Xva, yva, Vva = Xtr_all[split:], ytr_all[split:], Vtr_all[split:]
    print(f"  实际训练: {split}, 验证: {len(Xva)}")
    # 保存切分信息
    np.savez(f"{OUT_DIR}/split.npz",
             dates=np.array([str(d.date()) for d in dates]),
             train_mask=train_mask, eval_mask=eval_mask)

    # 随机搜索超参
    print("\n== 随机搜索超参 ==")
    results = random_search(Xtr, ytr, Vtr, Xva, yva, Vva, names, n_trials=24)
    best = results[0]
    print(f"\n最优 trial: val_sharpe={best['val_sharpe']:.3f}")

    # 保存最优模型与其他trial记录
    np.savez(f"{OUT_DIR}/best_model.npz", W=best["W"], b=best["b"],
             val_sharpe=best["val_sharpe"], seed=best["seed"])
    with open(f"{OUT_DIR}/search_results.json", "w") as f:
        json.dump([{k: (v if k not in ("W", "b") else "array") for k, v in r.items()}
                   for r in results], f, indent=1, default=str)

    # ---- 样本外回测 (多个种子取稳健性, 但主模型用最优) ----
    print("\n== 样本外回测 2022-2026 ==")
    # 主模型: 最优超参下, 再跑5个种子取平均组合净值
    trials_re = []
    hp = best["hparams"]
    for i in range(5):
        seed = 1000 + i
        W, b = init_slp(X.shape[1], len(names), seed)
        (Wb, bb), _ = train_model(Xtr, ytr, Vtr, Xva, yva, Vva, W, b, hp,
                                  np.random.default_rng(seed))
        _, s = forward(Xeval, Wb, bb)
        g = Veval * yeval
        port = np.mean(s * g, axis=1)  # 当日: (t-1信号, t收益) 已在 g 中配对
        trials_re.append({"seed": seed, "W": Wb, "b": bb, "port": port,
                          "signals": s, "dates": dates[eval_mask]})
    # 平均净值
    port_all = np.mean([tr["port"] for tr in trials_re], axis=0)
    eval_dates = dates[eval_mask]
    m = backtest_metrics(eval_dates, port_all)
    print(f"总收益: {m['total_ret']*100:.2f}%  年化: {m['ann_ret']*100:.2f}%  "
          f"夏普: {m['sharpe']:.2f}  最大回撤: {m['max_dd']*100:.2f}%  胜率: {m['win_rate']*100:.1f}%")
    print("年度收益:")
    for yr, v in m["yearly"].items():
        print(f"  {yr}: {v*100:.2f}%")
    # 保存回测
    np.savez(f"{OUT_DIR}/backtest.npz", port=port_all,
             nav=m["nav"].values, dd=m["dd"].values,
             dates=np.array([str(d.date()) for d in eval_dates]),
             yearly=m["yearly"].to_dict())
    # 单种子明细 (供胜率/交易质量)
    np.savez(f"{OUT_DIR}/trials.npz",
             seeds=[tr["seed"] for tr in trials_re],
             ports=np.vstack([tr["port"] for tr in trials_re]),
             signals=trials_re[0]["signals"])
    # 全度量JSON
    mjson = {k: (float(v) if not isinstance(v, (pd.Series, pd.DataFrame)) else
                 {str(kk): float(vv) for kk, vv in v.items()})
             for k, v in m.items() if k not in ("nav", "dd", "yearly")}
    mjson["yearly"] = {str(k): float(v) for k, v in m["yearly"].items()}
    with open(f"{OUT_DIR}/metrics.json", "w") as f:
        json.dump(mjson, f, indent=2)
    print(f"\n耗时 {time.time()-t0:.1f}s, 输出目录: {OUT_DIR}/")


if __name__ == "__main__":
    main()