"""Anchor-Lite 核心：特征构建 + 滚动 HMM（滤波概率）"""
import os, sys, json, warnings
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from hmmlearn.hmm import GaussianHMM

warnings.filterwarnings("ignore")
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
CUTOFF = "2026-04-14"

def load(name, col="close"):
    df = pd.read_csv(os.path.join(DATA, name + ".csv"))
    df["date"] = pd.to_datetime(df["date"])
    df = df[["date", col]].dropna().drop_duplicates("date").sort_values("date")
    df = df[df["date"] <= CUTOFF]
    return df.set_index("date")[col].astype(float)

def build_features():
    hs300 = load("etf_hs300"); kc50 = load("etf_kc50")
    oil_etf = load("etf_oil"); nf = load("etf_nonferrous")
    kospi = load("idx_kospi"); spx = load("idx_spx")
    wti = load("fut_wti"); cu = load("fut_copper")
    t10 = load("fut_t10"); tf5 = load("fut_tf5"); ts2 = load("fut_ts2")
    qv50 = load("qvix_50etf"); qvkb = load("qvix_kcb")

    y = pd.read_csv(os.path.join(DATA, "yields.csv"))
    y["date"] = pd.to_datetime(y["日期"])
    y = y[y["date"] <= CUTOFF].set_index("date").sort_index()
    us2 = pd.to_numeric(y["美国国债收益率2年"], errors="coerce")
    us10 = pd.to_numeric(y["美国国债收益率10年"], errors="coerce")
    cn10 = pd.to_numeric(y["中国国债收益率10年"], errors="coerce")

    cal = hs300.index  # A股交易日为基准日历
    def reidx(s):
        return s.reindex(cal, method="ffill")

    hs300, kc50, oil_etf, nf = reidx(hs300), reidx(kc50), reidx(oil_etf), reidx(nf)
    kospi, spx, wti, cu = reidx(kospi), reidx(spx), reidx(wti), reidx(cu)
    t10, tf5, ts2 = reidx(t10), reidx(tf5), reidx(ts2)
    qv50, qvkb = reidx(qv50), reidx(qvkb)
    us2, us10, cn10 = reidx(us2), reidx(us10), reidx(cn10)

    def r(s): return np.log(s).diff()
    def vol(s, n): return r(s).rolling(n).std() * np.sqrt(252)

    f = pd.DataFrame(index=cal)
    f["eq_mom"] = (r(hs300).rolling(20).sum()) / vol(hs300, 20)
    f["kc_mom"] = (r(kc50).rolling(20).sum()) / vol(kc50, 20)
    f["em_mom"] = (r(kospi).rolling(20).sum()) / vol(kospi, 20)
    f["oil_mom"] = (r(wti).rolling(20).sum()) / vol(wti, 20)
    f["cu_mom"] = (r(cu).rolling(20).sum()) / vol(cu, 20)
    f["vol_spx"] = vol(spx, 20)
    f["slope_us"] = us10 - us2
    f["d_rate"] = (us10 - us10.shift(20))
    f["corr_eq_bond"] = r(hs300).rolling(60).corr(r(t10))
    f["qvix"] = qv50

    # 交易用收益序列（供回测）
    rets = pd.DataFrame(index=cal)
    rets["hs300"] = hs300.pct_change()
    rets["kc50"] = kc50.pct_change()
    rets["oil"] = oil_etf.pct_change()
    rets["nonferrous"] = nf.pct_change()
    rets["t10"] = t10.pct_change()
    rets["tf5"] = tf5.pct_change()
    rets["kospi"] = kospi.pct_change()
    rets["wti"] = wti.pct_change()
    rets["cu"] = cu.pct_change()
    rets["_hs300_px"] = hs300
    rets["_kc50_px"] = kc50
    rets["_oil_px"] = oil_etf
    rets["_nf_px"] = nf
    rets["_t10_px"] = t10
    rets["_qvix"] = qv50
    rets["_rv20"] = vol(spx, 20) / 100.0
    rets["_rv20_cn"] = vol(hs300, 20)
    return f, rets

# 状态原型（标准化空间）——用于跨期稳定地给隐状态贴经济学标签
PROTO = {
    "低波趋势": [0.9, 0.9, 0.3, -0.9, 0.0, -0.5],
    "通胀交易": [0.2, 0.3, 1.2, 0.2, 0.6, 0.6],
    "高波危机": [-1.2, -1.2, -0.5, 1.5, -0.6, -0.9],
    "震荡未知": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
}
FEATS = ["eq_mom", "em_mom", "oil_mom", "vol_spx", "slope_us", "corr_eq_bond"]
LABELS = list(PROTO.keys())

COV_FLOOR = float(os.environ.get("ANCHOR_COV_FLOOR", "1.5"))
EPS = float(os.environ.get("ANCHOR_SHRINK", "0.15"))   # 模型不确定性收缩：与状态先验混合，避免后验硬化成 0/1

def stationary(A, iters=500):
    v = np.ones(A.shape[0]) / A.shape[0]
    for _ in range(iters):
        v = v @ A
        v = v / v.sum()
    return v

def floor_covars(m):
    """把对角协方差的下限抬到 COV_FLOOR（z 空间），并还原成 (K, F, F) 形状"""
    cv = np.asarray(m.covars_, dtype=float)
    n, f = m.means_.shape
    if cv.ndim == 3:
        d = np.diagonal(cv, axis1=1, axis2=2).copy()
    else:
        d = cv.copy()
    d = np.maximum(d, COV_FLOOR)
    return d

def label_states(means_z):
    P = np.array([PROTO[k] for k in LABELS])
    D = ((means_z[:, None, :] - P[None, :, :]) ** 2).sum(-1)
    r, c = linear_sum_assignment(D)
    order = np.zeros(len(means_z), dtype=int)
    order[c] = r  # 第 c 个标签 -> 状态 r
    return order

def fit_one(X, seed):
    best = None
    for rs in (seed, seed + 7, seed + 13):
        m = GaussianHMM(n_components=4, covariance_type="diag", n_iter=60,
                        tol=1e-4, random_state=rs)
        try:
            m.fit(X)
            ll = m.score(X)
        except Exception:
            continue
        if best is None or ll > best[1]:
            best = (m, ll)
    return best

def forward_filter(m, X):
    """标准前向算法，返回 P(s_t | y_1..t)，可逐步在线更新"""
    n = m.n_components
    A, mu, var = m.transmat_, m.means_, m.covars_
    alpha = m.startprob_.copy()
    out = np.zeros((len(X), n))
    for t, x in enumerate(X):
        lik = np.exp(-0.5 * (((x - mu) ** 2) / var).sum(1)) / np.sqrt((2 * np.pi) ** n * var.prod(1))
        a = lik * alpha
        s = a.sum()
        a = a / s if s > 0 else np.ones(n) / n
        out[t] = a
        alpha = a @ A
        alpha = alpha / alpha.sum()
    return out

def run():
    f, rets = build_features()
    raw = f[FEATS].dropna()
    print("feature span:", raw.index[0].date(), "->", raw.index[-1].date(), "n=", len(raw))

    # 月度重估：每月首个交易日用过去252日窗口拟合
    months = raw.index.to_period("M")
    refit_dates = {}
    for m in months.unique():
        sub = raw[months == m]
        if len(sub):
            refit_dates[m] = sub.index[0]
    refit_dates = dict(sorted(refit_dates.items()))

    probs = pd.DataFrame(index=raw.index, columns=LABELS, dtype=float)
    hist = {}
    cur = None
    alpha = None
    refit_log = []
    W = 252
    for i, dt in enumerate(raw.index):
        per = dt.to_period("M")
        if per in refit_dates and refit_dates[per] == dt and i >= W:
            win = raw[FEATS].iloc[i - W + 1:i + 1]
            mu_s, sd_s = win.mean(), win.std().replace(0, 1e-8)
            X = ((win - mu_s) / sd_s).values
            best = fit_one(X, seed=abs(hash(str(dt))) % 10000)
            if best is None:
                continue
            m, ll = best
            m._covars_ = floor_covars(m)
            order = label_states(m.means_)
            cur = dict(model=m, mu=mu_s, sd=sd_s, order=order, ll=ll, date=str(dt.date()),
                       trans=m.transmat_.copy(), start=m.startprob_.copy())
            if alpha is None:
                alpha = m.startprob_[order].copy()  # 首次初始化；之后按标签空间延续，避免重估日概率跳变
            hist[str(dt.date())] = dict(mu=cur["mu"], sd=cur["sd"], order=order,
                                        mu_z=m.means_, var_z=np.diagonal(np.asarray(m.covars_), axis1=1, axis2=2),
                                        A=cur["trans"][np.ix_(order, order)])
            refit_log.append(dict(date=str(dt.date()), loglik=float(ll),
                                  trans=cur["trans"][np.ix_(order, order)].round(3).tolist()))
        if cur is None:
            continue
        x = ((raw.loc[dt, FEATS] - cur["mu"]) / cur["sd"]).values
        m = cur["model"]; order = cur["order"]
        mu = m.means_[order]; var = np.asarray(m.covars_)[order]
        if var.ndim == 3: var = np.diagonal(var, axis1=1, axis2=2)
        A = m.transmat_[np.ix_(order, order)]
        n = 4
        lik = np.exp(-0.5 * (((x - mu) ** 2) / var).sum(1)) / np.sqrt((2 * np.pi) ** n * var.prod(1))
        a = lik * alpha
        s = a.sum()
        a = a / s if s > 0 else np.ones(n) / n
        stat = np.ones(4) / 4  # 先验取均匀：短样本估不准长期状态频率，避免用估计先验反向强化当期判断
        probs.loc[dt] = (1 - EPS) * a + EPS * stat
        alpha = a @ A
        alpha = alpha / alpha.sum()

    probs = probs.dropna(how="all")

    # 短窗滤波（近 10 日证据，起点用平稳分布）：反映"当期证据强度"，避免长记忆饱和
    short = pd.DataFrame(index=probs.index, columns=LABELS, dtype=float)
    for i, dt in enumerate(probs.index):
        if i < 10:
            continue
        # 找到该日生效的参数集合（最近一次重估）
        ref = [r for r in refit_dates.values() if r <= dt]
        if not ref:
            continue
        key = str(max(ref).date())
        st = hist.get(key)
        if st is None:
            continue
        idx = raw.index.get_loc(dt)
        seg = raw[FEATS].iloc[idx - 9:idx + 1]
        Xs = ((seg - st["mu"]) / st["sd"]).values
        mu = st["mu_z"][st["order"]]; var = st["var_z"][st["order"]]
        A = st["A"]
        n = len(FEATS)
        a = np.ones(4) / 4
        for x in Xs:
            lik = np.exp(-0.5 * (((x - mu) ** 2) / var).sum(1)) / np.sqrt((2 * np.pi) ** n * var.prod(1))
            a = lik * a
            a = a / a.sum()
            a = a @ A
            a = a / a.sum()
        short.loc[dt] = (1 - EPS) * a + EPS * (np.ones(4) / 4)
    short = short.dropna(how="all")
    short.to_csv(os.path.join(BASE, "probs_short.csv"))

    # 平滑概率（事后视角，含未来信息）——取最后窗口
    last_win = raw[FEATS].iloc[-W:]
    mu_s, sd_s = last_win.mean(), last_win.std().replace(0, 1e-8)
    X = ((last_win - mu_s) / sd_s).values
    m = GaussianHMM(n_components=4, covariance_type="diag", n_iter=200, tol=1e-5, random_state=42).fit(X)
    m._covars_ = floor_covars(m)
    order = label_states(m.means_)
    A_l = m.transmat_[np.ix_(order, order)]
    sm_arr = m.predict_proba(X)[:, order]
    sm_arr = (1 - EPS) * sm_arr + EPS * 0.25
    sm = pd.DataFrame(sm_arr, index=last_win.index, columns=LABELS)
    filt_tail = probs.reindex(last_win.index)

    # 当前（2026-04-14）状态
    p_now = probs.iloc[-1]
    print("\n=== 2026-04-14 滤波概率 ===")
    print(p_now.round(4).to_string())
    print("confidence:", round(float(p_now.max()), 4),
          "entropy:", round(float(-(p_now * np.log(p_now + 1e-12)).sum() / np.log(4)), 4))

    # 最近一次迁移的领先/滞后
    def cross_date(df, lab, thr=0.5):
        s = (df[lab] > thr)
        if not s.any():
            return None
        idx = s[s].index
        return idx[0]
    trans_tbl = []
    for lab in LABELS:
        fd = cross_date(filt_tail.iloc[-90:], lab)
        sd = cross_date(sm.iloc[-90:], lab)
        trans_tbl.append(dict(regime=lab,
                              smoothed_first=str(sd.date()) if sd is not None else None,
                              filtered_first=str(fd.date()) if fd is not None else None,
                              lag_days=(int(np.busday_count(sd.date(), fd.date())) if (sd is not None and fd is not None) else None)))

    # 状态特征均值（最近一次重估，原始单位）
    means_raw = pd.DataFrame(cur["model"].means_[cur["order"]], columns=FEATS)
    means_raw = means_raw.mul(cur["sd"].values).add(cur["mu"].values)
    means_raw.index = LABELS
    print("\n=== 各状态特征均值（原始单位，最近重估 %s）===" % cur["date"])
    print(means_raw.round(3).to_string())

    out = dict(cutoff=CUTOFF, feature_span=[str(raw.index[0].date()), str(raw.index[-1].date())],
               n_obs=int(len(raw)), p_now={k: float(v) for k, v in p_now.items()},
               confidence=float(p_now.max()),
               entropy=float(-(p_now * np.log(p_now + 1e-12)).sum() / np.log(4)),
               transitions=trans_tbl, means_raw=means_raw.round(4).to_dict(),
               refit_last=cur["date"], refit_log=refit_log[-6:],
               trans_matrix_last=pd.DataFrame(cur["trans"][np.ix_(cur["order"], cur["order"])],
                                              index=LABELS, columns=LABELS).round(3).to_dict())
    with open(os.path.join(BASE, "hmm_out.json"), "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2, default=str)
    probs.to_csv(os.path.join(BASE, "probs_filtered.csv"))
    sm.to_csv(os.path.join(BASE, "probs_smoothed_last_window.csv"))
    raw.to_csv(os.path.join(BASE, "features.csv"))
    rets.to_csv(os.path.join(BASE, "returns.csv"))
    print("\nsaved.")

if __name__ == "__main__":
    run()