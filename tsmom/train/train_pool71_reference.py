#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扩展池 2022~2026 回测:
- 模型池 16只(2016-08前上市, 训练 2016-08~2021-12): SLP/MLP/CNN/LSTM 4模型张量信号
- 规则池 72只(全池): 等权全仓 / 动量TopK × 组合层三件套
- 组合层: regime 等权净值>MA200 + 波动率目标化15% cap2 + 空仓现金2%
- 输出 逐年收益/回撤/胜率, 重点2026; 全量 JSON + nav
"""
import sys, os, json, time
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stmom as S

torch.set_num_threads(4)
torch.manual_seed(0)
np.random.seed(0)

TRAIN_START = "2016-08-09"; TRAIN_END = "2021-12-31"
EVAL_START = "2022-01-01"
OUT = "stmom_pool71_out"; os.makedirs(OUT, exist_ok=True)

class SLP(nn.Module):
    def __init__(self, D, N, hidden=None):
        super().__init__(); self.net = nn.Linear(D, N)
    def forward(self, x): return torch.tanh(self.net(x))

class MLP(nn.Module):
    def __init__(self, D, N, hidden=64):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(D, hidden), nn.GELU(), nn.Linear(hidden, N))
    def forward(self, x): return torch.tanh(self.net(x))

class CNNModel(nn.Module):
    def __init__(self, N, L, F, hidden=64):
        super().__init__()
        C1, C2 = max(8, hidden // 4), max(8, hidden // 2)
        self.causal = nn.Sequential(
            nn.Conv1d(F, C1, kernel_size=2, padding=1), nn.GELU(),
            nn.Conv1d(C1, C2, kernel_size=2, padding=1), nn.GELU())
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.mlp = nn.Sequential(nn.Linear(N * C2, hidden), nn.GELU(), nn.Linear(hidden, N))
    def forward(self, x):
        T, N, L, F = x.shape
        x = x.permute(0, 1, 3, 2).reshape(T * N, F, L)
        x = self.causal(x)[:, :, :L]
        x = self.pool(x).squeeze(-1).reshape(T, N, -1).reshape(T, -1)
        return torch.tanh(self.mlp(x))

class LSTMModel(nn.Module):
    def __init__(self, N, L, F, hidden=64):
        super().__init__()
        self.lstm = nn.LSTM(input_size=N * F, hidden_size=hidden, batch_first=True)
        self.head = nn.Linear(hidden, N)
    def forward(self, x):
        out, _ = self.lstm(x)
        return torch.tanh(self.head(out[:, -1, :]))

MODELS = {"SLP": SLP, "MLP": MLP, "CNN": CNNModel, "LSTM": LSTMModel}

def build_loss(model, Xb, g, l1, trn):
    if isinstance(model, CNNModel):
        N = g.shape[1]; xin = Xb.view(Xb.shape[0], N, S.L_WINDOW, 8)
    elif isinstance(model, LSTMModel):
        N = g.shape[1]; xin = Xb.view(Xb.shape[0], S.L_WINDOW, N * 8)
    else:
        xin = Xb
    s = model(xin)
    pi = s * g
    mu = pi.mean(dim=0); sd = pi.std(dim=0, unbiased=False) + 1e-12
    loss = -(mu / sd).mean()
    if l1 > 0: loss = loss + l1 * sum(p.abs().mean() for p in model.parameters())
    if trn > 0: loss = loss + trn * ((s[1:] - s[:-1]) ** 2).mean()
    return loss, s

def torch_train(model, Xtr, ytr, Vtr, Xva, yva, Vva, hparams):
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
        model.eval()
        with torch.no_grad():
            _, s = build_loss(model, Xv, gva, 0.0, 0.0)
            pi = s * gva
            vsh = float(((pi.mean(dim=0) / (pi.std(dim=0, unbiased=False) + 1e-12)).mean() * np.sqrt(252)))
        if vsh > best_sh:
            best_sh = vsh; best_state = {k: v.clone() for k, v in model.state_dict().items()}; bad = 0
        else:
            bad += 1
        if bad >= patience: break
    if best_state is None:
        best_state = {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    return best_sh

def hp_search(model_cls, N, L, F, D, Xtr, ytr, Vtr, Xva, yva, Vva, n_trials=24):
    rng = np.random.default_rng(42)
    best = None
    for t in range(n_trials):
        hp = {"lr": float(10 ** rng.uniform(-4, -2)), "l1": float(10 ** rng.uniform(-6, -3)),
              "turn": float(10 ** rng.uniform(-6, -3)), "epochs": int(rng.integers(60, 150)),
              "hidden": int(rng.choice([16, 32, 64, 128]))}
        m = model_cls(D, N) if model_cls is SLP else (model_cls(D, N, hp["hidden"]) if model_cls is MLP else model_cls(N, L, F, hp["hidden"]))
        vsh = torch_train(m, Xtr, ytr, Vtr, Xva, yva, Vva, hp)
        if best is None or vsh > best[0]: best = (vsh, hp, t)
        print(f"    trial {t}: val_sharpe={vsh:.3f}", flush=True)
    return best

def fit_seeds(model_cls, N, L, F, D, hp, Xtr, ytr, Vtr, Xva, yva, Vva, Xev, n_seeds=5):
    sigs = []
    for i in range(n_seeds):
        torch.manual_seed(1000 + i)
        m = model_cls(D, N) if model_cls is SLP else (model_cls(D, N, hp["hidden"]) if model_cls is MLP else model_cls(N, L, F, hp["hidden"]))
        torch_train(m, Xtr, ytr, Vtr, Xva, yva, Vva, hp)
        m.eval()
        with torch.no_grad():
            if isinstance(m, CNNModel):
                xev = torch.tensor(Xev.reshape(Xev.shape[0], N, L, F), dtype=torch.float32); s = m(xev).numpy()
            elif isinstance(m, LSTMModel):
                xev = torch.tensor(Xev.reshape(Xev.shape[0], L, N * F), dtype=torch.float32); s = m(xev).numpy()
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

def summarize(p, dates):
    p = np.asarray(p, float)
    fin = np.isfinite(p)
    p = p[fin]; dts = dates[fin]
    if len(p) == 0: return None
    nav = (1 + p).cumprod()
    total = float(nav[-1] - 1); n = len(p)
    ann = float((1 + total) ** (252.0 / n) - 1) if total > -1 else -1.0
    sharpe = float(p.mean() / p.std(ddof=1) * np.sqrt(252)) if p.std(ddof=1) > 0 else 0.0
    dd = float((nav / np.maximum.accumulate(nav) - 1).min())
    win = float((p > 0).mean())
    yearly = {}
    for y in sorted(set(str(d.date())[:4] for d in dts)):
        m = np.array([str(d.date())[:4] == y for d in dts])
        yp = p[m]
        if len(yp): yearly[y] = float((np.prod(1 + yp) - 1) * 100)
    return dict(total=total, ann=ann, sharpe=sharpe, dd=dd, win=win, yearly=yearly)

def ewm_std(s, span=60):
    s = np.asarray(pd.Series(s).ewm(span=span, adjust=False).std().values).copy()
    rs = pd.Series(s).rolling(20, min_periods=5).mean().values
    nan = ~np.isfinite(s); s[nan] = rs[nan]
    s[~np.isfinite(s)] = np.nanstd(s) if np.isfinite(np.nanstd(s)) else 0.0
    return s

def main():
    SMOKE = os.environ.get("SMOKE", "0") == "1"
    n_trials = 2 if SMOKE else 24
    n_seeds = 2 if SMOKE else 5
    t0 = time.time()
    meta = json.load(open("etf_pool_meta.json"))

    # ---------- 模型池: 4模型 ----------
    mpool = meta["model_pool"]
    raw = pd.read_csv("data_ashare/pool_model.csv", index_col=0, parse_dates=True)
    df = raw[mpool].sort_index()
    returns = df.pct_change()
    Xdf, Sigmadf, _ = S.build_tensor(df)
    X, y, V, dates = S.make_samples(Xdf, Sigmadf, returns, list(df.columns))
    tm = (dates >= pd.Timestamp(TRAIN_START)) & (dates <= pd.Timestamp(TRAIN_END))
    em = dates >= pd.Timestamp(EVAL_START)
    Xtr_all, ytr_all, Vtr_all = X[tm], y[tm], V[tm]
    Xev, yev, Vev = X[em], y[em], V[em]
    print(f"模型池 {len(mpool)}只 | 训练样本 {len(Xtr_all)} ({dates[tm][0].date()}~{dates[tm][-1].date()}) | 回测 {len(Xev)} ({dates[em][0].date()}~{dates[em][-1].date()})", flush=True)
    split = int(len(Xtr_all) * 0.9)
    N = len(mpool); L = S.L_WINDOW; F = 8; D = X.shape[1]
    ev_dates = dates[em]
    r_m = returns[mpool].reindex(ev_dates).ffill().values
    all_out = {}
    for mname, mcls in MODELS.items():
        print(f"\n=== {mname} ===", flush=True)
        best_sh, hp, bt = hp_search(mcls, N, L, F, D,
                                    Xtr_all[:split], ytr_all[:split], Vtr_all[:split],
                                    Xtr_all[split:], ytr_all[split:], Vtr_all[split:],
                                    n_trials=n_trials)
        if SMOKE: hp = dict(hp, epochs=40)
        print(f"  best trial {bt}: val_sharpe={best_sh:.3f} hp={hp}", flush=True)
        s_all, sigs = fit_seeds(mcls, N, L, F, D, hp, Xtr_all[:split], ytr_all[:split], Vtr_all[:split],
                                Xtr_all[split:], ytr_all[split:], Vtr_all[split:], Xev, n_seeds=n_seeds)
        corr = {mpool[j]: _spearman(np.roll(s_all, 1, axis=0)[:, j], r_m[:, j]) for j in range(N)}
        all_out[mname] = {"s": s_all, "hp": hp, "val_sharpe": best_sh, "corr": corr, "seeds": sigs}
        np.savez(f"{OUT}/sig_{mname}.npz", dates=np.array([str(d.date()) for d in ev_dates]),
                 s=s_all, **{f"s{i}": sigs[i] for i in range(len(sigs))})
    np.savez(f"{OUT}/sigs_all.npz", **{m: all_out[m]["s"] for m in all_out},
             dates=np.array([str(d.date()) for d in ev_dates]))

    # ---------- 组合层 (模型池的等权净值做regime) ----------
    ret_pool = returns[mpool].mean(axis=1)
    ew_nav = (1 + ret_pool).cumprod()
    def trend_switch(px, k):
        m = px.rolling(k, min_periods=k).mean()
        s = (px > m).astype(float)
        return s.shift(1).fillna(0.0)
    # MA 在训练段选样
    trn_nav = ew_nav.loc[:TRAIN_END]
    scores = {}
    for k in [20, 60, 120, 200]:
        sw = trend_switch(trn_nav, k)
        on = sw.sum(); tot = len(sw)
        # 训练段该标的等权开仓收益
        rtr = ret_pool.loc[trn_nav.index]
        per = (sw * rtr).sum() / rtr.sum() if rtr.sum() != 0 else 0
        scores[k] = round(on / tot * 100, 1)
        print(f"  训练段 MA{k}: 开仓 {on:.0f}/{tot} 天 ({on/tot*100:.0f}%)")
    best_k = 200
    regime = trend_switch(ew_nav, best_k).reindex(ev_dates).fillna(0.0).values

    def run_sig(s, regime_on, vol_target=0.15, cap=2.0, cash=0.02):
        sp = np.roll(s, 1, axis=0); sp[0] = 0
        w = np.maximum(sp, 0.0)
        p = (w * r_m).mean(axis=1)
        if regime_on is not None:
            p = p * regime_on
            if cash > 0: p = p + (1 - regime_on) * (cash / 252.0)
        if vol_target is not None:
            sd = ewm_std(p, 60)
            p = p * np.minimum(vol_target / np.sqrt(252) / (sd + 1e-9), cap)
        return p

    model_rows = {}
    for m in MODELS:
        s = all_out[m]["s"]
        r0 = summarize(run_sig(s, None), ev_dates)
        r1 = summarize(run_sig(s, regime, 0.15, 2.0, 0.02), ev_dates)
        r1x = summarize(run_sig(s, regime, 0.15, 1.0, 0.02), ev_dates)
        model_rows[m] = {"long": r0, "opt_EW": r1, "opt_nolev": r1x}
        print(f"[{m:4s}] long: 总{r0['total']*100:+7.1f}% 年化{r0['ann']*100:+5.1f}% 夏普{r0['sharpe']:.2f} 回撤{r0['dd']*100:5.1f}% | "
              f"opt : 总{r1['total']*100:+7.1f}% 年化{r1['ann']*100:+5.1f}% 夏普{r1['sharpe']:.2f} 回撤{r1['dd']*100:5.1f}% 逐年{ {k: round(v,1) for k,v in r1['yearly'].items()} }", flush=True)
    s_ens = np.mean([all_out[m]["s"] for m in MODELS], axis=0)
    ens_long = summarize(run_sig(s_ens, None), ev_dates)
    ens_opt = summarize(run_sig(s_ens, regime, 0.15, 2.0, 0.02), ev_dates)
    model_rows["ENS"] = {"long": ens_long, "opt_EW": ens_opt}
    print(f"[ENS ] long: 总{ens_long['total']*100:+7.1f}% 年化{ens_long['ann']*100:+5.1f}% | opt: 总{ens_opt['total']*100:+7.1f}% 年化{ens_opt['ann']*100:+5.1f}% 回撤{ens_opt['dd']*100:5.1f}%", flush=True)

    # ---------- 规则池: 全池规则 ----------
    rpool = meta["rules_pool"]
    rawR = pd.read_csv("data_ashare/pool_rules71.csv", index_col=0, parse_dates=True)
    dfR = rawR[rpool].reindex(ev_dates)
    retR = dfR.pct_change()
    rR = retR.values

    def run_rule(sel, sgn, vol_target=0.15, cap=2.0, cash=0.02):
        # sel: (T,N), 1=选中; 每行仅对有限标的求均值
        num = np.where(np.isfinite(rR) & (sel > 0.5), rR, np.nan)
        p = np.nanmean(num, axis=1)
        p = np.where(np.isfinite(p), p, 0.0)
        if sgn is not None:
            p = p * sgn
            if cash > 0: p = p + (1 - sgn) * (cash / 252.0)
        if vol_target is not None:
            sd = ewm_std(p, 60)
            p = p * np.minimum(vol_target / np.sqrt(252) / (sd + 1e-9), cap)
        return p

    def momentum_sel(k, top):
        mom = (dfR / dfR.shift(k) - 1.0).shift(1).values  # 截至 t-1
        sel = np.zeros((len(ev_dates), len(rpool)))
        for i in range(len(ev_dates)):
            row = mom[i]
            ok = np.isfinite(row)
            if ok.sum() >= top:
                idx = np.where(ok)[0][np.argsort(row[ok])[-top:]]
                sel[i, idx] = 1.0
        return sel

    rules = {}
    selAll = np.ones((len(ev_dates), len(rpool)))
    rules["等权全仓×三件套"] = run_rule(selAll, regime, 0.15, 2.0, 0.02)
    for k in [21, 63, 126]:
        for top in [3, 5, 8]:
            key = f"动量{k}日Top{top}×三件套"
            rules[key] = run_rule(momentum_sel(k, top), regime, 0.15, 2.0, 0.02)
    bh = run_rule(selAll, None, None, 1.0, 0.0)
    rules["基准_72池等权买入持有"] = bh
    rule_rows = {k: summarize(p, ev_dates) for k, p in rules.items()}
    for k, rr in rule_rows.items():
        print(f"[{k:22s}] 总{rr['total']*100:+7.1f}% 年化{rr['ann']*100:+5.1f}% 夏普{rr['sharpe']:.2f} 回撤{rr['dd']*100:5.1f}% 胜率{rr['win']*100:.0f}% | 逐年{ {y: round(v,1) for y,v in rr['yearly'].items()} }", flush=True)

    # ---------- 保存 ----------
    out = {"meta": {"model_pool": mpool, "rules_pool": rpool, "names": meta["names"],
                    "train": f"{TRAIN_START}~{TRAIN_END}", "eval": f"{ev_dates[0].date()}~{ev_dates[-1].date()}",
                    "n_train": int(len(Xtr_all)), "n_eval": int(len(Xev)), "regime_k": best_k,
                    "desc": f"扩展池: 模型16只(训练{TRAIN_START}~{TRAIN_END}) + 规则72只, 回测 2022~2026"}}
    out["models"] = {m: {"metrics": model_rows[m], "corr": all_out[m]["corr"], "hp": all_out[m]["hp"]} for m in MODELS}
    out["models"]["ENS"] = {"metrics": model_rows["ENS"]}
    out["rules"] = rule_rows
    json.dump(out, open(f"{OUT}/metrics.json", "w"), indent=1, default=str)

    nav_out = {"dates": [str(d.date()) for d in ev_dates]}
    for m in MODELS:
        nav_out[f"nav_{m}_long"] = (1 + run_sig(all_out[m]["s"], None)).cumprod().tolist()
        nav_out[f"nav_{m}_opt"] = (1 + run_sig(all_out[m]["s"], regime, 0.15, 2.0, 0.02)).cumprod().tolist()
    nav_out["nav_ENS_opt"] = (1 + run_sig(s_ens, regime, 0.15, 2.0, 0.02)).cumprod().tolist()
    for k, p in rules.items():
        nav_out[f"nav_{k}"] = (1 + p).cumprod().tolist()
    nav_out["regime"] = list(regime)
    json.dump(nav_out, open(f"{OUT}/nav.json", "w"), default=float)
    print(f"\n耗时 {time.time()-t0:.0f}s -> {OUT}/", flush=True)

if __name__ == "__main__":
    main()