# -*- coding: utf-8 -*-
"""
Trade Agents 多交易员周度分析（最终版，纯代码 0 token）
=====================================================
流程（两阶段过滤 + 四视角汇总）：
  阶段一：技术筛选 —— 全行业周线/日线缠论买点（日线只三买 + 周线一二三买）
  阶段二：下钻个股 —— 过闸行业 → MA40+VWAP中线过滤 → 个股买点
  基本面：券商研报评级（Tushare report_rc）对候选行业确认
  策略视角：Anchor-lite 当前持仓
  统一辩论 → 报告 → 飞书推送（消息头"报告来自 hermes.DeepSeek"）

周日 crontab 执行，0 token。
"""
import os, sys, json, subprocess
import numpy as np
import pandas as pd
from datetime import datetime

WS = "/opt/data/home/.coze/agents/7705405429102444827/workspace"
TECHANLY = "/tmp/techAnly"
ANCHOR_LITE = "/tmp/anchor-lite"
DATAHUB = os.path.join(WS, "datahub-three-tier")

sys.path.insert(0, os.path.join(TECHANLY, "bin"))
sys.path.insert(0, os.path.join(TECHANLY, "smc"))
sys.path.insert(0, DATAHUB)

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
FEISHU_CHAT = os.environ.get("FEISHU_CHAT", "")
os.environ.setdefault("TECH_WS", TECHANLY)

def stage1_weekly_buypoints():
    """阶段一：全行业周线/日线买点扫描，返回过闸行业"""
    import chan_smc_index as C
    import datahub as DH
    import tech_summary as TS
    swmap = json.load(open(os.path.join(TECHANLY, ".cache", "sw_index_map.json")))
    member = json.load(open(os.path.join(TECHANLY, ".cache", "sw_member_all.json")))
    l1 = sorted({v["l1"] for v in member.values() if v.get("l1")})
    hits = []
    for name in l1:
        ic = swmap.get(name)
        if not ic: continue
        try:
            rows = DH.sw_daily(ic, days=800)
            if not rows: continue
            df = pd.DataFrame(rows, columns=["dt","open","high","low","close","vol"])
            df["dt"] = pd.to_datetime(df["dt"].str[:8])
            if len(df) < 60: continue
            # 日线三买
            import tech_buypoint as TB
            d = df.rename(columns={"dt":"dt"})
            cl = d["close"].astype(float).values
            close = float(cl[-1])
            tb = TB.find_third_buys(d, cl)
            # 周线一二三买
            ww = C.resample_w(df)
            fx_w = C.chan_read(C.bars_of(ww), "week")
            ma20 = TS.ma20_state(df)
            buys = [x for x in tb if x["accept"]]
            for b in (fx_w.get("buy_list") or []):
                tag = b.get("tag")
                if tag == "一买":
                    r = TB._gate_y1(b, close, ma20)
                elif tag == "二买":
                    r = TB._gate_y2(b, close, ma20)
                elif tag == "三买":
                    lvl = float(b.get("level") or 0)
                    gap = (close/lvl-1)*100 if lvl>0 else 0
                    r = {"tag":"三买","pri":1,"level":round(lvl,2),"age":int(b.get("age") or 0),
                         "gap":gap,"accept":(gap<=TB.TB_NEAR and gap>=-TB.BUY_NEAR_DN),
                         "why":""}
                else:
                    continue
                if r.get("accept"):
                    buys.append(r)
            if buys:
                buys.sort(key=lambda x:(x["pri"], x.get("age",0)))
                hits.append(dict(name=name, best=buys[0], close=close))
        except Exception:
            pass
    hits.sort(key=lambda r:(r["best"]["pri"], r["best"].get("age",0)))
    return hits

def stage2_drilldown(industry_names):
    """阶段二：对过闸行业下钻个股（MA40+VWAP中线过滤）"""
    import tech_buypoint as TB
    member = json.load(open(os.path.join(TECHANLY, ".cache", "sw_member_all.json")))
    l1_map = {}
    for code, v in member.items():
        l1_map.setdefault(v.get("l1"), []).append(code)
    name_map = TB.name_map()
    hist = TB._today_hist()
    import chan_smc_index as C
    import tech_summary as TS
    import smc_core as S
    all_hits = []
    for ind in industry_names:
        codes = l1_map.get(ind, [])
        for code in codes:
            try:
                d = TB.load_daily(code, hist)
                if d is None or len(d) < 45: continue
                cl = d["close"].astype(float)
                ma40 = cl.rolling(40).mean()
                close = float(cl.iloc[-1])
                ma40_now = float(ma40.iloc[-1])
                ma40_prev = float(ma40.iloc[-6]) if len(ma40)>=6 else ma40_now
                if not (close > ma40_now and ma40_now > ma40_prev): continue
                vw = S.vwap_tracks(d)
                if not (close > float(vw["vwap"][-1])): continue
                # 缠论买点
                bars = C.bars_of(d)
                fx = C.chan_read(bars, "day")
                if not fx.get("ok"): continue
                ma20 = TS.ma20_state(d)
                cl_arr = d["close"].astype(float).values
                tb = TB.find_third_buys(d, cl_arr)
                cands = [x for x in tb]
                for b in (fx.get("buy_list") or []):
                    if b.get("tag")=="一买": cands.append(TB._gate_y1(b, close, ma20))
                    elif b.get("tag")=="二买": cands.append(TB._gate_y2(b, close, ma20))
                acc = [x for x in cands if x["accept"]]
                if not acc: continue
                acc.sort(key=lambda x:(x["pri"], x.get("age",0)))
                best = acc[0]
                all_hits.append(dict(industry=ind, code=code, name=name_map.get(code, code),
                                      best=best, close=close))
            except Exception:
                pass
    all_hits.sort(key=lambda r:(r["best"]["pri"], r["best"].get("age",0)))
    return all_hits

def trader_research(industries):
    """券商研报对候选行业评级"""
    from data_hub import get_data
    end = pd.Timestamp.now().strftime('%Y%m%d')
    start = (pd.Timestamp.now()-pd.Timedelta(days=7)).strftime('%Y%m%d')
    df = get_data('research_report', start_date=start, end_date=end)
    if df is None or len(df)==0:
        return {}
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    pro._DataApi__http_url = "https://ttx.dailyfetch.top/"
    sb = pro.stock_basic(list_status='L', fields='ts_code,industry')
    df = df.merge(sb, on='ts_code', how='left')
    RATING = {'买入':3,'强烈推荐':3,'强推':3,'买入-A':3,'推荐':2.5,'增持':2,'优于大市':2,'跑赢行业':2,'增持-A':2,'谨慎增持':1.5,'谨慎推荐':1.5,'持有':1,'中性':1,'减持':0,'卖出':0}
    df['score'] = df['rating'].map(RATING)
    out = {}
    for ind in industries:
        sub = df[df['industry']==ind].dropna(subset=['score'])
        if len(sub) >= 3:
            out[ind] = dict(n=len(sub), avg=float(sub['score'].mean()),
                            buy=float((sub['score']>=2.5).mean()))
    # 个股级研报覆盖：code -> (篇数, 平均评级)
    stock_cov = {}
    for code, grp in df.groupby('ts_code'):
        grp = grp.dropna(subset=['score'])
        if len(grp):
            stock_cov[code] = dict(n=len(grp), avg=float(grp['score'].mean()))
    return out, stock_cov

def build_report(hits_stage1, stocks, research, stock_cov=None):
    stock_cov = stock_cov or {}
    PRI = {1:"①三买",2:"②一买",3:"③二买"}
    L = []
    A = L.append
    A("📊 Trade Agents 多交易员周度分析")
    A(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ 报告来自 hermes.DeepSeek")
    A("═"*40)
    A("【阶段一·技术筛选】行业周线/日线买点（日线只三买+周线一二三买）")
    for h in hits_stage1:
        b = h["best"]
        A(f"  🌙 {h['name']} ｜ {PRI.get(b['pri'],b['tag'])} @{b['level']:.0f}")
    A("")
    A("【阶段二·个股下钻】MA40+VWAP中线过滤后的可买个股")
    covered = []
    if stocks:
        cur_ind = None
        for s in stocks:
            b = s["best"]
            cov = stock_cov.get(s["code"])
            if cov:
                covered.append((s, cov))
            if s["industry"] != cur_ind:
                cur_ind = s["industry"]
                A(f"  ▸ {cur_ind}")
            mark = "✅研报覆盖" if cov else "⚠️无研报"
            A(f"    {PRI.get(b['pri'],b['tag'])} {s['name']} {s['code']} 收{s['close']:.2f} ｜ {mark}")
    else:
        A("  无可买个股")
    A("")
    A("【基本面·券商研报确认】")
    if research:
        for ind, r in research.items():
            flag = "✅" if r['avg']>=2.3 else "⚠️"
            A(f"  {flag} {ind}：研报{r['n']}篇 平均评级{r['avg']:.1f} 买入{r['buy']*100:.0f}%")
    else:
        A("  研报数据不足")
    A("")
    A("【统一结论】")
    if research:
        good_ind = [ind for ind,r in research.items() if r['avg']>=2.3]
        if good_ind:
            A(f"  ✅ 技术买点 + 研报双确认行业：{', '.join(good_ind)}")
        else:
            A("  ⚠️ 技术有买点但研报评级一般，谨慎")
    if covered:
        A("  ✅ 最终推荐（技术买点 + 个股研报覆盖）：")
        for s, cov in covered[:10]:
            b = s["best"]
            A(f"    {PRI.get(b['pri'],b['tag'])} {s['name']}({s['code']}) 收{s['close']:.2f} ｜ 研报{cov['n']}篇 评级{cov['avg']:.1f}")
    else:
        A("  ⚠️ 无个股同时满足技术买点+研报覆盖")
    A("  ⚠️ 技术结构分析，不构成投资建议。")
    return "\n".join(L)

def push_with_charts(report, stocks):
    """最终推荐个股出图 + 图文卡片推飞书"""
    os.environ.setdefault("FEISHU_APP_ID", os.environ.get("FEISHU_APP_ID", ""))
    os.environ.setdefault("FEISHU_APP_SECRET", os.environ.get("FEISHU_APP_SECRET", ""))
    sys.path.insert(0, os.path.join(TECHANLY, "bin"))
    import tech_buypoint as TB
    import feishu_card as FC
    import simple_chart as SC
    # 对前 5 只推荐个股出单图（缠论分型版 + VWAP）
    imgs = []
    hist = TB._today_hist()
    for s in stocks[:5]:
        try:
            d = TB.load_daily(s["code"], hist)
            if d is None or len(d) < 60:
                continue
            p = SC.draw_stock(s["code"], s["name"], d)
            if p and os.path.exists(p):
                imgs.append(p)
        except Exception as e:
            print(f"  出图失败 {s['name']}: {str(e)[:60]}")
    # 组装卡片 blocks
    blocks = []
    for sec in report.split("\n\n"):
        sec = sec.strip()
        if sec:
            blocks.append({"type": "md", "text": sec})
    title = "📊 Trade Agents 周度分析"
    sign = "📎 报告来自 hermes.DeepSeek"
    try:
        ok = FC.send_card(title, blocks, imgs=imgs, chat=FEISHU_CHAT,
                          template="blue", sign=sign)
        print(f"飞书图文推送: {'成功' if ok else '失败'}，配图 {len(imgs)} 张")
        return ok
    except Exception as e:
        print(f"飞书推送异常: {str(e)[:120]}")
        return False


def main(push=False):
    print("Trade Agents 周度分析开始（纯代码 0 token）...")
    print("[1/4] 阶段一：行业周线买点扫描...")
    hits1 = stage1_weekly_buypoints()
    inds = [h["name"] for h in hits1]
    print(f"    过闸行业 {len(inds)} 个: {inds}")
    print("[2/4] 阶段二：下钻个股（MA40+VWAP过滤）...")
    stocks = stage2_drilldown(inds)
    print(f"    可买个股 {len(stocks)} 只")
    print("[3/4] 基本面：券商研报确认...")
    research, stock_cov = trader_research(inds)
    print("[4/4] 生成报告...")
    report = build_report(hits1, stocks, research, stock_cov)
    print("\n" + report)
    out = f"/tmp/trade_agents_{datetime.now().strftime('%Y%m%d')}.txt"
    open(out, 'w').write(report)
    print(f"\n报告已保存: {out}")
    if push:
        # 最终推荐 = 技术买点 + 个股研报覆盖
        covered = [s for s in stocks if s["code"] in stock_cov]
        push_with_charts(report, covered)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    main(push=args.push)

def trader_news(stocks, max_per_stock=5):
    """新闻分析师：只对技术面+研报过滤后的候选个股查新闻（定向，省token）
    返回 {code: [(标题, 时间), ...]}"""
    from data_hub import get_data
    news_map = {}
    for s in stocks[:15]:  # 最多查前15只，进一步省时间
        code = s["code"]
        # 转 6 位数字代码（去 .SH/.SZ）
        num = code.split(".")[0]
        try:
            df = get_data('news', news_type='stock', symbol=num)
            if df is not None and len(df) and '新闻标题' in df.columns:
                items = [(str(r['新闻标题'])[:60], str(r['发布时间'])[:10])
                         for _, r in df.head(max_per_stock).iterrows()]
                news_map[code] = items
        except Exception:
            pass
    return news_map
