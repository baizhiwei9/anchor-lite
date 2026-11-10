# -*- coding: utf-8 -*-
"""统一回测驱动：跑 V1 / V5 / V6 / V8，并附 MP 参考基线用于口径核对。

用法：
    python -m tsmom.run_all            # 在 anchor_lite 目录下
    python tsmom/run_all.py            # 亦可直接执行
输出：
    tsmom/out/results.json   各组合指标与逐年
    tsmom/out/nav.csv        各组合逐日净值
"""
import os
import sys
import json
import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tsmom.base import TsmomEngine
    from tsmom.variants import VARIANTS, EXPERIMENT_REFS
else:
    from .base import TsmomEngine
    from .variants import VARIANTS, EXPERIMENT_REFS

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")


class MPLBase(TsmomEngine):
    """参考基座：纯 MLP 信号 + vol 15%（与 STMOM 口径二同源，不含成本时为 +121.9%）。"""

    name = "参考 MP 基座(纯MLP,vol15%)"
    signal = "MLP"
    vol_target = 0.15
    cost_bp = 0.0


def main():
    os.makedirs(OUT, exist_ok=True)
    rows, navs = [], {}
    for cls in [MPLBase] + VARIANTS + EXPERIMENT_REFS:
        st = cls().prepare()
        p = st.run()
        res, nav = st.summarize(p)
        rows.append(res)
        navs[res["name"]] = nav
        print(f"{res['name']:26s} | 总{res['total']*100:+7.1f}% 年化{res['ann']*100:+6.1f}% "
              f"夏普{res['sharpe']:+.2f} 回撤{res['dd']*100:6.1f}% 胜率{res['win']*100:.0f}% | 逐年 "
              f"{ {k: round(v, 1) for k, v in res['yearly'].items()} }", flush=True)
    dates = MPLBase().prepare().dates
    json.dump({"generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
               "period": [str(dates[0].date()), str(dates[-1].date())],
               "n_days": len(dates), "rows": rows},
              open(os.path.join(OUT, "results.json"), "w"), indent=1, ensure_ascii=False)
    pd.DataFrame(navs, index=dates).to_csv(os.path.join(OUT, "nav.csv"))
    print("\n[out]", OUT)
    print("[done] TSMOM_BRANCH_DONE")


if __name__ == "__main__":
    main()