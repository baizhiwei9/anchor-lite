"""回撤复盘取证：回撤区间归因、敞口分布、2018-02 风险层单独测试"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import engine

BASE = os.path.dirname(os.path.abspath(__file__))
probs, rets = engine.load_inputs()
an = pd.read_csv(os.path.join(BASE, "engine_main.csv"), index_col=0, parse_dates=True)

out = {}
nav = an["nav"]
dd = nav / nav.cummax() - 1
trough = dd.idxmin()
peak = nav.loc[:trough].idxmax()
ep = an.loc[peak:trough]
out["dd_episode"] = dict(peak=str(peak.date()), trough=str(trough.date()),
                         depth=float(dd.min()), days=int(len(ep)),
                         contrib={c: round(float(ep[f"c_{c}"].sum()), 4) for c in engine.ASSETS},
                         avg_w={c: round(float(ep[f"w_{c}"].mean()), 4) for c in engine.ASSETS})

r = an["ret"]
out["daily"] = dict(max_loss=float(r.min()), max_loss_date=str(r.idxmin().date()),
                    max_gain=float(r.max()), days_gt1pct=int((r.abs() > 0.01).sum()),
                    n=int(len(r)), vol=float(r.std() * np.sqrt(252)))

out["exposure"] = dict(avg_net=float(an[["w_kc50", "w_hs300", "w_oil", "w_nonferrous"]].sum(axis=1).mean()),
                       max_net=float(an[["w_kc50", "w_hs300", "w_oil", "w_nonferrous"]].sum(axis=1).max()),
                       avg_gross=float(an[[f"w_{a}" for a in engine.ASSETS]].abs().sum(axis=1).mean()),
                       max_gross=float(an[[f"w_{a}" for a in engine.ASSETS]].abs().sum(axis=1).max()),
                       turnover=float(an[[f"w_{a}" for a in engine.ASSETS]].diff().abs().sum(axis=1).mean()),
                       sv_on_days=int((an["w_sv"] < -0.001).sum()),
                       sv_contrib=round(float(an["c_sv"].sum()), 4))

# 2018-02：HMM 概率不可得（样本起点限制），仅测风险层
fake = pd.DataFrame(index=rets.loc["2018-01-02":"2018-03-30"].index,
                    columns=engine.LABELS, data=0.25)
res18 = engine.run_engine(fake.fillna(0.25), rets, "2018-01-02", "2018-03-30")
out["stress_2018_riskonly"] = engine.metrics(res18)
out["stress_2018_positions"] = {a: round(float(res18[f"w_{a}"].mean()), 3) for a in engine.ASSETS}

# 该窗口若满仓沪深300
out["bh_hs300_2018"] = engine.buy_hold(rets, "2018-01-02", "2018-03-30", "hs300")
out["bh_hs300_2020"] = engine.buy_hold(rets, "2020-02-03", "2020-04-30", "hs300")

with open(os.path.join(BASE, "forensics.json"), "w") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2, default=str)
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))