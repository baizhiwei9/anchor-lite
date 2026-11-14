"""T1：拆解现有回测的股票腿——权重、贡献、约束来源。"""
import numpy as np, pandas as pd, dyn3 as D3, dyn5 as M5
CFG = dict(dd=0.025, oil_hi=0.25, band=0.04, oil_stop=0.08, kc_hi=0.20)
m, tr, tl = M5.run(**CFG)
tl["date"] = pd.to_datetime(tl.date); tl = tl.set_index("date")
R = D3.rets.reindex(tl.index).fillna(0)
MAP = {"kc50": "kc50", "nas": "nasdaq", "div": "dividend", "lowvol": "lowvol",
       "oil": "oil", "treas": "treasury10_etf"}
LAB = {"kc50": "科创50", "nas": "纳指", "div": "红利", "lowvol": "低波", "oil": "原油", "treas": "十年国债"}
for k in MAP:
    tl["cb_" + k] = tl[k] * R[MAP[k]]
tl["eq"] = tl[["kc50", "nas", "div", "lowvol"]].sum(axis=1)
tl["risk"] = tl[["kc50", "nas", "oil"]].sum(axis=1)
rows = []
for mo, g in tl.groupby(tl.index.to_period("M")):
    r = dict(mo=str(mo), eq=g["eq"].mean(), kc=g["kc50"].mean(), nas=g["nas"].mean(),
             div=g["div"].mean(), low=g["lowvol"].mean(), risk=g["risk"].mean(), tot=g["tot"].mean(),
             br_days=int((g["br"] < 1).sum()))
    for k in MAP: r["cb_" + k] = g["cb_" + k].sum() * 100
    rows.append(r)
d = pd.DataFrame(rows)
print(m)
print(d.round(3).to_string(index=False))
eqC = d[["cb_kc50", "cb_nas", "cb_div", "cb_lowvol"]].sum(axis=1)
print("\n股票腿（科创+纳指+红利+低波）合计 %+.2fpp；进攻腿（科创+纳指）%+.2fpp" % (eqC.sum(), d[["cb_kc50", "cb_nas"]].sum().sum()))
print("原油 %+.2fpp  债券 %+.2fpp" % (d.cb_oil.sum(), d.cb_treas.sum()))
print("平均仓位：股票腿 %.1f%%  风险腿 %.1f%%  总 %.1f%%" % (d["eq"].mean()*100, d["risk"].mean()*100, d["tot"].mean()*100))
print("熔断半仓天数 %d / %d = %.0f%%" % (d.br_days.sum(), len(tl), d.br_days.sum()/len(tl)*100))
print("\n股票腿 7 月前合计 %+.2fpp" % (d[d["mo"] < "2026-07"][["cb_kc50", "cb_nas", "cb_div", "cb_lowvol"]].sum().sum()))
print("资产池配置：科创50、纳指、红利、低波、原油、十年国债（6 类）")
print("面板可用但未进配置的股票类资产：")
for c in ["hs300", "zz500", "zz1000", "cyb", "semiconductor", "military", "pharma", "broker",
          "bank", "coal", "steel", "liquor", "consumer", "newenergy", "sp500", "nikkei",
          "hstech", "china_internet", "nasdaq2"]:
    print("  -", c)
tl.reset_index().to_csv("eq_legs.csv", index=False)
d.to_csv("eq_monthly.csv", index=False)
