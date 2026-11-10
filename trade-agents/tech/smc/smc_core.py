#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMC 多资产模拟交易引擎 · 核心结构逻辑 (1H 级别)
================================================================
复刻《SMC 股票现货多时间框架回测引擎设计文档》(m30 验证版) 的确定性规则，
但按用户要求把交易级别锁定在 **h1 及以上**（h1 / day / week），并扩展到大宗商品期货。

沿用硬约束：仅做多 / 无价格止损（结构状态机离场）/ 单边 8bp 成本 / 全仓单仓 / SMA。

级别映射（文档 4 节）：
  h1  : HTF=day  (bias), MTF=h2 (离场),     SUB=m30 (入场转折)
  day : HTF=month(bias), MTF=week(离场),    SUB=h1
  week: HTF=month(bias), MTF=week(离场),    SUB=day
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

COST_BPS = 8.0
# ⚠️ 2026-10-08 用户口径修正：**每个标的独立本金 50 万**（原先 100 万），
#   期货用保证金交易（1 手名义≈5 万，保证金 10%，最多 10 手=名义 50 万）；
#   股票/ETF 无保证金，50 万实盘买入，必须按**日线**交易。
INIT = float(os.environ.get("SMC_INIT", 500_000.0))   # 单标的实盘本金
CAP_STOCK = 500_000.0       # 股票/ETF 单品种实际投入上限
CAP_FUT_NOTIONAL = 500_000.0  # 期货名义仓位上限（≈10 手）
MARGIN_RATE = 0.10          # 期货保证金率 10%
CAP_FUT_MARGIN = CAP_FUT_NOTIONAL * MARGIN_RATE  # 期货保证金占用上限 5 万/品种
# 风控：有色+贵金属 合计保证金占用 ≤ 金属保证金池 40%（单个金属品种名义 ≤ 40 万）
METAL_CLS = ("贵金属", "有色")
METAL_MARGIN_CAP = 0.40
CAP_METAL_NOTIONAL = 400_000.0
FUT_CONTRACT_NOTIONAL = 50_000.0   # 1 手期货的名义价值（近似，保证金交易）
MAX_FUT_LOTS = 10                  # 单品种最多 10 手（名义 50 万）
K_HTF, K_MTF, K_SUB = 3, 1, 1
RANGE_ATR, GAP_ATR, LIQ_RATIO, ATR_N = 2.0, 0.5, 0.3, 14

# ── 止损规则（用户 2026-10-07 新增）─────────────────────────
# 期货：既要 2×ATR14 跟踪止损，又要相对持仓最高价回撤 5% 止损；ETF/股票：不适用止损
STOP_FUT_ATR = float(os.environ.get("SMC_STOP_ATR", 2.0))   # 期货跌破「持仓期最高价 - N×ATR14」→ 平
STOP_FUT_PCT = float(os.environ.get("SMC_STOP_PCT", 0.05)) # 期货相对持仓最高价回撤 ≥X → 平
STOP_ETF_ATR = None     # ETF/股票不加止损（无价格止损，靠结构离场）
if os.environ.get("SMC_STOP_OFF") == "1":
    STOP_FUT_ATR = STOP_FUT_PCT = None

# ── VWAP 多轨（大佬 2026-11-08 定稿口径）─────────────────────
# 大佬原话：
#   「止盈是两倍标准差，VWAP 加两倍标准差止盈，止损是减一倍标准差。
#     如果你有那个 MSS 了就不用了，你就把这个 VWAP 加减一倍标准差、
#     加减两倍标准差都算出来就行了。先当做一个 VWAP 的技能，
#     但是它也要加那个 MA40 的方向啊」
#
# ── 四条轨全部算出来（供播报/决策）──
#   UP2 = VWAP + 2σ   → 止盈位（主）
#   UP1 = VWAP + 1σ   → 减仓/警戒位
#   DN1 = VWAP - 1σ   → **止损位**
#   DN2 = VWAP - 2σ   → 深跌参考
#
# ── 优先级（大佬明确「有 MSS 就不用 VWAP」）──
#   离场优先级：① MSS 结构离场  >  ② VWAP 止损(DN1) / 止盈(UP2)
#   → 有 MSS 信号时走 MSS；MSS 没触发才看 VWAP 轨。
#
# ── MA40 方向过滤 ──
#   只在 **MA40 向上** 时才认做多；MA40 向下时不做多（股票不能做空，期货可反手）。
#
# 成交量为 0 的 bar 用典型价等权兜底（实测沪银 60m 有 volume=0）。
VWAP_WIN = int(os.environ.get("SMC_VWAP_WIN", 60))       # 滚动窗口（根）
# 三档止盈/止损（大佬 2026-11-08 定稿）：
#   VWAP + 2σ → **止盈一半**（减半仓）
#   VWAP + 3σ → **全部止盈**（清仓）
#   VWAP - 1σ → **全部止损**（清仓）
VWAP_HALF_K = float(os.environ.get("SMC_VWAP_HALF_K", 2.0))  # 减半止盈（+2σ）
VWAP_FULL_K = float(os.environ.get("SMC_VWAP_FULL_K", 3.0))  # 全平止盈（+3σ）
VWAP_SL_K = float(os.environ.get("SMC_VWAP_SL_K", 1.0))      # 止损（-1σ）
VWAP_MID_K = float(os.environ.get("SMC_VWAP_MID_K", 1.0))    # 警戒（+1σ，仅画图）
VWAP_TP = os.environ.get("SMC_VWAP_TP", "1") != "0"      # 总开关
# MSS 结构离场开关（默认开）。做「MSS vs VWAP止损」对比实验时设 SMC_MSS_OFF=1
MSS_ON = os.environ.get("SMC_MSS_OFF", "0") != "1"
# VWAP 止损开关（默认开）。关掉后 VWAP 只做止盈，止损交给 2×ATR
VWAP_SL_ON = os.environ.get("SMC_VWAP_SL_OFF", "0") != "1"
# VWAP 回补加仓开关（大佬 2026-11-08）：
#   「突破 vwap 加两个标准差要止盈一半。直到跌破两倍标准差没，然后再等那个
#     跌回两位标准差以内，并且 VWAP 还是向上的。然后再把那个仓位加满」
#   即：+2σ 减半 → 回落到 +2σ 以内 且 VWAP 向上 → 加回满仓
VWAP_ADDON = os.environ.get("SMC_VWAP_ADDON", "1") != "0"
VWAP_MA_DIR = int(os.environ.get("SMC_VWAP_MA", 40))     # 方向均线（MA40）


def vwap_band(df, win=VWAP_WIN):
    """滚动 VWAP 与 σ（只算基础量，四条轨由调用方组合）。

    VWAP_t = Σ(典型价 × 量) / Σ(量)   （窗口内）
    σ_t    = sqrt( Σ(量 × (典型价 - VWAP)²) / Σ(量) )   （量加权标准差）

    ⚠️ 成交量缺失或全为 0 时，退化为**等权**（典型价算术平均 / 等权标准差），
       不会出 NaN，也不会因为沪银那种 volume=0 的 bar 炸掉。
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    if "volume" in df.columns:
        vol = df["volume"].astype(float)
    elif "vol" in df.columns:
        vol = df["vol"].astype(float)
    else:
        vol = pd.Series(np.nan, index=df.index)
    vol = vol.replace(0, np.nan)          # 0 成交量不参与加权
    out_v = np.full(len(df), np.nan)
    out_s = np.full(len(df), np.nan)
    tpv = (tp * vol).values
    vv = vol.values
    tpv2 = (tp * tp * vol).values
    for i in range(len(df)):
        a = max(0, i - win + 1)
        w_tpv, w_v, w_tpv2 = tpv[a:i + 1], vv[a:i + 1], tpv2[a:i + 1]
        s_v = np.nansum(w_v)
        if s_v > 0:
            vwap = np.nansum(w_tpv) / s_v
            var = np.nansum(w_tpv2) / s_v - vwap * vwap
            out_v[i] = vwap
            out_s[i] = np.sqrt(max(var, 0.0))
        else:
            # 兜底：等权
            px = tp.values[a:i + 1]
            px = px[~np.isnan(px)]
            if len(px):
                out_v[i] = float(np.mean(px))
                out_s[i] = float(np.std(px))
    return out_v, out_s


def vwap_tracks(df, win=VWAP_WIN):
    """一次性算出四条轨 + MA40 方向，供播报与决策共用。

    返回 dict:
      vwap, sigma
      up2 / up1 / dn1 / dn2   四条轨
      ma_dir                  MA40 方向：'up' | 'dn' | 'flat'
      ma_val                  MA40 值
    """
    v, sg = vwap_band(df, win=win)
    ma = df["close"].rolling(VWAP_MA_DIR).mean().values
    n = len(df)
    ma_dir = np.full(n, "flat", dtype=object)
    for i in range(n):
        if i == 0 or not np.isfinite(ma[i]) or not np.isfinite(ma[i - 1]):
            continue
        if ma[i] > ma[i - 1]:
            ma_dir[i] = "up"
        elif ma[i] < ma[i - 1]:
            ma_dir[i] = "dn"
    return {
        "vwap": v, "sigma": sg,
        "up3": v + VWAP_FULL_K * sg,     # 全平止盈
        "up2": v + VWAP_HALF_K * sg,     # 减半止盈
        "up1": v + VWAP_MID_K * sg,      # 警戒（仅参考）
        "dn1": v - VWAP_SL_K * sg,       # 全平止损
        "dn2": v - VWAP_HALF_K * sg,
        "ma": ma, "ma_dir": ma_dir,
    }


# ───────────────────────── 指标 ─────────────────────────
def atr(df, n=ATR_N):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def stops_for(kind):
    """按品种类别返回止损参数 (atr_mult, pct_dd)；None 表示不加止损。
    ETF/股票：不加（用户明确要求）。期货：2×ATR14 跟踪 + 相对最高价回撤 5%。"""
    if kind == "fut":
        return STOP_FUT_ATR, STOP_FUT_PCT
    return STOP_ETF_ATR, None


def stop_lines(pos, i, atr14, kind, last_close=None):
    """返回 (止损价, 止损类型, 距离当前价百分比)；无止损返回 (None,None,None)。
    ETF/股票永远返回 (None,None,None)。"""
    am, pm = stops_for(kind)
    if kind != "fut" or pos is None:
        return None, None, None
    peak = float(pos.get("peak") or 0.0)
    at_ = atr14[i] if (atr14 is not None and i < len(atr14)) else float("nan")
    cands = []
    if am is not None and not np.isnan(at_):
        cands.append((peak - am * at_, f"{am:g}×ATR14 跟踪"))
    if pm is not None:
        cands.append((peak * (1 - pm), f"回撤 {pm * 100:g}%"))
    if not cands:
        return None, None, None
    sp, sk = max(cands, key=lambda x: x[0])      # 取更保守（更高）的止损位
    ref = float(last_close) if last_close is not None else None
    dist = ((sp / ref - 1) * 100) if ref else None
    return sp, sk, dist


def _ext(a, n):
    """扩展极值：e[i] = a[0..i] 最大值（不含 NaN）。"""
    if n <= 0 or a is None:
        return a
    w = a[:n]
    m = np.nanmax(w) if w.size else np.nan
    out = np.full(n, m, dtype=float)
    for i in range(1, n):
        v = a[i]
        if np.isnan(v):
            out[i] = out[i - 1]
        else:
            out[i] = v if v > out[i - 1] else out[i - 1]
    return out


def sma(s, n):
    return s.rolling(n).mean()


def pivots(df, k):
    h = df["high"].values
    l = df["low"].values
    hi, lo = [], []
    for i in range(k, len(df) - k):
        wh = h[i - k:i + k + 1]
        wl = l[i - k:i + k + 1]
        if h[i] == wh.max() and (wh == h[i]).sum() == 1:
            hi.append(i)
        if l[i] == wl.min() and (wl == l[i]).sum() == 1:
            lo.append(i)
    return hi, lo


# ═════════════════ FVG（公允价值缺口）/ OB（订单块）═════════════════
# ⚠️ 2026-11-08 新增：这一层**只做「机会 / 风险提示」**，
#    **绝不参与入场判定**（variant A/B/C/D 的规则一字未改），
#    否则会改掉已经验证过的回测结果。
# 所有函数只用 <= end 的数据 → 无 look-ahead。
# ⚠️ 2026-11-08 大佬关键补充（和均线版同一个道理）：
#   「不一定要穿越，就几根几根K线内穿越，也可能表示多头排列就可以」
#   → 区间也一样：**很久以前形成、但至今没被破的 FVG/OB，仍然是有效结构**，
#     不能因为 age > lookback 就丢掉。
#   做法：窗口放大到 FVG_LOOKBACK_FAR，再按 age 分两档：
#       近端（<= FVG_LOOKBACK）  = 新鲜信号
#       远端（>  FVG_LOOKBACK）  = 长期未破延续信号（只保留 state=active 的，即完全没被填过）
FVG_LOOKBACK = 50      # 近端窗口（新鲜）
FVG_LOOKBACK_FAR = 300 # 远端窗口（找长期未破的）
FVG_MIN_ATR = 0.25     # 缺口幅度至少 0.25×ATR14，过滤噪音
OB_LOOKBACK = 50       # OB 回溯窗口（突破前找反向K线的最远距离）
OB_LOOKBACK_FAR = 300  # OB 远端窗口
FAR_MIN_AGE = 50       # age 超过它就标 sustained（长期未破）
FILL_MITIGATE = 0.5    # 回补 >=50% → mitigated
# ── 「不破 + 向上走」才算买点（2026-11-08 大佬要求）──
# 光有 zone 不破不够，必须有方向确认：最近 CONF_WIN 根内出现上攻迹象，
# 且至少满足 CONF_SCORE 条。反之（压力位）同理反向判断。
CONF_WIN = 3           # 方向确认看最近 N 根
CONF_SCORE = 2         # 「向上」条件至少满足几条才算确认（共 4 条）
CONF_NEAR_ATR = 1.0    # 现价超出区间边沿 > 1×ATR 就算「已走远」，不再算买点（防追高）


def find_fvgs(df, atr_series, lookback=FVG_LOOKBACK, min_atr_ratio=FVG_MIN_ATR,
              end=None, include_filled=False):
    """3 根 K 线失衡缺口（FVG / Fair Value Gap）。

    看涨 FVG：low[i] > high[i-2] → 缺口区间 (high[i-2], low[i])
    看跌 FVG：high[i] < low[i-2] → 缺口区间 (high[i], low[i-2])
    强度 = 缺口幅度 / ATR14（形成那根的 ATR，<=i 已知）

    状态机（形成后只用 i+1..end 的价格打缺口）：
      active     未回补（<50%）
      mitigated  已部分回补（>=50%，未完全）
      filled     完全回补（看涨: 最低价跌破下沿 / 看跌: 最高价涨破上沿）

    include_filled=False 时只返回未 filled 的（默认，符合播报口径）。
    返回 list[dict]，按强度降序；每项含：
      dir / top / bottom / idx / dt / age / fill(0~1) / state / strength(×ATR)
      / width / dist_pct（现价相对近端边沿的%）
    """
    if df is None or len(df) < 3:
        return []
    if atr_series is not None and hasattr(atr_series, "values"):
        atr_series = np.asarray(atr_series, dtype=float)   # Series → 按位置取，避免标签索引坑
    n = len(df)
    i_end = (n - 1) if end is None else max(0, min(int(end), n - 1))
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    dts = df["dt"].values
    px_now = float(c[i_end])
    start = max(2, i_end - int(lookback) + 1)
    out = []
    for i in range(start, i_end + 1):
        if l[i] > h[i - 2]:                       # 看涨缺口
            d, bottom, top = "bull", float(h[i - 2]), float(l[i])
        elif h[i] < l[i - 2]:                     # 看跌缺口
            d, bottom, top = "bear", float(h[i]), float(l[i - 2])
        else:
            continue
        w = top - bottom
        if w <= 0:
            continue
        at = float(atr_series[i]) if (atr_series is not None and i < len(atr_series)) else float("nan")
        strength = (w / at) if (np.isfinite(at) and at > 0) else float("nan")
        if np.isfinite(strength) and strength < min_atr_ratio:
            continue
        # ── 回补进度：从形成后的下一根开始打 ──
        seg_l = l[i + 1:i_end + 1]
        seg_h = h[i + 1:i_end + 1]
        if d == "bull":
            ext = float(np.nanmin(seg_l)) if seg_l.size else float(l[i])
            prog_raw = (top - ext) / w            # ext 跌破 bottom → >1
            tch = np.where(seg_l <= top)[0]
        else:
            ext = float(np.nanmax(seg_h)) if seg_h.size else float(h[i])
            prog_raw = (ext - bottom) / w
            tch = np.where(seg_h >= bottom)[0]
        filled = bool(prog_raw >= 1.0)
        if filled and not include_filled:
            continue
        prog = max(0.0, min(1.0, prog_raw))
        if filled:
            st = "filled"
        elif prog >= FILL_MITIGATE:
            st = "mitigated"
        else:
            st = "active"
        age = int(i_end - i)
        # ⚠️ 长期未破延续信号：age 超过 FAR_MIN_AGE 且仍是 active（完全没被回补过）
        #    = 很久以前形成的缺口，到现在一直撑着/压着，不能因为老就丢掉
        sustained = bool(age > FAR_MIN_AGE and st == "active")
        out.append(dict(dir=d, top=round(top, 6), bottom=round(bottom, 6), width=round(w, 6),
                        idx=int(i), dt=str(dts[i])[:19], age=age,
                        fill=round(prog, 4), state=st,
                        fresh=bool(age <= FVG_LOOKBACK), sustained=sustained,
                        strength=(round(strength, 3) if np.isfinite(strength) else None),
                        dist_pct=_dist_pct(px_now, bottom, top),
                        last_touch_idx=(int(i + 1 + tch[-1]) if tch.size else None),
                        atr=(round(at, 6) if np.isfinite(at) else None)))
    # 排序：新鲜在前（按强度），长期未破的排后面（按 age 升序 = 越新越前）
    out.sort(key=lambda x: (0 if x.get("fresh") else 1,
                            -(x.get("strength") or 0.0) if x.get("fresh") else x["age"]))
    return out


def _dist_pct(px, bottom, top):
    """现价到区间「最近边沿」的百分比：
    价在区间内 → 0；价在上方 → 到上沿的距离(%)；价在下方 → 到下沿的距离(%)。"""
    if not px or top <= bottom:
        return None
    if px >= top:
        return round((px / top - 1) * 100, 2)
    if px <= bottom:
        return round((px / bottom - 1) * 100, 2)
    return 0.0


CONF_LABEL = {
    ("bull", "buy"): "✅ 买点确认",
    ("bull", "wait_up"): "🟡 已回踩·待上攻",
    ("bull", "wait_retest"): "🟡 未破·待回踩",
    ("bull", "broken"): "⚠️ 已破位",
    ("bull", "extended"): "🟠 已走远·不追",
    ("bear", "reject"): "🔻 压制有效",
    ("bear", "wait_up"): "🟡 已回抽·待转弱",
    ("bear", "wait_retest"): "🟡 未破·待回抽",
    ("bear", "breakout"): "🔺 压力被破·转强",
    ("bear", "extended"): "🟠 已跌远·不追空",
}


def confirm_zone(df, z, end=None, win=None, need=None, near_atr=None, atr_val=None):
    """判断单个 FVG / OB 现在算不算「买点（看涨区）」或「压制有效（看跌区）」。

    大佬口径：「它不破，另外得方向向上走才是买点」→ 三段判定：
      A 不破   ：看涨区收盘未跌破下沿 / FVG 未完全回补；
      B 已回踩 ：区间被测试过（OB: touched；FVG: 有触碰记录）；
      C 向上走 ：最近 win 根内至少 need 条成立
                 ① 阳线 ② 收高（> 上一根收盘）
                 ③ 破最近 win 根高点 ④ 收盘站上区间上沿
      D 不走远 ：现价离区间近端边沿 ≤ near_atr×ATR（默认 1.0），
                 否则判 extended（已走远·不追）——大佬要的是「贴着支撑起飞」，
                 价格已经飞出去 1 个 ATR 以上再喊买点就是追高。
    看跌区（压力）全部反向判断。

    只用 <= end 的数据 → 无 look-ahead。返回 dict 或 None。
    """
    if df is None or z is None or len(df) < 5:
        return None
    win = int(win or CONF_WIN)
    need = int(need if need is not None else CONF_SCORE)
    near_atr = float(CONF_NEAR_ATR if near_atr is None else near_atr)
    n = len(df)
    i = (n - 1) if end is None else max(0, min(int(end), n - 1))
    if i < win + 1:
        return None
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    px = float(c[i])
    top, bottom = float(z["top"]), float(z["bottom"])
    d = z["dir"]
    is_fvg = "fill" in z
    # ── A 不破 ──
    if d == "bull":
        broken = (float(z.get("fill", 0) or 0) >= 1.0) if is_fvg else bool(z.get("invalid"))
        broken = broken or (px < bottom)
        retested = (z.get("last_touch_idx") is not None) if is_fvg else bool(z.get("touched"))
    else:
        broken = (float(z.get("fill", 0) or 0) >= 1.0) if is_fvg else bool(z.get("invalid"))
        broken = broken or (px > top)
        retested = (z.get("last_touch_idx") is not None) if is_fvg else bool(z.get("touched"))
    # ── D 是否已走远（超出区间边沿 near_atr×ATR）──
    at = float(atr_val or z.get("atr") or 0)
    far = None
    if at > 0:
        far = ((px - top) / at) if d == "bull" else ((bottom - px) / at)
    too_far = bool(far is not None and far > near_atr)
    # ── C 方向确认（4 条）──
    hh = float(np.nanmax(h[i - win:i])) if i - win >= 0 else float(h[i])
    ll = float(np.nanmin(l[i - win:i])) if i - win >= 0 else float(l[i])
    if d == "bull":
        conds = {
            "yang": bool(px > o[i]),
            "up": bool(px > float(c[i - 1])),
            "newhigh": bool(px > hh),
            "above_top": bool(px >= top),
        }
    else:
        conds = {
            "yin": bool(px < o[i]),
            "down": bool(px < float(c[i - 1])),
            "newlow": bool(px < ll),
            "below_bottom": bool(px <= bottom),
        }
    score = int(sum(1 for v in conds.values() if v))
    # ── 结论 ──
    if d == "bull":
        if broken:
            state = "broken"
        elif too_far:
            state = "extended"
        elif retested and score >= need:
            state = "buy"
        elif retested:
            state = "wait_up"
        else:
            state = "wait_retest"
    else:
        if broken:
            state = "breakout"
        elif too_far:
            state = "extended"
        elif retested and score >= need:
            state = "reject"
        elif retested:
            state = "wait_up"
        else:
            state = "wait_retest"
    return dict(dir=d, kind=("fvg" if is_fvg else "ob"), top=top, bottom=bottom,
                px=px, age=int(i - int(z.get("idx", i))), retested=bool(retested),
                broken=bool(broken), score=score, need=need, conds=conds,
                state=state, label=CONF_LABEL.get((d, state), state),
                far_atr=(round(far, 2) if far is not None else None), near_atr_cap=near_atr,
                # 距下沿多少 ATR（看涨区）/ 距上沿（看跌区），衡量「有多贴近支撑」
                near_atr=(round((px - bottom) / w, 2) if (w := max(top - bottom, 1e-9)) else None))


def confirm_text(sig, px=None):
    """把 confirm_zone 的结果压成一行短文案。"""
    if not sig:
        return ""
    zn = {"yang": "阳线", "up": "收高", "newhigh": "破前高", "above_top": "站上沿",
          "yin": "阴线", "down": "收低", "newlow": "破前低", "below_bottom": "压下沿"}
    hit = "、".join(zn.get(k, k) for k, v in sig["conds"].items() if v) or "无"
    base = (f"{sig['label']}（{sig['kind'].upper()} {_fmt_px(sig['bottom'], px)}~{_fmt_px(sig['top'], px)}"
            f"，{sig['age']}根前，{'已回踩' if sig['retested'] else '未回踩'}")
    if sig.get("state") == "extended" and sig.get("far_atr") is not None:
        return base + f"，已偏离 {abs(sig['far_atr']):.2f}×ATR）"
    return base + f"，方向 {sig['score']}/{len(sig['conds'])}：{hit}）"


def pick_signals(fvgs, obs, df, end=None, max_n=4, win=None, need=None,
                 atr_val=None, near_atr=None):
    """从候选 zone 里挑最该被看见的几条确认信号：
    买点 > 已回踩待上攻 > 破位；同一时刻最多 max_n 条。"""
    order = {"buy": 0, "reject": 0, "wait_up": 1, "breakout": 2,
             "wait_retest": 3, "extended": 4, "broken": 5}
    sigs = []
    for z in (list(fvgs or [])[:3] + list(obs or [])[:3]):
        s = confirm_zone(df, z, end=end, win=win, need=need,
                         atr_val=atr_val, near_atr=near_atr)
        if s:
            sigs.append(s)
    sigs.sort(key=lambda s: (order.get(s["state"], 9), -s["score"], s["age"]))
    return sigs[:max_n]


_pick_signals_win = pick_signals   # 内部别名（Frame.signals 用）


def _last_opposite(o, c, j, lookback, bull):
    """从 j-1 往前找第一根「反向」K线：bull=True 找阴线(close<open)，False 找阳线。
    仅用 j 之前的数据 → 无 look-ahead。"""
    lo = max(0, j - int(lookback))
    for m in range(j - 1, lo - 1, -1):
        if bull and c[m] < o[m]:
            return int(m)
        if (not bull) and c[m] > o[m]:
            return int(m)
    return None


def find_order_blocks(df, hi_idx, lo_idx, atr_series=None, lookback=OB_LOOKBACK,
                      lookback_far=OB_LOOKBACK_FAR,
                      end=None, k=1, include_invalid=False, min_atr_ratio=0.0):
    """订单块（Order Block）。

    看涨 OB：**向上突破结构**（收盘 close[j] > 前一个「已确认」摆动高）之前
             的**最后一根阴线**（close<open），区间 = 该根的 (low, high)
    看跌 OB：向下破结构（收盘 < 前一个已确认摆动低）之前最后一根阳线的区间

    ⚠️ 摆动点确认延迟 k：hi_idx/lo_idx 是「窗口极值」，索引 x 的摆动点要到
       x+k 才确认。此处严格按 x+k <= j 才算可用，避免偷看未来。

    状态（只在 OB 形成之后的价格上判定）：
      touched   已被回踩（看涨: 某根 low <= 上沿；看跌: 某根 high >= 下沿）
      invalid   已失效（看涨: 收盘跌破下沿；看跌: 收盘涨破上沿）

    返回 list[dict]，按新鲜度（age 升序）排序。
    每项：dir / top / bottom / idx / dt / age / break_idx / break_dt
          / touched / invalid / dist_pct / height_atr
    """
    if df is None or len(df) < 5:
        return []
    if atr_series is not None and hasattr(atr_series, "values"):
        atr_series = np.asarray(atr_series, dtype=float)
    n = len(df)
    i_end = (n - 1) if end is None else max(0, min(int(end), n - 1))
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    dts = df["dt"].values
    px_now = float(c[i_end])
    hs = sorted(int(x) for x in (hi_idx or []) if 0 <= int(x) < n)
    ls = sorted(int(x) for x in (lo_idx or []) if 0 <= int(x) < n)
    ph = pl = 0
    last_hi = last_lo = None
    broken_hi, broken_lo = set(), set()
    picked = {}            # (dir, idx) -> dict
    for j in range(1, i_end + 1):
        while ph < len(hs) and hs[ph] + k <= j:
            last_hi, ph = hs[ph], ph + 1
        while pl < len(ls) and ls[pl] + k <= j:
            last_lo, pl = ls[pl], pl + 1
        # 向上破结构（CHoCH/BOS）
        if last_hi is not None and last_hi not in broken_hi and c[j] > h[last_hi]:
            broken_hi.add(last_hi)
            idx = _last_opposite(o, c, j, lookback, bull=True)
            if idx is not None:
                key = ("bull", idx)
                if key not in picked:
                    picked[key] = dict(dir="bull", idx=idx, top=float(h[idx]), bottom=float(l[idx]),
                                       break_idx=int(j), break_pivot=int(last_hi))
        # 向下破结构
        if last_lo is not None and last_lo not in broken_lo and c[j] < l[last_lo]:
            broken_lo.add(last_lo)
            idx = _last_opposite(o, c, j, lookback, bull=False)
            if idx is not None:
                key = ("bear", idx)
                if key not in picked:
                    picked[key] = dict(dir="bear", idx=idx, top=float(h[idx]), bottom=float(l[idx]),
                                       break_idx=int(j), break_pivot=int(last_lo))
    out = []
    for (d, idx), z in picked.items():
        # ⚠️ 2026-11-08：不再因为 age > lookback 直接丢弃。
        #    很久以前形成、至今没失效的 OB 仍是有效结构（sustained），
        #    窗口放到 lookback_far（默认 300），后面按 fresh/sustained 分档。
        if i_end - idx > max(lookback, lookback_far):
            continue
        top, bottom = z["top"], z["bottom"]
        w = top - bottom
        if w <= 0:
            continue
        touched = invalid = False
        m0 = idx + 1
        seg_c = c[m0:i_end + 1]
        if d == "bull":
            touched = bool(seg_c.size and np.nanmin(l[m0:i_end + 1]) <= top)
            invalid = bool(seg_c.size and np.nanmin(seg_c) < bottom)
        else:
            touched = bool(seg_c.size and np.nanmax(h[m0:i_end + 1]) >= bottom)
            invalid = bool(seg_c.size and np.nanmax(seg_c) > top)
        if invalid and not include_invalid:
            continue
        at = float(atr_series[idx]) if (atr_series is not None and idx < len(atr_series)) else float("nan")
        height_atr = (w / at) if (np.isfinite(at) and at > 0) else None
        if height_atr is not None and min_atr_ratio and height_atr < min_atr_ratio:
            continue
        age = int(i_end - idx)
        # 长期未破：age 超过 FAR_MIN_AGE 且没失效
        sustained = bool(age > FAR_MIN_AGE and not invalid)
        out.append(dict(dir=d, top=round(top, 6), bottom=round(bottom, 6), width=round(w, 6),
                        idx=int(idx), dt=str(dts[idx])[:19], age=age,
                        fresh=bool(age <= OB_LOOKBACK), sustained=sustained,
                        break_idx=int(z["break_idx"]), break_dt=str(dts[z["break_idx"]])[:19],
                        touched=bool(touched), invalid=bool(invalid),
                        dist_pct=_dist_pct(px_now, bottom, top),
                        height_atr=(round(height_atr, 3) if height_atr is not None else None)))
    out.sort(key=lambda x: (0 if x.get("fresh") else 1, x["age"]))
    return out


def _fmt_px(v, ref=None):
    """价格自适应精度：按参考价量级定小数位，避免 4421.5432 这种长串占版面。"""
    r = abs(float(ref if ref is not None else v))
    if r >= 1000:
        return f"{v:.1f}"
    if r >= 100:
        return f"{v:.2f}"
    if r >= 10:
        return f"{v:.3f}"
    return f"{v:.4f}"


def zones_text(fvgs, obs, px=None, max_each=2):
    """把 FVG / OB 列表压成两行播报文案（飞书卡片能装下）。
    返回 (lines, summary)：lines 是 2 行文本（FVG 一行 + OB 一行），
    summary 是给播报/日志用的结构化摘要。
    """
    lines = []
    s = {}
    # ── FVG ──
    bulls = [z for z in (fvgs or []) if z["dir"] == "bull"]
    bears = [z for z in (fvgs or []) if z["dir"] == "bear"]
    s["fvg_bull"] = len(bulls)
    s["fvg_bear"] = len(bears)
    if bulls or bears:
        segs = []
        for tag, arr in (("看涨", bulls), ("看跌", bears))[:max_each]:
            if not arr:
                continue
            z = arr[0]                      # 已按强度降序
            stt = {"active": "未回补", "mitigated": "部分回补", "filled": "已填"}.get(z["state"], z["state"])
            st = (f"{z.get('strength'):.2f}×ATR" if z.get("strength") else "—")
            segs.append(f"{tag} {len(arr)} 个（最近 {_fmt_px(z['bottom'], px)}~{_fmt_px(z['top'], px)}"
                        f"@{z['dt'][5:10]}，强度 {st}，回补 {z['fill'] * 100:.0f}%，"
                        f"{z['age']}根前，距现价 {z.get('dist_pct'):+.2f}%，{stt}）")
        lines.append("📐 FVG：" + "｜".join(segs))
    else:
        lines.append("📐 FVG：无未回补缺口")
    # ── OB ──
    bo = [z for z in (obs or []) if z["dir"] == "bull"]
    be = [z for z in (obs or []) if z["dir"] == "bear"]
    s["ob_bull"] = len(bo)
    s["ob_bear"] = len(be)
    if bo or be:
        segs = []
        for tag, arr in (("看涨", bo), ("看跌", be))[:max_each]:
            if not arr:
                continue
            z = arr[0]                      # 最新（age 最小）
            stt = "已回踩未破" if z.get("touched") else "未回踩"
            segs.append(f"{tag} OB {_fmt_px(z['bottom'], px)}~{_fmt_px(z['top'], px)}"
                        f"（{z['dt'][:10]}，{z['age']}根前，{stt}，距现价 {z.get('dist_pct'):+.2f}%）")
        lines.append("🧱 OB：" + "｜".join(segs))
    else:
        lines.append("🧱 OB：无有效订单块")
    s["fvg_top"] = (bulls[0] if bulls else (bears[0] if bears else None))
    s["ob_top"] = (bo[0] if bo else (be[0] if be else None))
    return lines, s


# ───────────────────────── 聚合 ─────────────────────────
def aggregate(df, tf):
    d = df.copy()
    d["dt"] = pd.to_datetime(d["dt"])
    d = d.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    x = d.set_index("dt")
    if tf == "h1":
        out = x[["open", "high", "low", "close", "volume"]]
    else:
        rule = {"m30": "30min", "h2": "2h", "day": "1D", "week": "W-SUN", "month": "1ME"}[tf]
        out = pd.DataFrame({
            "open": x["open"].resample(rule).first(),
            "high": x["high"].resample(rule).max(),
            "low": x["low"].resample(rule).min(),
            "close": x["close"].resample(rule).last(),
            "volume": x["volume"].resample(rule).sum(),
        }).dropna(subset=["close"])
    out = out.reset_index()
    out = out.rename(columns={out.columns[0]: "dt"})
    out["dt"] = pd.to_datetime(out["dt"])
    return out[["dt", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


class Frame:
    """单一级别：确认后摆动点链 + regime + 指标。"""

    def __init__(self, df, k):
        self.df = df.reset_index(drop=True)
        self.k = k
        self.hi_idx, self.lo_idx = pivots(self.df, k)
        self.atr = atr(self.df)
        self.s20 = sma(self.df["close"], 20)
        self.s40 = sma(self.df["close"], 40)
        self.s80 = sma(self.df["close"], 80)
        self.ema50 = self.df["close"].ewm(span=50, adjust=False).mean()

    # ── FVG / OB（只用 <= i 的数据；仅供播报，不参与入场）──
    def _clamp_i(self, i):
        n = len(self.df)
        return (n - 1) if i is None else max(0, min(int(i), n - 1))

    def fvgs(self, i=None, lookback=FVG_LOOKBACK_FAR, min_atr_ratio=FVG_MIN_ATR,
             include_filled=False):
        """第 i 根（含）之前形成的 FVG。新鲜(fresh)在前，长期未破(sustained)在后。

        ⚠️ 默认窗口用 FVG_LOOKBACK_FAR(300)，不是 50 —— 因为很久以前形成、
           至今没被填的缺口仍是有效结构（大佬 2026-11-08 口径）。
        """
        ii = self._clamp_i(i)
        return find_fvgs(self.df, self.atr.values, lookback=lookback,
                         min_atr_ratio=min_atr_ratio, end=ii,
                         include_filled=include_filled)

    def obs(self, i=None, lookback=OB_LOOKBACK_FAR, include_invalid=False):
        """第 i 根（含）之前形成的 OB（按确认延迟 k 判突破）。
        ⚠️ 默认窗口用 OB_LOOKBACK_FAR(300)，长期未失效的 OB 仍算有效。
        """
        ii = self._clamp_i(i)
        return find_order_blocks(self.df, self.hi_idx, self.lo_idx, self.atr.values,
                                 lookback=lookback, end=ii, k=self.k,
                                 include_invalid=include_invalid)

    def zones(self, i=None):
        """{"fvgs": [...], "obs": [...]} 快照（<= i）。"""
        ii = self._clamp_i(i)
        return dict(i=ii, dt=str(self.df["dt"].iloc[ii])[:19],
                    close=float(self.df["close"].iloc[ii]),
                    atr=(float(self.atr.iloc[ii]) if not pd.isna(self.atr.iloc[ii]) else None),
                    fvgs=self.fvgs(ii), obs=self.obs(ii))

    def signals(self, i=None, max_n=4, win=CONF_WIN, need=CONF_SCORE):
        """当前 zone 的「买卖点确认」判断（不破 + 方向确认）。

        返回 list[dict]（见 confirm_zone），按 买点 > 待上攻 > 破位 排序。
        ⚠️ 仅供播报/观察，**不参与入场判定**。
        """
        ii = self._clamp_i(i)
        z = self.zones(ii)
        at = None
        try:
            av = float(self.atr.iloc[ii])
            at = av if np.isfinite(av) else None
        except Exception:
            at = None
        return _pick_signals_win(z["fvgs"], z["obs"], self.df, end=ii,
                                 max_n=max_n, win=win, need=need, atr_val=at)

    def _hi(self, i):
        return [x for x in self.hi_idx if x + self.k <= i]

    def _lo(self, i):
        return [x for x in self.lo_idx if x + self.k <= i]

    def his(self, i):
        return [(str(self.df["dt"].iloc[x])[:19], float(self.df["high"].iloc[x])) for x in self._hi(i)]

    def los(self, i):
        return [(str(self.df["dt"].iloc[x])[:19], float(self.df["low"].iloc[x])) for x in self._lo(i)]

    def regime_at(self, i):
        h = self.df["high"].values
        l = self.df["low"].values
        c = self.df["close"].values
        hidx, lidx = self._hi(i), self._lo(i)
        up = dn = False
        if len(hidx) >= 2:
            up |= h[hidx[-1]] > h[hidx[-2]]
            dn |= h[hidx[-2]] > h[hidx[-1]]
        if len(lidx) >= 2:
            up |= l[lidx[-1]] > l[lidx[-2]]
            dn |= l[lidx[-2]] > l[lidx[-1]]
        if up:
            return "bull"
        if hidx and c[i] > h[hidx[-1]]:
            return "bull"
        if dn:
            return "bear"
        if lidx and l[lidx[-1]] > c[i]:
            return "bear"
        return "flat"


# ───────────────────────── 回测 ─────────────────────────
def _idx_at(fr, dt64):
    a = fr.df["dt"].values
    return int(np.searchsorted(a, np.datetime64(dt64), side="right") - 1)


def run_backtest(ltf, frames, variant="A", kind="stock", cls=None):
    ltf = ltf.reset_index(drop=True)
    sub, mtf, htf = frames["sub"], frames["mtf"], frames["htf"]
    n = len(ltf)
    o = ltf["open"].values
    h = ltf["high"].values
    l = ltf["low"].values
    c = ltf["close"].values
    t = ltf["dt"].values
    v = ltf["volume"].values if "volume" in ltf.columns else np.full(n, np.nan)
    s20 = ltf["close"].rolling(20).mean().values
    s40 = ltf["close"].rolling(40).mean().values
    s80 = ltf["close"].rolling(80).mean().values

    mtf_i = [_idx_at(mtf, x) for x in t]
    sub_i = [_idx_at(sub, x) for x in t]
    htf_i = [_idx_at(htf, x) for x in t]

    init_cap = INIT
    cash = init_cap      # 股票=现金；期货=保证金池
    units = 0.0
    state = "flat"          # flat | half | full
    consumed = set()        # 已消费 SUB 摆动高点 dt
    pending_entry = pending_add = False
    last_align = False
    pos = None              # 当前持仓 dict
    trades = []
    equity = []
    lev_series = []        # 杠杆率序列（名义持仓 / 总权益）
    margin_series = []     # 保证金占用序列（仅期货）
    events = []             # 增量事件流（供模拟交易引擎通知用）
    signals = []            # 信号日志（含被过滤的：含原因）
    atr14 = atr(ltf).values
    # VWAP 目标止盈带（入场后触及 VWAP + 1σ 就平）
    # VWAP 多轨（四条轨 + MA40 方向），供止盈/止损/播报共用
    vw = vwap_tracks(ltf) if VWAP_TP else None
    hi_ext = _ext(h, n)
    last_sig = None

    # ── 仓位 / 保证金 控制（2026-10-08 重写）──────────────────
    # 股票/ETF：50 万实盘买入（无杠杆、无保证金）
    # 期货：保证金交易 —— cash 即「保证金池」；1 手名义 5 万（保证金 5 千），最多 10 手
    #   equity = 保证金池 + (当前名义 - 建仓名义) + 建仓时占用保证金
    #   = 保证金池 + 浮动盈亏 + 占用保证金（见循环尾部）
    cap_stock = CAP_STOCK
    max_lots = MAX_FUT_LOTS

    def _deploy_cash(px, _cls=None):
        """股票/ETF 本次最多可投入的现金（单品种 ≤ 50 万）。"""
        room = cap_stock - units * px
        return max(0.0, min(cash, room))

    def log_sig(dt, k, tier, reason, px=None):
        signals.append(dict(dt=str(dt)[:19], kind=k, tier=tier, reason=reason,
                            px=(float(px) if px is not None else None)))

    def close_pos(px, reason, dt):
        nonlocal cash, units, pos, state
        gross = units * px
        fee = gross * COST_BPS / 1e4
        if kind == "fut" and pos.get("notional0"):
            # 保证金交易：退回占用保证金 + 价格盈亏(名义变动) - 平仓手续费
            n0, m0 = pos["notional0"], pos.get("margin0", 0.0)
            cash_in = m0 + (gross - n0) - fee
            base = n0
        else:
            cash_in = gross - fee
            base = pos["cash_out"] or 1.0
        cash += cash_in
        pos["cash_in"] += cash_in
        pos["exit_dt"] = str(dt)[:19]
        pos["exit_px"] = float(px)
        pos["reason"] = reason
        pos["pnl"] = round(pos["cash_in"] - pos["cash_out"], 2)
        pos["pnl_pct"] = round(pos["pnl"] / base * 100, 2)
        trades.append(pos)
        events.append(dict(kind="平仓", dt=pos["exit_dt"], px=float(px), reason=reason,
                           pnl=pos["pnl"], pnl_pct=pos["pnl_pct"],
                           entry_dt=pos["entry_dt"], entry_px=pos["entry_px"]))
        pos = None
        units = 0.0
        state = "flat"

    for i in range(n):
        # ---- 执行上一根确认的动作（T 确认 → T+1 开盘）----
        if pending_entry and state == "flat" and o[i] > 0:
            px = o[i]
            if kind == "fut":
                # 保证金交易：按固定名义/手 折算手数（最多 10 手 → 名义≈50 万）
                lots = max(1, int(round(CAP_FUT_NOTIONAL / FUT_CONTRACT_NOTIONAL)))
                qty = lots * FUT_CONTRACT_NOTIONAL / px     # units（=名义/价格）
                n0 = qty * px
                m0 = n0 * MARGIN_RATE
                cost = m0 + n0 * COST_BPS / 1e4
                if cost > cash:
                    pending_entry = False
                    continue
                cash -= cost
                units = qty
                pos = dict(entry_dt=str(t[i])[:19], entry_px=float(px), entry_units=round(qty, 4),
                           cash_out=cost, cash_in=0.0, notional0=n0, margin0=m0,
                           adds=[], xch_events=[],
                           peak=float(px), stop_atr=None, stop_pct=None, entry_bar=i,
                           exit_dt=None, exit_px=None, reason=None, pnl=None, pnl_pct=None)
            else:
                _cash = _deploy_cash(px)
                if _cash <= 1:
                    pending_entry = False
                    continue
                qty = _cash / (px * (1 + COST_BPS / 1e4))
                cost = qty * px * (1 + COST_BPS / 1e4)
                cash -= cost
                units = qty
                pos = dict(entry_dt=str(t[i])[:19], entry_px=float(px), entry_units=round(qty, 4),
                           cash_out=cost, cash_in=0.0, notional0=qty * px, margin0=0.0,
                           adds=[], xch_events=[],
                           peak=float(px), stop_atr=None, stop_pct=None, entry_bar=i,
                           exit_dt=None, exit_px=None, reason=None, pnl=None, pnl_pct=None)
            state = "full"
            events.append(dict(kind="开仓", dt=pos["entry_dt"], px=float(px), units=round(qty, 4),
                                   reason="SUB做多CHoCH", entry_dt=pos["entry_dt"]))
            pending_entry = False
        if pending_add and state == "half" and o[i] > 0 and cash > 1:
            px = o[i]
            # ⚠️ 2026-11-08 大佬定稿：
            #   「你这个加满是要根据原来的那个策略再定。你本层策略是加满，
            #     就是我无非就是相当于你把原来丢那一半要补回来」
            #   → 加回**只补回之前减掉的那一半**（pos["half_units"]），
            #     不是用光所有现金把仓位放大。原仓位多少就回到多少。
            _want = float((pos or {}).get("half_units") or 0.0)
            if _want <= 0:
                # 没有记录（老逻辑减半）→ 按「当前持仓等量」补回
                _want = float(units)
            if kind == "fut":
                # 期货按手数还原：只补回被减掉的那一半对应手数
                cur_lots = int(round(units * px / FUT_CONTRACT_NOTIONAL))
                add_lots = max(0, max_lots - cur_lots)
                want_lots = max(0, int(round(_want * px / FUT_CONTRACT_NOTIONAL)))
                lots = min(add_lots, want_lots)
                if lots <= 0:
                    pending_add = False
                else:
                    qty = lots * FUT_CONTRACT_NOTIONAL / px
                    n_add = qty * px
                    m_add = n_add * MARGIN_RATE
                    cost = m_add + n_add * COST_BPS / 1e4
                    if cost > cash:
                        # 现金不够 → 按现有现金能补多少补多少，绝不放大超出原仓位
                        qty = max(0.0, (cash - 1.0) / (px * MARGIN_RATE + px * COST_BPS / 1e4))
                        qty = min(qty, _want)          # 上限 = 被减掉的量
                        n_add = qty * px
                        m_add = n_add * MARGIN_RATE
                        cost = m_add + n_add * COST_BPS / 1e4
                    if qty * px <= 1:
                        pending_add = False
                    else:
                        cash -= cost
                        units += qty
                        if pos:
                            pos["notional0"] += n_add
                            pos["margin0"] += m_add
                            pos["cash_out"] += cost
                            pos["adds"].append(dict(dt=str(t[i])[:19], px=float(px), units=round(qty, 4)))
                            events.append(dict(kind="加回满仓", dt=str(t[i])[:19], px=float(px),
                                               units=round(qty, 4),
                                               reason=f"补回减掉的{_want:.0f}单位（VWAP+2σ内且向上）"))
                            pos.pop("vwap_half_reason", None)
                            pos.pop("half_units", None)
                        state = "full"
                        pending_add = False
            else:
                # 股票/ETF：补回原来那一半的单位数（上限就是 _want）
                want_qty = _want
                cost = want_qty * px * (1 + COST_BPS / 1e4)
                if cost > cash:
                    want_qty = min(_want, max(0.0, cash / (px * (1 + COST_BPS / 1e4))))
                    cost = want_qty * px * (1 + COST_BPS / 1e4)
                if want_qty * px <= 1:
                    pending_add = False
                else:
                    cash -= cost
                    units += want_qty
                    state = "full"
                    if pos:
                        pos["adds"].append(dict(dt=str(t[i])[:19], px=float(px), units=round(want_qty, 4)))
                        pos["cash_out"] += cost
                        events.append(dict(kind="加回满仓", dt=str(t[i])[:19], px=float(px),
                                           units=round(want_qty, 4),
                                           reason=f"补回减掉的{_want:.0f}单位（VWAP+2σ内且向上）"))
                        pos.pop("vwap_half_reason", None)
                        pos.pop("half_units", None)
                    pending_add = False

        mi, si, hi_ = mtf_i[i], sub_i[i], htf_i[i]

        # ---- 更新持仓期最高价 + 期货止损位 ----
        if state in ("full", "half") and pos is not None:
            pos["peak"] = max(float(pos.get("peak") or 0.0), float(h[i]))
            at_ = atr14[i] if i < len(atr14) else np.nan
            if kind == "fut":
                pos["stop_atr"] = (pos["peak"] - STOP_FUT_ATR * at_) if (not np.isnan(at_) and STOP_FUT_ATR) else None
                pos["stop_pct"] = (pos["peak"] * (1 - STOP_FUT_PCT)) if STOP_FUT_PCT else None

        # ---- 止损（仅期货）：2×ATR14 跟踪 或 相对最高价回撤 5% ----
        if kind == "fut" and state in ("full", "half") and pos is not None \
                and pos.get("entry_bar") != i:
            stop = None
            reason = None
            sa, sp = pos.get("stop_atr"), pos.get("stop_pct")
            if sa is not None and l[i] <= sa:
                stop = max(min(sa, float(h[i])), float(l[i]))
                reason = "止损(2×ATR)"
            elif sp is not None and l[i] <= sp:
                stop = max(min(sp, float(h[i])), float(l[i]))
                reason = "止损(回撤5%)"
            if stop is not None:
                close_pos(stop, reason, t[i])
                if i == n - 1:
                    log_sig(t[i], "stop", "high", reason, stop)

        # ---- 💰 VWAP 多轨离场（MSS 优先；MSS 没触发才看 VWAP）----
        # 大佬 2026-11-08 定稿：
        #   「止盈是两倍标准差，VWAP 加两倍标准差止盈，止损是减一倍标准差。
        #     如果你有那个 MSS 了就不用了，你就把这个 VWAP 加减一倍标准差、
        #     加减两倍标准差都算出来就行了。……它也要加那个 MA40 的方向啊」
        #
        # 优先级：① MSS/XCH 结构离场（在上面先判，已平仓则跳过这里）> ② VWAP 轨
        #   止盈 UP2 = VWAP + 2σ    止损 DN1 = VWAP - 1σ
        #   警戒 UP1 = VWAP + 1σ    深跌 DN2 = VWAP - 2σ（仅播报）
        _mss_fired = False
        if state in ("full", "half") and mi >= 0 and pos is not None \
                and pos.get("entry_bar") != i:
            m_last = float(mtf.df["close"].values[mi])
            los = mtf.los(mi)
            his = mtf.his(mi)
            _mss_ready = MSS_ON and len(los) >= 2 and los[-2][1] > m_last
            if _mss_ready:
                close_pos(c[i], "MSS清仓", t[i])
                _mss_fired = True
            elif MSS_ON and los and los[-1][1] > m_last:
                if len(los) == 1:
                    close_pos(c[i], "XCH全清(仅1低点)", t[i])
                    _mss_fired = True
                elif state == "full":
                    px = c[i]
                    half = units / 2.0
                    if kind == "fut":
                        n0, m0 = pos.get("notional0", units * pos["entry_px"]), pos.get("margin0", 0.0)
                        leg_n, leg_m = n0 / 2.0, m0 / 2.0
                        leg_net = leg_m + (half * px - leg_n) - half * px * COST_BPS / 1e4
                        cash += leg_net
                        pos["notional0"] = n0 - leg_n
                        pos["margin0"] = m0 - leg_m
                        pos["cash_out"] *= 0.5
                    else:
                        leg_net = half * px * (1 - COST_BPS / 1e4)
                        cash += leg_net
                    units -= half
                    state = "half"
                    # 统一：任何减半都用 VWAP 条件加回（2026-11-08 大佬口径）
                    pos["vwap_half_reason"] = "xch"
                    # 记录被减掉的单位数：加回只补回这一半
                    pos["half_units"] = half
                    pos["xch_events"].append(dict(dt=str(t[i])[:19], px=float(px), units=round(half, 4), net=round(leg_net, 2)))
                    pos["cash_in"] += leg_net
                    events.append(dict(kind="减半(XCH)", dt=str(t[i])[:19], px=float(px), units=round(half, 4),
                                       reason="MTF跌破最近摆动低点"))
            # ⚠️ 2026-11-08 改：不再用「MTF前高」加回，统一走 VWAP 条件
            #   （回落到 +2σ 以内 且 VWAP 向上 → 加满），见下方 VWAP 回补加仓段

        # ---- 💰 VWAP 三档离场（MSS 没触发时才有资格）----
        # 大佬 2026-11-08 定稿：
        #   「VWAP 加两倍标准差止盈一半，加三倍标准差全部止盈。
        #     VWAP 减一倍标准差是止损，全部止损」
        #
        #   +2σ → 减半（state: full → half）
        #   +3σ → 全平（half/full 都清）
        #   -1σ → 全平止损
        # 优先级：MSS/XCH 已触发（_mss_fired=True）→ 整段跳过
        if (not _mss_fired) and VWAP_TP and vw is not None \
                and state in ("full", "half") and pos is not None \
                and pos.get("entry_bar") != i:
            v_now = vw["vwap"][i]
            s_now = vw["sigma"][i]
            if np.isfinite(v_now) and np.isfinite(s_now) and s_now > 0:
                up3 = vw["up3"][i]      # +3σ 全平
                up2 = vw["up2"][i]      # +2σ 减半
                dn1 = vw["dn1"][i]      # -1σ 全平止损
                min_tp = float(pos["entry_px"]) * (1 + 2 * COST_BPS / 1e4)

                # ── ① 止损：跌破 VWAP - 1σ → 全部止损（可用 SMC_VWAP_SL_OFF=1 关闭）──
                if VWAP_SL_ON and np.isfinite(dn1) and l[i] <= dn1:
                    fill = min(dn1, float(o[i])) if o[i] > 0 else dn1
                    fill = max(fill, float(l[i]))
                    close_pos(fill, f"VWAP止损(-{VWAP_SL_K:g}σ)", t[i])
                    if i == n - 1:
                        log_sig(t[i], "exit", "high",
                                f"VWAP止损 跌破{v_now:.2f}-{VWAP_SL_K:g}σ={dn1:.2f}", fill)

                else:
                    # ── ② 止盈：先看 +3σ（全平），再看 +2σ（减半）──
                    # 同一根 bar 若已到 +3σ，直接全平，不做「先减半再全平」的无谓动作。
                    _hit3 = np.isfinite(up3) and h[i] >= up3
                    _hit2 = np.isfinite(up2) and h[i] >= up2

                    # ②-a 全平止盈：+3σ（full / half 都清）
                    if _hit3:
                        if not pos.get("vwap_armed3"):
                            eb = pos.get("entry_bar", i)
                            ev3 = vw["up3"][eb] if eb < n else np.nan
                            entry_ref = float(o[eb + 1]) if eb + 1 < n and o[eb + 1] > 0 else float(pos["entry_px"])
                            pos["vwap_armed3"] = bool(np.isfinite(ev3) and entry_ref < ev3)
                        if pos.get("vwap_armed3"):
                            fill = max(up3, float(o[i])) if o[i] > 0 else up3
                            fill = min(fill, float(h[i]))
                            if fill > min_tp:
                                close_pos(fill, f"VWAP止盈全平(+{VWAP_FULL_K:g}σ)", t[i])
                                if i == n - 1:
                                    log_sig(t[i], "exit", "high",
                                            f"VWAP全平 触及{v_now:.2f}+{VWAP_FULL_K:g}σ={up3:.2f}", fill)

                    # ②-b 减半止盈：+2σ（仅满仓，且未到 +3σ）
                    elif _hit2 and state == "full":
                        if not pos.get("vwap_armed2"):
                            eb = pos.get("entry_bar", i)
                            ev2 = vw["up2"][eb] if eb < n else np.nan
                            entry_ref = float(o[eb + 1]) if eb + 1 < n and o[eb + 1] > 0 else float(pos["entry_px"])
                            pos["vwap_armed2"] = bool(np.isfinite(ev2) and entry_ref < ev2)
                        if pos.get("vwap_armed2"):
                            fill = max(up2, float(o[i])) if o[i] > 0 else up2
                            fill = min(fill, float(h[i]))
                            if fill > min_tp:
                                px = fill
                                half = units / 2.0
                                if kind == "fut":
                                    n0, m0 = pos.get("notional0", units * pos["entry_px"]), pos.get("margin0", 0.0)
                                    leg_n, leg_m = n0 / 2.0, m0 / 2.0
                                    leg_net = leg_m + (half * px - leg_n) - half * px * COST_BPS / 1e4
                                    cash += leg_net
                                    pos["notional0"] = n0 - leg_n
                                    pos["margin0"] = m0 - leg_m
                                    pos["cash_out"] *= 0.5
                                else:
                                    leg_net = half * px * (1 - COST_BPS / 1e4)
                                    cash += leg_net
                                units -= half
                                state = "half"
                                # 标记：这次减半来自 VWAP+2σ，等「回落到 2σ 内 + VWAP向上」再加回
                                pos["vwap_half_reason"] = "tp2sig"
                                # ⚠️ 记录被减掉的单位数：加回时**只补回这一半**，不放大仓位
                                pos["half_units"] = half
                                pos["xch_events"].append(dict(dt=str(t[i])[:19], px=float(px),
                                                              units=round(half, 4), net=round(leg_net, 2)))
                                pos["cash_in"] += leg_net
                                events.append(dict(kind="减半(VWAP+2σ)", dt=str(t[i])[:19], px=float(px),
                                                   units=round(half, 4),
                                                   reason=f"VWAP+{VWAP_HALF_K:g}σ 止盈一半"))

        # ---- 💰 VWAP 回补加仓（大佬 2026-11-08 定稿）----
        #   「突破 vwap 加两个标准差要止盈一半。直到跌破两倍标准差没，
        #     然后再等那个跌回两位标准差以内，并且 VWAP 还是向上的。然后再把那个仓位加满」
        #
        # 触发链：+2σ 减半 → 价格回落到 +2σ 以内 → VWAP 向上 → 加回满仓
        # 只对「因 +2σ 止盈而减半」的持仓生效（MSS/XCH 减半不在此列）
        if VWAP_ADDON and VWAP_TP and vw is not None \
                and state == "half" and pos is not None \
                and pos.get("vwap_half_reason") in ("tp2sig", "xch") \
                and cash > 1 and pos.get("entry_bar") != i:
            v_now = vw["vwap"][i]
            s_now = vw["sigma"][i]
            up2_now = vw["up2"][i]
            if np.isfinite(v_now) and np.isfinite(s_now) and s_now > 0:
                # 条件①：收盘已回到 +2σ 之内（上方不带）
                back_inside = c[i] < up2_now
                # 条件②：VWAP 方向向上（较前一根抬高）
                vwap_up = False
                if i >= 1 and np.isfinite(vw["vwap"][i - 1]):
                    vwap_up = v_now > vw["vwap"][i - 1]
                if back_inside and vwap_up:
                    pending_add = True
                    events.append(dict(kind="待加回(VWAP)", dt=str(t[i])[:19], px=float(c[i]),
                                       units=None,
                                       reason=f"回落至+2σ内({c[i]:.2f}<{up2_now:.2f})且VWAP向上"))

        # ---- 入场触发（SUB 做多 CHoCH）----
        if state == "flat" and si >= 0:
            h_dir = htf.regime_at(hi_)
            if h_dir == "flat":
                h_dir = mtf.regime_at(mi)
            bias_ok = h_dir in ("bull", "flat")
            if h_dir == "bear":
                bias_ok = False
            # ⚠️ MA40 方向过滤（大佬 2026-11-08：「但是它也要加那个 MA40 的方向啊」）
            #   MA40 向下 → 不做多（股票不能做空就空仓；期货可反手做空由外部处理）
            ma40_dir = (vw["ma_dir"][i] if (VWAP_TP and vw is not None) else "flat")
            if ma40_dir == "dn":
                bias_ok = False
                if i == n - 1:
                    log_sig(t[i], "check", "low", "MA40向下·不做多", c[i])
            shis_probe = sub.his(si)
            if (not bias_ok) and i == n - 1 and shis_probe:
                k0, lv0 = shis_probe[-1]
                if k0 not in consumed and c[i] > lv0:
                    log_sig(t[i], "check", "low", f"方向过滤: HTF/MTF偏空(h_dir={h_dir})", c[i])
            # h1 级别 EMA50 价格过滤
            if bias_ok and h_dir in ("bull", "flat") and hi_ >= 0:
                e50 = htf.ema50.values[hi_]
                if not np.isnan(e50) and c[i] <= e50:
                    bias_ok = False
                    if i == n - 1 and shis_probe:
                        k0, lv0 = shis_probe[-1]
                        if k0 not in consumed and c[i] > lv0:
                            log_sig(t[i], "check", "low", "均线过滤: 价<=HTF EMA50", c[i])
            if bias_ok:
                shis = sub.his(si)
                if shis:
                    key, lvl = shis[-1]
                    if key not in consumed and c[i] > lvl:
                        ok = True
                        ok_reason = "SUB做多CHoCH"
                        at = sub.atr.iloc[si]
                        if not np.isnan(at) and at > 0:
                            if (h[i] - l[i]) > RANGE_ATR * at:
                                ok = False
                                ok_reason = f"幅度过滤: 单根振幅>{RANGE_ATR:g}×ATR"
                            if i > 0 and abs(o[i] - c[i - 1]) > GAP_ATR * at:
                                ok = False
                                ok_reason = f"缺口过滤: 开盘跳空>{GAP_ATR:g}×ATR"
                        v20 = np.nanmean(v[max(0, i - 19):i + 1])
                        if not np.isnan(v20) and v20 > 0 and not np.isnan(v[i]) and v[i] < LIQ_RATIO * v20:
                            ok = False
                            ok_reason = "流动性过滤: 量<20日均量30%"
                        if ok and variant in ("B", "C", "D"):
                            if np.isnan(s40[i]) or c[i] <= s40[i]:
                                ok = False
                                ok_reason = "趋势过滤: 价<=SMA40"
                            if variant == "C" and (np.isnan(s20[i]) or np.isnan(s80[i]) or not (s20[i] > s40[i] > s80[i])):
                                ok = False
                                ok_reason = "排列过滤: 需SMA20>SMA40>SMA80"
                        if ok:
                            consumed.add(key)
                            pending_entry = True
                            log_sig(t[i], "check", "high", "SUB做多CHoCH → 排队入场", c[i])
                        elif i == n - 1:
                            log_sig(t[i], "check", "low", ok_reason, c[i])
                    elif i == n - 1 and key not in consumed:
                        log_sig(t[i], "check", "mid", f"未突破 SUB 前高 {lvl:.4g}（现价 {c[i]:.4g}）", c[i])
        # ---- 变体 D：MA 排列翻转 ----
        if variant == "D" and state == "flat":
            align = (not np.isnan(s20[i]) and not np.isnan(s40[i]) and not np.isnan(s80[i])
                     and s20[i] > s40[i] > s80[i])
            if align and not last_align:
                pending_entry = True
            last_align = align

        if kind == "fut":
            # 期货：权益 = 保证金池 + 浮动盈亏 + 占用保证金
            n_cur = units * float(c[i])
            n0 = pos.get("notional0", 0.0) if pos is not None else 0.0
            m0 = pos.get("margin0", 0.0) if pos is not None else 0.0
            eq = cash + (n_cur - n0) + m0
            lev = (n_cur / eq) if eq > 0 else 0.0
            margin_used = n_cur * MARGIN_RATE
        else:
            eq = cash + units * float(c[i])
            lev = (units * float(c[i]) / eq) if eq > 0 else 0.0
            margin_used = 0.0
        equity.append(eq)
        lev_series.append(lev)
        margin_series.append(margin_used)

    # ── 当前持仓的止损位（仅期货）与待执行动作（供通知/看板用）──
    stop_px = stop_kind = stop_dist = None
    if state in ("full", "half") and pos is not None:
        stop_px, stop_kind, stop_dist = stop_lines(pos, n - 1, atr14, kind, last_close=float(c[-1]))
    atr_last = float(atr14[-1]) if (atr14 is not None and len(atr14) and not np.isnan(atr14[-1])) else None

    return dict(state=state, cash=round(cash, 2), units=round(units, 4), pos=pos, init=init_cap,
                equity_curve=pd.DataFrame({"dt": ltf["dt"], "equity": equity,
                                          "leverage": lev_series, "margin": margin_series}),
                trades=trades, events=events, signals=signals,
                stop_px=stop_px, stop_kind=stop_kind, stop_dist=stop_dist,
                atr_last=atr_last, pending_entry=bool(pending_entry), pending_add=bool(pending_add),
                last_dt=str(t[-1])[:19], last_close=float(c[-1]))


def summarize(res):
    init = res.get("init") or INIT
    eq = res["equity_curve"]
    total = eq["equity"].iloc[-1] / init - 1
    dd = (eq["equity"] / eq["equity"].cummax() - 1).min()
    closed = [t for t in res["trades"] if t.get("exit_dt")]
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    num = sum(t["pnl"] for t in closed if (t.get("pnl") or 0) > 0)
    den = -sum(t["pnl"] for t in closed if (t.get("pnl") or 0) < 0)
    pf = (num / den) if den > 0 else (None if num > 0 else 0.0)
    return dict(total_ret=round(total * 100, 2), max_dd=round(dd * 100, 2),
                n_trades=len(closed), win_rate=round(100 * len(wins) / len(closed), 1) if closed else 0.0,
                pf=(round(pf, 2) if pf is not None else "inf"),
                final_equity=round(eq["equity"].iloc[-1], 0),
                max_leverage=round(float(eq["leverage"].max()) * 100, 1) if "leverage" in eq else None,
                init_capital=init,
                holding=res["state"])


def build_frames(base_df, level="h1"):
    """base_df = 母数据（h1 级别: 60m 母数据；day 级别: 日线母数据）。
    角色周期等于母周期时不再聚合，直接用。"""
    from smc_config import LEVEL_MAP, base_freq
    m = LEVEL_MAP[level]
    base = base_freq(level)
    kk = {"htf": K_HTF, "mtf": K_MTF, "sub": K_SUB}
    fr = {}
    for role in ("htf", "mtf", "sub"):
        tf = m[role]
        if tf == base:
            agg = base_df.reset_index(drop=True)
        elif base == "1d" and tf in ("h1", "60m"):
            # 日线是母数据，无法下钻到小时：退而用日线代替 SUB
            agg = base_df.reset_index(drop=True)
        else:
            agg = aggregate(base_df, tf)
        fr[role] = Frame(agg, kk[role])
    return fr


if __name__ == "__main__":
    # 演示/自测入口：python3 -m smc.smc_core  或  python3 smc/smc_fvg_selftest.py
    import runpy
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "smc_fvg_selftest.py"), run_name="__main__")
