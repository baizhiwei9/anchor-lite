import itertools, json, pandas as pd, long2022 as L
rows=[]
for sb, dd, cap in itertools.product([0.30,0.38,0.45,0.52],[0.015,0.02,0.025],[0.45,0.60]):
    m,tl,sel,tr=L.run(stock_base=sb, dd=dd, stock_cap=cap)
    rows.append(dict(stock_base=sb,dd=dd,cap=cap,total=m["total"],cagr=m["cagr"],
                     maxdd=m["maxdd"],vol=float(m["vol"]),sharpe=float(m["sharpe"]),ntr=m["n_trades"],
                     eq=float(tl["eq"].mean())))
d=pd.DataFrame(rows)
print("达标（回撤≥-5%）前 8：")
ok=d[d.maxdd>=-0.05].sort_values("cagr",ascending=False)
print(ok.head(8).round(4).to_string(index=False) if len(ok) else "无")
print("\n全部按 CAGR 前 5：")
print(d.sort_values("cagr",ascending=False).head(5).round(4).to_string(index=False))
json.dump(rows,open("sweep_long.json","w"),ensure_ascii=False,default=float)
