#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技术分析汇总 · 单一标的四层合一（日线 + 小时线）· 报告 + 双图 + 飞书推送
=====================================================================
把本机四套技术分析口径一次性跑完并出图（纯程序，0 token）：

  ① 缠论·分型版   （包含处理→分型→笔→中枢→背驰；日线认一二三买；小时线只认三买 及 一买+金叉）
  ② 缠论·均线版   （趋势浪子原文：笔=MA5/10双穿MA20，大级别=价格 vs MA160）
  ③ SMC 结构      （摆动点→结构突破→FVG/OB→方向确认；日线 k=3，小时线 k=1）
  ④ VWAP 六轨     （VWAP ±1σ/±2σ/±3σ，量加权，窗口 60 根，右侧标正态分位）

标的可以是**指数**也可以是**股票**（大佬 2026-11-09：「不单单是中证500，将来可能分析指数、也可能分析股票」）

用法：
  python3 bin/tech_summary.py --target 中证500                  # 指数（5 个宽基之一）
  python3 bin/tech_summary.py --target 600519                   # 股票（自动判沪深）
  python3 bin/tech_summary.py --target 600519 --push            # 生成 + 推飞书卡片（含双图）
  python3 bin/tech_summary.py --target 中证500 --push --chat oc_xxx
  python3 bin/tech_summary.py --target 000905.SH --no-chart
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime
from math import erf
from math import sqrt as _sqrt

# ── 全局硬规矩：禁 IPv6（容器 IPv6 不通会静默卡死）──
_orig_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig_gai(*a, **k) if r[0] == socket.AF_INET]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WS, "bin"))
sys.path.insert(0, os.path.join(WS, "smc"))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

_FONT_FILES = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
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

import chan_smc_index as C          # 分型版核心 + 数据  # noqa: E402
import chan_ma_trend_v2 as M        # 均线版  # noqa: E402
import smc_core as S                # SMC FVG/OB/VWAP  # noqa: E402
import datahub as DH                # 股票/指数三级日线兜底  # noqa: E402
from chan_chart import chan_geometry  # 笔/分型几何（与画图口径一致）  # noqa: E402

OUT_DIR = os.path.join(WS, ".cache/charts")
RED, GRN, BLUE, ORANGE = "#d62728", "#2ca02c", "#1f4e79", "#e67e22"
NAME_CACHE = os.path.join(WS, ".cache/tech_stock_names.json")


def _norm_cdf(z):
    return 0.5 * (1.0 + erf(z / _sqrt(2.0)))


# ══════════════════════════ 月亮/星星标记（大佬 2026-11-09 指定）══════════════════════════
# 买点 = 绿色月亮 🌙（U+263E 用 FreeSerif 的月牙字形，系统无 emoji 彩色字体能进 matplotlib）
# 卖点 = 红色星星 ⭐（U+2605 实心星）
_GLYPH_FONT = None
for _p in ("/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
           "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf"):
    if os.path.exists(_p):
        _GLYPH_FONT = _p
        break
_GLYPH_CACHE = {}


def glyph_marker(ch):
    """把字符转成 matplotlib marker（Path）；字体缺失时退回 "*" / "v"""
    if ch in _GLYPH_CACHE:
        return _GLYPH_CACHE[ch]
    mk = "*"
    if _GLYPH_FONT:
        try:
            from matplotlib.textpath import TextPath
            from matplotlib.font_manager import FontProperties
            from matplotlib.path import Path as _MplPath
            tp = TextPath((0, 0), ch, size=1, prop=FontProperties(fname=_GLYPH_FONT))
            e = tp.get_extents()
            v = tp.vertices.copy()
            v[:, 0] -= (e.x0 + e.x1) / 2.0
            v[:, 1] -= (e.y0 + e.y1) / 2.0
            v /= max(e.width, e.height, 1e-9)
            mk = _MplPath(v, tp.codes)
        except Exception:
            mk = "*"
    _GLYPH_CACHE[ch] = mk
    return mk


MOON = glyph_marker("\u263e")      # 🌙 买点（绿）
STAR = glyph_marker("\u2605")      # ⭐ 卖点（红）
# 均线用**浅色调**，把深色留给结构线（笔/线段），否则橙色笔和 MA10 撞色看不清
#     ⚠️ 2026-11-09 大佬：MA5 在图上**不画**（太乱，和笔/MA10 全挤在一起）
#        ——只是不画！MA5 仍然是笔的定义要素（MA5/MA10 双穿 MA20），计算里必须保留
MA_PALETTE = {5: "#ffd8a8", 10: "#ffc078", 20: "#8fd6c7",
              40: "#a8c7dd", 80: "#c3b1e1", 160: "#bdbdbd"}
MA_DRAW = (10, 20)          # **只控制绘图**：图上画哪几条均线（MA5 画着太乱，不画）
#     ⚠️ 切不能因为「不画」就把 MA5 从 MAS / add_ind 里删掉 —— 笔的定义是 MA5/MA10 双穿 MA20
MA_LW = {5: 1.0, 10: 1.0, 20: 1.8, 40: 1.1, 80: 1.2, 160: 1.8}
MA_ALPHA = 0.95
# 均线版子图窗口（根）：线段/走势类型是大级别结构，120 根里只有 1-2 段，看不出来
MA_PANEL_BARS = int(os.environ.get("TECH_MA_PANEL_BARS", "400"))


# ══════════════════════════ 标的解析 ══════════════════════════
IDX_MAP = {nm: sh for nm, sh in C.IDX_SH.items()}        # 中文名 → sh000905
IDX_TS = dict(C.IDX)                                      # 中文名 → 000905.SH

# 其它常用指数（不在 C.IDX 里的，补一份中文名 → ts_code）
EXTRA_IDX = {
    "上证指数": "000001.SH", "上证综指": "000001.SH", "上证50": "000016.SH",
    "深证成指": "399001.SZ", "中证100": "000903.SH", "中证200": "000904.SH",
    "中证800": "000906.SH", "中证红利": "000922.CSI", "中证全指": "000985.CSI",
    "北证50": "899050.BJ",
}
for _k, _v in EXTRA_IDX.items():
    IDX_TS.setdefault(_k, _v)


def stock_name(ts_code):
    """股票中文名（带缓存，失败返回空串）"""
    try:
        m = json.load(open(NAME_CACHE, encoding="utf-8"))
    except Exception:
        m = {}
    if ts_code in m:
        return m[ts_code]
    try:
        import tushare_init as ti
        df = ti.get_pro().stock_basic(ts_code=ts_code, fields="ts_code,name")
        if df is not None and len(df):
            m[ts_code] = str(df["name"].iloc[0])
            os.makedirs(os.path.dirname(NAME_CACHE), exist_ok=True)
            json.dump(m, open(NAME_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
            return m[ts_code]
    except Exception:
        pass
    return ""


def resolve(target):
    """把用户给的目标解析成 {kind, name, sina, ts, disp}

    支持：中证500 等 5 个中文宽基名 / 000905.SH / sh000905 / 600519 / SZ000001 / 600519.SH
    """
    t = str(target).strip()
    if t in IDX_TS:
        ts = IDX_TS[t]
        return {"kind": "index", "name": t, "sina": DH.to_sina(ts) if "." in ts else t,
                "ts": ts, "disp": t}
    s = DH.norm(t)
    if DH.is_index(s):                       # 000905.SH
        code = s.split(".")[0]
        nm = next((k for k, v in IDX_TS.items() if v == s), None)
        disp = nm or s
        return {"kind": "index", "name": disp, "sina": DH.to_sina(s),
                "ts": s, "disp": disp + ("（%s）" % s if nm else "")}
    # 股票
    ts = DH.to_ts(s)
    nm = stock_name(ts)
    disp = ("%s %s" % (ts.split(".")[0], nm)).strip() if nm else ts.split(".")[0]
    return {"kind": "stock", "name": nm or ts.split(".")[0], "sina": DH.to_sina(s),
            "ts": ts, "disp": disp}


# ══════════════════════════ 数据 ══════════════════════════
def _rows_to_df(rows):
    if not rows:
        return None
    d = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"])
    d["dt"] = pd.to_datetime(d["dt"], format="%Y%m%d")
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna(subset=["close"]).sort_values("dt").reset_index(drop=True)


def load_day(tg):
    """日线：指数先走 tushare index_daily（chan_smc_index 缓存），失败或过短则 datahub 三级兜底；
    股票直接走 datahub 三级灾备"""
    if tg["kind"] == "index" and tg["ts"] in set(IDX_TS.values()):
        df = C.fetch_daily(tg["ts"])
        if df is not None and len(df) >= 200:
            d = df.copy()
            d["dt"] = pd.to_datetime(d["dt"])
            if "volume" not in d.columns:
                d["volume"] = d.get("vol", 0.0)
            return d.sort_values("dt").reset_index(drop=True)
    cache = os.path.join(WS, ".cache/tech_daily", tg["ts"].replace(".", "_").replace("/", "_") + ".csv")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    fresh = False
    try:
        fresh = (time.time() - os.path.getmtime(cache) < 6 * 3600)
    except Exception:
        pass
    if fresh:
        d = pd.read_csv(cache, parse_dates=["dt"])
        if len(d) > 200:
            return d
    rows = DH.daily(tg["ts"], days=1200)
    d = _rows_to_df(rows)
    if d is None or len(d) < 200:
        if os.path.exists(cache):
            d2 = pd.read_csv(cache, parse_dates=["dt"])
            if len(d2) > 200:
                return d2
        raise RuntimeError("日线数据不足（%d 根）" % (0 if d is None else len(d)))
    try:
        d.to_csv(cache, index=False)
    except Exception:
        pass
    return d


def load_h1(tg, n=400):
    """小时线 60m：minute_hub（带 volume）

    ⚠️ minute_hub 的磁盘缓存 key **不含 n**：之前用 n=60 取过一次，
       n=400 时会直接返回那份 60 根的旧缓存 → 误判「数据不足」。
       所以长度不够时必须强制刷新（cache=False）再取一次。
    """
    import minute_hub as mh
    kd = "index" if tg["kind"] == "index" else "stock"
    df = mh.get_kline(tg["sina"], "60", n=n, kind=kd, cache=True, cache_ttl_min=10)
    if df is None or len(df) < 120:
        df = mh.get_kline(tg["sina"], "60", n=n, kind=kd, cache=False)
    if df is None or len(df) < 120:
        raise RuntimeError("小时线数据不足（%d 根）" % (0 if df is None else len(df)))
    d = df.rename(columns={"day": "dt"}).copy()
    d["dt"] = pd.to_datetime(d["dt"])
    if "volume" not in d.columns:
        d["volume"] = 0.0
    return d[["dt", "open", "high", "low", "close", "volume"]].sort_values("dt").reset_index(drop=True)


# ══════════════════════════ 四层计算 ══════════════════════════
def chan_fx(df, level):
    bars = list(zip(df["high"].astype(float).values, df["low"].astype(float).values))
    return C.chan_read(bars, level)


def smc_layer(df, k, atr_n=14):
    at = S.atr(df, n=atr_n)
    hi, lo = S.pivots(df, k)
    fvgs = S.find_fvgs(df, at.values, lookback=S.FVG_LOOKBACK_FAR, end=len(df) - 1)
    obs = S.find_order_blocks(df, hi, lo, at.values, k=k, end=len(df) - 1,
                              lookback_far=S.OB_LOOKBACK_FAR)
    av = float(at.iloc[-1]) if np.isfinite(at.iloc[-1]) else None
    sigs = S.pick_signals(fvgs, obs, df, atr_val=av, max_n=4)
    return {"hi": hi, "lo": lo, "fvgs": fvgs, "obs": obs, "sigs": sigs, "atr": at, "k": k}


def vwap_layer(df):
    return S.vwap_tracks(df)


# ══════════════════════ 买点优先级（大佬 2026-11-09 定稿）══════════════════════
#   「我的优先是三买，然后是一买。一买是必须 MA20 均线向上。」
#   → 三买：优先级 1（离开中枢后回踩不破 ZG，结构确认度最高）
#   → 一买：优先级 2，**必须 MA20 向上**，否则不认
#   → 二买：优先级 3，**2026-11-10 大佬恢复（不再丢弃）**，按闸门接受
BUY_PRIORITY = {"三买": 1, "一买": 2, "二买": 3}
PRI_MARK = {1: "①三买", 2: "②一买", 3: "③二买"}
MA20_UP_BARS = int(os.environ.get("TECH_MA20_UP_BARS", "5"))   # MA20 斜率回看根数（一买向上）
MA20_DN_BARS = int(os.environ.get("TECH_MA20_DN_BARS", "5"))   # MA20 斜率回看根数（一卖向下，2026-11-10）
# ── 买点有效性闸门（与 bin/tech_buypoint.py 同一口径，2026-11-09 修复）──
#   ❗事故：风语筑 603466，日线只有「一买 8.78（116 根前）」，现价 13.94 已跑远 58.7%，
#      tech_summary 却输出「→ 可用买点：②一买 @8.78」提示买点。
#   根因：rank_buys 只看 MA20 向上，没有“年龄/距离/结构未破”闸门，
#          而扫描器 tech_buypoint._gate_y1 有 → 两边不一致。
#   → 这里补齐与 tech_buypoint 完全一致的闸门（环境变量同名同默认）。
BUY_NEAR_UP = float(os.environ.get("SCAN_BUY_NEAR_UP", "10"))   # 现价高于买点多少%以内算还在买点
BUY_NEAR_DN = float(os.environ.get("SCAN_BUY_NEAR_DN", "3"))    # 现价低于买点多少%以内算刚破
YB_MAX_AGE = int(os.environ.get("SCAN_YB_MAX_AGE", "40"))        # 一买最大年龄（根）
TB_MAX_AGE = int(os.environ.get("SCAN_TB_MAX_AGE", "90"))        # 三买最大年龄（根）
TB_NEAR = float(os.environ.get("SCAN_TB_NEAR", "10"))            # 三买：现价高于回踩低点%以内
# ── 二买闸门（2026-11-10 与 tech_buypoint._gate_y2 同口径）──
#   一买 **必须 MA20 向上**；二买 **MA20 向上仅加分、不作硬门槛**（回踩阶段均线常未拐头）
SCAN_EB_MAX_AGE = int(os.environ.get("SCAN_EB_MAX_AGE", "60"))    # 二买最大年龄（根）
SCAN_EB_NEAR = float(os.environ.get("SCAN_EB_NEAR", "10"))        # 二买：现价高于二买低点%以内


def ma20_state(df, k=None):
    """MA20 方向（一买须向上 / 一卖须向下，均为硬门槛）

    返回 up / down / slope（up 用 k_up 根，down 用 k_dn 根，默认都 5）：
      · up   = slope_up > 0（一买硬门槛）
      · down = slope_dn < 0（**一卖硬门槛**，2026-11-10 大佬口径：对称于一买）
    注意：down 用 slope < 0 直接判，不用 `not up` —— slope == 0 时两者都不算（走平不认）。
    """
    k_up = k or MA20_UP_BARS
    k_dn = k or MA20_DN_BARS
    c = df["close"].astype(float)
    need = 20 + max(k_up, k_dn) + 1
    if len(c) < need:
        return {"ok": False, "up": False, "down": False, "slope": 0.0,
                "slope_dn": 0.0, "ma20": None}
    ma20 = c.rolling(20).mean()
    cur = float(ma20.iloc[-1])
    prev = float(ma20.iloc[-1 - k_up])
    slope = (cur - prev) / prev * 100.0 if prev else 0.0
    prev_dn = float(ma20.iloc[-1 - k_dn])
    slope_dn = (cur - prev_dn) / prev_dn * 100.0 if prev_dn else 0.0
    return {"ok": True, "up": slope > 0, "down": slope_dn < 0,
            "slope": slope, "slope_dn": slope_dn, "ma20": cur, "k": k_up}


def rank_buys(buy_list, ma20, close=None):
    """按大佬口径给买点定级：三买 > 一买(须 MA20 向上) > 二买(仅参考)

    ⚠️ 除优先级外，**必须过有效性闸门**（与 tech_buypoint 同一口径）：
       ① 买点年龄（一买 ≤ YB_MAX_AGE 根 / 三买 ≤ TB_MAX_AGE 根）
       ② 距离（现价高于买点 ≤ BUY_NEAR_UP%、低于 ≤ BUY_NEAR_DN%）
       ③ 一买另需 MA20 向上
    不过闸门的 → accept=False（报告里只能算「见过但被否」）。
    close 不传则用 ma20 里的现价参考位；都没有则跳过距离闸门。

    返回每个买点附带 pri(1/2/3) / accept(是否算可买) / why(不认原因)；已排序。
    """
    out = []
    up = bool(ma20.get("up"))
    for b in (buy_list or []):
        tag = b.get("tag")
        pri = BUY_PRIORITY.get(tag, 9)
        accept, why = True, ""
        lvl = b.get("level")
        age = b.get("age")
        gap = None
        if close is not None and lvl:
            gap = (float(close) / float(lvl) - 1) * 100.0
        if tag == "一买":
            if not up:
                accept = False
                why = "MA20 未向上（斜率 %+.2f%%）→ 一买不认" % (ma20.get("slope") or 0.0)
            elif age is not None and age > YB_MAX_AGE:
                accept = False
                why = "买点太老（%d根前 > %d）→ 一买不认" % (age, YB_MAX_AGE)
            elif gap is not None and gap > BUY_NEAR_UP:
                accept = False
                why = "已跑远（现价比一买高 %.1f%% > %d%%）→ 一买不认" % (gap, BUY_NEAR_UP)
            elif gap is not None and gap < -BUY_NEAR_DN:
                accept = False
                why = "已跌破一买 %.1f%% → 一买不认" % abs(gap)
            else:
                why = "MA20 向上 %+.2f%% ✓%s" % (
                    ma20.get("slope") or 0.0,
                    ("、现价距买点 %+.1f%%" % gap) if gap is not None else "")
        elif tag == "二买":
            # 2026-11-10 大佬：二买恢复，按闸门接受（pri=3，最低优先级）
            #   闸门与 tech_buypoint._gate_y2 一致：年龄 / 未跌破 3% / 距买点 ≤ SCAN_EB_NEAR%
            #   ❗MA20 向上仅加分，不作硬门槛（与一买严格区分）
            ma_bit = (("MA20 向上 %+.2f%% ✓" % (ma20.get("slope") or 0.0)) if up
                      else ("MA20 未向上 %+.2f%%（二买仅加分）" % (ma20.get("slope") or 0.0)))
            if age is not None and age > SCAN_EB_MAX_AGE:
                accept = False
                why = "买点太老（%d根前 > %d）→ 二买不认" % (age, SCAN_EB_MAX_AGE)
            elif gap is not None and gap < -BUY_NEAR_DN:
                accept = False
                why = "已跌破二买 %.1f%%（结构破坏）→ 二买不认" % abs(gap)
            elif gap is not None and gap > SCAN_EB_NEAR:
                accept = False
                why = "已跑远（现价比二买高 %.1f%% > %d%%）→ 二买不认" % (gap, SCAN_EB_NEAR)
            else:
                why = "回踩不破一买低点（二买）｜ %s%s" % (
                    ma_bit, ("、现价距买点 %+.1f%%" % gap) if gap is not None else "")
        elif tag == "三买":
            if age is not None and age > TB_MAX_AGE:
                accept = False
                why = "买点太老（%d根前 > %d）→ 三买不认" % (age, TB_MAX_AGE)
            elif gap is not None and gap > TB_NEAR:
                accept = False
                why = "已跑远（现价比回踩低点高 %.1f%% > %d%%）→ 三买不认" % (gap, TB_NEAR)
            elif gap is not None and gap < -BUY_NEAR_DN:
                accept = False
                why = "已跌破回踩低点 %.1f%% → 三买不认" % abs(gap)
            else:
                why = "结构已确认（离开中枢后回踩不破 ZG）%s" % (
                    ("、现价距回踩低点 %+.1f%%" % gap) if gap is not None else "")
        out.append(dict(b, pri=pri, accept=accept, why=why))
    out.sort(key=lambda x: (not x["accept"], x.get("pri", 9), x.get("age", 0)))
    return out


def best_buy(ranked):
    """可买的最高优先级买点（没有则 None）"""
    for b in ranked:
        if b.get("accept"):
            return b
    return None


def ma_layer(df, level="day"):
    """缠论·均线版：直接在本机 df 上跑（指数和股票通用，不依赖 load_index）

    level="day" 原样跑；"week" 先重采样周线；"h1" 原样跑（小时线本来就是当前级别）
    """
    d = M.resample_week(df) if level == "week" else df
    if len(d) < M.BIG_MA + 10:
        return None
    d = M.add_ind(d)
    last = d.iloc[-1]
    bi = M.build_bi(d)
    return {"dt": str(d["dt"].iloc[-1])[:10], "close": float(last["close"]),
            "n": len(d), "loc": M.level_locate(d), "sigs": M.signals(d),
            "bi_n": len(bi), "bi_last": bi[-1] if bi else None,
            "macd": M.macd_state(d), "hist": float(last["hist"]),
            "mode": M.trend_mode(d), "beichi": M.macd_bei_chi(d, bi),
            "align": M.ma_alignment(d), "align_short": M.short_alignment(d),
            "walk": M.bi_walk(d)}


# ══════════════════════════ 文本报告 ══════════════════════════
def render_text(disp, day, h1, fx_d, fx_h, smc_d, smc_h, vw_d, vw_h, ma_d, today_pct,
                ma_h=None, ma20_d=None):
    L = []
    A = L.append
    ma20_d = ma20_d or ma20_state(day)
    A("📐 技术分析汇总 · %s · %s" % (disp, datetime.now().strftime("%Y-%m-%d %H:%M")))
    A("口径：①缠论分型版 ②缠论均线版 ③SMC(FVG/OB) ④VWAP 六轨（±1σ/±2σ/±3σ）")
    P = float(day["close"].iloc[-1])
    A("当前 %s 收 %.2f（今日 %+.2f%%）｜ 日线 %d 根 / 小时线 %d 根"
      % (str(day["dt"].iloc[-1])[:10], P, today_pct, len(day), len(h1)))
    A("")

    # ── 一、结论 ──
    A("═══ 一、结论（先看这里）═══")
    concl = []
    if fx_d.get("ok"):
        st = fx_d.get("stage", "-")
        if fx_d.get("zpos") == "below":
            concl.append("日线在中枢下方（趋势破坏），分型版阶段=%s" % st)
        elif fx_d.get("zpos") == "above":
            concl.append("日线已离开中枢向上，阶段=%s" % st)
        else:
            concl.append("日线在中枢内震荡，阶段=%s" % st)
        if fx_d.get("div"):
            concl.append("日线%s" % fx_d["div"])
    if fx_h.get("ok"):
        zg = fx_h.get("zg")
        concl.append("小时线阶段=%s%s" % (fx_h.get("stage", "-"),
                                       ("（距中枢上沿还差 %.2f%%）" % ((zg - P) / P * 100)
                                        if zg and P < zg else "")))
    if ma_d:
        concl.append("均线版：%s" % M.tradeable(ma_d))
        _wk = ma_d.get("walk") or {}
        if _wk.get("ok"):
            concl.append("均线版当前位置：%s → %s" % (_wk["where"], _wk.get("advice")))
    if smc_d["sigs"]:
        concl.append("SMC日线：%s" % smc_d["sigs"][0]["label"])
    vw = vw_d["vwap"][-1]
    if np.isfinite(vw) and vw_d["sigma"][-1] > 0:
        z = (P - vw) / vw_d["sigma"][-1]
        concl.append("价格在 VWAP %+.2fσ（分位 %.1f%%，VWAP %.2f，MA40 %s）"
                     % (z, _norm_cdf(z) * 100, vw,
                        {"up": "向上", "dn": "向下", "flat": "走平"}[vw_d["ma_dir"][-1]]))
    for i, c in enumerate(concl, 1):
        A("  %d) %s" % (i, c))
    A("")

    # ── 二、缠论·分型版 ──
    A("═══ 二、缠论·分型版（结构派）═══")
    A("  口径：包含处理→顶底分型→笔→中枢(ZG/ZD)→背驰 ｜ 日线认一二三买，小时线只认三买 及 一买+MA10金叉")
    A("── 日线 ──")
    if fx_d.get("ok"):
        A("  阶段：%s ｜ 中枢位置：%s" % (fx_d.get("stage", "-"),
                                    {"in": "中枢内", "above": "中枢上方",
                                     "below": "中枢下方", None: "无中枢"}.get(fx_d.get("zpos"), "-")))
        if fx_d.get("zs"):
            A("  中枢：ZD %.2f ~ ZG %.2f" % fx_d["zs"])
        A("  背驰：%s%s" % (fx_d.get("div") or "无",
                          ("（再跌 %.2f%% 破前低才谈底背驰）" % fx_d["div_dist"])
                          if not fx_d.get("div") and fx_d.get("div_dist") is not None else ""))
        # ⚠️ 买点口径统一：三买必须走「遍历所有中枢」的扫描
        #    chan_read 只在最后一个中枢上找三买 → 风语筑实测漏掉昨天刚出的三买 13.30
        #    但 chan_read 的一买仍保留（一买是底背驰口径，scan 里没有替代实现）
        b = list(fx_d.get("buy_list") or [])   # 2026-11-10：二买恢复，不再过滤（三买下面会被扫描结果替换）
        try:
            import tech_buypoint as _TB
            _cl = day["close"].astype(float).values
            # ⚠️ find_third_buys 已经做了「年龄/距离/结构/现价>ZG」全套闸门，accept 就是结论
            #    不要再额外用 MAX_AGE 过滤（它没有 fresh 字段，会被误杀）
            _tb = [x for x in _TB.find_third_buys(day, _cl) if x.get("accept")]
            if _tb:
                # 用扫描得到的「新鲜三买」替换 chan_read 的三买（chan_read 的三买常是几百根前的老货）
                b = [x for x in b if x.get("tag") != "三买"] + _tb
        except Exception:
            pass
        s = fx_d.get("sell_list") or []
        A("  买点：" + ("、".join("%s %.2f（%d根前%s）" % (x["tag"], x["level"], x["age"],
                                                     "·长期未破" if x.get("sustained") else "")
                                for x in b[:4]) if b else "无"))
        # 🌙 买点优先级（三买 > 一买，一买须 MA20 向上 + 年龄/距离闸门）
        rk = rank_buys(b, ma20_d, close=float(day["close"].iloc[-1]))
        bb = best_buy(rk)
        A("  MA20：%.2f ｜ 近%d根斜率 %+.2f%% → %s"
          % (ma20_d.get("ma20") or 0, MA20_UP_BARS, ma20_d.get("slope") or 0.0,
             "向上 ✓" if ma20_d.get("up") else "未向上 ✗（一买不认）"))
        if rk:
            A("  🌙 买点优先级：" + " ｜ ".join(
                "%s%s %.2f%s" % (PRI_MARK.get(x["pri"], x["tag"]), "✓" if x["accept"] else "✗",
                                 x["level"], ("（%s）" % x["why"] if x["why"] else ""))
                for x in rk[:3]))
            A("  → 可用买点：%s" % (("%s @%.2f（%d根前）" % (PRI_MARK.get(bb["pri"], bb["tag"]),
                                                          bb["level"], bb["age"]))
                                if bb else "❌ 无（三买 ≤%d根 / 一买 MA20向上+≤%d根 / 二买 ≤%d根，且均未跑远）"
                                % (TB_MAX_AGE, YB_MAX_AGE, SCAN_EB_MAX_AGE)))
        else:
            A("  🌙 买点优先级：无")
        A("  卖点：" + ("、".join("%s %.2f（%d根前%s）" % (x["tag"], x["level"], x["age"],
                                                     "·长期未破" if x.get("sustained") else "")
                                for x in s[:4]) if s else "无"))
        # ⛔ 卖点正式判定（2026-11-10 新增：三卖 / 一卖 / 二卖 过闸门）
        try:
            import tech_buypoint as _TBS
            _sr = _TBS.find_sells(day, fx_d, float(day["close"].iloc[-1]), ma20_d)
            if _sr:
                # 展示顺序：先能用的（accept），再每个 tag 补一条被否样本（保证「一卖 MA20 未向下」这类关键
                # 拒绝原因不会被前面的三卖挤掉；实测 300291 前 3 条都是三卖 → 一卖被截看不见）
                _show = [x for x in _sr if x["accept"]][:3]
                _seen_tag = {x["tag"] for x in _show}
                for x in _sr:
                    if not x["accept"] and x["tag"] not in _seen_tag:
                        _show.append(x)
                        _seen_tag.add(x["tag"])
                _show = _show[:4]
                A("  ⛔ 卖点优先级：" + " ｜ ".join(
                    "%s%s %.2f（%d根前，现价距卖点 %+.1f%%）%s" % (
                        {"三卖": "①三卖", "一卖": "②一卖", "二卖": "③二卖"}.get(x["tag"], x["tag"]),
                        "✓" if x["accept"] else "✗", x["level"], x.get("age", 0), x.get("gap", 0),
                        ("（%s）" % x["why"]) if (not x["accept"] and x.get("why")) else "")
                    for x in _show))
                _bs = next((x for x in _sr if x["accept"]), None)
                A("  → 当前卖点：%s" % (("%s @%.2f（%d根前）" % ({"三卖": "①三卖", "一卖": "②一卖",
                                                          "二卖": "③二卖"}.get(_bs["tag"], _bs["tag"]),
                                                        _bs["level"], _bs["age"]))
                                   if _bs else "✅ 无有效卖点"))
            else:
                A("  ⛔ 卖点优先级：无")
        except Exception as _e:
            A("  ⛔ 卖点判定：跳过（%s）" % str(_e)[:40])
    else:
        A("  （数据不足）")
    A("── 小时线 · 60m ──")
    if fx_h.get("ok"):
        A("  阶段：%s ｜ 中枢位置：%s" % (fx_h.get("stage", "-"),
                                    {"in": "中枢内", "above": "中枢上方",
                                     "below": "中枢下方", None: "无中枢"}.get(fx_h.get("zpos"), "-")))
        if fx_h.get("zs"):
            A("  中枢：ZD %.2f ~ ZG %.2f ｜ 现价距 ZG %.2f%%"
              % (fx_h["zs"][0], fx_h["zs"][1], (fx_h["zs"][1] - P) / P * 100))
        m10 = h1["close"].rolling(10).mean().values
        m20 = h1["close"].rolling(20).mean().values
        golden = bool(np.isfinite(m10[-1]) and np.isfinite(m20[-1]) and m10[-1] > m20[-1])
        keep = []
        # ⚠️ 小时线也要过年龄/距离闸门（与 tech_buypoint.h1_buys 同口径），
        #    否则小时线也会把“几百根前的老买点”当现价买点提示（风语筑事故同类）
        _h1_tb_age = int(os.environ.get("SCAN_H1_TB_MAX_AGE", "120"))
        _h1_tb_near = float(os.environ.get("SCAN_H1_TB_NEAR", "8"))
        _h1_yb_age = int(os.environ.get("SCAN_H1_YB_MAX_AGE", "60"))
        for x in (fx_h.get("buy_list") or []):
            _gap = (P / float(x["level"]) - 1) * 100 if x.get("level") else 0.0
            if x["tag"] == "三买":
                if x["age"] > _h1_tb_age:
                    keep.append("三买 %.2f（%d根前）✗太老" % (x["level"], x["age"]))
                elif _gap > _h1_tb_near:
                    keep.append("三买 %.2f（%d根前）✗已跑远 %.1f%%"
                                % (x["level"], x["age"], _gap))
                else:
                    keep.append("三买 %.2f（%d根前）✓" % (x["level"], x["age"]))
            elif x["tag"] == "一买":
                why = "✓一买+金叉" if golden else "✗无金叉不认"
                if x["age"] > _h1_yb_age:
                    why = "✗太老（%d根 > %d）" % (x["age"], _h1_yb_age)
                elif _gap > BUY_NEAR_UP:
                    why = "✗已跑远 %.1f%%" % _gap
                keep.append("一买 %.2f（%d根前）%s" % (x["level"], x["age"], why))
            elif x["tag"] == "二买":
                keep.append("二买 %.2f（%d根前）✗小级别不认" % (x["level"], x["age"]))
        A("  买点：" + ("、".join(keep) if keep else "无"))
        hs = fx_h.get("sell_list") or []
        A("  卖点：" + ("、".join("%s %.2f（%d根前）" % (x["tag"], x["level"], x["age"])
                              for x in hs[:3]) if hs else "无"))
        A("  均线：MA10 %.2f / MA20 %.2f → %s" % (m10[-1], m20[-1],
                                              "多头（金叉状态）" if golden else "空头（未金叉）"))
    else:
        A("  （数据不足）")
    A("")

    # ── 三、缠论·均线版 ──
    A("═══ 三、缠论·均线版（趋势浪子原文）═══")
    if ma_d:
        lad = ma_d["loc"]["ladder"]
        a0, a1, bg = lad["A0"], lad["A1"], lad.get("BIG", {})
        A("  结论：%s" % M.tradeable(ma_d))
        # 🌈 当前在笔上还是笔下 + 接下来怎么走（大佬 2026-11-09 要求）
        _wk = ma_d.get("walk") or {}
        if _wk.get("ok"):
            A("  ▶ 当前位置：%s（%s）" % (_wk["where"], {
                "up": "上笔", "dn": "下笔", "mid": "笔内缠绕"}.get(_wk.get("state"), "-")))
            A("    → 怎么走：%s" % _wk.get("advice"))
            A("    → 级别关系：%s" % _wk.get("ctx"))
            if _wk.get("levels"):
                A("    → 盯的位置：%s" % " ｜ ".join("%s %.2f" % (k, v) for k, v in _wk["levels"]))
        A("  笔(MA5/10穿MA20)：%s ｜ %s ｜ 已%d根"
          % (a0.get("align", "-"), a0.get("dir", "-"), a0.get("bars", 0)))
        A("  线段(MA20/40穿MA80)：%s ｜ %s ｜ 已%d根"
          % (a1.get("align", "-"), a1.get("dir", "-"), a1.get("bars", 0)))
        A("  大级别(价格vsMA160)：%s（%s，已%d根）"
          % (bg.get("align", "-"), bg.get("desc", "-"), bg.get("bars", 0)))
        A("  全均线排列：%s%s ｜ 形态：%s ｜ MACD：%s"
          % (ma_d["align"]["state"],
             ("（已%d根）" % ma_d["align"]["bars"]) if ma_d["align"].get("bars") else "",
             ma_d["mode"]["mode"], ma_d["macd"]))
        vals = ma_d["align"].get("vals") or []
        if len(vals) == 6:
            A("  均线值：MA5 %.2f ｜ MA10 %.2f ｜ MA20 %.2f ｜ MA40 %.2f ｜ MA80 %.2f ｜ MA160 %.2f"
              % tuple(vals))
            A("  现价 vs 各均线：%s"
              % " ｜ ".join("MA%d %+.2f%%" % (n, (ma_d["close"] - v) / v * 100)
                            for n, v in zip((5, 10, 20, 40, 80, 160), vals)))
        if ma_d.get("beichi"):
            bc = ma_d["beichi"]
            A("  ⚡ %s：力度 %.1f → %.1f" % (bc["type"], bc["power_prev"], bc["power_now"]))
        for x in (ma_d.get("sigs") or [])[:2]:
            mk = "🌙" if x["type"] == "买点" else "⭐"
            A("  %s %s：%s @%.2f" % (mk, x["type"], x["kind"], x["px"]))
    else:
        A("  （数据不足，需 ≥170 根日线）")
    if ma_h:
        lh = ma_h["loc"]["ladder"]
        h0, h1_, hb = lh["A0"], lh["A1"], lh.get("BIG", {})
        A("── 小时线 · 60m ──")
        A("  结论：%s" % M.tradeable(ma_h))
        _wk = ma_h.get("walk") or {}
        if _wk.get("ok"):
            A("  ▶ 当前位置：%s ｜ 怎么走：%s" % (_wk["where"], _wk.get("advice")))
            A("    → 级别关系：%s" % _wk.get("ctx"))
            if _wk.get("levels"):
                A("    → 盯的位置：%s" % " ｜ ".join("%s %.2f" % (k, v) for k, v in _wk["levels"]))
        A("  笔(MA5/10穿MA20)：%s ｜ %s ｜ 已%d根 ｜ 线段(20/40穿80)：%s ｜ %s ｜ 已%d根"
          % (h0.get("align", "-"), h0.get("dir", "-"), h0.get("bars", 0),
             h1_.get("align", "-"), h1_.get("dir", "-"), h1_.get("bars", 0)))
        A("  大级别(价格vsMA160)：%s（%s，已%d根）｜ 全均线排列：%s"
          % (hb.get("align", "-"), hb.get("desc", "-"), hb.get("bars", 0),
             ma_h["align"]["state"]))
        hv = ma_h["align"].get("vals") or []
        if len(hv) == 6:
            A("  均线值：MA5 %.2f ｜ MA10 %.2f ｜ MA20 %.2f ｜ MA40 %.2f ｜ MA80 %.2f ｜ MA160 %.2f"
              % tuple(hv))
        for x in (ma_h.get("sigs") or [])[:2]:
            mk = "🌙" if x["type"] == "买点" else "⭐"
            A("  %s %s：%s @%.2f" % (mk, x["type"], x["kind"], x["px"]))
    A("")

    # ── 四、SMC ──
    A("═══ 四、SMC 结构（FVG/OB + 方向确认）═══")
    for lv, r, df in (("日线", smc_d, day), ("小时线", smc_h, h1)):
        px = float(df["close"].iloc[-1])
        A("── %s（摆动点 k=%d）──" % (lv, r["k"]))
        hi, lo = r["hi"], r["lo"]
        if len(hi) >= 2 and len(lo) >= 2:
            H1, H2 = df["high"].values[hi[-1]], df["high"].values[hi[-2]]
            L1, L2 = df["low"].values[lo[-1]], df["low"].values[lo[-2]]
            up_hi, dn_lo = H1 > H2, L1 < L2
            struct = ("多头结构 (HH+HL)" if up_hi and not dn_lo else
                      "空头结构 (LH+LL)" if dn_lo and not up_hi else
                      "扩张（高低点同时外扩）" if up_hi and dn_lo else "收敛（高低点同时内收）")
            rl, rh = min(L1, L2), max(H1, H2)
            pos = (px - rl) / (rh - rl) * 100 if rh > rl else 50
            A("  结构：%s ｜ 摆动区间 %.2f ~ %.2f ｜ 位置 %.0f%%（%s）"
              % (struct, rl, rh, pos,
                 "偏下·折价" if pos < 45 else ("偏上·溢价" if pos > 55 else "中部")))
        ff = [z for z in r["fvgs"] if z.get("fresh")]
        fo = [z for z in r["obs"] if z.get("fresh")]
        A("  FVG：新鲜 %d 个%s" % (len(ff),
                                ("（最近 %s %s~%s，%d根前，回补 %.0f%%，%s×ATR）"
                                 % ("看涨" if ff[0]["dir"] == "bull" else "看跌",
                                    round(ff[0]["bottom"], 2), round(ff[0]["top"], 2),
                                    ff[0]["age"], ff[0]["fill"] * 100, ff[0].get("strength")))
                                if ff else ""))
        A("  OB：新鲜 %d 个%s" % (len(fo),
                               ("（%s %s~%s，%d根前，%s）"
                                % ("看涨OB" if fo[0]["dir"] == "bull" else "看跌OB",
                                   round(fo[0]["bottom"], 2), round(fo[0]["top"], 2), fo[0]["age"],
                                   "已回踩未破" if fo[0]["touched"] and not fo[0]["invalid"] else "未回踩"))
                                if fo else ""))
        if r["sigs"]:
            for s in r["sigs"][:3]:
                A("  · %s" % S.confirm_text(s, px))
        else:
            A("  买卖点确认：无")
    A("")

    # ── 五、VWAP 六轨 ──
    A("═══ 五、VWAP 六轨（量加权，窗口 %d 根）═══" % S.VWAP_WIN)
    A("  口径：+3σ 全部止盈 / +2σ 止盈一半 / +1σ 警戒 / VWAP 中轴 / -1σ 全部止损 / -2σ 深跌参考")
    for lv, r, df in (("日线", vw_d, day), ("小时线", vw_h, h1)):
        px = float(df["close"].iloc[-1])
        v, sg = r["vwap"][-1], r["sigma"][-1]
        if not (np.isfinite(v) and np.isfinite(sg)) or sg <= 0:
            A("  %s：数据不足" % lv)
            continue
        z = (px - v) / sg
        A("  %s：现价 %.2f ｜ VWAP %.2f ｜ 偏离 %+.2fσ（分位 %.1f%%）｜ MA%d %s"
          % (lv, px, v, z, _norm_cdf(z) * 100, S.VWAP_MA_DIR,
             {"up": "向上↑", "dn": "向下↓", "flat": "走平→"}[r["ma_dir"][-1]]))
        A("       +3σ %.2f ｜ +2σ %.2f ｜ +1σ %.2f ｜ -1σ %.2f ｜ -2σ %.2f"
          % (r["up3"][-1], r["up2"][-1], r["up1"][-1], r["dn1"][-1], r["dn2"][-1]))
        if z >= S.VWAP_FULL_K:
            A("       ⚠️ 已过 +3σ → 按口径「全部止盈」")
        elif z >= S.VWAP_HALF_K:
            A("       ⚠️ 已过 +2σ → 「止盈一半」，回落至 +2σ 内且 VWAP 向上再补回")
        elif z >= S.VWAP_MID_K:
            A("       ⚠️ 在 +1σ~+2σ 之间（警戒区）→ 减仓/不追高")
        elif z <= -S.VWAP_HALF_K:
            A("       ⚠️ 已破 -2σ → 深度超跌区，止损已在上方 -1σ 处生效")
        elif z <= -S.VWAP_SL_K:
            A("       ⚠️ 已破 -1σ → 按口径「全部止损」")
        elif z <= -S.VWAP_MID_K:
            A("       在 -1σ~-2σ 之间（弱势区）→ 不接刀，等企稳")
        else:
            A("       在 ±1σ 带内，持有区间")
    A("")

    # ── 六、综合 ──
    A("═══ 六、综合判断 ═══")
    score, reasons = 0, []
    if fx_d.get("zpos") == "below":
        score -= 1
        reasons.append("日线中枢下方（-）")
    if fx_d.get("zpos") == "above":
        score += 1
        reasons.append("日线中枢上方（+）")
    if fx_d.get("div") == "底背驰":
        score += 1
        reasons.append("日线底背驰（+）")
    if fx_d.get("div") == "顶背驰":
        score -= 1
        reasons.append("日线顶背驰（-）")
    if fx_h.get("zpos") == "above":
        score += 1
        reasons.append("小时线已向上离开中枢（+）")
    if ma_d:
        t = M.tradeable(ma_d)
        if t.startswith("✅"):
            score += 1
            reasons.append("均线版本级有买点（+）")
        elif t.startswith("⛔"):
            score -= 1
            reasons.append("均线版本级在卖点（-）")
        bg = ma_d["loc"]["ladder"].get("BIG", {})
        if bg.get("state") == "up":
            score += 1
            reasons.append("站上大级别 MA160（+）")
        elif bg.get("state") == "dn":
            score -= 1
            reasons.append("跌破大级别 MA160（-）")
    if np.isfinite(vw_d["vwap"][-1]) and vw_d["sigma"][-1] > 0:
        z = (P - vw_d["vwap"][-1]) / vw_d["sigma"][-1]
        if z <= -S.VWAP_SL_K:
            score -= 1
            reasons.append("日线跌破 VWAP -1σ（-）")
        elif z >= S.VWAP_HALF_K:
            score += 1
            reasons.append("日线在 VWAP +2σ 上方（+）")
    A("  打分：%+d（%s）" % (score, "、".join(reasons) if reasons else "无明显偏向"))
    A("  倾向：%s" % ("偏多，可等回踩结构支撑低吸" if score >= 2 else
                    "偏多但结构未确认，轻仓/等确认" if score == 1 else
                    "中性，等方向选择" if score == 0 else
                    "偏空，反弹减持、不追多" if score == -1 else
                    "空头结构，空仓等底部结构（背驰/三买）"))
    A("")
    A("⚠️ 技术结构解读；SMC 的 FVG/OB 只作机会/风险提示（回测无可用 α），不构成投资建议。")
    return "\n".join(L), score


# ══════════════════════════ 画图 ══════════════════════════
# ══════════════════════ 均线版结构几何（大佬 2026-11-09 要求必画）══════════════════════
#   均线版（趋势浪子原文）的三级结构：
#     笔   = MA5/MA10 双穿 MA20（状态翻转即笔的转折点）
#     线段 = MA20/MA40 双穿 MA80
#     走势类型 = 线段之上的「趋势上涨 / 趋势下跌 / 盘整」（女上位·男上位判定）
#   以前图里只画了 6 条均线，结构线一根没有 —— 这是错的。

def ma_structure(d):
    """算出均线版可绘制的结构：笔端点 / 线段端点 / 走势类型分段

    返回：
      bi   : [{i, px, dir}]       笔的转折点（原级别索引）
      seg  : [{i, px, dir}]       线段的转折点（A1 = MA20/40 穿 MA80）
      mode : [(i0, i1, kind)]     走势类型分段（trend_up / trend_dn / range）
    """
    if len(d) < 170:
        return None
    dd = M.add_ind(d)
    n = len(dd)
    out = {}
    for key, (f1, f2, slow) in (("bi", M.A0), ("seg", M.A1)):
        st = M.dual_cross_state(dd, f1, f2, slow)
        pts, cur = [], None
        for i in range(n):
            s = st.iloc[i]
            if s == "mid":
                continue
            if cur is None or s != cur:
                pts.append({"i": i, "px": float(dd["close"].iloc[i]),
                            "dir": "up" if s == "up" else "dn"})
                cur = s
        out[key] = pts
    # ⚠️ 2026-11-09 23:5x 大佬指出「高低点都不一样了」——真 bug：
    #   dual_cross_state 的状态翻转点是「均线穿完那根」，它的收盘价 ≠ 这一笔的高低点。
    #   直接把收盘价当转折点画，笔/线段的峰谷就整体缩水、位置也偏（实测风语筑 up 笔
    #   翻在 i=1185 收 14.23，而该笔区间实际最高 14.60）。
    #   → 转折点一律取「该笔区间内的真实最高/最低」，索引也归到极值那根。
    hi_arr = dd["high"].astype(float).values
    lo_arr = dd["low"].astype(float).values

    def _to_extremes(pts):
        res = []
        for k, p in enumerate(pts):
            a = int(p["i"])
            b = int(pts[k + 1]["i"]) if k + 1 < len(pts) else n - 1
            if b < a:
                a, b = b, a
            if p["dir"] == "up":            # 从低点起涨 → 取区间最低
                seg_arr = lo_arr[a:b + 1]
                off = int(np.argmin(seg_arr))
                px = float(seg_arr[off])
            else:                            # 从高点起跌 → 取区间最高
                seg_arr = hi_arr[a:b + 1]
                off = int(np.argmax(seg_arr))
                px = float(seg_arr[off])
            res.append({"i": a + off, "px": px, "dir": p["dir"], "flip": a})
            # ⚠️ 最后一笔还没走完：必须补一个「活的终点」（至今为止的极值），
            #   否则图上的笔/线段停在最后一次翻转那根，右边整段是空的
            #   （大佬 2026-11-09：「最后那部分你没有画出笔线段走势类型」）
            if k + 1 >= len(pts):
                # 起点必须用「本点极值那根」，不能用翻转那根（否则会把上一笔的
                #  高点当成本笔新高：风语筑实测会取到 10-26 的 15.98，比现价还高）
                e0 = a + off
                if p["dir"] == "up":
                    seg_arr = hi_arr[e0:n]
                    off2 = int(np.argmax(seg_arr))
                    res.append({"i": e0 + off2, "px": float(seg_arr[off2]),
                                "dir": "dn", "flip": a, "live": True})
                else:
                    seg_arr = lo_arr[e0:n]
                    off2 = int(np.argmin(seg_arr))
                    res.append({"i": e0 + off2, "px": float(seg_arr[off2]),
                                "dir": "up", "flip": a, "live": True})
        return res

    out["bi"] = _to_extremes(out["bi"])
    out["seg"] = _to_extremes(out["seg"])
    # ── 走势类型（线段之上的三态）：**用线段的高低点序列判** ──
    #   ⚠️ 2026-11-09 23:5x 大佬指出「走势类型没画出来」——真 bug：
    #     原来用 M.trend_mode(逐根) 判，那是 MA5/10/20 近 20 根不接触才算趋势，
    #     日线 1200 根里只有 1-3 根能成立 → 合并后整张图变成**一个 899 根的灰底**，
    #     等于什么都没画。trend_mode 适合「当下这一根是什么状态」，不适合画历史分段。
    #   → 走势类型是**线段级别**的概念，正确做法是比较相邻两条线段（摆）：
    #       后高>前高 且 后低>前低 → 上涨；后高<前高 且 后低<前低 → 下跌；其余 → 盘整
    #     （不是「相邻线段方向」，方向必然交替；比的是**高低点是否抬高/降低**）
    segs1 = out["seg"]
    #   ⚠️ segs1 的 dir 是「从该点出发的方向」→ up 点=低点，dn 点=高点，两者交替出现。
    #      必须按「低点→高点」配对，不能按「相邻两点」直接配（起点可能是高点，
    #      那样每一摆的高低都会错位一格，实测风语筑 11 摆全判成 range）。
    swings = []          # [(i_start, low, high)]
    for k in range(len(segs1) - 1):
        a, b = segs1[k], segs1[k + 1]
        if a["dir"] == "up" and b["dir"] == "dn":       # 低点 → 高点
            swings.append((a["i"], a["px"], b["px"]))
    kinds = []
    for k in range(len(swings)):
        if k == 0:
            kinds.append("range")
            continue
        _, l0, h0 = swings[k - 1]
        _, l1, h1 = swings[k]
        if h1 > h0 and l1 > l0:
            kinds.append("trend_up")
        elif h1 < h0 and l1 < l0:
            kinds.append("trend_dn")
        else:
            kinds.append("range")
    mode, cur, i0 = [], None, None
    for k, s in enumerate(kinds):
        x0 = swings[k][0]
        if cur is None:
            cur, i0 = s, x0
        elif s != cur:
            mode.append([i0, x0, cur])
            cur, i0 = s, x0
    if cur is not None:
        mode.append([i0, n - 1, cur])
    # 合并极短段（<MERGE_MIN 根）：并到前一段，避免图上出现一堆碎色块
    MERGE_MIN = 8
    merged = []
    for seg in mode:
        if merged and (seg[1] - seg[0]) < MERGE_MIN:
            merged[-1][1] = seg[1]
        elif merged and merged[-1][2] == seg[2]:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)
    out["mode"] = [tuple(x) for x in merged]

    # ── 中枢（均线版）：线段（A1 级别）的**连续三段重叠区** = 中枢 ZD~ZG ──
    #   均线版原文没有直接讲中枢（它讲的是均线组「缠绕」），但结构上等价：
    #   线段三段重叠 = 均线缠绕区。大佬要求「中枢没画出来」必须补上。
    zs_list = []
    segs = out["seg"]
    for k in range(len(segs) - 2):
        a0, b0, c0 = segs[k], segs[k + 1], segs[k + 2]
        lo = max(min(a0["px"], b0["px"]), min(b0["px"], c0["px"]))
        hi = min(max(a0["px"], b0["px"]), max(b0["px"], c0["px"]))
        if hi > lo:
            zs_list.append({"i0": a0["i"], "i1": c0["i"],
                            "zd": round(float(lo), 2), "zg": round(float(hi), 2)})
    # 合并重复的相邻中枢：只在「时间重叠 且 价格区间高度重合」时才并，
    #   否则会把一整年缠成一个大方块（实测中证500 会并出 770 根宽的怪东西）
    zs_merged = []
    for z in zs_list:
        if zs_merged:
            p0 = zs_merged[-1]
            ov_i = z["i0"] <= p0["i1"]
            ov_p = not (z["zg"] < p0["zd"] or z["zd"] > p0["zg"])
            same = abs(z["zd"] - p0["zd"]) < max(1e-9, (p0["zg"] - p0["zd"]) * 0.5) and \
                   abs(z["zg"] - p0["zg"]) < max(1e-9, (p0["zg"] - p0["zd"]) * 0.5)
            if ov_i and ov_p and same:
                p0["i1"] = max(p0["i1"], z["i1"])
                continue
        zs_merged.append(dict(z))
    # 太宽的（>窗口 35%）不画，那不是中枢是长期震荡
    out["zs"] = [z for z in zs_merged if (z["i1"] - z["i0"]) > 0][-8:]
    return out


def draw_chart(disp, level, df, fx, smc, vw, out, n_show=120, ma=None):
    """K线 + 均线版子图 + 缠论笔/中枢/买卖点 + SMC FVG/OB + VWAP 六轨 + 量 + MACD

    配色口径（MEMORY 硬规矩）：涨跌按 close vs 上一根 close，不按 close vs open。
    标记口径（大佬 2026-11-09）：买点 = 绿月亮 🌙，卖点 = 红星星 ⭐。
    """
    ma = ma or {}
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
    up_col = [RED if (np.isfinite(prev_c[i]) and c[i] >= prev_c[i]) else GRN for i in range(nv)]
    today_pct = (c[-1] - prev_c[-1]) / prev_c[-1] * 100

    # 均线版子图要能看结构（笔/线段/走势类型都是大级别），窗口比价格面板长
    n_ma = max(int(n_show), MA_PANEL_BARS)
    n_ma = min(n_ma, len(df))
    off_ma = len(df) - n_ma                     # 均线版面板的起始偏移（独立 x 轴）
    viewA = df.iloc[off_ma:].reset_index(drop=True)
    nvA = len(viewA)
    xA = np.arange(nvA)

    fig = plt.figure(figsize=(17, 14.5), facecolor="white")
    gs = fig.add_gridspec(4, 1, height_ratios=[4.2, 3.6, 1.0, 1.6], hspace=0.09)
    axP = fig.add_subplot(gs[0])
    axA = fig.add_subplot(gs[1])      # 独立 x 轴（窗口更长，才能看到结构）
    axV = fig.add_subplot(gs[2], sharex=axP)
    axM = fig.add_subplot(gs[3], sharex=axP)

    bw = 0.62
    for i in range(nv):
        col = up_col[i]
        axP.vlines(i, l[i], h[i], color=col, lw=0.9, zorder=3)
        y0, y1 = min(o[i], c[i]), max(o[i], c[i])
        axP.add_patch(Rectangle((i - bw / 2, y0), bw,
                                max(y1 - y0, (h.max() - l.min()) * 0.0008),
                                facecolor=col, edgecolor=col, lw=0.5, zorder=4))
    axV.bar(x, view["volume"].values.astype(float), width=bw, color=up_col, alpha=0.75)

    # ── 子图②：缠论·均线版（走势类型 + 线段 + 笔 + 6条均线）──
    #   ⚠️ 这一块用**独立 x 轴**（xA / nvA / off_ma），窗口比价格面板长（MA_PANEL_BARS=260），
    #      否则线段只有 2 个点、走势类型只有 1 段，结构根本看不出来
    _mad = M.add_ind(df)
    _st = ma_structure(df)
    MODE_COL = {"trend_up": ("#ffe3e3", "上涨"), "trend_dn": ("#e0f2e4", "下跌"),
                "range": ("#efefef", "盘整")}
    MODE_TXT = {"trend_up": "#c62828", "trend_dn": "#1b7e32", "range": "#555555"}

    # ① 走势类型背景带（最底层，最淡）
    if _st:
        labeled = set()
        for i0_, i1_, kind in _st["mode"]:
            x0, x1 = i0_ - off_ma, i1_ - off_ma
            if x1 < -1 or x0 > nvA:
                continue
            x0, x1 = max(x0, -1), min(x1, nvA)
            fc, lb = MODE_COL[kind]
            axA.axvspan(x0, x1, facecolor=fc, alpha=0.55, zorder=0, lw=0)
            if (x1 - x0) > nvA * 0.06 and len(labeled) < 6:
                labeled.add(kind)
                axA.text((x0 + x1) / 2, 0.985, lb, transform=axA.get_xaxis_transform(),
                         ha="center", va="top", fontsize=8.5, color=MODE_TXT[kind],
                         fontweight="bold", zorder=3, alpha=0.55)

    # ② 收盘线 —— 淡灰细线（只看结构，不看价格本身）
    axA.plot(xA, viewA["close"].values.astype(float), color="#b0b0b0", lw=0.9,
             alpha=0.85, label="收盘", zorder=3)

    # ③ 均线——图上只画 MA10/MA20 + MA160（MA5 画着太乱，故不画；但计算里 MA5 仍在用）
    for nn in MA_DRAW:
        colc = "ma%d" % nn
        if colc not in _mad.columns:
            continue
        y = _mad[colc].values[off_ma:]
        if np.all(~np.isfinite(y)):
            continue
        axA.plot(xA, y, color=MA_PALETTE[nn], lw=0.8, alpha=0.55,
                 label="MA%d" % nn, zorder=4)
    # 大级别 MA160 单独一条（大级别支撑，稍微明显一点）
    if "ma160" in _mad.columns:
        y = _mad["ma160"].values[off_ma:]
        if np.any(np.isfinite(y)):
            axA.plot(xA, y, color="#8e44ad", lw=1.3, alpha=0.55, ls="--",
                     label="MA160(大级别)", zorder=4)
            yv = y[-1]
            if np.isfinite(yv):
                axA.annotate("MA160 %.2f" % yv, xy=(nvA - 0.6, yv), xytext=(6, 0),
                             textcoords="offset points", va="center", ha="left",
                             fontsize=7.4, color="#8e44ad", fontweight="bold",
                             annotation_clip=False, zorder=12)

    # ④ 中枢 —— 线段三段重叠区，黄色框 + ZG/ZD 虚线（大佬要求必须画）
    if _st and _st.get("zs"):
        for zs in _st["zs"]:
            x0, x1 = zs["i0"] - off_ma, zs["i1"] - off_ma
            if x1 < -1 or x0 > nvA:
                continue
            x0, x1 = max(x0, -0.5), min(x1, nvA)
            if x1 <= x0:
                continue
            axA.add_patch(Rectangle((x0, zs["zd"]), x1 - x0, zs["zg"] - zs["zd"],
                                    facecolor="#ffd24d", alpha=0.30,
                                    edgecolor="#c07800", lw=2.2, zorder=6))
            # ⚠️ axhline 的 xmin/xmax 是**轴坐标比例**，传数据坐标会画错位置
            #    必须用 hlines（数据坐标）或 plot
            axA.hlines([zs["zg"], zs["zd"]], x0, x1,
                       colors="#c07800", lw=1.4, ls="--", alpha=0.85, zorder=7)
            if (x1 - x0) > nvA * 0.05:
                axA.text((x0 + x1) / 2, zs["zg"], "中枢 %.0f~%.0f" % (zs["zd"], zs["zg"]),
                         fontsize=7.4, color="#8b6508", ha="center", va="bottom",
                         fontweight="bold", zorder=11,
                         bbox=dict(boxstyle="round,pad=0.14", fc="white",
                                   ec="#b8860b", lw=0.5, alpha=0.85))

    # ⑤ 线段（MA20/40 穿 MA80）—— **主结构，最深最粗**
    if _st and len(_st["seg"]) >= 2:
        sx, sy = [], []
        for p in _st["seg"]:
            xi = p["i"] - off_ma
            if -2 <= xi <= nvA + 2:
                sx.append(xi)
                sy.append(p["px"])
        if len(sx) >= 2:
            axA.plot(sx, sy, color="#0d2b45", lw=3.6, alpha=1.0, zorder=9,
                     label="线段(MA20/40穿MA80)")
            # 未完成的线段：从最后极值虚线连到最新收盘（否则右边一段空白）
            _lp = _st["seg"][-1]
            if _lp.get("live") and sx and sx[-1] < nvA - 1:
                axA.plot([sx[-1], nvA - 1], [sy[-1], float(viewA["close"].iloc[-1])],
                         color="#0d2b45", lw=2.0, ls="--", alpha=0.75, zorder=9)
            for i in range(len(sx)):
                axA.scatter([sx[i]], [sy[i]], s=62, marker="s",
                            color="#0d2b45", edgecolor="white", lw=1.4, zorder=10)
                if i:
                    axA.annotate("线段", xy=((sx[i] + sx[i - 1]) / 2, (sy[i] + sy[i - 1]) / 2),
                                 fontsize=7.6, color="#0d2b45", ha="center", va="center",
                                 fontweight="bold", zorder=10,
                                 bbox=dict(boxstyle="round,pad=0.16", fc="white",
                                           ec="#0d2b45", lw=0.6, alpha=0.88))

    # ⑥ 笔（MA5/10 穿 MA20）—— 次级结构，中等粗细
    if _st and len(_st["bi"]) >= 2:
        bx2, by2 = [], []
        for p in _st["bi"]:
            xi = p["i"] - off_ma
            if -2 <= xi <= nvA + 2:
                bx2.append(xi)
                by2.append(p["px"])
        if len(bx2) >= 2:
            axA.plot(bx2, by2, color="#e8955a", lw=1.3, alpha=0.9, zorder=7,
                     label="笔(MA5/10穿MA20)")
            _bp = _st["bi"][-1]
            if _bp.get("live") and bx2 and bx2[-1] < nvA - 1:
                axA.plot([bx2[-1], nvA - 1], [by2[-1], float(viewA["close"].iloc[-1])],
                         color="#e8955a", lw=1.0, ls="--", alpha=0.7, zorder=7)
            for i in range(len(bx2)):
                axA.scatter([bx2[i]], [by2[i]], s=12, marker="o", color="#e8955a",
                            edgecolor="white", lw=0.5, zorder=8)

    # ⑤ 均线版面板收尾：排列态/走势类型角标 + 轴设置
    try:
        _al = M.ma_alignment(_mad)
        _txt = {"多头排列": ("多头排列 MA5>10>20>40>80>160", RED),
                "空头排列": ("空头排列 MA5<10<20<40<80<160", GRN)}.get(
            _al.get("state"), (_al.get("state", ""), "#666666"))
        if _txt[0]:
            axA.text(0.008, 0.04, _txt[0] + ("（已%d根）" % _al["bars"] if _al.get("bars") else ""),
                     transform=axA.transAxes, fontsize=9.5, color=_txt[1],
                     fontweight="bold", va="bottom", ha="left",
                     bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=_txt[1],
                               alpha=0.92, lw=1.1))
        if _st and _st["mode"]:
            cur_mode = _st["mode"][-1][2]
            _c = MODE_TXT.get(cur_mode, "#666")
            axA.text(0.992, 0.04, "当前走势：%s ｜ 笔 %d ｜ 线段 %d 段 %d"
                     % ({"trend_up": "上涨趋势", "trend_dn": "下跌趋势",
                         "range": "盘整"}.get(cur_mode, "-"),
                        len(_st["bi"]), len(_st["seg"]) // 2, len(_st["mode"])),
                     transform=axA.transAxes, fontsize=9, color=_c,
                     fontweight="bold", va="bottom", ha="right",
                     bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=_c,
                               alpha=0.92, lw=1.1))
        # 技能核心结论 tradeable()（与 chan-ma-trend 技能同一口径）+ 大级别 MA160
        _tr = M.verdict(_mad)
        _lv = M.level_locate(_mad)
        _big = _lv.get("big") or {}
        _tcol = {"✅": "#1b7e32", "⛔": "#c62828", "🟡": "#b8860b", "⚪": "#666666"}
        _c2 = next((v for k, v in _tcol.items() if _tr.startswith(k)), "#333333")
        # ⚠️ 图内不能出现 emoji（Noto/WQY 无彩色 emoji 字形 → 豆腐块），去掉前缀只靠颜色区分
        _tr_plain = _tr
        for _e in _tcol:
            if _tr_plain.startswith(_e):
                _tr_plain = _tr_plain[len(_e):].strip()
                break
        axA.text(0.008, 0.975, _tr_plain + " ｜ 大级别(MA160)：%s" % (_big.get("align") or "—"),
                 transform=axA.transAxes, fontsize=9.2, color=_c2, fontweight="bold",
                 va="top", ha="left",
                 bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=_c2,
                           alpha=0.93, lw=1.1))
        # ▶ 当前位置（笔上/笔下）+ 关键位（大佬 2026-11-09 要求直接标在图上）
        _wk = M.bi_walk(_mad)
        if _wk.get("ok"):
            _wc = {"up": "#c62828", "dn": "#2ca02c", "mid": "#b8860b"}.get(_wk["state"], "#333")
            _lv_txt = " ｜ ".join("%s %.2f" % (k, v) for k, v in (_wk.get("levels") or [])[:3])
            axA.text(0.5, 0.975, _wk["where"] + (("  " + _lv_txt) if _lv_txt else ""),
                     transform=axA.transAxes, fontsize=8.6, color=_wc, fontweight="bold",
                     va="top", ha="center",
                     bbox=dict(boxstyle="round,pad=0.28", fc="white", ec=_wc,
                               alpha=0.93, lw=1.1))
    except Exception:
        pass
    axA.set_ylabel("缠论·均线版\n结构(笔/线段/走势)", fontsize=9)
    axA.grid(True, ls=":", color="#cccccc", alpha=0.6)
    axA.legend(loc="upper left", fontsize=8, framealpha=0.92, ncol=5)
    axA.set_xlim(-1, nvA)
    axA.set_title("缠论·均线版（近 %d 根）：笔 = MA5/10 双穿 MA20 ｜ "
                  "线段 = MA20/40 双穿 MA80 ｜ 底色 = 走势类型" % nvA,
                  fontsize=11, color="#333333", pad=6)

    # ── SMC FVG / OB ──
    def _xa(idx):
        return idx - start

    for z in smc["fvgs"]:
        if not z.get("fresh"):
            continue
        x0 = _xa(z["idx"])
        if x0 < -5:
            continue
        col = GRN if z["dir"] == "bull" else RED
        xs = max(x0, -0.5)
        axP.add_patch(Rectangle((xs, z["bottom"]), nv - xs + 0.5, z["top"] - z["bottom"],
                                facecolor=col, alpha=0.13, edgecolor=col, lw=0.8,
                                ls=":", zorder=1))
        axP.text(max(x0, 0) + 0.3, (z["top"] + z["bottom"]) / 2, "FVG%d" % z["age"],
                 fontsize=7, color=col, va="center", zorder=2, alpha=0.9)
    for z in smc["obs"]:
        if not z.get("fresh"):
            continue
        x0 = _xa(z["idx"])
        if x0 < -5:
            continue
        col = "#00695c" if z["dir"] == "bull" else "#8e24aa"
        xs = max(x0, -0.5)
        axP.add_patch(Rectangle((xs, z["bottom"]), nv - xs + 0.5, z["top"] - z["bottom"],
                                facecolor="none", edgecolor=col, lw=1.1, ls="--",
                                alpha=0.75, zorder=2))
        axP.text(max(x0, 0) + 0.3, z["top"], "OB", fontsize=7, color=col,
                 va="bottom", zorder=2)

    # ── VWAP 六轨 ──
    def plot_vw(key, color, lw, ls, label, alpha=1.0):
        v = vw[key][start:]
        if not np.all(np.isnan(v)):
            axP.plot(x, v, color=color, lw=lw, ls=ls, label=label, alpha=alpha, zorder=5)
    plot_vw("up3", "#8e0000", 1.3, "-", "VWAP+3σ 全部止盈")
    plot_vw("up2", "#c0392b", 1.2, "--", "VWAP+2σ 止盈一半")
    plot_vw("up1", "#e08b7a", 0.9, ":", "VWAP+1σ 警戒")
    plot_vw("vwap", "#222222", 1.9, "-", "VWAP 中轴")
    plot_vw("dn1", "#1e8449", 1.2, "--", "VWAP-1σ 全部止损")
    plot_vw("dn2", "#0b4f2c", 1.3, "-", "VWAP-2σ 深跌")

    # ── 缠论几何 ──
    geo = chan_geometry(df)
    bx, by = [], []
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -2 <= xi <= nv + 2:
            bx.append(xi)
            by.append(p["price"])
    if len(bx) >= 2:
        axP.plot(bx, by, color=BLUE, lw=1.6, zorder=6, label="缠论笔")
    for p in geo["bi"]:
        xi = p["orig"] - start
        if -1 <= xi <= nv:
            axP.scatter([xi], [p["price"]], s=22, zorder=7,
                        color=(RED if p["type"] == 1 else GRN),
                        marker=("v" if p["type"] == 1 else "^"))

    if geo["zs"] and geo["zs_x"]:
        x0 = max(geo["zs_x"][0] - start, -0.5)
        x1 = geo["zs_x"][1] - start
        if x1 >= -1:
            zs0, zs1 = geo["zs"]
            axP.add_patch(Rectangle((x0, zs0), x1 - x0, zs1 - zs0, facecolor="#ffd24d",
                                    alpha=0.25, edgecolor="#b8860b", lw=1.5, zorder=3))
            axP.axhline(zs1, color="#b8860b", lw=0.9, ls="--", alpha=0.7, zorder=4)
            axP.axhline(zs0, color="#b8860b", lw=0.9, ls="--", alpha=0.7, zorder=4)
            axP.text(max(x0, 0), zs1, " ZG %.2f" % zs1, fontsize=8.5, color="#8b6508",
                     va="bottom", ha="left", zorder=9)
            axP.text(max(x0, 0), zs0, " ZD %.2f" % zs0, fontsize=8.5, color="#8b6508",
                     va="top", ha="left", zorder=9)
            axP.text(max(x0, 0) + 0.5, (zs0 + zs1) / 2, "中枢", fontsize=11,
                     color="#8b6508", alpha=0.8, va="center", zorder=9)

    # 买点 = 绿月亮 🌙；卖点 = 红星星 ⭐（大佬 2026-11-09 指定）
    # ⚠️ 2026-11-09 只画「过了闸门」的买点（年龄/距离/MA20 方向）——
    #    风语筑事故：116 根前的一买 8.78 被画成当前买点，误导。失效的只进文字「被否」，不上图。
    BUY = {"一买": ("#0b6e0b", 480), "二买": ("#1a9850", 360),
           "三买": ("#2e9b4f", 280)}
    SELL = {"一卖": ("#8b0000", 430), "二卖": ("#d73027", 330),
            "三卖": ("#e8553b", 260)}
    _clx = float(df["close"].iloc[-1])
    _m20x = ma20_state(df)
    _buys_draw = [x for x in rank_buys(fx.get("buy_list") or [], _m20x, close=_clx)
                  if x["accept"]]
    for lst, styles, dy, is_buy in ((_buys_draw, BUY, -1, True),
                                    (fx.get("sell_list") or [], SELL, 1, False)):
        for b in lst:
            if b["tag"] not in styles:
                continue
            col, sz = styles[b["tag"]]
            cand = [p for p in geo["bi"] if p["type"] == (1 if not is_buy else -1)]
            if not cand:
                continue
            pt = min(cand, key=lambda p: abs(p["price"] - b["level"]))
            xi = pt["orig"] - start
            if not (-1 <= xi <= nv):
                continue
            axP.scatter([xi], [pt["price"]], s=sz, marker=(MOON if is_buy else STAR),
                        color=col, edgecolor=(col if is_buy else "white"),
                        lw=(1.9 if is_buy else 1.2), zorder=10)
            axP.annotate("%s %s %.2f\n%d根前%s" % ("买点" if is_buy else "卖点",
                                                  b["tag"], b["level"], b["age"],
                                                  "\n长期未破" if b.get("sustained") else ""),
                         xy=(xi, pt["price"]), xytext=(0, 34 * dy),
                         textcoords="offset points", ha="center",
                         va=("top" if dy < 0 else "bottom"),
                         fontsize=8.4, color=col, fontweight="bold", zorder=11,
                         bbox=dict(boxstyle="round,pad=0.26", fc="white", ec=col,
                                   alpha=0.93, lw=0.9))

    # ── 均线版买卖点同样标月亮/星星（画在均线子图上）──
    for xitem in (ma.get("sigs") or []):
        typ = xitem.get("type")
        ago = xitem.get("bars_ago")
        px = xitem.get("px")
        # 定位到近端最接近的位置（长期排列类信号：定位到排列起点，y 用该根收盘）
        xi = nv - 1 - min(max(int(ago or 0), 0), max(nv - 1, 0))
        if 0 <= xi < nv:
            px = float(view["close"].iloc[xi])
        if px is None:
            continue
        is_buy = (typ == "买点")
        col = "#1a9850" if is_buy else "#d73027"
        axA.scatter([xi], [px], s=300, marker=(MOON if is_buy else STAR), color=col,
                    edgecolor=(col if is_buy else "white"),
                    lw=(1.8 if is_buy else 1.0), zorder=10)
        axA.annotate(("买点" if is_buy else "卖点") + "·均线版",
                     xy=(xi, px), xytext=(0, 15 if is_buy else -15),
                     textcoords="offset points", ha="center",
                     va=("bottom" if is_buy else "top"), fontsize=7.8, color=col,
                     fontweight="bold", zorder=11,
                     bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=col,
                               alpha=0.9, lw=0.8))

    # ── MACD(10,20,5) ──
    cl = df["close"].astype(float)
    dif = cl.ewm(span=C.MACD_FAST, adjust=False).mean() - cl.ewm(span=C.MACD_SLOW, adjust=False).mean()
    dea = dif.ewm(span=C.MACD_SIGNAL, adjust=False).mean()
    hist = 2 * (dif - dea)
    axM.bar(x, hist.values[start:],
            color=["#d62728" if v >= 0 else "#2ca02c" for v in hist.values[start:]],
            width=bw, alpha=0.7)
    axM.plot(x, dif.values[start:], color=ORANGE, lw=1.0, label="DIF")
    axM.plot(x, dea.values[start:], color="#2980b9", lw=1.0, label="DEA")
    axM.axhline(0, color="#888888", lw=0.7)
    axM.legend(loc="upper left", fontsize=8, framealpha=0.85, ncol=3)

    tick_step = max(1, nv // 12)
    ticks = list(range(0, nv, tick_step))
    fmt = "%m-%d %H:%M" if level == "h1" else "%m-%d"
    axM.set_xticks(ticks)
    axM.set_xticklabels([pd.Timestamp(view["dt"].iloc[i]).strftime(fmt) for i in ticks],
                        rotation=0, fontsize=8.5)
    for a in (axP, axV):
        a.tick_params(labelbottom=False)
    # 均线版面板：自己一套日期刻度（窗口不同）
    step_a = max(1, nvA // 14)
    tk_a = list(range(0, nvA, step_a))
    axA.set_xticks(tk_a)
    axA.set_xticklabels([pd.Timestamp(viewA["dt"].iloc[i]).strftime(fmt) for i in tk_a],
                        rotation=0, fontsize=8.2)
    axP.set_xlim(-1, nv)
    axV.set_ylabel("量", fontsize=9)
    axM.set_ylabel("MACD\n(10,20,5)", fontsize=9)
    axP.set_ylabel("价格", fontsize=10)
    axP.grid(True, ls=":", color="#dddddd", alpha=0.7)
    axM.grid(True, ls=":", color="#dddddd", alpha=0.5)
    axP.legend(loc="upper left", fontsize=8.2, framealpha=0.88, ncol=3)

    # ── 右侧分位标注 ──
    for key, label, col in (("up3", "+3σ · 99.9%分位", "#8e0000"),
                            ("up2", "+2σ · 97.7%", "#c0392b"),
                            ("up1", "+1σ · 84.1%", "#e08b7a"),
                            ("vwap", "VWAP · 50%", "#222222"),
                            ("dn1", "-1σ · 15.9%", "#1e8449"),
                            ("dn2", "-2σ · 2.3%", "#0b4f2c")):
        y = vw[key][-1]
        if not np.isfinite(y):
            continue
        ylo, yhi = axP.get_ylim()
        if not (ylo <= y <= yhi):
            continue
        axP.annotate(label, xy=(nv - 0.6, y), xytext=(6, 0), textcoords="offset points",
                     va="center", ha="left", fontsize=8.2, color=col, fontweight="bold",
                     annotation_clip=False, zorder=12)

    lv_txt = {"day": "日线", "h1": "小时线60m"}[level]
    ts = pd.Timestamp(view["dt"].iloc[-1])
    ts_txt = ts.strftime("%Y-%m-%d %H:%M") if level == "h1" else ts.strftime("%Y-%m-%d")
    fig.suptitle("%s · %s ｜ %s ｜ 收 %.2f  今日 %+.2f%%"
                 % (disp, ts_txt, lv_txt, c[-1], today_pct),
                 fontsize=16, fontweight="bold", y=0.975)
    zs_txt = ("｜ 中枢 %.2f~%.2f" % (fx["zs"][0], fx["zs"][1])) if fx.get("zs") else ""
    _z = (c[-1] - vw["vwap"][-1]) / vw["sigma"][-1] if vw["sigma"][-1] > 0 else 0
    fig.text(0.5, 0.942,
             "阶段：%s ｜ 背驰：%s%s ｜ VWAP %.2f（%+.2fσ · %.1f%%分位）"
             % (fx.get("stage", "-"), fx.get("div") or "无", zs_txt,
                vw["vwap"][-1], _z, _norm_cdf(_z) * 100),
             ha="center", fontsize=11)
    fig.text(0.5, 0.917,
             "口径：分型版(笔/中枢ZG-ZD/背驰) + 均线版(笔=MA5/10穿20·线段=MA20/40穿80·走势类型底色·图只画MA10/20/MA160) + SMC(FVG/OB)"
             " + VWAP 六轨(±1σ/±2σ/±3σ，右侧标正态分位)　｜　涨跌按「收盘 vs 前收」上色　｜　买点=绿月牙 / 卖点=红星",
             ha="center", fontsize=9, color="#555555")

    os.makedirs(os.path.dirname(out) or OUT_DIR, exist_ok=True)
    fig.savefig(out, dpi=125, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


# ══════════════════════════ 飞书推送 ══════════════════════════
def push_feishu(disp, txt, score, imgs, chat=None):
    """程序直推飞书卡片（图文同一条，0 token）"""
    import feishu_card as FC
    try:
        _c = json.load(open(os.path.join(os.path.dirname(WS), "openclaw.json"),
                            encoding="utf-8"))["channels"]["feishu"]
        chat = chat or _c.get("defaultChat") or FC.DEFAULT_CHAT
    except Exception:
        chat = chat or FC.DEFAULT_CHAT

    # 文本按「═══」切块，卡片里分段更好读
    body = txt.split("\n")
    secs, cur = [], []
    for ln in body:
        if ln.startswith("═══") and cur:
            secs.append("\n".join(cur).strip())
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        secs.append("\n".join(cur).strip())

    blocks = []
    for i, s in enumerate(secs):
        if i:
            blocks.append({"type": "hr"})
        blocks.append({"type": "md", "text": s})
    tmpl = "green" if score >= 1 else ("blue" if score == 0 else "red")
    title = "📐 技术分析汇总 · %s · 2026-11-09" % disp
    ok = FC.send_card(title, blocks, imgs=imgs, chat=chat, template=tmpl,
                      sign="📎 来自 OpenClaw.Deepseek")
    return ok, chat


# ══════════════════════════ main ══════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", "--index", dest="target", default="中证500",
                    help="标的：中证500 等宽基名 / 000905.SH / 600519 / SZ000001")
    ap.add_argument("--n-day", type=int, default=120)
    ap.add_argument("--n-h1", type=int, default=150)
    ap.add_argument("--no-chart", action="store_true")
    ap.add_argument("--out", default=None, help="报告文本存档路径")
    ap.add_argument("--chart-dir", default=OUT_DIR)
    ap.add_argument("--push", action="store_true", help="推送飞书卡片（含双图）")
    ap.add_argument("--chat", default=None, help="飞书 chat_id（默认私聊）")
    a = ap.parse_args()

    tg = resolve(a.target)
    print("[标的] %s ｜ kind=%s ｜ ts=%s ｜ sina=%s" % (tg["disp"], tg["kind"], tg["ts"], tg["sina"]),
          file=sys.stderr)

    day = load_day(tg)
    h1 = load_h1(tg)
    P = float(day["close"].iloc[-1])
    today_pct = (P - float(day["close"].iloc[-2])) / float(day["close"].iloc[-2]) * 100

    fx_d = chan_fx(day, "day")
    fx_h = chan_fx(h1, "h1")
    smc_d = smc_layer(day, k=3)          # 股票/ETF/指数 交易级别=日线 → HTF k=3
    smc_h = smc_layer(h1, k=1)
    vw_d = vwap_layer(day)
    vw_h = vwap_layer(h1)
    ma_d = ma_layer(day, "day")
    ma20_d = ma20_state(day)
    try:
        ma_h = ma_layer(h1, "h1")
    except Exception:
        ma_h = None

    txt, score = render_text(tg["disp"], day, h1, fx_d, fx_h, smc_d, smc_h,
                             vw_d, vw_h, ma_d, today_pct, ma_h, ma20_d)
    print(txt)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        print("\n[存档]", a.out, file=sys.stderr)

    paths = []
    if not a.no_chart:
        d8 = datetime.now().strftime("%Y%m%d")
        safe = tg["disp"].replace(" ", "_").replace("/", "_")
        p1 = os.path.join(a.chart_dir, "tech_%s_day_%s.png" % (safe, d8))
        p2 = os.path.join(a.chart_dir, "tech_%s_h1_%s.png" % (safe, d8))
        draw_chart(tg["disp"], "day", day, fx_d, smc_d, vw_d, p1, a.n_day, ma_d)
        draw_chart(tg["disp"], "h1", h1, fx_h, smc_h, vw_h, p2, a.n_h1, ma_h)
        paths = [p1, p2]
        print("\n[图1 日线]", p1)
        print("[图2 小时线]", p2)

    if a.push:
        ok, chat = push_feishu(tg["disp"], txt, score, paths, a.chat)
        print("\n[飞书]", "已推送" if ok else "推送失败", "→", chat, file=sys.stderr)
        if not ok:
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
