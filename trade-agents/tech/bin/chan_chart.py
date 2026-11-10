#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论 · K线图生成器（mplfinance + 缠论示意图）
=====================================================================
把「某一级别的缠论买卖点」画成图片：K线 + 笔 + 中枢矩形 + 买点/卖点 + MACD。

口径（与 chan_signal_monitor / chan_smc_index 完全一致）
  版本：缠论·分型版（包含处理→顶底分型→笔→中枢ZG/ZD→背驰）
  级别：--level h1  → 小时线60m
        --level day → 日线

用法：
  python3 bin/chan_chart.py                       # 中证500 · 小时线60m（默认）
  python3 bin/chan_chart.py --index 沪深300
  python3 bin/chan_chart.py --index 中证500 --level day
  python3 bin/chan_chart.py --index 科创50 --n 90 --out /tmp/a.png
  python3 bin/chan_chart.py --all                 # 5 指数 × 小时线，各出一张

输出：PNG 文件（默认 .cache/charts/chan_<指数>_<级别>_<日期>.png），并打印路径。
"""
import os, sys, socket, argparse
from datetime import datetime

WS = os.environ.get("TECH_WS") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]

import numpy as np
import pandas as pd
# ── MACD 口径：直接复用 chan_smc_index 的常量/函数（10/20/5），避免本地硬编码走偏 ──
from chan_smc_index import MACD_FAST, MACD_SLOW, MACD_SIGNAL  # noqa: E402
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib import font_manager

# 中文字体：显式注册字体文件（.ttc 按 family 名查找会失败，直接给文件最稳）
_FONT_FILES = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
]
_FAM_OK = []
for _f in _FONT_FILES:
    if os.path.exists(_f):
        try:
            font_manager.fontManager.addfont(_f)
            _FAM_OK.append(font_manager.FontProperties(fname=_f).get_name())
        except Exception:
            pass
matplotlib.rcParams["font.sans-serif"] = _FAM_OK + ["WenQuanYi Micro Hei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

import mplfinance as mpf
import chan_smc_index as C

OUT_DIR = os.path.join(WS, ".cache/charts")
LV_TXT = {"h1": "小时线60m", "day": "日线"}
VER = "缠论·分型版"


# ─────────── 缠论：带原始索引的合并K线 ───────────
def merge_bars_idx(bars):
    """包含处理，保留每条合并K线对应的原始索引（hi/lo 极值所在位置）。
    ⚠️ 逻辑必须与 chan_smc_index.merge_bars 完全一致，否则画出来的笔和判定口径对不上。
    """
    out = []   # [h, l, first_i, last_i, hi_i, lo_i]
    for i, (h, l) in enumerate(bars):
        if not out:
            out.append([h, l, i, i, i, i])
            continue
        ph, pl, _pf, _plast, phi, plo = out[-1]
        if (h <= ph and l >= pl) or (h >= ph and l <= pl):
            up = len(out) < 2 or out[-1][0] >= out[-2][0]
            if up:
                nhi = i if h > ph else phi
                nlo = i if l > pl else plo
                out[-1] = [max(ph, h), max(pl, l), out[-1][2], i, nhi, nlo]
            else:
                nhi = i if h < ph else phi
                nlo = i if l < pl else plo
                out[-1] = [min(ph, h), min(pl, l), out[-1][2], i, nhi, nlo]
        else:
            out.append([h, l, i, i, i, i])
    return out


def fractals_idx(mb):
    """顶底分型（与 chan_smc_index.fractals 同判据），返回 (merged_idx, type, price)"""
    fr = []
    for i in range(1, len(mb) - 1):
        a, b, c = mb[i - 1], mb[i], mb[i + 1]
        if b[0] > a[0] and b[0] > c[0] and b[1] > a[1] and b[1] > c[1]:
            fr.append((i, 1, b[0]))
        elif b[0] < a[0] and b[0] < c[0] and b[1] < a[1] and b[1] < c[1]:
            fr.append((i, -1, b[1]))
    return fr


def build_bi_idx(mb, fr):
    """成笔（与 build_bi 同判据：异类且间隔≥3根合并K线）"""
    bi = []
    for idx, t, px in fr:
        if not bi:
            bi.append([idx, t, px])
        elif bi[-1][1] == t:
            if (t == 1 and px > bi[-1][2]) or (t == -1 and px < bi[-1][2]):
                bi[-1] = [idx, t, px]
        else:
            if abs(idx - bi[-1][0]) >= 3:
                bi.append([idx, t, px])
    return bi


def chan_geometry(hb):
    """从 (high, low) 列算出一整套可绘图几何：笔端点 / 中枢 / 买卖点"""
    bars = list(zip(hb["high"].astype(float).values, hb["low"].astype(float).values))
    mb = merge_bars_idx(bars)
    fr = fractals_idx(mb)
    bi = build_bi_idx(mb, fr)
    # 合并K线索引 → 原始K线索引（顶取 hi_i，底取 lo_i）
    pts = []
    for midx, t, px in bi:
        oi = mb[midx][4] if t == 1 else mb[midx][5]
        pts.append({"orig": oi, "type": t, "price": px})
    segs = []
    for k in range(len(pts) - 1):
        a, b = pts[k], pts[k + 1]
        segs.append({"up": b["type"] == 1, "from": a["price"], "to": b["price"],
                     "lo": min(a["price"], b["price"]), "hi": max(a["price"], b["price"]),
                     "i0": a["orig"], "i1": b["orig"]})
    # 中枢：最近三段重叠
    zs, zs_i = None, None
    for i in range(len(segs) - 3, -1, -1):
        w = segs[i:i + 3]
        zg = min(x["hi"] for x in w)
        zd = max(x["lo"] for x in w)
        if zg > zd:
            zs = (round(zd, 2), round(zg, 2))
            zs_i = i
            break
    zs_x = None
    if zs_i is not None:
        zs_x = (segs[zs_i]["i0"], len(bars) - 1)
    # 买卖点（复用 chan_smc_index 的正式判定，保证和图一致）
    ck = C.chan_read(bars, "h1")
    return {"bars": bars, "mb": mb, "bi": pts, "segs": segs,
            "zs": zs, "zs_x": zs_x, "ck": ck}


# ─────────── 画图 ───────────
def draw(name, level="h1", n=120, out=None, show_macd=True):
    if level == "h1":
        # 直接用 minute_hub 取（要 volume；C.fetch_60 只返回 OHLC 四列）
        import minute_hub as mh
        df = mh.get_kline(C.IDX_SH[name], "60", n=400, kind="index",
                          cache=True, cache_ttl_min=10)
        if df is not None:
            df = df.rename(columns={"day": "Date"})
            if "volume" not in df.columns:
                df["volume"] = 0.0
    else:
        df = C.fetch_daily(dict((x[0], x[1]) for x in C.IDX)[name])
        if df is not None:
            df = df.rename(columns={"dt": "Date"})
            if "volume" not in df.columns:
                df["volume"] = df.get("vol", 0.0)
    if df is None or len(df) < 60:
        raise RuntimeError("%s 数据不足" % name)

    df = df[["Date", "open", "high", "low", "close", "volume"]].copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    # 图上只画最近 n 根，但**缠论用全量K线算**（不然笔/中枢会变），再按索引裁切
    total = len(df)
    full_geo = chan_geometry(df)
    start = max(0, total - n)
    view = df.iloc[start:].reset_index(drop=True)
    view = view.set_index("Date")          # mplfinance 要求 DatetimeIndex
    nview = len(view)

    def vx(orig_i):
        """原始索引 → 视图内的 x（mplfinance 用 0..N-1 整数位）"""
        return orig_i - start

    geo = chan_geometry(df)
    ck = geo["ck"]
    zs = geo["zs"]
    P = float(view["close"].iloc[-1])
    last_ts = view.index[-1]

    # MACD(10,20,5)：与 chan_smc_index.macd_hist 同口径（常量复用，勿硬编码）
    c = df["close"].astype(float)
    dif = (c.ewm(span=MACD_FAST, adjust=False).mean()
           - c.ewm(span=MACD_SLOW, adjust=False).mean())
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    hist = (2 * (dif - dea))
    macd_df = pd.DataFrame({"DIF": dif.values[start:], "DEA": dea.values[start:],
                            "HIST": hist.values[start:]}, index=view.index)

    aps = [
        mpf.make_addplot(macd_df["DIF"], panel=2, color="#e67e22", width=0.9, ylabel="MACD"),
        mpf.make_addplot(macd_df["DEA"], panel=2, color="#2980b9", width=0.9),
        mpf.make_addplot(macd_df["HIST"], panel=2, type="bar", color="#95a5a6", alpha=0.55),
    ]

    mc = mpf.make_marketcolors(up="#d62728", down="#2ca02c", edge="inherit",
                               wick="inherit", volume="in")
    style = mpf.make_mpf_style(base_mpf_style="yahoo", marketcolors=mc,
                               rc={"font.sans-serif": matplotlib.rcParams["font.sans-serif"],
                                   "axes.unicode_minus": False},
                               gridstyle=":", gridcolor="#dddddd")
    fig, axlist = mpf.plot(
        view, type="candle", style=style,
        volume=True, addplot=aps if show_macd else None,
        panel_ratios=(6, 1.6, 3) if show_macd else (6, 2),
        figsize=(16, 10), returnfig=True, xrotation=0,
        title="",
    )
    ax = axlist[0]

    # ── 笔（折线）──
    seg_x, seg_y = [], []
    for p in geo["bi"]:
        x = vx(p["orig"])
        if -2 <= x <= nview + 2:
            seg_x.append(x)
            seg_y.append(p["price"])
    if len(seg_x) >= 2:
        ax.plot(seg_x, seg_y, color="#1f4e79", lw=1.5, zorder=6, label="笔")

    # 分型端点
    for p in geo["bi"]:
        x = vx(p["orig"])
        if -1 <= x <= nview:
            ax.scatter([x], [p["price"]], s=26, zorder=7,
                       color=("#d62728" if p["type"] == 1 else "#2ca02c"),
                       marker=("v" if p["type"] == 1 else "^"))

    # ── 中枢矩形 ──
    if zs and geo["zs_x"]:
        x0, x1 = vx(geo["zs_x"][0]), vx(geo["zs_x"][1])
        x0 = max(x0, -0.5)
        if x1 >= -1:
            ax.add_patch(Rectangle((x0, zs[0]), x1 - x0, zs[1] - zs[0],
                                   facecolor="#ffd24d", alpha=0.22,
                                   edgecolor="#b8860b", lw=1.4, zorder=3))
            ax.axhline(zs[1], color="#b8860b", lw=0.9, ls="--", alpha=0.75, zorder=4)
            ax.axhline(zs[0], color="#b8860b", lw=0.9, ls="--", alpha=0.75, zorder=4)
            ax.text(max(x0, 0), zs[1], " ZG %.2f" % zs[1], va="bottom", ha="left",
                    fontsize=9, color="#8b6508", zorder=8)
            ax.text(max(x0, 0), zs[0], " ZD %.2f" % zs[0], va="top", ha="left",
                    fontsize=9, color="#8b6508", zorder=8)
            ax.text(max(x0, 0) + 0.4, (zs[0] + zs[1]) / 2, "中枢", va="center",
                    ha="left", fontsize=12, color="#8b6508", alpha=0.85, zorder=8)

    # ── 买点 / 卖点 ──
    # ⚠️ matplotlib marker 必须用符号名：买点="*"，卖点="v"（不能用中文 ★ ▼）
    BUY_STYLE = {"一买": ("*", "#0b6e0b", 420), "二买": ("*", "#1a9850", 300),
                 "三买": ("*", "#4da34d", 220)}
    SELL_STYLE = {"一卖": ("v", "#8b0000", 260), "二卖": ("v", "#d73027", 200),
                  "三卖": ("v", "#f46d43", 160)}
    for kind, lst, styles, dy in (("买", ck.get("buy_list") or [], BUY_STYLE, -1),
                                  ("卖", ck.get("sell_list") or [], SELL_STYLE, 1)):
        for b in lst:
            tag = b["tag"]
            if tag not in styles:
                continue
            mk, col, sz = styles[tag]
            # 找最近的笔端点作为 x 位置
            price = b["level"]
            cand = [p for p in geo["bi"] if p["type"] == (1 if kind == "卖" else -1)]
            if not cand:
                continue
            pt = min(cand, key=lambda p: abs(p["price"] - price))
            x = vx(pt["orig"])
            if not (-1 <= x <= nview):
                continue
            ax.scatter([x], [pt["price"]], s=sz, marker=mk, color=col,
                       edgecolor="white", linewidth=1.2, zorder=10)
            # 标注自动避让：默认买点标下方、卖点标上方；贴到图边就翻到另一侧
            y0, y1 = ax.get_ylim()
            span = y1 - y0
            d = dy
            if dy < 0 and pt["price"] - span * 0.14 < y0:
                d = 1          # 太低 → 改标上方
            elif dy > 0 and pt["price"] + span * 0.14 > y1:
                d = -1         # 太高 → 改标下方
            ax.annotate("%s %.2f\n(%d根前)" % (tag, price, b["age"]),
                        xy=(x, pt["price"]),
                        xytext=(0, 30 * d), textcoords="offset points",
                        ha="center", va=("top" if d < 0 else "bottom"),
                        fontsize=9, color=col, fontweight="bold", zorder=11,
                        bbox=dict(boxstyle="round,pad=0.28", fc="white",
                                  ec=col, alpha=0.92, lw=0.9))

    # ── 标题 / 说明 ──
    stage = ck.get("stage", "")
    div = ck.get("div") or "无"
    zpos_txt = {"in": "中枢内", "above": "中枢上方", "below": "中枢下方"}.get(ck.get("zpos"), "-")
    lv = LV_TXT.get(level, level)
    ts_txt = last_ts.strftime("%Y-%m-%d %H:%M") if level == "h1" else last_ts.strftime("%Y-%m-%d")
    fig.suptitle("%s · %s ｜ 级别：%s ｜ 版本：%s" % (name, ts_txt, lv, VER),
                 fontsize=16, fontweight="bold", y=0.975)
    sub = ("收盘 %.2f ｜ 阶段：%s ｜ 中枢位置：%s ｜ 中枢 %.2f~%.2f ｜ 背驰：%s"
           % (P, stage, zpos_txt, zs[0], zs[1], div)) if zs else \
          ("收盘 %.2f ｜ 阶段：%s ｜ 背驰：%s" % (P, stage, div))
    fig.text(0.5, 0.938, sub, ha="center", fontsize=11.5)
    fig.text(0.5, 0.912,
             "口径：包含处理→顶底分型→笔→中枢(ZG/ZD)→背驰　｜　★买点 ▼卖点　黄框=最近中枢　蓝线=笔",
             ha="center", fontsize=9.5, color="#555555")

    ax.set_ylabel("价格")
    if show_macd:
        axlist[-2].set_ylabel("MACD")
    ax.legend(loc="upper left", fontsize=9, framealpha=0.85)

    os.makedirs(OUT_DIR, exist_ok=True)
    if not out:
        d8 = datetime.now().strftime("%Y%m%d")
        out = os.path.join(OUT_DIR, "chan_%s_%s_%s.png" % (name, level, d8))
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="中证500", choices=[x[0] for x in C.IDX])
    ap.add_argument("--level", default="h1", choices=["h1", "day"])
    ap.add_argument("--n", type=int, default=120, help="图上显示最近多少根K线（判定仍用全量）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--all", action="store_true", help="5 指数小时线各出一张")
    a = ap.parse_args()

    if a.all:
        paths = []
        for nm, _c in C.IDX:
            try:
                p = draw(nm, a.level, a.n)
                print("[OK]", p, flush=True)
                paths.append(p)
            except Exception as e:
                print("[ERR]", nm, str(e)[:200], flush=True)
        return 0 if paths else 1

    p = draw(a.index, a.level, a.n, a.out)
    print("[OK]", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
