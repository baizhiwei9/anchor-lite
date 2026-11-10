#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trade Agents 周度分析（cron 脚本驱动，no_agent 模式）
周日早 8:15 执行：阶段一行业买点 → 阶段二个股下钻 → 研报覆盖 → LLM 辩论 → 飞书推送
"""
import subprocess, os, sys

def main():
    cmd = [
        "/tmp/venv/bin/python",
        "/opt/data/home/.coze/agents/7705405429102444827/workspace/trade_agents_ai.py",
        "--push",
    ]
    env = dict(os.environ)
    env["TUSHARE_PROXY_TOKEN"] = os.environ.get("TUSHARE_TOKEN", "")
    env["TECH_WS"] = "/tmp/techAnly"
    env["PYTHONPATH"] = "/tmp/techAnly/bin:/tmp/techAnly/smc:/opt/data/home/.coze/agents/7705405429102444827/workspace"
    # source datahub .env
    envfile = "/opt/data/home/.coze/agents/7705405429102444827/workspace/datahub-three-tier/.env"
    if os.path.exists(envfile):
        for line in open(envfile):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k, v)
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    # 只输出末尾(报告/推送结果)
    out = (r.stdout or "") + (r.stderr or "")
    lines = [l for l in out.split("\n") if l.strip()][-20:]
    print("\n".join(lines))
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
