#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论·均线版（趋势浪子《交易悟道之路》原文口径）

⚠️ 2026-11-08 17:00 按大佬给的 PDF 原文重写。之前口述的参数是错的，原文为准。

━━━ 原文核心（PDF 第 286-297 行、439-490 行、673-690 行）━━━

1) 笔的定义（不是简单的均线金叉，是**双穿**）：
   「当 5 日均线 10 日均线全部穿过 20 日均线后，又再次全部穿过…」
   「注意是全部，5 穿过了 20 但 10 没穿过不成立；10 穿过了 20 但 5 没穿过也不行」
   → 笔 = MA5 与 MA10 **同时** 在 MA20 同一侧（双穿成立），方向翻转即笔翻转
   → 不是「MA10 上穿 MA20」单线金叉！我上一版做错了。

2) 双倍均线组三级别（笔 → 线段 → 走势类型）：
   笔     = MA5/10/20        （最小级别）
   线段   = MA20/40/80       （放大 4 倍，是笔的上一级）
   走势类型 = MA80/160/320   （放大 16 倍，再上一级）
   规则：「一个级别的笔，必然是其次级别的段」

3) MACD 参数 = 10/20/5
   原文：「MACD参数修改成 10/20/5，DIF 是 EMA10，其零轴也就是拉平的 EMA20」
   （理由：与最小级别 5/10/20 对应）

4) 级别联立性（同一套均线在别的周期上等价）：
   日线 MA4/8 = 120分钟 MA8/16 = 60分钟 MA16/32 = 30分钟 MA32/64
   → 所以「日线版就看日线」，不需要降级到 4 小时（这点大佬说得对）

5) 买卖点（原文 120 分钟例子）：
   一类买点 = 笔由下转上的转折点
   延续点(二三买) = 上涨笔中，快线回踩不接触慢线的任何一次转折
   卖点 = 出现背驰（3-5 背驰）或 笔翻向下

用法：
  python3 bin/chan_ma_trend_v2.py                     # 5宽基 日线
  python3 bin/chan_ma_trend_v2.py --level week        # 周线
  python3 bin/chan_ma_trend_v2.py --level day --code 000300.SH
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
from datetime import datetime

_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))

# ── 参数（2026-11-08 大佬定稿：大级别只看 MA160，不要 MA320）──
A0 = (5, 10, 20)          # 笔：MA5/MA10 双穿 MA20
A1 = (20, 40, 80)         # 线段：MA20/MA40 双穿 MA80
# ══════════════════════════════════════════════════════════════════
# 📌 大级别支撑规则（大佬 2026-11-08 定稿，固定口径，勿改）
#
#   「如果三百二十均线数据不全，那你就不需要三百均线，
#     你只需要穿越一百六十均线就行了」
#   「你想判断能不能突破 320 均线，是不是？其实那你就不需要了，
#     就只要突破 160 均线就行了。我大级别就能支撑了」
#
#   → 大级别支撑 = **价格 vs MA160**，只用这一条。
#   → MA320 永久移除：数据不够（需 320 根）反而造成标的被丢弃，
#     而「站上 160 均线」已经足够支撑交易判断。
# ══════════════════════════════════════════════════════════════════
BIG_MA = 160              # 大级别支撑线（唯一，不用 MA320）
MAS = [5, 10, 20, 40, 80, 160]   # 去掉 320
MACD_F, MACD_S, MACD_SIG = 10, 20, 5      # 原文 673-690 行

IDX = [
    ("沪深300", "000300.SH"),
    ("中证500", "000905.SH"),
    ("中证1000", "000852.SH"),
    ("创业板指", "399006.SZ"),
    ("科创50", "000688.SH"),
]
LEVEL_TXT = {"day": "日线", "week": "周线", "60m": "60分钟"}


# ───────────────────── 指标 ─────────────────────
def add_ind(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for n in MAS:
        d["ma%d" % n] = d["close"].rolling(n).mean()
    ef = d["close"].ewm(span=MACD_F, adjust=False).mean()
    es = d["close"].ewm(span=MACD_S, adjust=False).mean()
    d["dif"] = ef - es
    d["dea"] = d["dif"].ewm(span=MACD_SIG, adjust=False).mean()
    d["hist"] = (d["dif"] - d["dea"]) * 2
    return d


def resample_week(df: pd.DataFrame) -> pd.DataFrame:
    d = df.set_index("dt")
    o = pd.DataFrame({
        "open": d["open"].resample("W-FRI").first(),
        "high": d["high"].resample("W-FRI").max(),
        "low": d["low"].resample("W-FRI").min(),
        "close": d["close"].resample("W-FRI").last(),
    }).dropna().reset_index()
    return o.rename(columns={o.columns[0]: "dt"})


# ───────────── 双穿判定（原文核心） ─────────────
def dual_cross_state(d: pd.DataFrame, fast1: int, fast2: int, slow: int) -> pd.Series:
    """双穿状态：两条快线**同时**在慢线上方 = up，同时下方 = dn，其余 = mid

    原文：「要注意是全部，5 穿过了 20 但 10 没穿过不成立」
    """
    m1, m2, ms = d["ma%d" % fast1], d["ma%d" % fast2], d["ma%d" % slow]
    up = (m1 > ms) & (m2 > ms)
    dn = (m1 < ms) & (m2 < ms)
    st = pd.Series("mid", index=d.index)
    st[up] = "up"
    st[dn] = "dn"
    return st


def build_bi(d: pd.DataFrame, fast1=5, fast2=10, slow=20) -> list:
    """按原文「双穿」造笔：状态翻转即为笔的转折点。

    原文：「上涨笔：是前一个条件形成的前的低位跟后一个条件形成前的高位相连」
    这里简化为：转折点的收盘价 = 笔端点（与原文的「峰值相连」一致到够用）。
    """
    st = dual_cross_state(d, fast1, fast2, slow)
    bi = []
    cur = None
    for i in range(len(d)):
        s = st.iloc[i]
        if s == "mid":
            continue
        if cur is None or s != cur:
            bi.append({"dir": "up" if s == "up" else "dn",
                       "i": i, "dt": str(d["dt"].iloc[i])[:10],
                       "px": float(d["close"].iloc[i])})
            cur = s
    return bi


def dual_cross_age(d: pd.DataFrame, fast1: int, fast2: int, slow: int) -> int:
    """当前双穿状态已经持续多少根（= 穿越发生在多少根之前）。"""
    st = dual_cross_state(d, fast1, fast2, slow)
    cur = st.iloc[-1]
    if cur == "mid":
        return 0
    n = 0
    for k in range(len(st) - 1, -1, -1):
        if st.iloc[k] == cur:
            n += 1
        else:
            break
    return n


def ma_alignment(d: pd.DataFrame, cols=None) -> dict:
    """均线排列 —— 大佬 2026-11-08 关键补充：

    「它不一定是穿越，也可能是因为穿越还在很长时间了，也可能 MA40 和 MA20 和 MA80
      就多头排列，就这种。就多头排列，它也表示在很远很远的那个节点上，它就穿越了」

    → **多头排列本身就是「很久以前穿越过且一直没破」的结果**，
      所以不能只抓「最近 N 根的新鲜穿越」，长期排列同样是有效信号。

    判定（严格全序列）：
      多头排列：MA5 > MA10 > MA20 > MA40 > MA80 > MA160（依次递减）
      空头排列：完全反向
      混合排列：其余
    """
    cols = cols or [5, 10, 20, 40, 80, 160]      # 去掉 320（大佬定稿）
    vals = []
    for n in cols:
        v = d["ma%d" % n].iloc[-1] if "ma%d" % n in d.columns else None
        if v is None or pd.isna(v):
            return {"state": "数据不足", "desc": "均线数据不足"}
        vals.append(float(v))

    down = all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
    up = all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))
    seq = " > ".join("MA%d" % c for c in cols)

    if down:
        # 排了多久：从头往前数，多少根内一直保持
        n = 0
        for k in range(len(d) - 1, -1, -1):
            row = d.iloc[k]
            vs = [row["ma%d" % c] for c in cols]
            if any(pd.isna(x) for x in vs):
                break
            if all(vs[i] > vs[i + 1] for i in range(len(vs) - 1)):
                n += 1
            else:
                break
        return {"state": "多头排列", "bars": n, "vals": vals,
                "desc": "MA5>MA10>MA20>MA40>MA80>MA160", "seq": seq}
    if up:
        n = 0
        for k in range(len(d) - 1, -1, -1):
            row = d.iloc[k]
            vs = [row["ma%d" % c] for c in cols]
            if any(pd.isna(x) for x in vs):
                break
            if all(vs[i] < vs[i + 1] for i in range(len(vs) - 1)):
                n += 1
            else:
                break
        return {"state": "空头排列", "bars": n, "vals": vals,
                "desc": "MA5<MA10<MA20<MA40<MA80<MA160", "seq": seq}
    return {"state": "混合排列", "vals": vals,
            "desc": "均线未形成完整单向排列（纠缠）", "seq": seq}


def short_alignment(d: pd.DataFrame, cols=(5, 10, 20)) -> dict:
    """短均线排列（笔级别用）：只看给定的几条，判多头/空头/混合。"""
    return ma_alignment(d, list(cols))


def ladder(d: pd.DataFrame) -> dict:
    """两级 + 大级别支撑：笔 / 线段 / 大级别(MA160)

    ⚠️ 2026-11-08 大佬定稿：大级别只看「价格 vs MA160」，**不要 MA320**。
       「你想判断能不能突破 320 均线，是不是？其实那你就不需要了，
         就只要突破 160 均线就行了。我大级别就能支撑了」

    每一项报两件事：
      · 排列（align）：多头/空头排列 —— 反映「很久以前的穿越还在延续」
      · 双穿（state + bars）：双穿状态 + 已持续多少根
    """
    out = {}
    for name, (f1, f2, sl) in (("A0", A0), ("A1", A1)):
        if "ma%d" % sl not in d.columns or pd.isna(d["ma%d" % sl].iloc[-1]):
            out[name] = {"state": "数据不足"}
            continue
        st = dual_cross_state(d, f1, f2, sl)
        cur = st.iloc[-1]
        n = dual_cross_age(d, f1, f2, sl)
        # 本级三条均线的排列（f1, f2, sl 谁大谁小）
        # 本级三条均线的排列：快1 > 快2 > 慢 = 多头；全反 = 空头；其余 = 三条纠缠
        trio = [float(d["ma%d" % x].iloc[-1]) for x in (f1, f2, sl)]
        if trio[0] > trio[1] > trio[2]:
            align = "多头排列"
        elif trio[0] < trio[1] < trio[2]:
            align = "空头排列"
        else:
            align = "三条纠缠"
        out[name] = {"state": cur, "bars": n, "align": align,
                     "desc": f"MA{f1}/MA{f2}穿MA{sl}",
                     "dir": {"up": "向上", "dn": "向下", "mid": "纠缠"}.get(cur, cur)}

    # ── 大级别支撑：价格 vs MA160（只看这一条，不需要 MA320）──
    key = "ma%d" % BIG_MA
    if key not in d.columns or pd.isna(d[key].iloc[-1]):
        out["BIG"] = {"state": "数据不足", "align": "—", "dir": "—", "bars": 0,
                      "desc": f"MA{BIG_MA}"}
    else:
        px = float(d["close"].iloc[-1])
        mv = float(d[key].iloc[-1])
        # 在 MA160 上方多久了
        n = 0
        for k in range(len(d) - 1, -1, -1):
            v = d[key].iloc[k]
            p = float(d["close"].iloc[k])
            if pd.isna(v):
                break
            if (p > v) == (px > mv):
                n += 1
            else:
                break
        if px > mv:
            out["BIG"] = {"state": "up", "align": "站上大级别", "dir": "向上", "bars": n,
                          "desc": f"价格 > MA{BIG_MA}（{mv:.2f}）"}
        elif px < mv:
            out["BIG"] = {"state": "dn", "align": "跌破大级别", "dir": "向下", "bars": n,
                          "desc": f"价格 < MA{BIG_MA}（{mv:.2f}）"}
        else:
            out["BIG"] = {"state": "mid", "align": "大级别附近", "dir": "纠缠", "bars": 0,
                          "desc": f"价格 = MA{BIG_MA}"}
    return out


# ───────────── 趋势 / 盘整（女上位·男上位）─────────────
def wei_state(d: pd.DataFrame, fast: int, slow: int, look: int = 20) -> str:
    """女上位/男上位 —— 原文 751/776 行：
    「相邻两组均线**不接触**形成女上位（向上趋势），男上位是向下趋势」
    判定：最近 look 根内，MA(fast) 从未下穿 MA(slow) → 女上位；
          从未上穿 → 男上位；否则 = 接触（盘整）
    """
    if len(d) < max(fast, slow) + look + 1:
        return "数据不足"
    mf = d["ma%d" % fast].iloc[-look - 1:].values
    ms = d["ma%d" % slow].iloc[-look - 1:].values
    diff = mf - ms
    if any(pd.isna(x) for x in diff):
        return "数据不足"
    if (diff > 0).all():
        return "女上位（向上趋势）"
    if (diff < 0).all():
        return "男上位（向下趋势）"
    return "接触中（盘整）"


def trend_mode(d: pd.DataFrame) -> dict:
    """同级别走势只有三种：趋势上 / 趋势下 / 盘整（原文 838 行）"""
    a0 = wei_state(d, A0[0], A0[1], look=20)      # MA5 vs MA10 不接触
    a0b = wei_state(d, A0[1], A0[2], look=20)     # MA10 vs MA20 不接触
    if "女" in a0 and "女" in a0b:
        return {"mode": "趋势上涨", "detail": "MA5/10/20 依次女上位，无接触"}
    if "男" in a0 and "男" in a0b:
        return {"mode": "趋势下跌", "detail": "MA5/10/20 依次男上位，无接触"}
    return {"mode": "盘整", "detail": "均线相互接触，走势重叠（中枢震荡）"}


# ───────────── 背驰（原文 700-730 行）─────────────
def macd_bei_chi(d: pd.DataFrame, bi: list, look_bi: int = 6) -> dict | None:
    """背驰判定 —— 原文要求「同级别」+ MACD 三个流程：
    1) 柱子缩短  2) 黄白线交叉  3) 黄白线回零轴附近或穿越零轴
    方法：比较最近两段**同向笔**的力度（笔内 MACD 柱面积）与价格新高/新低。
    """
    if len(bi) < 4:
        return None
    dif = d["dif"].values
    hist = d["hist"].values
    # 取最近两段同向笔（都是 up 或都是 dn）
    ups = [b for b in bi if b["dir"] == "up"][-2:]
    dns = [b for b in bi if b["dir"] == "dn"][-2:]

    idx_of = {id(b): k for k, b in enumerate(bi)}

    def seg_power(b0):
        """一段笔的 MACD 力度 = 该笔区间内 hist 绝对值之和。

        区间 = 本笔起点 → 下一笔起点（下一笔不存在则取到末根）。
        ⚠️ 不能直接用「笔起点的价格」求力度，笔的终点是下一笔的起点。
        """
        k = idx_of.get(id(b0))
        a = int(b0["i"])
        b = int(bi[k + 1]["i"]) if (k is not None and k + 1 < len(bi)) else len(hist)
        if b <= a:
            return 0.0
        return float(np.abs(hist[a:b]).sum())

    # 顶背驰：两段上涨笔，后段价更高但力度更小
    if len(ups) == 2:
        p1, p2 = ups[0], ups[1]
        if p2["px"] > p1["px"]:
            w1, w2 = seg_power(p1), seg_power(p2)
            if w2 < w1 * 0.9:
                near_zero = abs(float(dif[-1])) < abs(float(np.nanstd(dif[-60:]))) * 1.5
                return {"type": "顶背驰", "price_new_high": round(p2["px"], 2),
                        "power_prev": round(w1, 1), "power_now": round(w2, 1),
                        "macd_near_zero": bool(near_zero),
                        "detail": "后段新高但 MACD 力度衰减"}
    # 底背驰：两段下跌笔，后段价更低但力度更小
    if len(dns) == 2:
        p1, p2 = dns[0], dns[1]
        if p2["px"] < p1["px"]:
            w1, w2 = seg_power(p1), seg_power(p2)
            if w2 < w1 * 0.9:
                near_zero = abs(float(dif[-1])) < abs(float(np.nanstd(dif[-60:]))) * 1.5
                return {"type": "底背驰", "price_new_low": round(p2["px"], 2),
                        "power_prev": round(w1, 1), "power_now": round(w2, 1),
                        "macd_near_zero": bool(near_zero),
                        "detail": "后段新低但 MACD 力度衰减"}
    return None


def level_locate(d: pd.DataFrame) -> dict:
    """当前级别定位：笔 / 线段在做什么 + 大级别(MA160)给不给支撑。

    只回答「当前级别能不能交易」，不做递归、不要求 MA320。
    """
    lad = ladder(d)
    # 当前在主走哪一级：线段优先（更大一级在走时，笔只是它的次级）
    main, main_dir = "A0", "—"
    for nm in ("A1", "A0"):
        if lad[nm].get("state") in ("up", "dn"):
            main = nm
            main_dir = lad[nm]["dir"]
            break
    dirs = [lad[nm]["dir"] for nm in ("A0", "A1") if lad[nm].get("state") in ("up", "dn")]
    agree = len(set(dirs)) == 1 and len(dirs) >= 2
    LABEL = {"A0": "笔", "A1": "线段"}
    return {"level": main, "label": LABEL[main] + "级别", "dir": main_dir,
            "agree": agree, "ladder": lad,
            "big": lad.get("BIG", {})}


def bi_walk(d: pd.DataFrame) -> dict:
    """当前到底在「笔上」还是「笔下」，以及接下来怎么走（大佬 2026-11-09 要求）

    全部复用原文双穿口径（不另写一套）：
      state = dual_cross_state(d, *A0)[-1]
        up  = 正在上笔（MA5、MA10 同时站上 MA20）
        dn  = 正在下笔（两条同时跌破 MA20）
        mid = 笔内缠绕（一上一下，笔还没走成）

    关键位：
      本笔起点 = 最近一次翻笔那根的收盘（build_bi 最后一个点）
      前高/前低 = 上一个反向笔端点（上一波的极值）
    """
    dd = d if "ma20" in d.columns else add_ind(d)
    if len(dd) < max(A0) + 25:
        return {"ok": False, "where": "数据不足", "advice": "数据不足", "levels": []}
    st = dual_cross_state(dd, *A0)
    s = str(st.iloc[-1])
    pts = build_bi(dd)
    P = float(dd["close"].iloc[-1])
    m5 = float(dd["ma5"].iloc[-1])
    m10 = float(dd["ma10"].iloc[-1])
    m20 = float(dd["ma20"].iloc[-1])
    # build_bi 的点都是「翻转点」，pts[k].dir = 从该点开始这笔的方向。
    # ⚠️ 翻转点的 px 是「翻转那根的收盘」，不是这笔的极值 → 不能直接拿它当前高/前低。
    #    （实测风语筑：up 笔翻在 i=1162 收 12.91，但该笔区间最高冲到 13.59）
    # → 一律用**该笔区间内的真实高/低**：up 笔取 max(high)，dn 笔取 min(low)。
    n_bar = len(dd)
    hi_arr = dd["high"].astype(float).values
    lo_arr = dd["low"].astype(float).values

    def _rng(k):
        """第 k 笔（pts[k] → pts[k+1]）的区间高/低"""
        a = pts[k]["i"]
        b = pts[k + 1]["i"] if k + 1 < len(pts) else n_bar - 1
        if b < a:
            a, b = b, a
        return float(hi_arr[a:b + 1].max()), float(lo_arr[a:b + 1].min())

    start = ext = None
    cur_dir = points_last_dir = pts[-1]["dir"] if pts else s
    if pts:
        k = len(pts) - 1
        h_cur, l_cur = _rng(k)
        start = l_cur if cur_dir == "up" else h_cur      # 本笔起点极值
        # 上一同向笔（隔一笔）= k-2
        for kk in range(k - 2, 1, -1):
            if pts[kk]["dir"] == cur_dir:
                h_p, l_p = _rng(kk)
                ext = h_p if cur_dir == "up" else l_p     # 真前高/前低
                break
        if ext is None and k >= 2:
            h_p, l_p = _rng(k - 2)
            ext = h_p if cur_dir == "up" else l_p

    def _pc(x):
        return (P - x) / x * 100.0 if x else 0.0

    lad = ladder(dd)
    a0d, a1d = lad.get("A0", {}), lad.get("A1", {})
    big = lad.get("BIG", {})
    seg_dir = a1d.get("dir", "—")
    levels = [("MA20", round(m20, 2))]
    if s == "mid":
        # 缠绕里不谈前高/前低（笔还没成立），改报「近 20 根区间」
        h20 = float(hi_arr[-20:].max())
        l20 = float(lo_arr[-20:].min())
        levels += [("近20高", round(h20, 2)), ("近20低", round(l20, 2))]
    else:
        if start:
            levels.append(("本笔起点", round(start, 2)))
        if ext:
            levels.append(({"up": "前高", "dn": "前低"}.get(cur_dir, "同向前极值"), round(ext, 2)))

    if s == "up":
        where = "笔上（上笔运行中）"
        new_ext = bool(ext and P > ext)
        if new_ext:
            advice = ("上笔延伸创新高（前高 %.2f）→ 笔未结束，持有；"
                      "跌破本笔起点 %.2f 才算笔转折，跌破 MA20 %.2f 先减"
                      % (ext, start or 0.0, m20))
        else:
            advice = ("上笔运行中但未过前高 %.2f（现价 %+.2f%%）→ 前高是压力，"
                      "过不去且 MA5 掉下 MA10 要防笔结束；支撑看本笔起点 %.2f"
                      % (ext or 0.0, _pc(ext), start or 0.0))
    elif s == "dn":
        where = "笔下（下笔运行中）"
        new_ext = bool(ext and P < ext)
        if new_ext:
            advice = ("下笔延伸创新低（前低 %.2f）→ 下跌笔没走完，别接；"
                      "等底背驰 或 MA5/MA10 同时重回 MA20 %.2f 上方" % (ext, m20))
        else:
            advice = ("下笔运行中但未破前低 %.2f（现价 %+.2f%%）→ 前低是支撑，"
                      "撑住就等笔转折：MA5、MA10 同时上穿 MA20 %.2f 才确认翻上"
                      % (ext or 0.0, _pc(ext), m20))
    else:
        if m5 > m20 >= m10:
            where = "笔内（向上临界）"
            advice = ("笔内缠绕、临界转上：MA5 已上穿 MA20、MA10 还差 %.2f%% → "
                      "MA10 补上（≈%.2f）才成立上笔，没补上不算买点"
                      % ((m20 - m10) / m20 * 100, m20))
        elif m10 > m20 >= m5:
            where = "笔内（向下临界）"
            advice = ("笔内缠绕、临界转下：MA10 还在 MA20 上方、MA5 已破 → "
                      "MA10 也跌破 %.2f 就走下笔，先减不加" % m20)
        else:
            where = "笔内（缠绕）"
            advice = ("笔内缠绕（MA5 %.2f / MA10 %.2f 分居 MA20 %.2f 两侧），方向未定 → "
                      "等双穿成立再动，别在缠绕里猜方向" % (m5, m10, m20))
        new_ext = None

    # 级别关系 —— 这一笔是「顺级别」还是「逆级别」
    if s == "up" and seg_dir == "向上":
        ctx = "线段同向向上 = 顺级别，本笔是线段上涨里的一笔"
    elif s == "dn" and seg_dir == "向下":
        ctx = "线段同向向下 = 顺级别，本笔是线段下跌里的一笔，别抢"
    elif s == "up" and seg_dir == "向下":
        ctx = "线段向下、本笔向上 = 逆级别反弹，前高附近要先减"
    elif s == "dn" and seg_dir == "向上":
        ctx = "线段向上、本笔向下 = 顺级别回踩，笔转折就是回踩买点"
    elif s == "mid" and seg_dir in ("向上", "向下"):
        ctx = ("本笔方向未定，但线段是%s的 → 双穿翻%s就顺着线段做，别抢在缠绕里"
               % (seg_dir, "上" if seg_dir == "向上" else "下"))
    else:
        ctx = "线段方向不明（%s），本笔方向以双穿为准" % (seg_dir or "纠缠")
    if big.get("state") == "dn":
        ctx += "；⚠️ 已跌破 MA160 大级别支撑"

    return {"ok": True, "state": s, "where": where, "start": start, "ext": ext,
            "new_ext": new_ext, "gap": _pc(start) if start else 0.0,
            "advice": advice, "ctx": ctx,
            "pen_bars": a0d.get("bars", 0), "seg_dir": seg_dir,
            "levels": levels}


# ───────────── 买卖点（原文 120 分钟例子） ─────────────
def signals(d: pd.DataFrame, look: int = 6) -> list:
    """均线视角的买卖点。两种来源，都要报：

    ① **新鲜穿越**（转折点）：最近 look 根内 笔 由下转上 / 由上转下
    ② **长期排列**（延续态）：多头/空头排列已经维持很久 ——
       大佬 2026-11-08：「多头排列，它也表示在很远很远的那个节点上，它就穿越了」
       ⚠️ 只报 ① 会漏掉绝大多数行情（实测 257 个多头排列日，「近6根穿越」命中 0 天）
    """
    st = dual_cross_state(d, *A0)
    out = []
    n = len(st)
    # ① 新鲜穿越
    for k in range(1, min(look + 1, n - 1)):
        i = n - k
        prev, cur = st.iloc[i - 1], st.iloc[i]
        if prev == "dn" and cur == "up":
            out.append({"type": "买点", "kind": "一类买点（笔翻上）",
                        "bars_ago": k, "px": float(d["close"].iloc[i]),
                        "dt": str(d["dt"].iloc[i])[:10], "src": "新鲜穿越"})
        elif prev == "up" and cur == "dn":
            out.append({"type": "卖点", "kind": "一卖（笔翻下）",
                        "bars_ago": k, "px": float(d["close"].iloc[i]),
                        "dt": str(d["dt"].iloc[i])[:10], "src": "新鲜穿越"})
    if out:
        return out

    # ② 长期排列（没有新鲜穿越时，报当前排列状态 —— 这是「很多根以前穿越过」的延续）
    #    ⚠️ 回测分级（4宽基 2015-2026，20根后收益）：
    #       排列 1-5 根   → 平均 -0.74%~+2.37%，胜率 45~59%：刚排齐时往往已涨一段，追不得
    #       排列 6-15 根  → 平均 +0.83%~+4.05%，胜率 57~66%：★最佳区间，排列站稳了还在走
    #       排列 16 根以上 → 沪深300/上证50 转负(胜率27%/57%)，创业板指仍强(+5.30%/76%)：分化，需警惕
    al = ma_alignment(d)
    age = dual_cross_age(d, *A0)
    ab = al.get("bars", 0)
    zone = ("刚排齐·追高慎" if ab <= 5 else ("排列稳固·最佳" if ab <= 15 else "排列过久·警惕"))
    if al.get("state") == "多头排列":
        out.append({"type": "买点",
                    "kind": "多头排列延续（MA5>10>20>40>80>160 已%d根 · %s）" % (ab, zone),
                    "bars_ago": ab, "px": float(d["close"].iloc[-1]),
                    "dt": str(d["dt"].iloc[-1])[:10], "src": "长期排列", "zone": zone})
    elif al.get("state") == "空头排列":
        out.append({"type": "卖点",
                    "kind": "空头排列延续（MA5<10<20<40<80<160 已%d根 · %s）" % (ab, zone),
                    "bars_ago": ab, "px": float(d["close"].iloc[-1]),
                    "dt": str(d["dt"].iloc[-1])[:10], "src": "长期排列", "zone": zone})
    elif st.iloc[-1] == "up" and age > 0:
        # 全均线没排齐，但笔级别双穿向上且持续很久
        out.append({"type": "买点",
                    "kind": "笔级别向上延续（MA5/10 双穿 MA20 已%d根）" % age,
                    "bars_ago": age, "px": float(d["close"].iloc[-1]),
                    "dt": str(d["dt"].iloc[-1])[:10], "src": "长期排列"})
    elif st.iloc[-1] == "dn" and age > 0:
        out.append({"type": "卖点",
                    "kind": "笔级别向下延续（MA5/10 双穿 MA20 已%d根）" % age,
                    "bars_ago": age, "px": float(d["close"].iloc[-1]),
                    "dt": str(d["dt"].iloc[-1])[:10], "src": "长期排列"})
    return out


def macd_state(d: pd.DataFrame) -> str:
    if len(d) < 3:
        return "—"
    h = d["hist"].iloc[-3:].values
    if h[-1] > 0 and h[-2] <= 0:
        return "金叉初起"
    if h[-1] < 0 and h[-2] >= 0:
        return "死叉初起"
    if h[-1] > h[-2] > 0:
        return "红柱放大"
    if h[-1] < h[-2] < 0:
        return "绿柱放大"
    if h[-1] > h[-2] and h[-1] < 0:
        return "绿柱缩短（底背征兆）"
    if h[-1] < h[-2] and h[-1] > 0:
        return "红柱缩短（顶背征兆）"
    return "震荡"


# ───────────── 数据 ─────────────
def load_index(ts_code: str) -> pd.DataFrame | None:
    cache = os.path.join(WS, ".cache", "ma_trend_%s_d.csv" % ts_code.replace(".", "_"))
    try:
        import tushare_init as ti
        pro = ti.get_pro()
        for _ in range(3):
            try:
                df = pro.index_daily(ts_code=ts_code, start_date="20150101")
                if df is not None and len(df) > 200:
                    df = df.rename(columns={"trade_date": "dt"})
                    df["dt"] = pd.to_datetime(df["dt"])
                    df = df.sort_values("dt").reset_index(drop=True)
                    try:
                        df.to_csv(cache, index=False)
                    except Exception:
                        pass
                    return df[["dt", "open", "high", "low", "close"]]
            except Exception:
                pass
    except Exception:
        pass
    if os.path.exists(cache):
        try:
            df = pd.read_csv(cache, parse_dates=["dt"])
            if len(df) > 200:
                return df
        except Exception:
            pass
    return None


# ───────────── 渲染 ─────────────
def analyse(name, ts_code, level="day") -> dict | None:
    """只看**当前级别**够不够支撑交易，不递归、不要求 A2/MA320。

    大佬 2026-11-08 定稿：
      「我不需要递归啊……你只要找买卖点得了。你这个递归无非让我找更细的买卖点，我不需要。
        你只要当时出现买卖点，我就拿着就行了。期货的话，我就上到下一级别，上到子级别去看了，
        然后那个 A2 是属于大级别，那你没有的话我就不需要，我只需要当前级别，
        够不够买卖点就行了，能不能支撑交易就行了」
    → 数据门槛 = 够算 MA160（大级别支撑线）+ 10 根余量 = 170 根。
      不再要求 MA320（大佬定稿：只要突破 160 均线就行）。
      走势类型层已取消，大级别支撑改用「价格 vs MA160」。
    """
    raw = load_index(ts_code)
    if raw is None:
        return None
    d = raw if level == "day" else resample_week(raw)
    # 门槛 = 够算 MA160（大级别支撑线）+ 余量。不再要求 MA320。
    if len(d) < BIG_MA + 10:      # 170 根
        return None
    d = add_ind(d)
    last = d.iloc[-1]
    loc = level_locate(d)
    bi = build_bi(d)
    return {"name": name, "code": ts_code, "level": level,
            "dt": str(d["dt"].iloc[-1])[:10], "close": float(last["close"]),
            "n": len(d), "loc": loc, "sigs": signals(d),
            "bi_n": len(bi), "bi_last": bi[-1] if bi else None,
            "macd": macd_state(d), "hist": float(last["hist"]),
            "mode": trend_mode(d), "beichi": macd_bei_chi(d, bi),
            "align": ma_alignment(d), "align_short": short_alignment(d),
            "walk": bi_walk(d)}


def verdict(d: pd.DataFrame) -> str:
    """直接从 DataFrame 出「当前级别能不能交易」一句话（不用先跑 analyse 拿 r）

    供绘图/扫描等只拿到 df 的场景使用，判定逻辑与 tradeable(r) 完全一致。
    """
    dd = d if "ma160" in d.columns else add_ind(d)
    if len(dd) < BIG_MA + 10:
        return "数据不足（需 ≥%d 根）" % (BIG_MA + 10)
    loc = level_locate(dd)
    sigs = signals(dd)
    mo = trend_mode(dd)
    if any(s["type"] == "买点" for s in sigs):
        return "✅ 可以交易（当前级别有买点）"
    if any(s["type"] == "卖点" for s in sigs):
        return "⛔ 不宜做多（当前级别在卖点）"
    pen = loc["ladder"]["A0"]
    if mo.get("mode") == "趋势下跌" or pen.get("dir") == "向下":
        return "⚪ 无买点（当前级别向下，空仓等）"
    if mo.get("mode") == "趋势上涨":
        return "🟡 无新鲜买点（趋势向上，可持有不加仓）"
    return "⚪ 无买点（盘整，等结构明朗）"


def tradeable(r) -> str:
    """当前级别能不能支撑交易 —— 大佬要的就这一句话。

    「我只需要当前级别，够不够买卖点就行了，能不能支撑交易就行了」
    """
    sigs = r.get("sigs") or []
    if any(s["type"] == "买点" for s in sigs):
        return "✅ 可以交易（当前级别有买点）"
    if any(s["type"] == "卖点" for s in sigs):
        return "⛔ 不宜做多（当前级别在卖点）"
    mo = (r.get("mode") or {}).get("mode", "")
    pen = r["loc"]["ladder"]["A0"]
    if mo == "趋势下跌" or pen.get("dir") == "向下":
        return "⚪ 无买点（当前级别向下，空仓等）"
    if mo == "趋势上涨":
        return "🟡 无新鲜买点（趋势向上，可持有不加仓）"
    return "⚪ 无买点（盘整，等结构明朗）"


def render(results, level):
    lt = LEVEL_TXT.get(level, level)
    L = ["📈 缠论·均线版 · %s · %s   —— 只问一件事：当前级别能不能支撑交易"
         % (lt, datetime.now().strftime("%Y-%m-%d %H:%M"))]
    L.append("笔 = MA5/10 双穿 MA20 ｜ 线段 = MA20/40 双穿 MA80 ｜ 大级别 = 价格 vs MA160")
    L.append("只看%s级别本身，不递归；大级别支撑看 MA160（不用 MA320）" % lt)
    L.append("")
    buys, sells, wait = [], [], []
    for r in results:
        lad = r["loc"]["ladder"]
        L.append("【%s·%s】收 %.2f（%s）  → %s"
                 % (r["name"], lt, r["close"], r["dt"], tradeable(r)))
        # 笔 / 线段（当前级别核心两层；走势类型算不出就跳过）
        for nm, tag in (("A0", "笔"), ("A1", "线段")):
            v = lad[nm]
            if v.get("state") == "数据不足":
                continue
            if v["bars"] and v["state"] != "mid":
                L.append("  %s = %s ｜ %s ｜ 已持续%d根（穿越在%d根前）"
                         % (tag, v["align"], v["dir"], v["bars"], v["bars"]))
            else:
                L.append("  %s = %s ｜ %s ｜ 无（纠缠中）" % (tag, v["align"], v["dir"]))
        # 大级别支撑：只看价格 vs MA160
        bg = lad.get("BIG", {})
        if bg.get("state") == "数据不足":
            L.append("  大级别 = 数据不足（MA160 不够）")
        elif bg:
            tag = "✅" if bg["state"] == "up" else ("⛔" if bg["state"] == "dn" else "⚪")
            L.append("  大级别 = %s %s（已 %d 根）"
                     % (tag, bg["align"], bg.get("bars", 0)))
        # 排列
        al = r.get("align") or {}
        if al.get("state") == "数据不足":
            L.append("  全均线排列 = 数据不足（不影响交易判断）")
        elif al.get("state") in ("多头排列", "空头排列"):
            L.append("  全均线排列 = %s（已 %d 根没散）→ 穿越在很久以前但一直没破"
                     % (al["state"], al.get("bars", 0)))
        elif al.get("state"):
            L.append("  全均线排列 = %s" % al["state"])
        bl = r["bi_last"]
        if bl:
            L.append("  最近一笔：%s 起于 %s @%.2f（共 %d 笔）"
                     % ("上涨笔" if bl["dir"] == "up" else "下跌笔", bl["dt"], bl["px"], r["bi_n"]))
        # ▶ 当前位置（笔上/笔下）+ 怎么走（大佬 2026-11-09 要求：最后必须回答这个）
        wk = r.get("walk") or {}
        if wk.get("ok"):
            L.append("  ▶ 当前位置 = %s" % wk["where"])
            L.append("    → 怎么走：%s" % wk.get("advice"))
            L.append("    → 级别关系：%s" % wk.get("ctx"))
            if wk.get("levels"):
                L.append("    → 盯的位置：%s"
                         % " ｜ ".join("%s %.2f" % (k, v) for k, v in wk["levels"]))
        L.append("  当下形态 = %s" % ((r.get("mode") or {}).get("mode", "—")))
        bc = r.get("beichi")
        if bc:
            L.append("  ⚡ %s：力度 %s → %s" % (bc["type"], bc["power_prev"], bc["power_now"]))
        # 买卖点
        if r["sigs"]:
            for sg in r["sigs"]:
                L.append("  %s %s：%s @%.2f（%d 根前，%s）"
                         % ("🌙" if sg["type"] == "买点" else "⭐", sg["type"], sg["kind"],
                            sg["px"], sg["bars_ago"], sg["dt"]))
                (buys if sg["type"] == "买点" else sells).append("%s@%.2f" % (r["name"], sg["px"]))
        else:
            wait.append(r["name"])
            L.append("  买卖点：当前无（近 6 根笔未转折）")
        L.append("")
    L.append("── 汇总（当前级别够不够交易）──")
    L.append("✅ 有买点：" + ("、".join(buys) if buys else "无"))
    L.append("⛔ 有卖点：" + ("、".join(sells) if sells else "无"))
    if wait:
        L.append("⚪ 无信号（空仓等待）：" + "、".join(wait))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="day", choices=["day", "week"])
    ap.add_argument("--code")
    ap.add_argument("--out")
    a = ap.parse_args()
    items = IDX
    if a.code:
        items = [x for x in IDX if x[1] == a.code] or [(a.code, a.code)]
    res = []
    for name, ts in items:
        try:
            r = analyse(name, ts, a.level)
            if r:
                res.append(r)
            else:
                print("[WARN] %s 数据不足" % name, flush=True)
        except Exception as e:
            print("[ERR] %s: %s" % (name, str(e)[:120]), flush=True)
    if not res:
        return 1
    txt = render(res, a.level)
    print(txt)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
