#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STMOM 4模型统一复现 (PyTorch): SLP / MLP / CNN / LSTM

迁移版说明：池与数据路径改为命令行参数（--panel / --pool / --pool-file / --out），
默认仍是 13 池与 assets/panel13.csv；换行业 ETF 或股票 ETF 时无需改代码。
训练执行需 PyTorch；本文件未落地训练，仅提供可复跑入口。
口径与 stmom_ashare_global.py 完全一致:
  13标全球池, 训练 2017-08~2021-12 (90/10 分验证), 回测 2022-01~2026-09 样本外
  特征: 波动率归一化 lag收益(1/21/63/126/252) + 波动率归一化 MACD(8_24/16_48/32_96)
  张量: (N, L=5, F=8)
  损失: 等权各资产 Sharpe + L1 收缩 + 换手率正则, Adam, 早停 patience=25, 随机搜索超参
  回测: 信号 tanh 压缩, long = max(sig,0)*r 等权, 波动率目标化前的组合层
"""
import sys, os, json, time, argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stmom as S

torch.set_num_threads(4)
torch.manual_seed(0)
np.random.seed(0)

# ---------------- 数据 ----------------
DEFAULT_POOL13 = ["sh510300","sh510500","sh510050","sz159915","sz159949",
          "sh512880","sh512660","sh512010",
          "sh513100","sh513500","sz159941","sh513030","sz159920"]
DEFAULT_NAMES = {"sh510300":"沪深300","sh510500":"中证500","sh510050":"上证50","sz159915":"创业板",
         "sz159949":"创业板50","sh512880":"证券","sh512660":"军工","sh512010":"医药",
         "sh513100":"纳指ETF","sh513500":"标普500","sz159941":"纳指100(广发)",
         "sh513030":"德国DAX","sz159920":"恒生"}
S.TRAIN_END = "2021-12-31"; S.EVAL_START = "2022-01-01"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out"); os.makedirs(OUT, exist_ok=True)  # 运行时可用 --out 覆盖

# ---------------- 4 模型定义 ----------------
class SLP(nn.Module):
    """单层感知器: 展平时空张量 -> Linear -> tanh"""
    def __init__(self, D, N, hidden=None):
        super().__init__()
        self.net = nn.Linear(D, N)
    def forward(self, x):  # (T, D)
        return torch.tanh(self.net(x))

class MLP(nn.Module):
    """多层感知器: 展平时空张量 -> Linear -> GELU -> Linear -> tanh"""
    def __init__(self, D, N, hidden=64):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(D, hidden), nn.GELU(), nn.Linear(hidden, N))
    def forward(self, x):
        return torch.tanh(self.net(x))

class CNNModel(nn.Module):
    """自回归因果卷积 + 平均池化 + MLP
    输入时空张量 (T, N, L, F) -> 转 (T, N, F, L), 沿时间 L 做两层因果Conv1d(k=2)
    -> 平均池化 -> MLP 混合截面 -> 输出 (T, N) tanh"""
    def __init__(self, N, L, F, hidden=64):
        super().__init__()
        C1, C2 = max(8, hidden // 4), max(8, hidden // 2)
        self.causal = nn.Sequential(
            nn.Conv1d(F, C1, kernel_size=2, padding=1), nn.GELU(),
            nn.Conv1d(C1, C2, kernel_size=2, padding=1), nn.GELU())
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.mlp = nn.Sequential(nn.Linear(N * C2, hidden), nn.GELU(), nn.Linear(hidden, N))
    def forward(self, x):  # (T, N, L, F)
        T, N, L, F = x.shape
        x = x.permute(0, 1, 3, 2)                     # (T, N, F, L)
        x = x.reshape(T * N, F, L)                    # 合并为 batch (T*N, F, L)
        x = self.causal(x)[:, :, :L]                  # 因果: 去掉未来 padding, (T*N, C2, L)
        x = self.pool(x).squeeze(-1)                  # (T*N, C2)
        x = x.reshape(T, N, -1)                       # (T, N, C2)
        x = x.reshape(T, -1)                          # (T, N*C2)
        return torch.tanh(self.mlp(x))

class LSTMModel(nn.Module):
    """LSTM 门控 + 时间分布全连接: 张量 (T, L, N*F) -> LSTM -> 最后隐状态 -> Linear -> tanh"""
    def __init__(self, N, L, F, hidden=64):
        super().__init__()
        self.lstm = nn.LSTM(input_size=N * F, hidden_size=hidden, batch_first=True)
        self.head = nn.Linear(hidden, N)
    def forward(self, x):  # (T, L, N*F)
        out, _ = self.lstm(x)
        return torch.tanh(self.head(out[:, -1, :]))

MODELS = {"SLP": SLP, "MLP": MLP, "CNN": CNNModel, "LSTM": LSTMModel}

# ---------------- 损失与训练 ----------------
def build_loss(model, Xb, g, l1, trn):
    """loss = 等权Sharpe损失 + L1 + 换手; 返回 loss 与信号 (T,N)"""
    if isinstance(model, (CNNModel,)):
        N = g.shape[1]
        xin = Xb.view(Xb.shape[0], N, S.L_WINDOW, 8)
    elif isinstance(model, LSTMModel):
        N = g.shape[1]
        xin = Xb.view(Xb.shape[0], S.L_WINDOW, N * 8)
    else:
        xin = Xb
    s = model(xin)
    pi = s * g
    mu = pi.mean(dim=0); sd = pi.std(dim=0, unbiased=False) + 1e-12
    loss = - (mu / sd).mean()                       # 等权各资产 Sharpe (负)
    if l1 > 0:
        loss = loss + l1 * sum(p.abs().mean() for p in model.parameters())
    if trn > 0:
        d = s[1:] - s[:-1]
        loss = loss + trn * (d ** 2).mean()
    return loss, s

def torch_train(model, Xtr, ytr, Vtr, Xva, yva, Vva, hparams):
    """Adam + 早停 (val Sharpe). 返回最优权重与 val_sharpe"""
    opt = torch.optim.Adam(model.parameters(), lr=hparams["lr"])
    max_ep = hparams.get("epochs", 120); patience = 25
    best_sh, best_state, bad = -np.inf, None, 0
    gtr = torch.tensor(Vtr * ytr, dtype=torch.float32)
    gva = torch.tensor(Vva * yva, dtype=torch.float32)
    Xt = torch.tensor(Xtr, dtype=torch.float32)
    Xv = torch.tensor(Xva, dtype=torch.float32)
    for ep in range(max_ep):
        model.train(); opt.zero_grad()
        loss, _ = build_loss(model, Xt, gtr, hparams["l1"], hparams["turn"])
        loss.backward(); opt.step()
        # 逐 epoch 验证 + 早停 (patience 个 epoch 无提升)
        model.eval()
        with torch.no_grad():
            _, s = build_loss(model, Xv, gva, 0.0, 0.0)
            pi = s * gva
            mu = pi.mean(dim=0); sd = pi.std(dim=0, unbiased=False) + 1e-12
            vsh = float(((mu / sd).mean() * np.sqrt(252)))
        if vsh > best_sh:
            best_sh = vsh; best_state = {k: v.clone() for k, v in model.state_dict().items()}; bad = 0
        else:
            bad += 1
        if bad >= patience:
            break
    if best_state is None:
        best_state = {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    return best_sh

def hp_search(model_cls, N, L, F, D, Xtr, ytr, Vtr, Xva, yva, Vva, n_trials=24):
    rng = np.random.default_rng(42)
    best = None
    for t in range(n_trials):
        hp = {
            "lr": float(10 ** rng.uniform(-4, -2)),
            "l1": float(10 ** rng.uniform(-6, -3)),
            "turn": float(10 ** rng.uniform(-6, -3)),
            "epochs": int(rng.integers(60, 150)),
            "hidden": int(rng.choice([16, 32, 64, 128])),
        }
        if model_cls is SLP:
            m = model_cls(D, N)
        elif model_cls is MLP:
            m = model_cls(D, N, hp["hidden"])
        elif model_cls is CNNModel:
            m = model_cls(N, L, F, hp["hidden"])
        else:
            m = model_cls(N, L, F, hp["hidden"])
        vsh = torch_train(m, Xtr, ytr, Vtr, Xva, yva, Vva, hp)
        if best is None or vsh > best[0]:
            best = (vsh, hp, t)
        print(f"    trial {t}: val_sharpe={vsh:.3f} hp={hp}", flush=True)
    return best

def fit_seeds(model_cls, N, L, F, D, hp, Xtr, ytr, Vtr, Xva, yva, Vva, Xev, n_seeds=5):
    """用最优 hp 训练 5 个种子, 返回全样本外信号均值 (T,N) 与各种子信号"""
    sigs = []
    for i in range(n_seeds):
        torch.manual_seed(1000 + i)
        if model_cls is SLP:
            m = model_cls(D, N)
        elif model_cls is MLP:
            m = model_cls(D, N, hp["hidden"])
        else:
            m = model_cls(N, L, F, hp["hidden"])
        torch_train(m, Xtr, ytr, Vtr, Xva, yva, Vva, hp)
        m.eval()
        with torch.no_grad():
            if isinstance(m, CNNModel):
                xev = torch.tensor(Xev.reshape(Xev.shape[0], N, L, F), dtype=torch.float32)
                s = m(xev).numpy()
            elif isinstance(m, LSTMModel):
                xev = torch.tensor(Xev.reshape(Xev.shape[0], L, N * F), dtype=torch.float32)
                s = m(xev).numpy()
            else:
                s = m(torch.tensor(Xev, dtype=torch.float32)).numpy()
        sigs.append(s)
    return np.mean(sigs, axis=0), sigs

def _spearman(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 3: return np.nan
    rx = x.argsort().argsort().astype(float); ry = y.argsort().argsort().astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])

def report_model(name, s_all, ev_dates, r, pool, names_map):
    T = len(ev_dates); N = r.shape[1]
    sig_prev = np.roll(s_all, 1, axis=0); sig_prev[0] = 0
    p_ls   = (sig_prev * r).mean(axis=1)
    p_long = (np.maximum(sig_prev, 0) * r).mean(axis=1)
    oh = {}
    for tag, p in [("ls", p_ls), ("long", p_long)]:
        nav = (1 + p).cumprod()
        total = float(nav[-1] - 1); n = len(p)
        ann = float((1 + total) ** (252.0 / n) - 1) if n > 0 else np.nan
        sharpe = float(p.mean() / p.std(ddof=1) * np.sqrt(252)) if p.std(ddof=1) > 0 else 0.0
        dd = float((nav / np.maximum.accumulate(nav) - 1).min())
        win = float((p > 0).mean())
        yearly = {}
        yrs = sorted(set(str(d.date())[:4] for d in ev_dates))
        for y in yrs:
            m = np.array([str(d.date())[:4] == y for d in ev_dates])
            yp = p[m]
            if len(yp): yearly[y] = float((np.prod(1 + yp) - 1) * 100)
        oh[tag] = dict(total=total, ann=ann, sharpe=sharpe, dd=dd, win=win, yearly=yearly)
        print(f"  [{name}] {tag}: 总{total*100:+.1f}% 年化{ann*100:+.1f}% 夏普{sharpe:.2f} "
              f"回撤{dd*100:+.1f}% 胜率{win*100:.0f}% | 逐年 { {k: round(v,1) for k,v in yearly.items()} }", flush=True)
    corr = {names_map.get(c, c): _spearman(sig_prev[:, j], r[:, j]) for j, c in enumerate(pool)}
    return dict(metrics=oh, corr=corr, s=s_all)

def main():
    ap = argparse.ArgumentParser(description="STMOM 四模型训练（可切换标的池，不执行训练则只做数据校验）")
    ap.add_argument("--panel", default=os.path.join(HERE, "..", "assets", "panel13.csv"))
    ap.add_argument("--pool", default="", help="逗号分隔的标的代码，留空则用面板全部列")
    ap.add_argument("--pool-file", default="", help="JSON 数组文件（标的代码列表）")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--train-end", default="2021-12-31")
    ap.add_argument("--eval-start", default="2022-01-01")
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    global OUT
    OUT = args.out; os.makedirs(OUT, exist_ok=True)
    S.TRAIN_END = args.train_end; S.EVAL_START = args.eval_start
    SMOKE = args.smoke or os.environ.get("SMOKE", "0") == "1"
    n_trials = 2 if SMOKE else 24
    n_seeds = 2 if SMOKE else 5
    t0 = time.time()
    raw = pd.read_csv(args.panel, index_col=0, parse_dates=True)
    if args.pool_file:
        pool = json.load(open(args.pool_file, encoding="utf-8"))
    elif args.pool:
        pool = [x.strip() for x in args.pool.split(",") if x.strip()]
    else:
        pool = [c for c in raw.columns]
    names_map = dict(DEFAULT_NAMES)
    missing = [c for c in pool if c not in raw.columns]
    if missing:
        print(f"[warn] 面板缺少 {len(missing)} 个标的，已跳过: {missing[:5]}", flush=True)
    pool = [c for c in pool if c in raw.columns]
    df = raw[pool]; names = list(df.columns)
    returns = df.pct_change()
    Xdf, Sigmadf, _ = S.build_tensor(df)
    X, y, V, dates = S.make_samples(Xdf, Sigmadf, returns, names)
    tm = dates <= pd.Timestamp(S.TRAIN_END); em = dates >= pd.Timestamp(S.EVAL_START)
    Xtr_all, ytr_all, Vtr_all = X[tm], y[tm], V[tm]
    Xev, yev, Vev = X[em], y[em], V[em]
    print(f"训练样本 {len(Xtr_all)} ({dates[tm][0].date()}~{dates[tm][-1].date()}) "
          f"| 回测样本 {len(Xev)} ({dates[em][0].date()}~{dates[em][-1].date()})", flush=True)
    split = int(len(Xtr_all) * 0.9)
    N = len(names); L = S.L_WINDOW; F = 8; D = X.shape[1]
    ev_dates = dates[em]
    r = returns[names].reindex(ev_dates).values
    all_out = {}
    for mname, mcls in MODELS.items():
        print(f"\n=== {mname} ===", flush=True)
        best_sh, hp, bt = hp_search(mcls, N, L, F, D,
                                    Xtr_all[:split], ytr_all[:split], Vtr_all[:split],
                                    Xtr_all[split:], ytr_all[split:], Vtr_all[split:],
                                    n_trials=n_trials)
        if SMOKE:
            hp = dict(hp, epochs=40)
        print(f"  best trial {bt}: val_sharpe={best_sh:.3f} hp={hp}", flush=True)
        s_all, sigs = fit_seeds(mcls, N, L, F, D, hp, Xtr_all[:split], ytr_all[:split], Vtr_all[:split],
                                Xtr_all[split:], ytr_all[split:], Vtr_all[split:], Xev, n_seeds=n_seeds)
        out = report_model(mname, s_all, ev_dates, r, pool, names_map)
        out["hp"] = hp; out["best_val_sharpe"] = best_sh; out["seed_sigs"] = sigs
        all_out[mname] = out
        np.savez(f"{OUT}/sig_{mname}.npz", dates=np.array([str(d.date()) for d in ev_dates]),
                 s=s_all, **{f"s{i}": sigs[i] for i in range(len(sigs))})
    json.dump({m: {k: (v if k != "s" and k != "seed_sigs" else None) for k, v in all_out[m].items()}
               for m in all_out}, open(f"{OUT}/metrics.json", "w"), indent=1, default=str)
    print(f"\n耗时 {time.time()-t0:.0f}s -> {OUT}/", flush=True)

if __name__ == "__main__":
    main()