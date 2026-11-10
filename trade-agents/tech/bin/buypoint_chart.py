#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买点图：日线 / 小时线 / 缠论均线版 —— 三面板一张图
=====================================================================
给 bin/tech_buypoint.py 的个股买点出图（大佬 2026-11-09 21:00 要求：
「你这个日线版、小时线版还有均线版，这都是要带图的，都是子图放在一个图了」）

面板（从上到下）：
  ① 日线      K线 + 缠论分型版笔/中枢/买卖点（买点=绿月亮🌙 卖点=红星⭐）+ 量 + MACD
  ② 小时线60m  同上，只画缠论分型版（小时线只认三买 / 一买须金叉）
  ③ 缠论均线版  MA5/10/20/40/80/160 + 笔(MA5/10穿20) + 线段(MA20/40穿80) + 走势类型底色

实现复用 tech_summary 的 draw 逻辑与 chan_geometry，口径完全一致。
0 token，纯 matplotlib。
"""
from __future__ import annotations

import os
import socket
import sys

_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
# 中文字体（否则中文乱码/方框）—— 优先文泉驿正黑，其次 Noto Sans CJK
import matplotlib.font_manager as _fm  # noqa: E402
for _f in ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
    if os.path.exists(_f):
        try:
            _fm.fontManager.addfont(_f)
            _name = _fm.FontProperties(fname=_f).get_name()
            matplotlib.rcParams["font.sans-serif"] = [_name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            pass
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))

import chan_ma_trend_v2 as M          # noqa: E402
import tech_summary as TS            # noqa: E402
from tech_summary import (            # noqa: E402
    MOON, STAR, MA_PALETTE, MA_LW, RED, GRN, BLUE, ORANGE,
    glyph_marker, ma_structure, ma20_state,
)

CHART_DIR = os.path.join(WS, ".cache", "charts")
os.makedirs(CHART_DIR, exist_ok=True)


def _fmt_pct(v):
    return "%+.2f%%" % v


def _draw_kline_panel(ax, axV, axM, df, fx, n_show, title, kind="日线"):
    """K线 + 缠论分型版结构 + 量 + MACD，返回 today_pct"""
    total = len(df)
    start = max(0, total - n_show)
    view = df.iloc[start:].reset_index(drop=True)
    nv = len(view)
    x = np.arange(nv)
    o = view["open"].values.astype(float)
    h = view["high"].values.astype(float)
    l = view["low"].values.astype(float)
    c = view["close"].values.astype(float)
    prev_c = np.concatenate([[np.nan], c[:-1]])
    if start > 0:
        prev_c[0] = float(df["close"].iloc[start - 1])
    # ⚠️ 涨跌按 close vs 上一根 close 上色（MEMORY 硬规矩）
    up_col = [RED if (np.isfinite(prev_c[i]) and c[i] >= prev_c[i]) else GRN for i in range(nv)]
    today_pct = (c[-1] - prev_c[-1]) / prev_c[-1] * 100 if np.isfinite(prev_c[-1]) else 0.0

    bw = 0.62
    for i in range(nv):
        ax.vlines(x[i], l[i], h[i], color=up_col[i], lw=0.9, zorder=3)
        body_lo, body_hi = min(o[i], c[i]), max(o[i], c[i])
        if abs(body_hi - body_lo) < 1e-9:
            body_hi = body_lo + (h[i] - l[i]) * 0.001 + 1e-6
        ax.add_patch(Rectangle((x[i] - bw / 2, body_lo), bw, body_hi - body_lo,
                               facecolor=up_col[i], edgecolor=up_col[i], lw=0.5, zorder=4))

    # 缠论几何（用全量算，再按索引裁切）
    from chan_chart import chan_geometry
    geo = chan_geometry(df)
    bx, by = [], []
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -2 <= xi <= nv + 2:
            bx.append(xi)
            by.append(p["price"])
    if len(bx) >= 2:
        ax.plot(bx, by, color=BLUE, lw=1.5, zorder=6, label="缠论笔")
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -1 <= xi <= nv:
            ax.scatter([xi], [p["price"]], s=20, zorder=7,
                       color=(RED if p["type"] == 1 else GRN),
                       marker=("v" if p["type"] == 1 else "^"))

    # 中枢
    if geo["zs"] and geo["zs_x"]:
        zs0, zs1 = geo["zs"]
        x0 = max(geo["zs_x"][0] - start, -0.5)
        x1 = min(geo["zs_x"][1] - start, nv)
        if x1 >= -1 and x1 > x0:
            ax.add_patch(Rectangle((x0, zs0), x1 - x0, zs1 - zs0,
                                   facecolor="#ffd24d", alpha=0.25,
                                   edgecolor="#b8860b", lw=1.4, zorder=3))
            ax.axhline(zs1, color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)
            ax.axhline(zs0, color="#b8860b", lw=0.8, ls="--", alpha=0.7, zorder=4)
            ax.text(max(x0, 0) + 0.5, (zs0 + zs1) / 2, "中枢 ZD %.2f~ZG %.2f" % (zs0, zs1),
                    fontsize=8, color="#8b6508", va="center", zorder=9)

    # 买卖点：买点=绿月牙 卖点=红星
    # ⚠️ 2026-11-09：买点必须过闸门（年龄/距离/MA20 方向），否则不画——
    #    风语筑事故：116 根前的一买 8.78 被画成当前买点，误导。
    BUY = {"一买": ("#0b6e0b", 460), "二买": ("#1a9850", 340),
           "三买": ("#2e9b4f", 300)}
    SELL = {"一卖": ("#8b0000", 420), "二卖": ("#d73027", 300)}
    _cl = float(df["close"].iloc[-1])
    _buy_ok = [x for x in TS.rank_buys(fx.get("buy_list") or [],
                                       TS.ma20_state(df), close=_cl) if x["accept"]]
    for lst, styles, dy, is_buy in ((_buy_ok, BUY, -1, True),
                                     (fx.get("sell_list") or [], SELL, 1, False)):
        for b in lst:
            if b["tag"] not in styles:
                continue
            if not (b.get("fresh") or b.get("sustained")):
                continue
            col, sz = styles[b["tag"]]
            cand = [p for p in geo["bi"] if p["type"] == (1 if not is_buy else -1)]
            if not cand:
                continue
            pt = min(cand, key=lambda p: abs(p["price"] - b["level"]))
            xi = pt["orig"] - start
            if not (-1 <= xi <= nv):
                continue
            ax.scatter([xi], [pt["price"]], s=sz, marker=(MOON if is_buy else STAR),
                       color=col, edgecolor=(col if is_buy else "white"),
                       lw=(1.9 if is_buy else 1.2), zorder=10)
            ax.annotate("%s %.2f\n%d根前" % (b["tag"], b["level"], b["age"]),
                        xy=(xi, pt["price"]), xytext=(0, 30 * dy),
                        textcoords="offset points", ha="center",
                        va=("top" if dy < 0 else "bottom"),
                        fontsize=8, color=col, fontweight="bold", zorder=11,
                        bbox=dict(boxstyle="round,pad=0.24", fc="white", ec=col,
                                  alpha=0.94, lw=0.9))

    ax.set_ylabel("价格", fontsize=9)
    ax.grid(True, ls=":", color="#dddddd", alpha=0.7)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9, ncol=2)
    ax.set_xlim(-1, nv)
    ax.set_title("%s ｜ 收 %.2f ｜ 今日 %s" % (title, c[-1], _fmt_pct(today_pct)),
                 fontsize=11.5, color="#222222", pad=7, loc="left")
    ax.tick_params(labelbottom=False)

    # 量
    axV.bar(x, view["volume"].values.astype(float), width=bw, color=up_col, alpha=0.75)
    axV.set_ylabel("量", fontsize=8)
    axV.grid(True, ls=":", color="#dddddd", alpha=0.5)
    axV.tick_params(labelbottom=False)

    # MACD(10,20,5)
    cl = df["close"].astype(float)
    dif = cl.ewm(span=10, adjust=False).mean() - cl.ewm(span=20, adjust=False).mean()
    dea = dif.ewm(span=5, adjust=False).mean()
    hist = 2 * (dif - dea)
    axM.bar(x, hist.values[start:],
            color=["#d62728" if v >= 0 else "#2ca02c" for v in hist.values[start:]],
            width=bw, alpha=0.7)
    axM.plot(x, dif.values[start:], color=ORANGE, lw=1.0, label="DIF")
    axM.plot(x, dea.values[start:], color="#2980b9", lw=1.0, label="DEA")
    axM.axhline(0, color="#888888", lw=0.7)
    axM.legend(loc="upper left", fontsize=7.5, framealpha=0.85, ncol=3)
    axM.set_ylabel("MACD", fontsize=8)
    step = max(1, nv // 12)
    ticks = list(range(0, nv, step))
    axM.set_xticks(ticks)
    axM.set_xticklabels([pd.Timestamp(view["dt"].iloc[i]).strftime("%m-%d") for i in ticks],
                        fontsize=8)
    return today_pct


def _draw_ma_panel(ax, df, n_ma=400):
    """缠论均线版：走势类型底色 + 线段 + 笔 + 6 条均线"""
    n = len(df)
    n_ma = min(max(n_ma, 60), n)
    off = n - n_ma
    view = df.iloc[off:].reset_index(drop=True)
    x = np.arange(n_ma)
    dd = M.add_ind(df)
    st = ma_structure(df)

    MODE_COL = {"trend_up": ("#ffe3e3", "上涨"), "trend_dn": ("#e0f2e4", "下跌"),
                "range": ("#efefef", "盘整")}
    MODE_TXT = {"trend_up": "#c62828", "trend_dn": "#1b7e32", "range": "#555555"}

    # ① 走势类型背景（最淡）
    if st:
        labeled = set()
        for i0, i1, kind in st["mode"]:
            x0, x1 = i0 - off, i1 - off
            if x1 < -1 or x0 > n_ma:
                continue
            x0, x1 = max(x0, -1), min(x1, n_ma)
            fc, lb = MODE_COL[kind]
            ax.axvspan(x0, x1, facecolor=fc, alpha=0.55, zorder=0, lw=0)
            if (x1 - x0) > n_ma * 0.06 and len(labeled) < 6:
                labeled.add(kind)
                ax.text((x0 + x1) / 2, 0.985, lb, transform=ax.get_xaxis_transform(),
                        ha="center", va="top", fontsize=8.5, color=MODE_TXT[kind],
                        fontweight="bold", zorder=3, alpha=0.55)

    # ② 收盘线（淡灰）
    ax.plot(x, view["close"].values.astype(float), color="#b0b0b0", lw=0.9,
            alpha=0.85, label="收盘", zorder=3)

    # ③ 均线：图上只画 MA10/20 + MA160（MA5 画着太乱，故不画；计算里 MA5 仍在用 —— 笔要双穿）
    for nn in (10, 20):
        col = "ma%d" % nn
        if col not in dd.columns:
            continue
        y = dd[col].values[off:]
        if np.all(~np.isfinite(y)):
            continue
        ax.plot(x, y, color=MA_PALETTE[nn], lw=0.8, alpha=0.55,
                label="MA%d" % nn, zorder=4)
    if "ma160" in dd.columns:
        y = dd["ma160"].values[off:]
        if np.any(np.isfinite(y)):
            ax.plot(x, y, color="#8e44ad", lw=1.3, alpha=0.55, ls="--",
                    label="MA160(大级别)", zorder=4)
            yv = y[-1]
            if np.isfinite(yv):
                ax.annotate("MA160 %.2f" % yv, xy=(n_ma - 0.6, yv), xytext=(6, 0),
                            textcoords="offset points", va="center", ha="left",
                            fontsize=7.4, color="#8e44ad", fontweight="bold",
                            annotation_clip=False, zorder=12)

    # ④ 中枢（线段三段重叠区）—— 黄框 + ZG/ZD 虚线
    if st and st.get("zs"):
        for zs in st["zs"]:
            x0, x1 = zs["i0"] - off, zs["i1"] - off
            if x1 < -1 or x0 > n_ma:
                continue
            x0, x1 = max(x0, -0.5), min(x1, n_ma)
            if x1 <= x0:
                continue
            ax.add_patch(Rectangle((x0, zs["zd"]), x1 - x0, zs["zg"] - zs["zd"],
                                   facecolor="#ffd24d", alpha=0.30,
                                   edgecolor="#c07800", lw=2.2, zorder=6))
            ax.hlines([zs["zg"], zs["zd"]], x0, x1,
                      colors="#c07800", lw=1.4, ls="--", alpha=0.85, zorder=7)
            if (x1 - x0) > n_ma * 0.05:
                ax.text((x0 + x1) / 2, zs["zg"], "中枢 %.0f~%.0f" % (zs["zd"], zs["zg"]),
                        fontsize=7.4, color="#8b6508", ha="center", va="bottom",
                        fontweight="bold", zorder=11,
                        bbox=dict(boxstyle="round,pad=0.14", fc="white",
                                  ec="#b8860b", lw=0.5, alpha=0.85))

    # ⑤ 线段（MA20/40 穿 MA80）—— 主结构，最深最粗
    if st and len(st["seg"]) >= 2:
        sx, sy = [], []
        for p in st["seg"]:
            xi = p["i"] - off
            if -2 <= xi <= n_ma + 2:
                sx.append(xi)
                sy.append(p["px"])
        if len(sx) >= 2:
            ax.plot(sx, sy, color="#0d2b45", lw=3.6, alpha=1.0, zorder=9,
                    label="线段(MA20/40穿MA80)")
            # 未完成的线段：最后极值 → 最新收盘 用虚线补齐
            _lp = st["seg"][-1]
            if _lp.get("live") and sx[-1] < n_ma - 1:
                ax.plot([sx[-1], n_ma - 1], [sy[-1], float(view["close"].iloc[-1])],
                        color="#0d2b45", lw=2.0, ls="--", alpha=0.75, zorder=9)
            for i in range(len(sx)):
                ax.scatter([sx[i]], [sy[i]], s=62, marker="s", color="#0d2b45",
                           edgecolor="white", lw=1.4, zorder=10)
                if i and abs(sx[i] - sx[i - 1]) > 2:
                    ax.annotate("线段", xy=((sx[i] + sx[i - 1]) / 2, (sy[i] + sy[i - 1]) / 2),
                                fontsize=7.6, color="#0d2b45", ha="center", va="center",
                                fontweight="bold", zorder=10,
                                bbox=dict(boxstyle="round,pad=0.16", fc="white",
                                          ec="#0d2b45", lw=0.6, alpha=0.88))

    # ⑥ 笔（MA5/10 穿 MA20）—— 次级结构
    if st and len(st["bi"]) >= 2:
        bx2, by2 = [], []
        for p in st["bi"]:
            xi = p["i"] - off
            if -2 <= xi <= n_ma + 2:
                bx2.append(xi)
                by2.append(p["px"])
        if len(bx2) >= 2:
            ax.plot(bx2, by2, color="#e8955a", lw=1.3, alpha=0.9, zorder=7,
                    label="笔(MA5/10穿MA20)")
            _bp = st["bi"][-1]
            if _bp.get("live") and bx2[-1] < n_ma - 1:
                ax.plot([bx2[-1], n_ma - 1], [by2[-1], float(view["close"].iloc[-1])],
                        color="#e8955a", lw=1.0, ls="--", alpha=0.7, zorder=7)
            for i in range(len(bx2)):
                ax.scatter([bx2[i]], [by2[i]], s=12, marker="o", color="#e8955a",
                           edgecolor="white", lw=0.5, zorder=8)

    # 技能核心结论（tradeable 一句话）+ 大级别 MA160（与 chan-ma-trend 技能完全同口径）
    try:
        tr = M.verdict(dd)
        lv = M.level_locate(dd)
        big = lv.get("big") or {}
        tcol = {"✅": "#1b7e32", "⛔": "#c62828", "🟡": "#b8860b", "⚪": "#666666"}
        cc2 = next((v for k, v in tcol.items() if tr.startswith(k)), "#333333")
        # ⚠️ 图内不能出现 emoji（无彩色字形 → 豆腐块），去前缀只靠颜色区分
        for _e in tcol:
            if tr.startswith(_e):
                tr = tr[len(_e):].strip()
                break
        ax.text(0.008, 0.975, tr + " ｜ 大级别(MA160)：%s" % (big.get("align") or "—"),
                transform=ax.transAxes, fontsize=9.2, color=cc2, fontweight="bold",
                va="top", ha="left",
                bbox=dict(boxstyle="round,pad=0.26", fc="white", ec=cc2, alpha=0.93, lw=1.1))
        # ▶ 当前位置（笔上/笔下）+ 关键位
        wk = M.bi_walk(dd)
        if wk.get("ok"):
            wc = {"up": "#c62828", "dn": "#2ca02c", "mid": "#b8860b"}.get(wk["state"], "#333")
            lvt = " ｜ ".join("%s %.2f" % (k, v) for k, v in (wk.get("levels") or [])[:3])
            ax.text(0.5, 0.975, wk["where"] + (("  " + lvt) if lvt else ""),
                    transform=ax.transAxes, fontsize=8.6, color=wc, fontweight="bold",
                    va="top", ha="center",
                    bbox=dict(boxstyle="round,pad=0.26", fc="white", ec=wc, alpha=0.93, lw=1.1))
    except Exception:
        pass
    ax.set_ylabel("缠论·均线版", fontsize=9)
    ax.grid(True, ls=":", color="#cccccc", alpha=0.6)
    ax.legend(loc="upper left", fontsize=7.6, framealpha=0.9, ncol=5)
    ax.set_xlim(-1, n_ma)
    ax.set_title("缠论·均线版（近 %d 根）｜ 笔 = MA5/10 双穿 MA20 ｜ 线段 = MA20/40 双穿 MA80 ｜ 底色 = 走势类型"
                 % n_ma, fontsize=10, color="#333333", pad=5, loc="left")
    step = max(1, n_ma // 14)
    tk = list(range(0, n_ma, step))
    ax.set_xticks(tk)
    ax.set_xticklabels([pd.Timestamp(view["dt"].iloc[i]).strftime("%m-%d") for i in tk],
                       fontsize=8)
    return st


def draw_buypoint_chart(disp, day, h1=None, out=None, n_day=120, n_h1=110, n_ma=400):
    """三面板买点图：日线 / 小时线 / 缠论均线版 → 出 PNG 路径"""
    d = day.copy()
    if "volume" not in d.columns:
        d["volume"] = d.get("vol", 0.0)
    fx_d = TS.chan_fx(d, "day")

    has_h1 = h1 is not None and len(h1) > 60
    # 日线：价/量/MACD + （小时线：价/量/MACD）+ 均线版
    heights = [2.8, 1.1, 1.0]
    if has_h1:
        heights = [2.8, 1.1, 1.0, 2.4, 1.0, 0.9]
    heights.append(2.6)
    nrows = len(heights)
    fig = plt.figure(figsize=(16, 17.5 if has_h1 else 12.0), facecolor="white")
    gs = fig.add_gridspec(nrows, 1, height_ratios=heights, hspace=0.10)

    axP = fig.add_subplot(gs[0])
    axV = fig.add_subplot(gs[1], sharex=axP)
    axM = fig.add_subplot(gs[2], sharex=axP)
    pct_d = _draw_kline_panel(axP, axV, axM, d, fx_d, n_day,
                              "%s · 日线（缠论分型版）" % disp, "day")

    if has_h1:
        hh = h1.copy()
        if "volume" not in hh.columns:
            hh["volume"] = 0.0
        fx_h = TS.chan_fx(hh, "h1")
        axP2 = fig.add_subplot(gs[3])
        axV2 = fig.add_subplot(gs[4], sharex=axP2)
        axM2 = fig.add_subplot(gs[5], sharex=axP2)
        _draw_kline_panel(axP2, axV2, axM2, hh, fx_h, n_h1,
                          "%s · 小时线 60m（缠论分型版）" % disp, "h1")

    axA = fig.add_subplot(gs[nrows - 1])
    st = _draw_ma_panel(axA, d, n_ma)

    fig.suptitle("%s ｜ 买点图（日线优先 · 小时线次级 · 均线版结构）｜ 买点=绿月牙 卖点=红星"
                 % disp, fontsize=13, y=0.995, color="#111111")
    fig.text(0.5, 0.002,
             "K线上色 = 收盘 vs 前收（涨跌口径）　｜　日线/小时线画缠论分型版：笔(蓝) 中枢(黄) 买卖点(月牙/星)　"
             "｜　均线版画：笔(深橙) 线段(深蓝) 走势类型底色(红涨/绿跌/灰盘整)",
             ha="center", fontsize=8.6, color="#666666")

    if out is None:
        out = os.path.join(CHART_DIR, "buypoint_%s_%s.png"
                           % (disp.replace("/", "_"),
                              pd.Timestamp(d["dt"].iloc[-1]).strftime("%Y%m%d")))
    fig.savefig(out, dpi=125, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out
