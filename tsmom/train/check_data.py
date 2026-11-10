#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据与池校验（不训练、不依赖 PyTorch）。

做三件事：
1. 读面板、解析标的池（--pool / --pool-file / --pool-key 从 etf_pool_meta.json 取），报告列数与缺失标的；
2. 复用训练同款特征工程（stmom.build_tensor / make_samples）构建张量，报告 N / L / F / D；
3. 报告训练段与回测段样本数与日期范围；若训练段为空会明确告警（该池多数标的上市过晚）。

用法：
    python tsmom/train/check_data.py                                  # 13 池
    python tsmom/train/check_data.py --panel tsmom/assets/pool_model.csv --pool-key model_pool
    python tsmom/train/check_data.py --panel tsmom/assets/pool_rules71.csv --pool-key rules_pool
"""
import os
import sys
import json
import argparse

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "assets")
sys.path.insert(0, HERE)
import stmom as S  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=os.path.join(ASSETS, "panel13.csv"))
    ap.add_argument("--pool", default="")
    ap.add_argument("--pool-file", default="")
    ap.add_argument("--pool-key", default="", help="从 assets/etf_pool_meta.json 读取 model_pool / rules_pool")
    ap.add_argument("--meta", default=os.path.join(ASSETS, "etf_pool_meta.json"))
    ap.add_argument("--train-end", default="2021-12-31")
    ap.add_argument("--eval-start", default="2022-01-01")
    args = ap.parse_args()

    raw = pd.read_csv(args.panel, index_col=0, parse_dates=True).sort_index()
    if args.pool_key:
        pool = json.load(open(args.meta, encoding="utf-8"))[args.pool_key]
    elif args.pool_file:
        pool = json.load(open(args.pool_file, encoding="utf-8"))
    elif args.pool:
        pool = [x.strip() for x in args.pool.split(",") if x.strip()]
    else:
        pool = list(raw.columns)

    missing = [c for c in pool if c not in raw.columns]
    pool = [c for c in pool if c in raw.columns]
    print(f"面板: {os.path.relpath(args.panel)}  区间 {raw.index.min().date()} ~ {raw.index.max().date()}  列数={raw.shape[1]}")
    print(f"池大小: {len(pool)}  缺失跳过: {len(missing)} {missing[:5] if missing else ''}")
    if not pool:
        print("[fail] 池为空，无法构建张量")
        return

    df = raw[pool]
    returns = df.pct_change()
    Xdf, Sigmadf, _ = S.build_tensor(df)
    X, y, V, dates = S.make_samples(Xdf, Sigmadf, returns, list(df.columns))

    S.TRAIN_END = args.train_end
    S.EVAL_START = args.eval_start
    tm = dates <= pd.Timestamp(S.TRAIN_END)
    em = dates >= pd.Timestamp(S.EVAL_START)

    N = len(df.columns)
    print(f"张量: N={N} L={S.L_WINDOW} F=8 展平维 D={X.shape[1]}  (N*L*F={N * S.L_WINDOW * 8})")
    print(f"样本总数={len(X)}  训练段={int(tm.sum())}  回测段={int(em.sum())}")
    if tm.sum() > 0:
        print(f"  训练段日期: {dates[tm][0].date()} ~ {dates[tm][-1].date()}")
    else:
        print(f"  [warn] 训练段为空（<= {S.TRAIN_END}）：该池多数标的上市晚于训练起点，"
              f"建议改用 model_pool（16 只早期上市标的）训练，全池只做规则回测")
    if em.sum() > 0:
        print(f"  回测段日期: {dates[em][0].date()} ~ {dates[em][-1].date()}")
    print(f"NaN: X={int(np.isnan(X).sum())} y={int(np.isnan(y).sum())} 面板缺失率={float(df.isna().mean().mean()):.3f}")
    print("[ok] CHECK_DATA_DONE")


if __name__ == "__main__":
    main()