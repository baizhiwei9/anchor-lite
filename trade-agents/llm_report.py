# -*- coding: utf-8 -*-
"""
LLM 多Agent 辩论报告生成器
==========================
数据层：纯代码（研报/动量/缠论买点/Anchor持仓）
辩论层：调用 DeepSeek 扮演 4 个交易员 + 多空辩论，生成统一结论
"""
import os, json, urllib.request

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MODEL = "deepseek-v4-pro"

def llm_chat(messages, max_tokens=16000, temperature=0.3):
    """调用 DeepSeek 对话"""
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }).encode()
    req = urllib.request.Request(DEEPSEEK_URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + DEEPSEEK_KEY,
    })
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=180).read())
        return r["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[LLM调用失败] {str(e)[:200]}"

def debate_report(data_context):
    """多交易员辩论：4 个视角数据 → DeepSeek 生成统一报告"""
    system = (
        "你是量化交易团队的策略总监，主持一场周度多空辩论会。"
        "有 4 位交易员各自给出视角数据，你需要组织辩论并给出统一、可操作的下周结论。"
        "要求：1) 客观呈现各方分歧 2) 找出多视角共振点 3) 给出明确的行业/个股方向建议 "
        "4) 用中文 5) 标注风险 6) 不要编造数据。"
        "格式要求：看多/利好的标的用绿色🌙月亮标记，看空/谨慎的标的用红色⭐星星标记；"
        "报告结构按「策略持仓 → 基本面 → 技术面 → 共振结论 → 操作建议 → 风险提示」顺序。"
    )
    user = f"""以下是本周 4 位交易员的视角数据：

{data_context}

请输出周度多空辩论报告，包含：
【多空辩论】各方观点 + 分歧点
【共振结论】多视角一致的方向
【操作建议】具体行业/个股 + 优先级
【风险提示】

报告开头第一行固定写：📊 Trade Agents 多交易员周度分析（报告来自 hermes.DeepSeek）
"""
    return llm_chat([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

if __name__ == "__main__":
    # 测试：把已抓到的数据喂给 LLM
    data = """
交易员1·券商研报（基本面）：家用电器 研报134篇 平均评级2.5 买入49%；医药生物 研报数据不足。
交易员2·量化动量（技术面）：多头仅银行(+3.2%)、30年国债、原油；站上20日线广度18%（偏弱）。
交易员3·缠论买点（周线）：家用电器 ②一买@8157；医药生物 ③二买@7167。
交易员4·Anchor-lite策略：股票7.2% + 国债92.8%（极度防守）。
个股下钻（MA40+VWAP过滤+研报覆盖）：极米科技①三买/莱克电气①三买/美的集团①三买/同仁堂①三买/云南白药①三买/达仁堂①三买/益丰药房①三买/一心堂①三买/马应龙①三买/九州通②一买。
"""
    report = debate_report(data)
    print(report)
