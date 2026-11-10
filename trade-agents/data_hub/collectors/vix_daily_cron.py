#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIX 日频库每日日终入库（工作日 15:35 由 crontab 调度，纯 Python 零 token）
==================================================================
逻辑：
  1. 可选传入目标日期（默认今天）
  2. 判断是否交易日（SSE trade_cal）
  3. 扫描最近 5 个交易日缺口，一并补齐（幂等，已有数据自动跳过）
  4. 调用 backfill_batched() 走 Tushare 统一接口自算 VIX 入库
  5. 打印入库结果 + 数据库统计

crontab:
  35 15 * * 1-5 cd /Coze/Drive/蓁宝/所有对话/主对话/codeact/scripts && /usr/bin/python3 data_hub/collectors/vix_daily_cron.py >> /var/log/vix_daily_cron.log 2>&1

手动回补指定日期:
  python3 data_hub/collectors/vix_daily_cron.py 20261029
"""
import sys
import os
import time
from datetime import datetime, timedelta

# 确保 data_hub 包可以被正确 import
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CODEACT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, CODEACT_DIR)
sys.path.insert(0, SCRIPT_DIR)


def main():
    t0 = time.time()

    # 支持可选日期参数（手动回补）
    target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")

    print(f"[vix_daily_cron] {datetime.now().isoformat()} 开始")
    print(f"[vix_daily_cron] 目标日期: {target_date}")

    from data_hub.sources.tushare_source import TushareSource
    from data_hub.collectors.option_metrics_history import _call_with_ip_retry
    from data_hub.collectors.vix_history import (
        _connect, VIX_KEYS, backfill_batched, get_db_stats,
    )

    src = TushareSource()

    # 1. 计算扫描区间：target_date 往前推 7 天（覆盖周末+节假日）
    dt_target = datetime.strptime(target_date, "%Y%m%d")
    scan_start = (dt_target - timedelta(days=7)).strftime("%Y%m%d")
    scan_end = target_date

    # 2. 查交易日历
    cal = _call_with_ip_retry(
        src._call_pro_api, "trade_cal",
        exchange="SSE", start_date=scan_start, end_date=scan_end,
        is_open="1", fields="cal_date",
    )
    if cal is None or cal.empty:
        print(f"[vix_daily_cron] 交易日历查询失败，退出")
        sys.exit(1)

    trading_days = sorted(cal["cal_date"].astype(str).tolist())
    print(f"[vix_daily_cron] 扫描区间 {scan_start}~{scan_end} 共 {len(trading_days)} 个交易日")

    # 3. 找出有缺口的交易日
    conn = _connect()
    gap_dates = []
    try:
        for td in trading_days:
            count = conn.execute(
                "SELECT COUNT(*) FROM vix_daily WHERE trade_date=? AND vix IS NOT NULL AND vix>0",
                (td,),
            ).fetchone()[0]
            if count < len(VIX_KEYS):
                gap_dates.append((td, count))
    finally:
        conn.close()

    if not gap_dates:
        print(f"[vix_daily_cron] ✅ 扫描区间内无缺口，全部 {len(trading_days)} 个交易日数据完整")
        return

    print(f"[vix_daily_cron] 发现 {len(gap_dates)} 个交易日有缺口:")
    for td, cnt in gap_dates:
        print(f"  {td}: {cnt}/{len(VIX_KEYS)} keys")

    # 4. 回补（backfill_batched 内部已处理跳过逻辑）
    first_gap = gap_dates[0][0]
    last_gap = gap_dates[-1][0]
    try:
        result = backfill_batched(
            keys=VIX_KEYS,
            start_date=first_gap,
            end_date=last_gap,
            verbose=True,
        )
        written = result.get("written", 0)
        print(f"[vix_daily_cron] 写入 {written} 行")
    except Exception as e:
        print(f"[vix_daily_cron] ❌ 回补失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 5. 验证 + 统计
    stats = get_db_stats()
    print(f"[vix_daily_cron] 入库后统计:")
    for k, s in stats.items():
        print(f"  {k}: {s['valid_rows']} 行, {s['start']}~{s['end']}")

    # 6. 校验缺口是否已补齐
    conn = _connect()
    try:
        still_gaps = []
        for td, _ in gap_dates:
            count = conn.execute(
                "SELECT COUNT(*) FROM vix_daily WHERE trade_date=? AND vix IS NOT NULL AND vix>0",
                (td,),
            ).fetchone()[0]
            if count < len(VIX_KEYS):
                still_gaps.append((td, len(VIX_KEYS) - count))
    finally:
        conn.close()

    if still_gaps:
        print(f"[vix_daily_cron] ⚠️ 仍有 {len(still_gaps)} 个交易日未补齐:")
        for td, miss in still_gaps:
            print(f"  {td}: 缺 {miss} 个 key（Tushare opt_daily 可能尚未发布，次日重跑自动补）")
    else:
        print(f"[vix_daily_cron] ✅ 所有缺口已补齐")

    elapsed = round(time.time() - t0, 1)
    print(f"[vix_daily_cron] 耗时 {elapsed}s")


if __name__ == "__main__":
    main()
