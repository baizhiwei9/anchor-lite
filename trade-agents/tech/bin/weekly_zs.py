#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周线中枢 · 突破/跌破 周度预警（五大宽基 + 两个雪球实盘持仓行业）
================================================================================
大佬 2026-10-26 需求原话：
  「对那五大宽基指数进行那个周度的预警，涨破中枢和跌破中枢都会发预警……
   那你再加一个，就这两个实盘（默尔根 / 亚丽），它的股票所属的行业是申万二级行业
   或者申万三级行业都可以，然后它跌破中枢或者是突破中枢，你也要发预警，
   报告里边说一下的。都用程序实现啊」

口径（唯一）：
  · 级别   = **周线**（日线 resample W-FRI；只用**已收盘的周**算中枢，当周现价只用来判位置）
  · 版本   = 缠论·分型版（包含处理→顶底分型→笔→中枢ZG/ZD），复用 bin/chan_smc_index.py
  · 标的①  = 五大宽基：沪深300 / 中证500 / 中证1000 / 创业板指 / 科创50
  · 标的②  = 两个实盘持仓的**申万二级行业**（A股走 tushare index_member_all，港股走 HK_SW_MAP）
             申万三级作为明细列一起显示，不单独告警
  · 事件   = 周线收盘价上破 ZG → 🌙 突破中枢；下破 ZD → ⭐ 跌破中枢；回到中枢内 → ↪ 拉回
  · 去重   = 同一个「周」只报一次（状态文件 memory/weekly_zs_state.json 记到周标签）

推送：
  · 报告内嵌：bin/xq_report.py 调 section_text()，早晚报都会带一段
  · 周度独立推送：本脚本 --push（周五 20:40 系统 crontab）→ 飞书私聊 + QQ 私聊
  · 全程纯脚本、0 token

用法：
  python3 bin/weekly_zs.py                 # 只打印
  python3 bin/weekly_zs.py --push          # 打印 + 变化则推飞书私聊+QQ
  python3 bin/weekly_zs.py --push --force  # 无变化也推（周报全文）
  python3 bin/weekly_zs.py --date 2026-10-23   # 指定口径日（回放）
  python3 bin/weekly_zs.py --no-cache      # 忽略当日结果缓存
"""
import os
import sys
import json
import time
import socket
import argparse
import datetime

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import pandas as pd
import numpy as np

import chan_smc_index as C

CACHE = os.path.join(WS, ".cache", "weekly_zs")
SWDIR = os.path.join(CACHE, "sw_daily")
STATE = os.path.join(WS, "memory", "weekly_zs_state.json")
for _p in (CACHE, SWDIR):
    os.makedirs(_p, exist_ok=True)

FEISHU_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("WZS_FEISHU_CHAT") or ""
QQ_TARGET = os.environ.get("WZS_QQ_TARGET", "")
# 申万三级行业只在「两组合合计权重 ≥ 该值」时才单独算（控制 tushare 请求数与报告长度）
L3_MIN_W = float(os.environ.get("WZS_L3_MIN_W", "5"))

# ── 五大宽基 ──
WIDE = [("沪深300", "000300.SH"), ("中证500", "000905.SH"), ("中证1000", "000852.SH"),
        ("创业板指", "399006.SZ"), ("科创50", "000688.SH")]

# ── 组合持仓快照（雪球，已被 xq_fetch.py 每交易日更新）──
PORTS = [("默尔根", os.path.join(WS, ".xueqiu/last_snapshot.json")),
         ("亚丽", os.path.join(WS, ".xueqiu/portfolios/yali/last_snapshot.json"))]

# ── 港股 → 申万二级（本机没有港股申万分类，按同业对标人工映射；改口径只改这里）──
HK_SW = {
    "00981": "半导体",      # 中芯国际
    "00522": "半导体",      # ASMPT
    "06809": "半导体",      # 澜起科技-H
    "06082": "半导体",      # 壁仞科技
    "01801": "化学制药",    # 信达生物
    "06160": "化学制药",    # 百济神州
    "09926": "化学制药",    # 康方生物
    "09995": "生物制品",    # 荣昌生物
    "02269": "医疗服务",    # 药明生物（CXO）
    "00700": "游戏Ⅱ",       # 腾讯控股
    "02400": "游戏Ⅱ",       # 心动公司
    "00451": "游戏Ⅱ",       # 时代数字
    "09988": "互联网电商",  # 阿里巴巴-W
    "09618": "互联网电商",  # 京东集团
    "03690": "本地生活服务Ⅱ",  # 美团-W
    "09863": "乘用车",      # 零跑汽车
    "06869": "通信设备",    # 长飞光纤光缆
    "02476": "元件",        # 胜宏科技-H
}

# 申万二级名称 → 代码（缓存到 .cache/weekly_zs/sw_l2.json，缺失自动用 index_classify 重建）
L2_CODE_CACHE = os.path.join(CACHE, "sw_l2.json")


def log(*a):
    print(*a, flush=True)


# ────────────────────────── 配置/映射 ──────────────────────────
def sw_l2_map():
    """申万二级 名称→代码；本地缓存 30 天，缺失自动重建"""
    if os.path.exists(L2_CODE_CACHE):
        try:
            m = json.load(open(L2_CODE_CACHE, encoding="utf-8"))
            if len(m) > 100 and (time.time() - os.path.getmtime(L2_CODE_CACHE)) / 86400 < 30:
                return m
        except Exception:
            pass
    m = {}
    try:
        p = os.path.join(WS, ".cache/longxia/sw_classify_L2.json")
        if os.path.exists(p):
            m = json.load(open(p, encoding="utf-8"))
    except Exception:
        m = {}
    if len(m) < 100:
        try:
            from tushare_init import ts_call
            df = ts_call("index_classify", level="L2", src="SW2021")
            if df is not None and len(df):
                m = {str(r["index_code"]): r["industry_name"] for _, r in df.iterrows()}
        except Exception as e:
            log("[WARN] index_classify L2 失败 %s" % str(e)[:100])
    if m:
        json.dump(m, open(L2_CODE_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return m


def sw_l3_map():
    p = os.path.join(WS, ".cache/longxia/sw_classify_L3.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}


MEMBER_CACHE = os.path.join(CACHE, "sw_member.json")
_MEMBER = {}
if os.path.exists(MEMBER_CACHE):
    try:
        _MEMBER = json.load(open(MEMBER_CACHE, encoding="utf-8"))
        _MEMBER = {k: tuple(v) for k, v in _MEMBER.items()}
    except Exception:
        _MEMBER = {}


def _save_member():
    try:
        json.dump({k: list(v) for k, v in _MEMBER.items()},
                  open(MEMBER_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def stock_sw(ts_code):
    """个股 → 申万 (l2_name, l3_name)；A股用 tushare index_member_all，失败返回 (None, None)"""
    if ts_code in _MEMBER:
        return _MEMBER[ts_code]
    res = (None, None)
    try:
        from tushare_init import ts_call
        df = ts_call("index_member_all", ts_code=ts_code)
        if df is not None and len(df):
            r = None
            if "is_new" in df.columns:
                cur = df[df["is_new"].astype(str).str.upper().eq("Y")]
                r = cur.iloc[0] if len(cur) else df.iloc[-1]
            else:
                r = df.iloc[-1]
            res = (str(r.get("l2_name") or "") or None, str(r.get("l3_name") or "") or None)
        time.sleep(0.35)
    except Exception as e:
        log("[WARN] index_member_all %s: %s" % (ts_code, str(e)[:80]))
        time.sleep(0.8)
    if res[0]:
        _MEMBER[ts_code] = res
        _save_member()
    return res


# ────────────────────────── 数据 ──────────────────────────
def _sw_path(code):
    return os.path.join(SWDIR, "%s.csv" % code)






def _ak_timeout(fn, *a, timeout=15, **k):
    """akshare 调用带硬超时（超时立刻切下一层，不干等）"""
    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(1)
    f = ex.submit(fn, *a, **k)
    try:
        r = f.result(timeout=timeout)
        ex.shutdown(wait=False)
        return r
    except Exception:
        ex.shutdown(wait=False)
        return None

def _sw_name(code):
    """申万代码 → 行业名（给 L3 东财兜底用）"""
    try:
        import json
        m = json.load(open(os.path.join(os.path.dirname(__file__),
                                        "..", ".cache", "sw_l2_name.json"),
                           encoding="utf-8"))
        return m.get(code)
    except Exception:
        pass
    try:
        import weekly_zs as _w
        mm = _w.sw_l2_map()
        return mm.get(code)
    except Exception:
        return None

def fetch_sw_daily(code, start="20200101", ttl_min=None):
    """申万行业指数日线（ts_code, dt, open/high/low/close），按 mtime 做增量缓存

    ⚠️ 2026-10-26 修：原来按「末行日期距今 ≤4 天」判缓存新鲜 → 周一会读到上周五的缓存，
    盘中/收盘后都拿不到当日数据。改成 **mtime TTL**（默认 180 分钟，可用 SWD_TTL_MIN 覆盖），
    过期就增量补最新。
    """
    p = _sw_path(code)
    today8 = datetime.date.today().strftime("%Y%m%d")
    ttl = float(os.environ.get("SWD_TTL_MIN", "180")) if ttl_min is None else ttl_min
    old = None
    if os.path.exists(p):
        try:
            old = pd.read_csv(p, parse_dates=["dt"])
            if old is not None and len(old) > 100:
                age_min = (time.time() - os.path.getmtime(p)) / 60.0
                if age_min <= ttl:
                    return old
        except Exception:
            old = None
    beg = start
    if old is not None and len(old) > 100:
        beg = (old["dt"].max() - pd.Timedelta(days=7)).strftime("%Y%m%d")
    # ══ 三级降级取数（2026-11-07 大佬：「TuShare 限流，你走 AKShare 和网页爬虫啊」）
    #   L1 akshare index_hist_sw → 直连申万，1.4s 拉全量 6487 行，**最稳最快**
    #   L2 东财行业板块日线       → 网页接口
    #   L3 tushare sw_daily       → 有权限时最准，但会限流，垫底
    df = None
    layer = None

    # ── L1 akshare（申万代码去掉 .SI 后缀）
    try:
        import akshare as ak
        sym = code.split(".")[0]
        a = _ak_timeout(ak.index_hist_sw, symbol=sym, period="day", timeout=20)
        if a is not None and len(a):
            a = a.rename(columns={"日期": "dt", "收盘": "close", "开盘": "open",
                                  "最高": "high", "最低": "low"})
            a["dt"] = pd.to_datetime(a["dt"])
            df = a[["dt", "open", "high", "low", "close"]]
            layer = "L1-akshare"
    except Exception as e:
        print("[sw] %s L1 akshare 失败 %s" % (code, str(e)[:60]), flush=True)

    # ── L2 东财行业板块（按名称匹配）
    if df is None or not len(df):
        try:
            import akshare as ak
            nm = _sw_name(code)
            if nm:
                a = _ak_timeout(ak.stock_board_industry_hist_em, symbol=nm,
                                period="日k", start_date="20200101",
                                end_date=today8, timeout=20)
                if a is not None and len(a):
                    a = a.rename(columns={"日期": "dt", "收盘": "close", "开盘": "open",
                                          "最高": "high", "最低": "low"})
                    a["dt"] = pd.to_datetime(a["dt"])
                    df = a[["dt", "open", "high", "low", "close"]]
                    layer = "L2-eastmoney"
        except Exception as e:
            print("[sw] %s L2 东财失败 %s" % (code, str(e)[:60]), flush=True)

    # ── L3 tushare（垫底，8s 硬超时）
    if df is None or not len(df):
        try:
            from tushare_init import ts_call
            df = ts_call("sw_daily", ts_code=code, start_date=beg, end_date=today8)
            if df is not None and len(df):
                layer = "L3-tushare"
                df = df.rename(columns={"trade_date": "dt"})
        except Exception:
            pass

    if df is None or not len(df):
        return old
    if layer:
        print("[sw] %s ← %s（%d 行）" % (code, layer, len(df)), flush=True)
    df["dt"] = pd.to_datetime(df["dt"])
    df = df[["dt", "open", "high", "low", "close"]].sort_values("dt")
    if old is not None and len(old):
        df = pd.concat([old, df]).drop_duplicates("dt", keep="last").sort_values("dt")
    df = df.reset_index(drop=True)
    try:
        df.to_csv(p, index=False)
    except Exception:
        pass
    return df


def weekly_of(df):
    """日线 → 周线（W-FRI）；返回 (全部周, 已收盘周)"""
    d = df.set_index("dt")
    w = pd.DataFrame({
        "open": d["open"].resample("W-FRI").first(),
        "high": d["high"].resample("W-FRI").max(),
        "low": d["low"].resample("W-FRI").min(),
        "close": d["close"].resample("W-FRI").last()}).dropna().reset_index()
    last_day = df["dt"].max()
    done = w[w["dt"] <= last_day]
    if not len(done):
        done = w
    return w, done


def zs_state(df):
    """周线中枢 + 当前位置。df=日线。返回 dict"""
    if df is None or len(df) < 300:
        return {"ok": False, "why": "数据不足(%s)" % (0 if df is None else len(df))}
    _w, done = weekly_of(df)
    if len(done) < 20:
        return {"ok": False, "why": "周线不足 %d 根" % len(done)}
    r = C.chan_read(C.bars_of(done), "week")
    if not r.get("ok") or not r.get("zs"):
        return {"ok": False, "why": "无中枢"}
    zd, zg = float(r["zs"][0]), float(r["zs"][1])
    close = float(df["close"].iloc[-1])
    last_week = done["dt"].iloc[-1]
    wk_close = float(done["close"].iloc[-1])
    if close > zg:
        zpos = "above"
    elif close < zd:
        zpos = "below"
    else:
        zpos = "in"
    # 位置判定用「最新收盘」（周内滚动），中枢用已收盘周
    prev_wk = float(done["close"].iloc[-2]) if len(done) >= 2 else wk_close
    return {"ok": True, "zg": round(zg, 2), "zd": round(zd, 2), "close": round(close, 2),
            "zpos": zpos, "week": last_week.strftime("%Y-%m-%d"),
            "wk_close": round(wk_close, 2),
            "prev_zpos": _pos(prev_wk, zd, zg),
            "prev_wk_close": round(prev_wk, 2),
            "to_zg": round((zg - close) / close * 100, 2),
            "to_zd": round((close - zd) / zd * 100, 2),
            "n_week": int(len(done)),
            "wbin": int(len(_w) - len(done))}


def _pos(price, zd, zg):
    if price > zg:
        return "above"
    if price < zd:
        return "below"
    return "in"


# ────────────────────────── 持仓 → 行业 ──────────────────────────
def holdings_industries(port_file):
    """返回 {l2_name: {'w': 权重合计, 'l3': {l3_name: w}, 'stocks': [(name, w, l2, l3)]}}"""
    out = {}
    if not os.path.exists(port_file):
        return out
    try:
        d = json.load(open(port_file, encoding="utf-8"))
    except Exception:
        return out
    for h in d.get("holdings") or []:
        sym = str(h.get("symbol") or "")
        w = float(h.get("weight") or 0)
        if w <= 0:
            continue
        nm = h.get("name") or sym
        if sym[:2] in ("SH", "SZ"):
            if sym[2:5] in ("159", "161", "164", "501", "588", "510", "512", "513"):  # ETF/LOF 跳过
                continue
            l2, l3 = stock_sw(sym[2:] + "." + sym[:2])
        else:
            l2, l3 = HK_SW.get(sym), None
        if not l2:
            continue
        o = out.setdefault(l2, {"w": 0.0, "l3": {}, "stocks": []})
        o["w"] += w
        if l3:
            o["l3"][l3] = o["l3"].get(l3, 0.0) + w
        o["stocks"].append((nm, w, l2, l3))
    return out


def merge_ports():
    """合并两个组合的行业权重（按行业名）"""
    tot = {}
    for lab, f in PORTS:
        for ind, v in holdings_industries(f).items():
            o = tot.setdefault(ind, {"w": 0.0, "by": {}, "l3": {}, "stocks": []})
            o["w"] += v["w"]
            o["by"][lab] = v["w"]
            for l3n, l3w in (v.get("l3") or {}).items():
                o["l3"][l3n] = o["l3"].get(l3n, 0.0) + l3w
            o["stocks"].extend(v["stocks"])
    return tot


def merge_l3(inds, min_w):
    """从 L2 结果里把三级行业汇总出来（只保留权重 ≥ min_w 的，控制请求数）"""
    tot = {}
    for ind, v in inds.items():
        for l3n, l3w in (v.get("l3") or {}).items():
            o = tot.setdefault(l3n, {"w": 0.0, "own2": set(), "stocks": []})
            o["w"] += l3w
            o["own2"].add(ind)
            for nm, w, l2, l3 in v["stocks"]:
                if l3 == l3n:
                    o["stocks"].append((nm, w))
    return {k: v for k, v in tot.items() if v["w"] >= min_w}


# ────────────────────────── 主计算 ──────────────────────────
def compute(no_cache=False, ttl_min=60):
    today = datetime.date.today().strftime("%Y%m%d")
    cpath = os.path.join(CACHE, "res_%s.json" % today)
    if not no_cache and os.path.exists(cpath):
        try:
            r = json.load(open(cpath, encoding="utf-8"))
            if r.get("wide") and time.time() - os.path.getmtime(cpath) < ttl_min * 60:
                return r
        except Exception:
            pass

    res = {"date": today, "wide": [], "ind": [], "errs": []}
    l2m = sw_l2_map()
    rev2 = {v: k for k, v in l2m.items()}

    # ① 五大宽基
    for name, code in WIDE:
        try:
            df = C.fetch_daily(code)
            st = zs_state(df)
            st.update({"name": name, "code": code, "kind": "wide"})
            res["wide"].append(st)
            if not st.get("ok"):
                res["errs"].append("%s: %s" % (name, st.get("why")))
        except Exception as e:
            res["errs"].append("%s: %s" % (name, str(e)[:80]))
        time.sleep(0.3)

    # ② 持仓行业（申万二级）
    inds = merge_ports()
    for ind, v in sorted(inds.items(), key=lambda x: -x[1]["w"]):
        code = rev2.get(ind)
        if not code:
            res["errs"].append("行业[%s] 无申万代码" % ind)
            continue
        try:
            df = fetch_sw_daily(code)
            if df is None or not len(df):
                res.setdefault("nodata", []).append(ind)
                continue
            st = zs_state(df)
            st.update({"name": ind, "code": code, "kind": "ind",
                       "w": round(v["w"], 2), "by": v["by"],
                       "l3": {k: round(x, 2) for k, x in v["l3"].items()},
                       "stocks": [(a, round(b, 2)) for a, b, _, _ in
                                  sorted(v["stocks"], key=lambda z: -z[1])[:4]]})
            res["ind"].append(st)
            if not st.get("ok"):
                res["errs"].append("行业[%s]: %s" % (ind, st.get("why")))
        except Exception as e:
            res["errs"].append("行业[%s]: %s" % (ind, str(e)[:80]))
        time.sleep(0.4)

    # ③ 申万三级（只在权重够大时单独算一条，默认 ≥ L3_MIN_W）
    l3m = sw_l3_map()
    rev3 = {v: k for k, v in l3m.items()}
    l3s = merge_l3(inds, L3_MIN_W)
    res["ind3"] = []
    for nm, v in sorted(l3s.items(), key=lambda x: -x[1]["w"]):
        code = rev3.get(nm)
        if not code:
            continue
        try:
            df = fetch_sw_daily(code)
            if df is None or not len(df):
                res.setdefault("nodata", []).append(nm + "(三级)")
                continue
            st = zs_state(df)
            st.update({"name": nm, "code": code, "kind": "ind3",
                       "w": round(v["w"], 2), "parent": "、".join(sorted(v["own2"])),
                       "stocks": [(a, round(b, 2)) for a, b in
                                  sorted(v["stocks"], key=lambda z: -z[1])[:3]]})
            res["ind3"].append(st)
        except Exception as e:
            res["errs"].append("三级[%s]: %s" % (nm, str(e)[:80]))
        time.sleep(0.4)

    try:
        json.dump(res, open(cpath, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return res


# ────────────────────────── 事件（周度去重） ──────────────────────────
def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def events(res, save=True):
    """返回新发生的中枢事件 [(kind,name,zpos_prev,zpos_now,st)]，并落状态

    基线优先级：① 状态文件上次记录的位置（最准）② 没有状态时用数据自身算出的
    上一周收盘位置 prev_zpos —— 这样首次运行也能报出本周真实发生的突破/跌破，
    而不是静默建完基线等下周。
    """
    st = load_state()
    ev = []
    for grp in ("wide", "ind", "ind3"):
        for s in res.get(grp) or []:
            if not s.get("ok"):
                continue
            key = "%s|%s" % (s["kind"], s["name"])
            prev = st.get(key) or {}
            pz = prev.get("zpos")
            if not pz:
                pz = s.get("prev_zpos")        # 首次：用上周收盘位置当基线
            # 去重口径：**按位置变化**，不按「周」。
            #   同一位置跑多少次都不重复报；同一周内先破后回（假突破）会连报两次，
            #   那是真实发生的事，应该报。
            if pz and pz != s["zpos"]:
                ev.append((s["kind"], s["name"], pz, s["zpos"], s))
            st[key] = {"week": s["week"], "zpos": s["zpos"], "zg": s["zg"], "zd": s["zd"],
                       "close": s["close"]}
    if save:
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception as e:
            log("[WARN] 状态写入失败 %s" % str(e)[:80])
    return ev


TAG = {"above": "🌙突破中枢", "below": "⭐跌破中枢", "in": "↪中枢内"}
SHORT = {"above": "🌙", "below": "⭐", "in": ""}

# 位置迁移 → 事件描述
MOVE = {
    ("in", "above"): "🌙 上破中枢上沿 ZG（突破中枢）",
    ("in", "below"): "⭐ 下破中枢下沿 ZD（跌破中枢）",
    ("below", "above"): "🌙 直接穿越中枢向上（强突破）",
    ("above", "below"): "⭐ 直接跌破整个中枢（强跌破）",
    ("above", "in"): "↪ 跌回中枢内（突破失败）",
    ("below", "in"): "↪ 拉回中枢内（跌破修复）",
}


def ev_desc(pz, nz):
    return MOVE.get((pz, nz), "%s → %s" % (TAG.get(pz, pz), TAG.get(nz, nz)))


def _line_wide(s):
    if not s.get("ok"):
        return "  %-8s 数据不足（%s）" % (s["name"], s.get("why"))
    tag = TAG[s["zpos"]]
    if s["zpos"] == "above":
        extra = "高于 ZG %.2f%%" % (-s["to_zg"])
    elif s["zpos"] == "below":
        extra = "低于 ZD %.2f%%" % s["to_zd"]
    else:
        extra = "距上沿 %.1f%% / 距下沿 %.1f%%" % (s["to_zg"], s["to_zd"])
    return "  %-6s %9.2f %s ｜ 中枢 %.2f~%.2f ｜ %s" % (
        s["name"], s["close"], tag, s["zd"], s["zg"], extra)


def _line_ind(s):
    if not s.get("ok"):
        return "  %-10s 仓%4.1f%% ｜ 数据不足（%s）" % (s["name"], s.get("w", 0), s.get("why"))
    tag = TAG[s["zpos"]]
    if s["zpos"] == "above":
        extra = "高于 ZG %.2f%%" % (-s["to_zg"])
    elif s["zpos"] == "below":
        extra = "低于 ZD %.2f%%" % s["to_zd"]
    else:
        extra = "中枢内"
    return "  %-10s 仓%4.1f%% %s ｜ 中枢 %.2f~%.2f ｜ %s" % (
        s["name"], s.get("w", 0), tag, s["zd"], s["zg"], extra)


def section_text(date=None, res=None, evs=None, title="【📐 周线中枢 · 五大宽基 + 持仓行业（周度）】",
                 max_ind=8):
    """给 xq_report / 独立推送用的文本块（纯文本，卡片可直读）

    evs=None → 不判事件、**不写状态**（报告内嵌用：每周同一份快照，稳定不打扰）
    evs=[]   → 已判过、本周无事件
    evs=[..] → 本周新发生的事件
    """
    if res is None:
        res = compute()
    L = [title]
    L.append("  口径：周线（W-FRI，只用已收盘周算中枢）｜版本：缠论·分型版"
             "｜🌙突破中枢 ⭐跌破中枢 ｜ 按位置变化告警，同一位置不重复")
    L.append("  ── 五大宽基 ──")
    for s in res["wide"]:
        L.append(_line_wide(s))
    L.append("  ── 持仓行业 · 申万二级（默尔根+亚丽 合计权重）──")
    for s in (res["ind"][:max_ind] if max_ind else res["ind"]):
        L.append(_line_ind(s))
    if max_ind and len(res["ind"]) > max_ind:
        L.append("  ……另有 %d 个行业（权重更小，未列）" % (len(res["ind"]) - max_ind))
    if res.get("ind3"):
        L.append("  ── 持仓行业 · 申万三级（权重≥%.0f%%）──" % L3_MIN_W)
        for s in res["ind3"]:
            L.append(_line_ind(s).replace("仓", "三级仓"))
    if evs is None:
        pass
    elif evs:
        L.append("  🔔 本周新发生中枢事件：")
        for k, nm, pz, nz, s in evs:
            L.append("    · %s %s → %s（%s）" % (
                {"wide": "宽基", "ind": "二级", "ind3": "三级"}.get(k, "行业"),
                nm, TAG[nz],
                "%.2f~%.2f" % (s["zd"], s["zg"])))
    else:
        L.append("  ✅ 本周无新的中枢突破/跌破")
    if res.get("errs"):
        L.append("  ⚠️ 数据：%s" % "；".join(res["errs"][:3]))
    return "\n".join(L)


def week_report(res, evs):
    """独立周报（比报告内嵌版更全，含全部行业 + 三级明细）"""
    d = res["date"]
    L = ["📐 周线中枢 · 周度预警 · %s-%s-%s" % (d[:4], d[4:6], d[6:])]
    L.append("级别：周线（W-FRI，只用已收盘周算中枢）｜版本：缠论·分型版"
             "（包含处理→顶底分型→笔→中枢ZG/ZD）")
    L.append("🌙突破中枢（上破 ZG）｜⭐跌破中枢（下破 ZD）｜↪中枢内")
    L.append("")
    L.append("━━ 一、五大宽基 ━━")
    for s in res["wide"]:
        L.append(_line_wide(s))
    L.append("")
    L.append("━━ 二、持仓行业 · 申万二级（两组合合并权重）━━")
    for s in res["ind"]:
        L.append(_line_ind(s))
        if s.get("ok") and s.get("l3"):
            l3 = "、".join("%s%.1f%%" % (k, v) for k, v in
                          sorted(s["l3"].items(), key=lambda x: -x[1]))
            L.append("      三级：%s" % l3)
        if s.get("ok") and s.get("stocks"):
            L.append("      个股：%s" % "、".join("%s%.1f%%" % (a, b) for a, b in s["stocks"]))
    if res.get("ind3"):
        L.append("")
        L.append("━━ 二·B 持仓行业 · 申万三级（权重≥%.0f%%，单独判中枢）━━" % L3_MIN_W)
        for s in res["ind3"]:
            L.append(_line_ind(s).replace("仓", "三级仓"))
            if s.get("ok") and s.get("parent"):
                L.append("      归属二级：%s" % s["parent"])
            if s.get("ok") and s.get("stocks"):
                L.append("      个股：%s" % "、".join("%s%.1f%%" % (a, b) for a, b in s["stocks"]))
    L.append("")
    L.append("━━ 三、本周预警 ━━")
    if evs:
        for k, nm, pz, nz, s in evs:
            L.append("  🔔 %s %s：%s → %s ｜ 中枢 %.2f~%.2f ｜ 现价 %.2f" % (
                {"wide": "宽基", "ind": "二级", "ind3": "三级"}.get(k, "行业"),
                nm, TAG[pz], TAG[nz],
                s["zd"], s["zg"], s["close"]))
    else:
        L.append("  ✅ 本周无新的中枢突破/跌破（所有标的仍在原区间/原方向）")
    if res.get("errs"):
        L.append("")
        L.append("⚠️ 数据缺口：" + "；".join(res["errs"][:6]))
    L.append("")
    L.append("口径：周线中枢取「已收盘周」的缠论分型版中枢；位置用最新收盘价对比 ZG/ZD；"
             "只在位置发生变化（上破ZG/下破ZD/回到中枢内）时告警，同一位置不重复。")
    return "\n".join(L)


def report_section(max_ind=8, wide_only=False):
    """报告内嵌版（xq_report 用）：**只看不消费**事件

    → 位置一变化，之后的每份早晚报都会在「本周新发生中枢事件」里列出来，
      直到周五那次独立周报把它消费掉（消费后报告里就只剩状态表）。
    wide_only=True → 只出五大宽基（行业部分交给 xq_ind_monitor 的专项段，避免重复）
    """
    res = compute()
    evs = events(res, save=False)
    if wide_only:
        title = "【📐 周线中枢 · 五大宽基（周度）】"
        L = [title,
             "  口径：周线（W-FRI，只用已收盘周算中枢）｜版本：缠论·分型版"
             "｜🌙突破中枢 ⭐跌破中枢",
             ]
        for s in res["wide"]:
            L.append(_line_wide(s))
        return "\n".join(L)
    return section_text(res=res, evs=evs, max_ind=max_ind)


# ────────────────────────── 推送 ──────────────────────────
def push_feishu(text, title):
    try:
        import feishu_card as FC
        blocks = []
        for i, para in enumerate(text.split("\n━━ ")):
            blocks.append({"type": "md", "text": para if i == 0 else "━━ " + para})
        ok = FC.send_card(title, blocks, chat=FEISHU_CHAT, template="turquoise",
                          sign="📎 来自 OpenClaw.Deepseek")
        log("[feishu] %s" % ("ok" if ok else "fail"))
        return bool(ok)
    except Exception as e:
        log("[feishu] ERR %s" % str(e)[:120])
        return False


def push_qq(text):
    try:
        import subprocess
        r = subprocess.run(["openclaw", "message", "send", "--channel", "qqbot",
                            "--target", QQ_TARGET, "--message", text, "--json"],
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        ok = ('"messageId"' in out and '"messageId": ""' not in out) or "receipt" in out
        log("[qq] rc=%s %s" % (r.returncode, "ok" if ok else out[-160:]))
        return ok
    except Exception as e:
        log("[qq] ERR %s" % str(e)[:120])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--force", action="store_true", help="无变化也推（周报全文）")
    ap.add_argument("--mail", action="store_true", help="有变化时同时发邮件")
    ap.add_argument("--qq-only", action="store_true")
    ap.add_argument("--feishu-only", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()

    t0 = time.time()
    res = compute(no_cache=a.no_cache)
    evs = events(res, save=True)
    log(section_text(res=res, evs=evs))
    log("")
    rep = week_report(res, evs)
    log(rep)
    log("[耗时 %.1fs ｜ 宽基 %d ｜ 行业 %d ｜ 事件 %d]" % (
        time.time() - t0, len(res["wide"]), len(res["ind"]), len(evs)))

    if not a.push:
        return
    if not evs and not a.force:
        log("[push] 本周无新事件，静默（--force 可强制推）")
        return
    d = res["date"]
    title = "📐 周线中枢预警 · %s-%s-%s ｜ 宽基+持仓行业" % (d[:4], d[4:6], d[6:])
    if a.mail:
        try:
            import xq_ind_monitor as XIM
            XIM.send_mail("🔔 五大宽基·周线中枢 · %s-%s-%s ｜ %d 条变化"
                          % (d[:4], d[4:6], d[6:], len(evs)), rep)
        except Exception as e:
            log("[mail] ERR %s" % str(e)[:120])
    if not a.qq_only:
        push_feishu(rep, title)
    if not a.feishu_only:
        push_qq(rep)


if __name__ == "__main__":
    main()
