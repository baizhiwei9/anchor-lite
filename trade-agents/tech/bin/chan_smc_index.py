#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5宽基 · 缠论「几买/距离」+ SMC 日线/周线机会风险（极简版）
输出极简，只讲：几买、差多少到买点、差多少背驰、SMC机会/风险
"""
import os, sys, json, socket, time
from datetime import datetime

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEISHU_CHAT = os.environ.get("TECH_FEISHU_CHAT") or os.environ.get("CHAN_FEISHU_CHAT") or ""  # 留空=不发
sys.path.insert(0, os.path.join(WS, "bin"))
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd

# ─────────── 机会/风险 判定参数（2026-10-22 用户要求：只给真买卖点打图标）───────────
NEAR = float(os.environ.get("CHAN_NEAR_PCT", "3"))     # 现价与买卖点 ±NEAR% 内 = 「就在点位上」
MAX_AGE = {"h1": 30, "day": 25, "week": 12}            # 买卖点新鲜度（多少根K线内还算数）
BUY_STARS = {"一买": 3, "二买": 2, "三买": 1}           # 买点强度 → 月亮数
SELL_STARS = {"一卖": 3, "二卖": 2, "三卖": 1}          # 卖点强度 → 星星数

# ⚠️ 2026-11-08 大佬定稿：**小时线只认三买，一买二买只认日线**
#   原话：「这个缠论二买，它是二买和一买，它都是在那个中枢以下的。其实我不需要，
#          你只有三日线的一买二买还有意义，小时级别的一买二买都没有意义，只有三买有意义」
#   理由：一买二买都发生在中枢下方，小级别噪音太大；三买是「离开中枢后回踩不破」，
#         结构确认度高，小级别才有操作价值。
H1_ALLOWED_BUY = {"三买"}      # 小时线允许的买点类型
H1_ALLOWED_SELL = {"三卖"}     # 小时线允许的卖点类型（对称口径）

# ─────────── 缠论核心（与 xq_industry 同口径） ───────────
def merge_bars(bars):
    out = []
    for h, l in bars:
        if not out:
            out.append([h, l]); continue
        ph, pl = out[-1]
        if (h <= ph and l >= pl) or (h >= ph and l <= pl):
            up = len(out) < 2 or out[-1][0] >= out[-2][0]
            if up: out[-1] = [max(ph, h), max(pl, l)]
            else:  out[-1] = [min(ph, h), min(pl, l)]
        else:
            out.append([h, l])
    return out

def fractals(mb):
    fr = []
    for i in range(1, len(mb) - 1):
        a, b, c = mb[i-1], mb[i], mb[i+1]
        if b[0] > a[0] and b[0] > c[0] and b[1] > a[1] and b[1] > c[1]: fr.append((i, 1, b[0]))
        elif b[0] < a[0] and b[0] < c[0] and b[1] < a[1] and b[1] < c[1]: fr.append((i, -1, b[1]))
    return fr

def build_bi(mb):
    fr = fractals(mb); bi = []
    for idx, t, px in fr:
        if not bi: bi.append([t, px, idx])
        elif bi[-1][0] == t:
            if (t == 1 and px > bi[-1][1]) or (t == -1 and px < bi[-1][1]): bi[-1] = [t, px, idx]
        else:
            if abs(idx - bi[-1][2]) >= 3: bi.append([t, px, idx])
    return bi

def segs_from_bi(bi):
    segs = []
    for k in range(len(bi) - 1):
        a, b = bi[k], bi[k+1]
        segs.append({"up": b[0] == 1, "from": a[1], "to": b[1],
                     "lo": min(a[1], b[1]), "hi": max(a[1], b[1]), "i0": a[2], "i1": b[2]})
    return segs

# ── MACD 参数（2026-10-29 22:48 大佬定稿：改成 10/20/5，不是原来的 12/26/9）──
# 可用环境变量覆盖，方便回测对比；默认即 10/20/5
MACD_FAST = int(os.environ.get("CHAN_MACD_FAST", "10"))
MACD_SLOW = int(os.environ.get("CHAN_MACD_SLOW", "20"))
MACD_SIGNAL = int(os.environ.get("CHAN_MACD_SIGNAL", "5"))


def macd_hist(close):
    """MACD：默认 (10, 20, 5) —— 缠论·分型版本机统一口径

    缠论有两个流派，本机统一走**分型版**：
      · 均线版   = MA10/20/40/80/160 + MACD 判背驰（不采用）
      · 分型版   = 包含处理→顶底分型→笔→中枢 ZG/ZD→背驰（★本机唯一口径）
    两者都只是「输入数据 → 缠论判断」的同一套流程，MACD 只服务于背驰判定。
    """
    c = pd.Series(close).astype(float)
    dif = (c.ewm(span=MACD_FAST, adjust=False).mean()
           - c.ewm(span=MACD_SLOW, adjust=False).mean())
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return (2 * (dif - dea)).values, dif.values, dea.values


def chan_read(bars, tag):
    mb = merge_bars(bars)
    if len(mb) < 12: return {"ok": False}
    bi = build_bi(mb)
    if len(bi) < 4: return {"ok": False}
    segs = segs_from_bi(bi)
    approx = np.array([(h + l) / 2 for h, l in mb], float)
    hist, dif, dea = macd_hist(approx)
    # 中枢
    zs, zs_i = None, None
    for i in range(len(segs) - 3, -1, -1):
        w = segs[i:i+3]
        zg = min(x["hi"] for x in w); zd = max(x["lo"] for x in w)
        if zg > zd: zs = (round(zd, 2), round(zg, 2)); zs_i = i; break
    last = segs[-1]; d = "上升" if last["up"] else "下降"
    cur = float(mb[-1][0])
    amp = lambda s: abs(s["to"] - s["from"])
    def pw(s, up):
        a, b = max(0, s["i0"]-1), min(len(hist)-1, s["i1"])
        if b <= a: return 0.0
        seg = hist[a:b]
        return float(seg.max() if up else -seg.min())
    dn_div = []
    for k, s in enumerate(segs):
        if s["up"]: continue
        same = [j for j in range(k) if not segs[j]["up"]]
        if not same: continue
        p = segs[same[-1]]
        if s["to"] < p["to"] and (amp(s) < amp(p) or pw(s, False) < pw(p, False)):
            dn_div.append(k)
    y1_low = round(segs[dn_div[-1]]["to"], 2) if dn_div else None
    # ── 买点（侦测用）：一买/二买/三买 ──
    buys = []      # (tag, price, bar_idx)
    if dn_div:
        k0 = dn_div[-1]
        buys.append(("一买", round(segs[k0]["to"], 2), segs[k0]["i1"]))
        for k in range(k0 + 1, len(segs)):
            if not segs[k]["up"] and segs[k]["to"] > segs[k0]["to"]:
                buys.append(("二买", round(segs[k]["to"], 2), segs[k]["i1"]))
                break
    # ── 卖点（对称口径，仅侦测用）：up_div = 上升笔 + 顶背驰 = 一卖候选 ──
    up_div = []
    for k, s in enumerate(segs):
        if not s["up"]: continue
        same = [j for j in range(k) if segs[j]["up"]]
        if not same: continue
        p = segs[same[-1]]
        if s["to"] > p["to"] and (amp(s) < amp(p) or pw(s, True) < pw(p, True)):
            up_div.append(k)
    sells = []     # (tag, price, bar_idx)
    if up_div:
        k0 = up_div[-1]
        sells.append(("一卖", round(segs[k0]["to"], 2), segs[k0]["i1"]))
        for k in range(k0 + 1, len(segs)):
            if segs[k]["up"] and segs[k]["to"] < segs[k0]["to"]:
                sells.append(("二卖", round(segs[k]["to"], 2), segs[k]["i1"]))
                break
    if zs:
        dleaf = [k for k in range(zs_i + 3, len(segs) - 1)
                 if not segs[k]["up"] and segs[k]["lo"] < zs[0]]
        for k in dleaf:
            r = segs[k + 1]
            if r["up"] and r["hi"] < zs[0]:
                sells.append(("三卖", round(r["hi"], 2), r["i1"]))
                break
        # 三买：向上离开中枢后回调不破 ZG
        uleaf = [k for k in range(zs_i + 3, len(segs) - 1)
                 if segs[k]["up"] and segs[k]["hi"] > zs[1]]
        for k in uleaf:
            r = segs[k + 1]
            if not r["up"] and r["lo"] > zs[1]:
                buys.append(("三买", round(r["lo"], 2), r["i1"]))
                break
    # 一买/二买/三买 距离
    P = cur
    out = {"ok": True, "dir": d, "cur": round(cur, 2), "zs": zs,
           "y1_low": y1_low, "div": None,
           "to_low": round((P - last["lo"]) / P * 100, 2) if not last["up"] else None,
           # 2026-10-26 一买预备（bin/y1_watch.py）需要的原始量：
           #   最后一笔方向 / 所有下跌笔低点 / 所有下跌笔 MACD 力度 / 最近 MACD 柱
           "last_up": bool(last["up"]),
           "dn_lows": [round(float(s["to"]), 2) for s in segs if not s["up"]],
           "dn_pw": [round(pw(s, False), 6) for s in segs if not s["up"]],
           "hist_tail": [round(float(x), 6) for x in hist[-6:]]}
    # 背驰（最近笔）
    up = last["up"]
    div = None
    if not up:
        same = [j for j in range(len(segs)-1) if not segs[j]["up"]]
        if len(same) >= 2:
            a, b = segs[same[-2]], segs[same[-1]]
            if b["to"] < a["to"] and (amp(b) < amp(a) or pw(b, False) < pw(a, False)):
                div = "底背驰"
    else:
        same = [j for j in range(len(segs)-1) if segs[j]["up"]]
        if len(same) >= 2:
            a, b = segs[same[-2]], segs[same[-1]]
            if b["to"] > a["to"] and (amp(b) < amp(a) or pw(b, True) < pw(a, True)):
                div = "顶背驰"
    out["div"] = div
    # 距下一买点
    if zs:
        out["to_3"] = round((zs[1] - P) / P * 100, 2)
        out["to_zd"] = round((P - zs[0]) / P * 100, 2)
    if y1_low is not None:
        out["to_y1"] = round((y1_low - P) / P * 100, 2)   # 正=需涨到一买低点之上, 负=已在上方
    # ── 阶段（买点/卖点区）判定 ──
    u1_high = round(segs[up_div[-1]]["to"], 2) if up_div else None
    if zs and P > zs[1]:
        out["stage"] = "三买区（离开中枢向上）" if up else "中枢上方回落（观察）"
    elif zs and P < zs[0]:
        out["stage"] = "三卖结构（反抽不破中枢下沿）" if up else "中枢下方下跌（趋势破坏）"
    elif y1_low is not None and P >= y1_low and not up:
        out["stage"] = "二买区（回踩不破一买低点）"
    elif u1_high is not None and P <= u1_high and up:
        out["stage"] = "二卖区（反抽不破一卖高点）"
    elif div == "底背驰":
        out["stage"] = "一买区（底背驰）"
    elif div == "顶背驰":
        out["stage"] = "一卖区（顶背驰）"
    elif not up:
        out["stage"] = "下跌笔中·无买卖点"
    else:
        out["stage"] = "上升笔中·无买卖点"
    # 距背驰
    if div:
        out["div_dist"] = 0.0
    else:
        # 需再创新低(旧低)才谈底背驰
        out["div_dist"] = round((P - last["lo"]) / P * 100, 2)
    # ── 逐条买卖点清单（带新鲜度 age + 是否就在点位上 active）──
    n_mb = len(mb) - 1
    maxage = MAX_AGE.get(tag, 25)
    def _mk(lst, is_buy=True):
        res, seen = [], set()
        for t, px, idx in sorted(lst, key=lambda x: -x[2]):      # 新的在前
            if t in seen:
                continue
            seen.add(t)
            age = max(0, n_mb - idx)
            # ⚠️ 2026-11-08 大佬关键补充：
            # 「不一定要穿越，就几根几根K线内穿越，也可能表示多头排列就可以」
            # → 超过 maxage 的买点不能直接丢掉。只要**结构没被破坏**
            #   （现价仍在买点之上 / 卖点之下），它就是「长期有效」的延续信号。
            #   fresh=新鲜信号，sustained=长期未破的延续信号，两者都要报。
            gap = ((cur - px) / px * 100) if px > 0 else 0.0
            # 长期信号必须**结构确实没破**：买点现价要站上（gap>=0），卖点要在下方（gap<=0）
            # （新鲜的信号可以宽容到 ±1.5%：刚到点位附近也算；长期的必须真站住）
            ok_struct = (gap >= 0) if is_buy else (gap <= 0)
            res.append({"tag": t, "level": px, "age": age,
                        "active": abs(cur - px) / px * 100 <= NEAR,
                        "fresh": age <= maxage,
                        "sustained": (age > maxage) and ok_struct})
        return res
    out["sells"] = [(t, px) for t, px, _ in sells[-4:]]   # 兼容旧调用（chan_signal_monitor）
    out["buys"] = [(t, px) for t, px, _ in buys[-4:]]
    out["sell_list"] = _mk(sells, is_buy=False)
    out["buy_list"] = _mk(buys, is_buy=True)
    out["maxage"] = maxage
    # ── 中枢突破/跌破位置（2026-10-23 新增，供 chan_signal_monitor 做穿入告警）──
    # zone: in=中枢内 / above=中枢上方 / below=中枢下方 / None=无中枢
    if zs:
        zg, zd = zs[1], zs[0]
        if P > zg:
            zpos = "above"
        elif P < zd:
            zpos = "below"
        else:
            zpos = "in"
        out["zpos"] = zpos
        out["zg"], out["zd"] = zg, zd
        out["to_zg"] = round((zg - P) / P * 100, 2)    # 正=还要涨多少到上沿
        out["to_zd"] = round((P - zd) / P * 100, 2)    # 正=离下沿还有多少缓冲
    else:
        out["zpos"] = None
    return out

# ─────────── SMC 结构（简化） ───────────
def pivots(df, k):
    h, l = df["high"].values, df["low"].values
    hi, lo = [], []
    for i in range(k, len(df) - k):
        wh, wl = h[i-k:i+k+1], l[i-k:i+k+1]
        if h[i] == wh.max() and (wh == h[i]).sum() == 1: hi.append(i)
        if l[i] == wl.min() and (wl == l[i]).sum() == 1: lo.append(i)
    return hi, lo

def smc_read(df, k, tag):
    if len(df) < 3 * k + 5: return {"ok": False}
    hi, lo = pivots(df, k)
    if len(hi) < 2 or len(lo) < 2: return {"ok": False}
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    P = float(c[-1])
    H1, H2 = float(h[hi[-1]]), float(h[hi[-2]])   # 最近/次近 摆动高
    L1, L2 = float(l[lo[-1]]), float(l[lo[-2]])   # 最近/次近 摆动低
    up_hi = H1 > H2          # 高点上移
    dn_lo = L1 < L2          # 低点下移
    # ── 结构（高点/低点序列）──
    if up_hi and not dn_lo:   struct = "多头结构 (高点↑ 低点↑)"
    elif dn_lo and not up_hi: struct = "空头结构 (高点↓ 低点↓)"
    elif up_hi and dn_lo:     struct = "扩张 (高低点同时外扩)"
    else:                     struct = "收敛 (高低点同时内收)"
    # ── 破位状态（价格 vs 最近摆动点）──
    if P > H1:
        bos = "已上破最近摆动高 %.2f → 转多信号" % H1
    elif P < L1:
        bos = "已下破最近摆动低 %.2f → 转空信号" % L1
    else:
        bos = "在最近摆动区间 %.2f ~ %.2f 内" % (L1, H1)
    # ── 位置（近两个摆动构成的区间）──
    rng_h, rng_l = max(H1, H2), min(L1, L2)
    raw = (P - rng_l) / (rng_h - rng_l) * 100 if rng_h > rng_l else 50.0
    if raw > 100:   zone = "已上破区间上沿"
    elif raw < 0:   zone = "已跌破区间下沿"
    elif raw > 55:  zone = "区间偏上（溢价）"
    elif raw < 45:  zone = "区间偏下（折价）"
    else:           zone = "区间中部（均衡）"
    # ── 机会 / 风险（⭐风险 🌙机会）──
    opp, rk = [], []
    if P > H1:
        opp.append("上破摆动高，结构偏多")
    if struct.startswith("多头"):
        opp.append("多头结构 (HH+HL)")
    if P < L1:
        rk.append("下破摆动低，结构偏空")
    if struct.startswith("空头"):
        rk.append("空头结构 (LH+LL)，反弹减持")
    if raw > 100:
        rk.append("上破区间上沿，追高风险")
    elif raw < 0:
        rk.append("跌破区间下沿，弱势")
    elif raw > 60:
        rk.append("区间偏上，回踩概率大")
    elif raw < 40:
        opp.append("区间偏下，回踩可低吸")
    if struct.startswith("扩张"):
        rk.append("区间扩张，波动放大")
    if struct.startswith("收敛"):
        opp.append("区间收敛，等方向选择")
    return {"ok": True, "struct": struct, "pos": round(raw, 1), "zone": zone,
            "P": round(P, 2), "rng": (round(rng_l, 2), round(rng_h, 2)),
            "bos": bos,
            "to_bos_up": round((rng_h - P) / P * 100, 2),
            "to_bos_dn": round((P - rng_l) / P * 100, 2),
            "opp": opp, "rk": rk}

# ─────────── 数据 ───────────
IDX = [("沪深300", "000300.SH"), ("中证500", "000905.SH"),
       ("中证1000", "000852.SH"), ("创业板指", "399006.SZ"), ("科创50", "000688.SH")]

def fetch_daily(ts_code):
    cache = os.path.join(WS, ".cache/chan_daily", ts_code + ".csv")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    import tushare_init as ti
    pro = ti.get_pro()
    for k in range(4):
        try:
            df = pro.index_daily(ts_code=ts_code, start_date="20220101")
            if df is not None and len(df) > 100:
                df = df.rename(columns={"trade_date": "dt"}); df["dt"] = pd.to_datetime(df["dt"])
                df = df.sort_values("dt").reset_index(drop=True)
                try: df.to_csv(cache, index=False)
                except Exception: pass
                return df
        except Exception as e:
            time.sleep(6 + k * 6)
    # 回退本地缓存
    if os.path.exists(cache):
        try:
            df = pd.read_csv(cache, parse_dates=["dt"])
            if len(df) > 100: return df
        except Exception: pass
    return None

def resample_w(df):
    d = df.set_index("dt")
    o = pd.DataFrame({
        "open": d["open"].resample("W-FRI").first(),
        "high": d["high"].resample("W-FRI").max(),
        "low": d["low"].resample("W-FRI").min(),
        "close": d["close"].resample("W-FRI").last()}).dropna().reset_index()
    return o.rename(columns={"index": "dt"})

def bars_of(df): return list(zip(df["high"].astype(float).values, df["low"].astype(float).values))

IDX_SH = {"沪深300": "sh000300", "中证500": "sh000905", "中证1000": "sh000852",
          "创业板指": "sz399006", "科创50": "sh000688"}


def fetch_60(sh_code):
    """60分钟（小时线）——腾讯/新浪分钟K线，多级回退"""
    try:
        sys.path.insert(0, os.path.join(WS, "bin"))
        import minute_hub as mh
        df = mh.get_kline(sh_code, "60", n=400, kind="index", cache=True)
        if df is not None and len(df) > 40:
            return df.rename(columns={"day": "dt"})[["dt", "open", "high", "low", "close"]]
    except Exception as e:
        print("[WARN] 60min %s: %s" % (sh_code, str(e)[:80]), flush=True)
    return None


# ─────────── 飞书私聊推送（0 token）───────────
def feishu_push(text, title="📐 宽基缠论+SMC · 机会/风险"):
    import urllib.request
    aid, sec = os.environ.get("FEISHU_APP_ID"), os.environ.get("FEISHU_APP_SECRET")
    if not (aid and sec):
        c = json.load(open(os.path.join(os.path.dirname(WS), "openclaw.json"), encoding="utf-8"))["channels"]["feishu"]
        aid, sec = c["appId"], c["appSecret"]
    b = json.dumps({"app_id": aid, "app_secret": sec}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=b, headers={"Content-Type": "application/json"})
    tok = json.loads(urllib.request.urlopen(req, timeout=20).read())["tenant_access_token"]
    card = {"config": {"wide_screen_mode": True},
            "header": {"template": "blue",
                       "title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": "📎 来自 OpenClaw.Deepseek"},
                         {"tag": "markdown", "content": text}]}
    body = json.dumps({"receive_id": FEISHU_CHAT, "msg_type": "interactive",
                       "content": json.dumps(card)}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=body, headers={"Authorization": "Bearer " + tok,
                            "Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    print("feishu push:", r.get("code"), r.get("msg"))
    return r.get("code") == 0


# ─────────── ASCII 示意图 / 折线 ───────────
def spark(series):
    chars = " ▁▂▃▄▅▆▇█"
    s = [float(x) for x in series if x == x]
    if len(s) < 2:
        return ""
    lo, hi = min(s), max(s)
    if hi <= lo:
        return "▄" * len(s)
    return "".join(chars[int(round((v - lo) / (hi - lo) * (len(chars) - 1)))] for v in s)


def ladder(levels, cur, rows=13, width=52):
    """竖轴示意图：levels=[(price, text, tag)]；cur=现价。
    tag: "buy"→🌙（真买点）/ "sell"→⭐（真卖点）/ 其他→┄（纯参考线，不打图标）
    2026-10-22 用户要求：上轨下轨不再打月亮星星，只有真买卖点才打。"""
    if not levels:
        return ""
    ys = [p for p, _, _ in levels] + [cur]
    hi, lo = max(ys), min(ys)
    span = (hi - lo) or 1.0
    step = span / (rows - 1)

    def row_of(p):
        return int(round((hi - p) / span * (rows - 1)))

    # 每个 level 对应行（取整后去重）：同一行冲突时 买卖点(🌙/⭐) 优先于参考线
    rowmap = {}
    pri = lambda t: 0 if t in ("buy", "sell") else 1
    for p, text, tag in sorted(levels, key=lambda x: (-x[0], pri(x[2]))):
        r = row_of(p)
        cur_lvl = rowmap.get(r)
        if cur_lvl is None or (pri(tag) < pri(cur_lvl[2])):
            rowmap[r] = (p, text, tag)
    cur_row = row_of(cur)

    out = []
    for r in range(rows):
        price = hi - r * step
        lvl = rowmap.get(r)
        axis = "%8.2f ┤" % price
        mark = ""
        if lvl:
            p, text, tag = lvl
            icon = "🌙" if tag == "buy" else ("⭐" if tag == "sell" else "┄")
            mark += "%s %s" % (icon, text)
        if r == cur_row:
            bar = "● 现价 %.2f" % cur
            mark = (bar + ("  " + mark if mark else ""))
        out.append(axis + " " + mark)
    return "\n".join(out)

def _fmt_pct(x):
    return ("+%.1f%%" % x) if x >= 0 else ("%.1f%%" % x)


def collect(include_h1=True):
    """抓一次数据，返回每个指数的完整判定 dict（供榜单 + 明细复用）

    include_h1=False → **不抓 60 分钟数据**（省时间），ch 给 {"ok": False}、n_h1=0
    （2026-10-26 大佬分工：20:15 大盘技术拆解不要小时级别，小时线归 19:40 宽基监控）
    """
    rows = []
    for name, code in IDX:
        dd = fetch_daily(code)
        if dd is None:
            rows.append({"name": name, "ok": False})
            continue
        ww = resample_w(dd)
        P = float(dd["close"].iloc[-1])
        h6 = fetch_60(IDX_SH.get(name, "")) if include_h1 else None
        cd = chan_read(bars_of(dd), "day")
        ch = chan_read(bars_of(h6), "h1") if (h6 is not None and len(h6) > 40) else {"ok": False}
        sd = smc_read(dd, 3, "day"); sw = smc_read(ww, 2, "week")
        # 一买预备（可能有一买机会，且 MA40 向上）—— 2026-10-26 大佬要求
        try:
            import y1_watch as _Y1
            prep_day = _Y1.y1_prepare(cd, dd["close"].values, P)
            prep_week = _Y1.y1_prepare(chan_read(bars_of(ww), "week"),
                                       ww["close"].values, P) if cd.get("ok") else None
        except Exception as e:
            print("[WARN] y1_prepare %s: %s" % (name, str(e)[:80]), flush=True)
            prep_day = prep_week = None
        rows.append({"name": name, "ok": True, "P": P, "closes": dd["close"].values,
                     "cd": cd, "ch": ch, "sd": sd, "sw": sw,
                     "prep_day": prep_day, "prep_week": prep_week,
                     "n_day": len(dd),
                     "n_h1": len(h6) if h6 is not None else 0,
                     "last_h1": str(h6["dt"].iloc[-1]) if (h6 is not None and len(h6)) else "-",
                     "last_day": str(dd["dt"].iloc[-1].date()) if "dt" in dd.columns else "-"})
    return rows


def _signals(r, include_h1=True):
    """把一个指数拆成 (机会列表, 风险列表)。
    机会 = 缠论真买点（一买/二买/三买，且新鲜）｜风险 = 缠论真卖点 + SMC 结构性破位
    2026-10-22 用户口径：只有「到了几买/几卖」才给 🌙/⭐，上轨下轨这类参考线不配图标。
    include_h1=False → 榜单只看日线（大盘报告不含小时级别）"""
    opp, risk = [], []
    P = r["P"]
    levels = (("日线", r.get("cd")),) if not include_h1 else (
        ("小时线60m", r.get("ch")), ("日线", r.get("cd")))
    for lv_name, ck in levels:
        if not ck or not ck.get("ok"):
            continue
        for b in ck.get("buy_list", []):
            # ⚠️ 2026-11-08 大佬定稿：小时线**只认三买**，一买/二买只有日线才算数
            if lv_name == "小时线60m" and b["tag"] not in H1_ALLOWED_BUY:
                continue
            # ⚠️ 2026-11-08：不能只认新鲜信号。长期没破的买点（sustained）同样是有效信号，
            #   否则会漏掉「多头排列持续很久」这类行情（均线版实测漏 100%）。
            if not (b["fresh"] or b.get("sustained")):
                continue
            n = BUY_STARS.get(b["tag"], 1)
            if n <= 0:
                continue
            d = (P - b["level"]) / b["level"] * 100
            # 一买必须现价不能离低点太远（跌破 = 买点失效）
            if b["tag"] == "一买" and d < -1.5:
                continue
            # ⚠️ 长期延续信号：现价必须真站在买点之上，跌破的不算机会（只是历史记录）
            if not b["fresh"] and d < 0:
                continue
            # 机会只看「现价附近的」：买点最多在上面 3% 内（再远就不算已经到了）
            if d < -1.5 or d > 3:
                continue
            opp.append({"name": r["name"], "level_tag": lv_name, "tag": b["tag"],
                        "stars": n, "level": b["level"], "age": b["age"],
                        "dist": d, "active": abs(d) <= 1.0,   # 用收盘价重算，别用 chan_read 的高点
                        "score": n * 100 - abs(d) - b["age"] * 0.5})
    for lv_name, ck in levels:
        if not ck or not ck.get("ok"):
            continue
        for s in ck.get("sell_list", []):
            # 小时线只认三卖（对称口径）
            if lv_name == "小时线60m" and s["tag"] not in H1_ALLOWED_SELL:
                continue
            if not (s["fresh"] or s.get("sustained")):
                continue
            n = SELL_STARS.get(s["tag"], 1)
            d = (s["level"] - P) / P * 100          # 现价距卖点还有多少
            # 长期延续卖点：现价必须在卖点之下，涨破的不算风险
            if not s["fresh"] and d < 0:
                continue
            # 风险只看「现价附近的」：卖点在下方 1% 内（已跌破）或上方 3% 内（就在头顶）
            if not (-6 <= d <= 3):
                continue
            risk.append({"name": r["name"], "level_tag": lv_name, "tag": s["tag"],
                         "stars": n, "level": s["level"], "age": s["age"],
                         "dist": d, "active": abs(d) <= 1.0,
                         "score": n * 100 - abs(d) - s["age"] * 0.5})
        # SMC 结构性破位（只作风险提示，单独一段，不混进买卖点榜）
    return opp, risk


def _top_block(rows, include_h1=True):
    """报告开头：机会在前、风险在后（用户 2026-10-22 要求）

    include_h1=False → 榜单只含日线，口径说明改成「级别：日线」
    """
    L = []
    opps, risks, pres, notes = [], [], [], []
    for r in rows:
        if not r.get("ok"):
            notes.append("【%s】数据失败" % r["name"]); continue
        o, k = _signals(r, include_h1=include_h1)
        opps += o; risks += k
        # 一买预备（可能有一买机会，且 MA40 向上）—— 2026-10-26 大佬要求
        for lv_name, pre in (("日线", r.get("prep_day")), ("周线", r.get("prep_week"))):
            if pre and pre.get("ok"):
                pres.append({"name": r["name"], "level_tag": lv_name, **pre})

    # ── 口径声明（2026-10-23 用户要求：必须写明「级别」和「版本」）──
    L.append("📋 口径说明（先看这里）")
    if include_h1:
        L.append("  级别：小时线60m（60分钟K线） + 日线。本节买卖点两者都会标，行内【小时线60m】/【日线】注明")
    else:
        L.append("  级别：日线。本节买卖点只标日线，行内【日线】注明")
    L.append("  版本：**缠论·分型版**（全篇只用这一个版本）= 包含处理→顶底分型→笔→中枢ZG/ZD→背驰")
    L.append("  说明：本报告不含「均线版/趋势浪子版」，所有买卖点、中枢、背驰均为分型版口径")
    L.append("")
    L.append("🎯 一、有机会的指数【分型版·缠论结构】（只有真到缠论买点才打🌙，月亮越多越强）")
    if opps:
        opps.sort(key=lambda x: -x["score"])
        for i, o in enumerate(opps[:8], 1):
            if o["active"]:
                st = "📍现价就在买点上"
            elif o["dist"] > 0:
                st = "现价已站上买点 %.1f%%" % o["dist"]
            else:
                st = "现价还在买点下方 %.1f%%（已破，需收回）" % abs(o["dist"])
            L.append("%d. %s %s ｜%s %s %.2f ｜%s ｜%d根K线前" % (
                i, "🌙" * o["stars"], o["name"], o["level_tag"], o["tag"],
                o["level"], st, o["age"]))
    else:
        L.append("今日无新鲜买点（5指数 × %s，均未到一买/二买/三买）" % ("小时/日线" if include_h1 else "日线"))

    # ── 一买预备：可能有一买机会出现（且 MA40 向上）──
    # 2026-10-26 大佬：「如果要出现一买的机会，并且 MA40 是向上，你也要提示
    # 可能有一买机会出现」→ 单独一段，紧跟机会榜（它不是真买点，不能混进去冒充）
    if pres:
        L.append("")
        L.append("🌙 一买预备（**可能**有一买机会 · MA40 已向上）：")
        for i, p in enumerate(sorted(pres, key=lambda x: abs(x.get("dist") or 99))[:6], 1):
            d = p.get("dist") or 0
            if p.get("mode") == "B":
                st = "刚破前低 %.2f（已破 %.1f%%），等底背驰确认 → 确认即一买" % (
                    p.get("prev_low") or 0, abs(d))
            else:
                st = "回踩前低 %.2f 附近（还差 %.1f%%），下跌力度已衰减 → 再创新低即一买" % (
                    p.get("prev_low") or 0, d)
            L.append("%d. 🌙 %s ｜%s ｜MA40 %.2f↑（现价偏离 %+.1f%%）｜%s" % (
                i, p["name"], p["level_tag"], p.get("ma40") or 0,
                p.get("ma_dev") or 0, st))
    else:
        L.append("")
        L.append("🌙 一买预备：无（当前没有「MA40 向上 + 已回踩到前低附近」的指数）")

    L.append("")
    L.append("⚠️ 二、有风险的指数【分型版·缠论结构】（只有真到缠论卖点才打⭐，星星越多越重）")
    if risks:
        risks.sort(key=lambda x: -x["score"])
        for i, k in enumerate(risks[:8], 1):
            if k["active"]:
                st = "📍现价就在卖点上"
            elif k["dist"] >= 0:
                st = "卖点就在上方 %.1f%%" % k["dist"]
            else:
                st = "已跌破卖点 %.1f%%" % abs(k["dist"])
            L.append("%d. %s %s ｜%s %s %.2f ｜%s ｜%d根K线前" % (
                i, "⭐" * k["stars"], k["name"], k["level_tag"], k["tag"],
                k["level"], st, k["age"]))
    else:
        L.append("现价附近无新鲜卖点")
    L.append("（本榜单口径：买点/卖点取现价 ±3%% 内、%s日线 25 根 / 周线 12 根以内、版本=缠论·分型版；"
             "中枢上沿下沿只是参考线，不打图标。全篇仅分型版口径）"
             % ("小时线 30 根 / " if include_h1 else ""))
    if notes:
        L += [""] + notes
    return "\n".join(L)


def build_text(include_h1=True):
    """include_h1=False → 全文不含小时级别（20:15 大盘技术拆解用）"""
    raw = collect(include_h1=include_h1)

    # ── 报告开头：机会 → 风险（用户 2026-10-22 要求放最前面）──
    front = _top_block(raw, include_h1=include_h1)

    out = []
    for r in raw:
        name = r["name"]
        if not r.get("ok"):
            out.append("══════ 【%s】数据失败 ══════\n" % name); continue
        P = r["P"]; dd_closes = r["closes"]
        cd, ch, sd, sw = r["cd"], r["ch"], r["sd"], r["sw"]

        out.append("══════ 【%s】收 %.2f ══════" % (name, P))
        # ── ASCII 折线（近40日收盘）──
        # ⚠️ 2026-11-06 大佬：「今天是跌的，你怎么还是向上的？」
        # 40日折线分辨率粗（40日区间 1151~1665），今日 -2.6% 会落进同一档看着像走平/向上。
        # 折线后面直接补「今日 ±x%」，避免只看形状误判方向。
        _pct1 = ""
        try:
            _cl = [float(x) for x in dd_closes if x == x]
            if len(_cl) >= 2 and _cl[-2]:
                _pct1 = "   今日 %+.2f%%" % ((_cl[-1] / _cl[-2] - 1) * 100)
        except Exception:
            pass
        out.append("  日线近40日: %s%s" % (spark(dd_closes[-40:]), _pct1))
        # ── 近10日放大折线（自己一段纵轴，今日涨跌看得见）──
        try:
            _c10 = [float(x) for x in dd_closes[-10:] if x == x]
            if len(_c10) >= 3:
                out.append("  日线近10日: %s   （放大版，纵轴只取这10日高低）" % spark(_c10))
        except Exception:
            pass
        # ── 数据区间（级别标注用）──
        if include_h1:
            out.append("  数据：日线至 %s（%d根）｜小时线至 %s（%d根）" % (
                r.get("last_day", "-"), r.get("n_day", 0),
                str(r.get("last_h1", "-"))[:16], r.get("n_h1", 0)))
        else:
            out.append("  数据：日线至 %s（%d根）" % (r.get("last_day", "-"), r.get("n_day", 0)))
        # ── ASCII 阶梯示意图（小时线关键位；只有真买卖点打图标）──
        lvls = []
        base = (ch if ch.get("ok") else cd) if include_h1 else cd
        if base.get("ok"):
            zs = base.get("zs"); y1 = base.get("y1_low")
            if zs:
                lvls.append((zs[1], "中枢上沿 ZG %.2f（站上才是三买，现在%s）" % (
                    zs[1], "在上方" if P > zs[1] else "还差 %.1f%%" % ((zs[1] - P) / P * 100)),
                    None))
                lvls.append((zs[0], "中枢下沿 ZD %.2f（跌破→结构走坏）" % zs[0], None))
            if y1:
                lvls.append((y1, "一买低点基准 %.2f" % y1, None))
        # 台阶图：新鲜 + 长期未破都上图标（长期加「延续」字样，仍限制 2 个免得满屏星星）
        def _sig_txt(x, tag_name):
            suf = "" if x["fresh"] else "·延续"
            return "%s%s %.2f（%d根前）" % (x["tag"], suf, x["level"], x["age"])
        _bl = [x for x in (base.get("buy_list") or []) if x["fresh"] or x.get("sustained")][:2]
        _sl = [x for x in (base.get("sell_list") or []) if x["fresh"] or x.get("sustained")][:2]
        for b in _bl:
            lvls.append((b["level"], _sig_txt(b, "buy"), "buy"))
        for s in _sl:
            lvls.append((s["level"], _sig_txt(s, "sell"), "sell"))
        if lvls:
            out.append("  %s示意图（🌙真买点 ⭐真卖点 ┄参考线 ●现价）:" % ("小时线" if include_h1 else "日线"))
            out.append(ladder(lvls, P, rows=11))

        def chan_line(lv, ck):
            """分型版（缠论结构）—— 明确写版本 + 级别"""
            if not ck.get("ok"): return "  【分型版·%s】数据不足" % lv
            stg = ck["stage"]
            if "买" in stg or "上升" in stg or "突破" in stg:
                stg = "🌙 " + stg
            elif "卖" in stg or "下跌" in stg or "趋势破坏" in stg:
                stg = "⭐ " + stg
            bits = ["阶段=%s" % stg]
            # 新鲜 + 长期未破（sustained）都要展示：长期信号代表「很久以前穿越/形成，现在仍有效」
            bl = [b for b in (ck.get("buy_list") or []) if b["fresh"] or b.get("sustained")]
            sl = [s for s in (ck.get("sell_list") or []) if s["fresh"] or s.get("sustained")]
            if ck.get("zs"):
                bits.append("中枢=%.2f~%.2f" % ck["zs"])
            if bl:
                bits.append("买点 " + "、".join("🌙%s%.2f(%d根前)" % (
                    b["tag"] + ("" if b["fresh"] else "·延续"), b["level"], b["age"]) for b in bl))
            if sl:
                bits.append("卖点 " + "、".join("⭐%s%.2f(%d根前)" % (
                    s["tag"] + ("" if s["fresh"] else "·延续"), s["level"], s["age"]) for s in sl))
            if ck.get("div"):
                bits.append("背驰=%s" % ck["div"])
            else:
                bits.append("背驰=无(再跌%.1f%%破前低才有)" % (ck.get("div_dist") or 0))
            return "  【分型版·%s】%s" % (lv, " | ".join(bits))

        def smc_line(lv, sk):
            if not sk.get("ok"): return "  SMC%s：数据不足" % lv
            pos = sk["pos"]
            if pos > 100:
                posi = "🌙 已上破区间上沿 +%.1f%%" % (-sk["to_bos_up"])
            elif pos < 0:
                posi = "⭐ 已跌破区间下沿 -%.1f%%" % abs(sk["to_bos_dn"])
            elif pos >= 67:
                posi = "⭐ 区间偏上 %.0f%%（%s）" % (pos, sk["zone"])
            elif pos <= 33:
                posi = "🌙 区间偏下 %.0f%%（%s）" % (pos, sk["zone"])
            else:
                posi = "区间内 %.0f%%（%s）" % (pos, sk["zone"])
            # 2026-10-26 大佬要求：**机会标月亮🌙、风险标星⭐**
            opp = "、".join(sk["opp"]) or "无"
            rk = "、".join(sk["rk"]) or "无"
            if sk["opp"]:
                opp = "🌙 " + opp
            if sk["rk"]:
                rk = "⭐ " + rk
            # 破位的一句话也给标签：偏空→⭐，偏多→🌙
            bos = sk["bos"]
            if "转空" in bos or "下破" in bos:
                bos = "⭐ " + bos
            elif "转多" in bos or "上破" in bos:
                bos = "🌙 " + bos
            st_text = sk["struct"]
            if any(k in st_text for k in ("空头", "下破", "扩张")):
                st_text = "⭐ " + st_text
            elif any(k in st_text for k in ("多头", "上破", "收敛")):
                st_text = "🌙 " + st_text
            return "  SMC%s：%s\n       破位：%s ｜ 位置：%s\n       机会：%s\n       风险：%s" % (
                lv, st_text, bos, posi, opp, rk)

        out.append("  ── 缠论·分型版（包含处理→顶底分型→笔→中枢→背驰）· %s ──"
                   % ("小时线+日线" if include_h1 else "日线"))
        if include_h1:
            out.append(chan_line("小时线60m", ch if ch.get("ok") else {"ok": False}))
        out.append(chan_line("日线", cd))
        # 一买预备（可能有一买机会 · MA40 向上）—— 2026-10-26 大佬要求
        try:
            import y1_watch as _Y1
            for lv_name, pre in (("日线", r.get("prep_day")),):
                if pre and pre.get("ok"):
                    out.append("  🌙 一买预备（%s）：%s" % (lv_name, _Y1.tag(pre)))
            if not (r.get("prep_day") or {}).get("ok"):
                ms = _Y1.ma_state(r.get("closes"))
                if ms.get("ok"):
                    out.append("     一买预备：无（MA40 %s｜%s）" % (
                        "↑" if ms["ma_up"] else "↓",
                        "当前上升笔中" if (cd.get("last_up")) else "未回踩到前低附近/力度未衰减"))
        except Exception as _e:
            print("[WARN] y1 明细 %s: %s" % (name, str(_e)[:80]), flush=True)
        out.append("  ── SMC（日线+周线）──")
        out.append(smc_line("日", sd))
        out.append(smc_line("周", sw))
        out.append("")
    return front + "\n\n" + "─" * 30 + "\n\n" + "\n".join(out)




if __name__ == "__main__":
    # ⚠️ 2026-11-08 改：**默认不推送**，必须显式加 --push 才发
    #   原因：手工调试时（不带任何参数）会误推到飞书，大佬收到不需要的报告。
    #   原逻辑是「默认推、加 --no-push 才不推」，容易踩坑。
    txt = build_text()
    print(txt)
    p = os.path.join(WS, ".cache/chan_smc_%s.txt" % datetime.now().strftime("%Y%m%d_%H%M"))
    open(p, "w", encoding="utf-8").write(txt)
    print("[saved]", p)
    if "--push" in sys.argv:
        try:
            feishu_push(txt)
            print("[pushed]")
        except Exception as e:
            print("[ERR] push:", str(e)[:150])
    else:
        print("[no-push] 默认不推送（需显式 --push）")
