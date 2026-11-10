"""
data_hub 冒烟测试每日运行 + 代码 Bug 告警
===========================================

每日运行 smoke_test.py，解析结果 JSON，仅在出现 code_bug（代码 Bug）
失败类型时推送飞书告警。其它失败类型（source_down / not_implemented /
insufficient_data）不告警，因为降级能兜住。

用法：
    python smoke_test_daily.py display_only                    # 全量测试 + 告警
    python smoke_test_daily.py no_reply                        # 静默模式（推荐每日定时）
    python smoke_test_daily.py display_only stock_daily        # 只测指定采集器
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

# ============================================================
# 路径配置
# ============================================================

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATAHUB_DIR = os.path.dirname(_SCRIPT_DIR)
_CODEACT_SCRIPTS_DIR = os.path.dirname(_DATAHUB_DIR)
_BASE_DIR = os.path.dirname(os.path.dirname(_CODEACT_SCRIPTS_DIR))
_OUTPUT_DIR = os.path.join(_CODEACT_SCRIPTS_DIR, "output")

# 确保 feishu_push 可导入
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

SMOKE_TEST_SCRIPT = os.path.join(_SCRIPT_DIR, "smoke_test.py")

# 失败类型常量（与 smoke_test.py 保持一致）
FAILURE_CODE_BUG = "code_bug"
FAILURE_SOURCE_DOWN = "source_down"
FAILURE_DATA_INSUFFICIENT = "data_insufficient"
FAILURE_NOT_IMPLEMENTED = "not_implemented"


# ============================================================
# 冒烟测试运行
# ============================================================

def run_smoke_test(output_path: str, collector_filter: str = "",
                   timeout: int = 1200) -> tuple[int, str]:
    """
    运行 smoke_test.py，返回 (exit_code, stdout)。

    - 用 subprocess 调用
    - 退出码 0 = 全部通过，1 = 有失败
    - 结果写入 output_path（JSON 格式）
    """
    cmd = [sys.executable, SMOKE_TEST_SCRIPT, "--output", output_path]
    if collector_filter:
        cmd += ["--collector", collector_filter]

    print(f"[执行] {' '.join(cmd)}")
    print(f"[超时] {timeout}s")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    # 打印 stdout 便于排查
    print(result.stdout[-3000:])
    if result.stderr:
        print("--- STDERR ---")
        print(result.stderr[-2000:])

    return result.returncode, result.stdout


# ============================================================
# 结果解析
# ============================================================

def load_result(output_path: str) -> dict:
    """读取冒烟测试结果 JSON。"""
    if not os.path.exists(output_path):
        raise FileNotFoundError(f"结果文件不存在: {output_path}")
    with open(output_path, "r", encoding="utf-8") as f:
        return json.load(f)


def count_failures_by_type(result: dict) -> dict[str, int]:
    """按失败类型统计数量。"""
    counts = {
        FAILURE_CODE_BUG: 0,
        FAILURE_SOURCE_DOWN: 0,
        FAILURE_DATA_INSUFFICIENT: 0,
        FAILURE_NOT_IMPLEMENTED: 0,
    }
    for case in result.get("failed_cases", []):
        ft = case.get("failure_type", "unknown")
        counts[ft] = counts.get(ft, 0) + 1
    return counts


def get_code_bug_cases(result: dict) -> list[dict]:
    """获取所有 code_bug 类型的失败用例。"""
    return [
        c for c in result.get("failed_cases", [])
        if c.get("failure_type") == FAILURE_CODE_BUG
    ]


# ============================================================
# 飞书告警
# ============================================================

def build_alert_message(
    bug_cases: list[dict],
    result: dict,
    output_path: str,
) -> str:
    """
    构造飞书告警文本（Markdown 格式）。
    包含：Bug 数量、每个 Bug 的采集器+数据源+错误摘要、完整结果路径。
    """
    ts = result.get("timestamp", "")
    total = result.get("total_cases", 0)
    passed = result.get("passed", 0)
    failed = result.get("failed", 0)

    lines = [
        "🐛 **data_hub 冒烟测试 - 代码 Bug 告警**",
        "",
        f"测试时间：{ts}",
        f"总用例：{total} | 通过：{passed} | 失败：{failed}",
        f"**代码 Bug：{len(bug_cases)} 个** ⚠️",
        "",
        "---",
        "",
        "**Bug 明细：**",
        "",
    ]

    for i, case in enumerate(bug_cases, 1):
        collector = case.get("collector", "?")
        source = case.get("source", "?")
        tier = case.get("source_tier", "?")
        err_msg = case.get("error_msg", "")
        # 截断错误信息到 200 字
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."

        lines.append(f"**{i}. {collector} :: {source} (tier{tier})**")
        lines.append(f"```\n{err_msg}\n```")
        lines.append("")

    lines.append("---")
    lines.append(f"完整结果：`{os.path.abspath(output_path)}`")
    lines.append("")
    lines.append("> 其它失败类型（数据源挂了/未实现/数据不足）不告警，降级能兜住。")

    return "\n".join(lines)


def send_feishu_alert(
    bug_cases: list[dict],
    result: dict,
    output_path: str,
    chat_id: str | None = None,
) -> bool:
    """
    推送飞书告警。
    优先用 push_to_feishu（文本），失败打印错误。
    """
    try:
        from feishu_push import push_to_feishu
    except ImportError as e:
        print(f"❌ 无法导入 feishu_push: {e}")
        return False

    message = build_alert_message(bug_cases, result, output_path)

    try:
        ok = push_to_feishu(text=message, chat_id=chat_id)
        if ok:
            print("✅ 飞书告警已发送")
        else:
            print("❌ 飞书告警发送失败")
        return ok
    except Exception as e:
        print(f"❌ 飞书推送异常: {e}")
        return False


# ============================================================
# 主流程
# ============================================================

async def main():
    from codeact_sdk import CodeActSDK

    # 参数解析：result_mode (位置1), collector_filter (位置2, 可选)
    result_mode = sys.argv[1] if len(sys.argv) > 1 else "no_reply"
    collector_filter = sys.argv[2] if len(sys.argv) > 2 else ""

    # auto 模式映射：有 bug → display_only，无 bug → no_reply
    if result_mode == "auto":
        result_mode = "no_reply"  # 默认静默，有 bug 时改为 display_only

    print(f"[参数] result_mode={result_mode}")
    print(f"[参数] collector_filter={collector_filter or '全部'}")

    sdk = CodeActSDK()

    try:
        # 确保输出目录存在
        os.makedirs(_OUTPUT_DIR, exist_ok=True)

        # 生成结果文件路径（按日期命名）
        today = datetime.now().strftime("%Y%m%d")
        suffix = f"_{collector_filter}" if collector_filter else ""
        output_filename = f"smoke_test_{today}{suffix}.json"
        output_path = os.path.join(_OUTPUT_DIR, output_filename)

        # 1. 运行冒烟测试
        print(f"\n========== 步骤 1: 运行冒烟测试 ==========")
        exit_code, _stdout = run_smoke_test(
            output_path=output_path,
            collector_filter=collector_filter,
        )
        print(f"冒烟测试退出码: {exit_code}")

        # 2. 解析结果
        print(f"\n========== 步骤 2: 解析结果 ==========")
        result = load_result(output_path)
        failure_counts = count_failures_by_type(result)
        bug_cases = get_code_bug_cases(result)

        print(f"失败分类统计: {failure_counts}")
        print(f"代码 Bug 数量: {len(bug_cases)}")

        # 3. 判断是否需要告警
        code_bug_count = len(bug_cases)
        alert_needed = code_bug_count > 0

        if alert_needed:
            print(f"\n========== 步骤 3: 推送飞书告警 ==========")
            push_ok = send_feishu_alert(bug_cases, result, output_path)

            # 有 bug → 改为 display_only 模式展示
            actual_mode = "display_only"
            status = "success"  # 脚本本身执行成功，只是发现了 bug
            message = (
                f"[主人](at://owner) 🐛 data_hub 冒烟测试发现 {code_bug_count} 个代码 Bug，"
                f"已推送飞书告警。"
                f"总用例 {result.get('total_cases', 0)} | "
                f"通过 {result.get('passed', 0)} | "
                f"失败 {result.get('failed', 0)}"
            )
            data = {
                "code_bug_count": code_bug_count,
                "failure_counts": failure_counts,
                "push_ok": push_ok,
                "result_file": output_path,
                "total_cases": result.get("total_cases", 0),
                "passed": result.get("passed", 0),
                "failed": result.get("failed", 0),
            }
        else:
            print(f"\n========== 步骤 3: 无代码 Bug，静默通过 ==========")
            # 无 bug → 保持 no_reply（或用户指定的模式）
            actual_mode = result_mode if result_mode in (
                "no_reply", "display_only"
            ) else "no_reply"
            status = "success"
            if actual_mode == "no_reply":
                message = "NO_REPLY"
            else:
                message = (
                    f"✅ data_hub 冒烟测试全部正常，无代码 Bug。"
                    f"总用例 {result.get('total_cases', 0)} | "
                    f"通过 {result.get('passed', 0)} | "
                    f"失败 {result.get('failed', 0)}（非代码 Bug，降级可兜）"
                )
            data = {
                "code_bug_count": 0,
                "failure_counts": failure_counts,
                "result_file": output_path,
                "total_cases": result.get("total_cases", 0),
                "passed": result.get("passed", 0),
                "failed": result.get("failed", 0),
            }

        await sdk.submit_result(
            result_mode=actual_mode,
            status=status,
            message=message,
            data=data,
        )

    except subprocess.TimeoutExpired as e:
        # 超时特殊处理 - 也告警
        print(f"❌ 冒烟测试超时: {e}")
        await sdk.submit_result(
            result_mode="notify",
            status="error",
            message=f"冒烟测试执行超时（>{1200}s），请检查",
            data={"error_type": "TimeoutExpired"},
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        await sdk.submit_result(
            result_mode="notify",
            status="error",
            message=f"冒烟测试每日脚本执行失败：{str(e)[:200]}",
            data={"error_type": type(e).__name__},
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
