# Trade Agents · 周度多交易员分析任务（anchor-report）

> 参考 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的多空辩论思路，
> 结合自研 **缠论技术选股（techAnly）** + **券商研报基本面** + **个股新闻情绪面** + **Anchor-lite 组合持仓**，
> 每周日自动生成一份「明天该买什么」的多空辩论报告，并推送到飞书。

报告消息头固定标注：**📊 报告来自 hermes.DeepSeek**。看多标绿月亮 🌙，看空标红星星 ⭐，基本面在前、技术面在后。

---

## 一、这个任务解决什么问题

单靠任何一个视角选股都有盲区：

| 视角 | 单独用的问题 |
|------|-------------|
| 缠论买点（技术面） | 会一视同仁地选中「趋势好但基本面正在恶化」的票（如苏泊尔净利-6.84%、格力铜价挤压、药店链医保控费） |
| 券商研报（基本面） | 覆盖滞后、行业颗粒度粗，不知道「现在是不是买点」 |
| 个股新闻（情绪面） | 噪音大，全市场扫会烧光 token |
| Anchor-lite 持仓（策略面） | 只有总仓位，没有个股方向 |

本任务的解法是**「先过滤、再定向分析、最后多空辩论」**：

1. 先用**纯代码**（0 token）把全市场收敛到「行业有买点 + 个股站上 MA40/VWAP + 有研报覆盖」的一小撮候选；
2. 只对这小撮候选**定向**抓新闻、喂 LLM；
3. LLM 扮演多个交易员做多空辩论，输出带优先级的「下周可买/该回避」清单。

实测这轮跑下来，新闻把达仁堂/上海医药/同仁堂**升级**进优先级 1，把苏泊尔/格力/药店链**降级**到红星星 ⭐，明显改变了结论。

---

## 二、目录结构

```
trade-agents/
├── README.md                     ← 本说明书
├── .env.example                  ← 密钥/配置模板（复制为 .env 后填入）
├── requirements.txt              ← 运行依赖
├── trade_agents_ai.py            ← 【主入口】AI 版编排（含新闻），--push 推飞书
├── trade_agents_final.py         ← 各阶段函数（stage1/stage2/trader_research/trader_news/push_with_charts）
├── llm_report.py                 ← DeepSeek 封装 + 多交易员辩论（debate_report）
├── trade_agents_weekly.py        ← 旧版：4 交易员纯代码版（无 LLM）
├── weekly_analysis.py            ← 旧版：券商研报 × 量化动量辩论（无 LLM）
├── stage1_weekly_chan.py         ← 阶段一独立脚本（行业周线买点扫描）
├── stage2_drilldown.py           ← 阶段二独立脚本（MA40+VWAP 下钻个股）
├── tushare_init.py               ← Tushare 代理统一入口
├── cron/
│   └── trade_agents_weekly.py    ← cron 包装脚本（周日 08:15 触发）
├── tech/                         ← 【技术分析选择引擎】techAnly（重点，见第五节）
│   ├── bin/
│   │   ├── tech_buypoint.py      ← 买点/卖点分析入口（闸门 + MA40/VWAP 预过滤）
│   │   ├── chan_smc_index.py     ← 缠论分型版：包含处理→分型→笔→中枢→背驰
│   │   ├── chan_ma_trend_v2.py   ← 缠论均线版：笔=MA5/10 双穿 MA20
│   │   ├── tech_summary.py       ← 四层汇总（分型/均线/SMC/VWAP）+ rank_buys/best_buy
│   │   ├── datahub.py            ← 数据三级降级：AKShare→Tushare→东财 push2his
│   │   ├── simple_chart.py       ← 缠论分型版 + VWAP 单图（含中文字体）
│   │   ├── buypoint_chart.py     ← 买点图
│   │   ├── chan_chart.py         ← 缠论图
│   │   ├── feishu_card.py        ← 飞书图文卡片推送
│   │   ├── minute_hub.py / weekly_zs.py / tushare_init.py
│   ├── smc/
│   │   ├── smc_core.py           ← SMC 结构 + VWAP 六轨核心
│   │   └── smc_config.py
│   └── tools/bootstrap_cache.py  ← 缓存引导（申万行业成分、股票名称等）
└── data_hub/                     ← 【数据层】研报 + 新闻（三级降级）
    ├── __init__.py               ← 统一入口 get_data('research_report'|'news', ...)
    ├── sources/
    │   ├── tushare_source.py     ← 研报 report_rc（走代理 ttx.dailyfetch.top）
    │   ├── akshare_source.py     ← 新闻 stock_news_em + news_cctv
    │   ├── coze_gateway_source.py / web_source.py
    ├── collectors/
    │   ├── research_report.py    ← 研报采集器
    │   └── news.py               ← 新闻采集器
    └── cache/                    ← 缓存层（策略/存储/预取）
```

---

## 三、完整流水线（4 阶段 + 1 个定向新闻步骤）

```
周日 08:15 cron 触发
        │
        ▼
[1/4] 阶段一 · 行业周线/日线买点扫描（技术闸门，纯代码 0 token）
        │   全申万一级行业 → 行业指数日线 + 周线缠论
        │   闸门：日线只认三买，周线认一二三买 → 过闸行业清单
        ▼
[2/4] 阶段二 · 个股下钻（MA40 + VWAP 预过滤，纯代码 0 token）
        │   过闸行业 → 成分股 → 站上MA40且MA40向上 且 站上VWAP中线
        │   → 缠论买点（三买>一买>二买）→ 可买个股清单
        ▼
[3/4] 阶段三 · 券商研报覆盖（基本面，纯代码 0 token）
        │   Tushare report_rc 近 7 日研报 → 行业评级聚合 + 个股研报覆盖
        ▼
[3.5/4] 阶段3.5 · 个股新闻（情绪面，定向查询）
        │   只对「技术买点 + 研报覆盖」后的候选（≤15 只）抓新闻 → 省 token
        ▼
[4/4] 阶段四 · LLM 多交易员辩论（DeepSeek，唯一耗 token 的步骤）
        │   组装 data_context → debate_report()
        │   输出：策略持仓 → 基本面 → 技术面 → 多空辩论 → 共振结论 → 操作建议 → 风险提示
        ▼
    保存 /tmp/trade_agents_ai_YYYYMMDD.txt  （--push 时生成前5只个股缠论图 + 推飞书卡片）
```

**data_context 的组装顺序（决定了报告的版面顺序）**：
1. Anchor-lite 策略持仓（组合层面，先看大方向）
2. 券商研报·行业评级（基本面）
3. 技术面·行业周线/日线买点
4. 技术面·个股下钻（MA40+VWAP 过滤 + 研报覆盖）
5. 资讯·个股新闻（情绪面，定向）

---

## 四、环境变量与密钥

**所有密钥一律走环境变量，不硬编码。** 复制 `.env.example` 为 `.env` 填入：

| 变量 | 用途 |
|------|------|
| `TUSHARE_TOKEN` | Tushare 代理 token（走 `https://ttx.dailyfetch.top/` 代理地址） |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（模型 `deepseek-v4-pro`） |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飞书自建应用凭证 |
| `FEISHU_CHAT` | 飞书群 chat_id（`oc_xxx`） |

```bash
set -a && source .env && set +a
/tmp/venv/bin/python trade_agents_ai.py --push
```

> ⚠️ 运行时依赖两个本地缓存目录（**不在本仓库内，需单独生成**）：
> - `tech/.cache/`：`sw_index_map.json`（行业→指数代码）、`sw_member_all.json`（行业→成分股）、`stock_names.csv` 等，由 `tech/tools/bootstrap_cache.py` 引导生成；
> - `tech/.cache/holding_deep/daily/`：个股日线缓存（`_today_hist()` / `load_daily` 复用）。
> 详见第六节「首次部署」。

---

## 五、技术分析选择引擎（重点）★

这是整个任务里最核心、也是你最关心的「技术分析选的那个」。逻辑全部在 `tech/` 目录，**纯 Python 规则计算，0 token，不依赖任何 LLM**。

### 5.1 四层口径（tech_summary.py）

单一标的做四层分析，买点判定以**缠论分型版为第一优先**：

| 层 | 内容 | 说明 |
|----|------|------|
| ① 缠论·分型版 | 包含处理 → 分型 → 笔 → 中枢 → 背驰 | **买点判定主链**（本任务用的就是它） |
| ② 缠论·均线版 | 笔 = MA5/10 双穿 MA20，大级别 = 价格 vs MA160 | 参考 |
| ③ SMC 结构 | 摆动点 → 结构突破 → FVG/OB → 方向确认 | 参考 |
| ④ VWAP 六轨 | VWAP ±1σ/±2σ/±3σ，窗口 60 根 | 用于**中线预过滤** |

### 5.2 缠论分型版核心流程（chan_smc_index.py）

```
原始K线 ──merge_bars──▶ 包含处理后的合并K线
   │
   ├─ fractals()     分型识别（顶分型/底分型）
   ├─ build_bi()     分型连笔（相邻分型 → 笔）
   ├─ segs_from_bi() 笔成段（段 = 走势的最小结构）
   └─ chan_read()    中枢识别（三段重叠 → ZG/ZD）+ 背驰判断 + 买卖点标注
```

买点/卖点由 `chan_read` 输出到 `buy_list` / `sell_list`，每个点带 `tag`（一买/二买/三买）、`level`（价位）、`age`（距现在几根K线）。

### 5.3 买卖点定义与优先级（2026-11-10 定稿口径）

| 优先级 | 买点 | 定义 | 硬门槛 |
|--------|------|------|--------|
| ① | **三买** | 离开中枢后回踩不破 ZG（结构已确认） | 年龄 ≤ 90 根；现价距回踩低点 0~+10%；收盘不跌回中枢内 |
| ② | **一买** | 底背驰（力度衰减） | **必须 MA20 向上**（斜率 > 0）；年龄 ≤ 40 根；现价距买点 +10%/-3% 内 |
| ③ | **二买** | 回踩不破一买低点 | MA20 向上**只加分不作硬门槛**；年龄 ≤ 60 根；现价不破低点 3%、距买点 ≤ 10% |

> 卖点对称：三卖 > 一卖（须 MA20 向下）> 二卖。

### 5.4 买点有效性闸门（为什么「见过买点」不等于「能买」）

光历史上出现过买点没用，还得**现价还在买点附近 + 结构没被破坏**。三档闸门：

1. **新鲜度**：买点年龄不能太老（三买 ≤90 根 / 一买 ≤40 根 / 二买 ≤60 根）；
2. **距离**：现价比买点高不超过上限（三买/二买 +10%、一买 +10%）、跌破不超过下限（-3%）——已跑远或已跌破的都不算可买；
3. **结构未破**（三买特有）：回踩低点之后收盘**从未跌破**该低点，且现价没有跌回中枢内（≤ZG 即三买失效）。

闸门参数集中在 `tech_buypoint.py` 顶部，可用环境变量覆盖：

```
SCAN_TB_MAX_AGE  = 90    # 三买最大年龄（根）
SCAN_TB_NEAR     = 10    # 三买：现价高于回踩低点 % 以内
SCAN_YB_MAX_AGE  = 40    # 一买最大年龄（根）
SCAN_BUY_NEAR_UP = 10    # 现价高于买点 % 以内算「还在买点」
SCAN_BUY_NEAR_DN = 3     # 现价低于买点 % 以内算「刚破但可接受」
SCAN_EB_MAX_AGE  = 60    # 二买最大年龄（根）
SCAN_EB_NEAR     = 10    # 二买：现价高于二买低点 % 以内
```

### 5.5 两阶段过滤（先过滤、再下钻，省时省 token）

**行业闸门（`_sector_block`）** —— 行业自身没买点就直接不下钻：

- **日线只认三买**（一买二买在日线噪音大，不认）；
- **周线认一二三买**（大级别结构更可靠）；
- 行业过闸后，才进入个股扫描。

**个股预过滤（`scan_codes` / `stage2_drilldown`）** —— 两道快筛，筛掉大部分成分股：

1. **站上 MA40 且 MA40 向上**（现价 > MA40，且 MA40 近 6 根斜率 > 0）；
2. **站上 VWAP 中线**（现价 > VWAP，即 sigma > 0）。

只有两道筛都过的个股，才进入缠论买点判定（三买 → 一买 → 二买）。医药生物这类成分股极多的行业，靠这两道筛能把几百只砍到十几只，再逐个算缠论。

### 5.6 最终排序

所有过闸的候选按 `(优先级, 年龄)` 升序排：三买最优先，同一买点越新鲜越靠前。`best_buy()` 取每个标的**可买的最高优先级买点**。

---

## 六、数据层（data_hub，三级降级）

数据获取统一走 `data_hub` 包，自动降级：

```
AKShare ──失败──▶ Tushare（代理 ttx.dailyfetch.top）──失败──▶ 东财 push2his / Web
```

本任务实际用到两个数据入口：

| 数据 | 入口 | 来源 |
|------|------|------|
| 行业/个股日线 | `tech/bin/datahub.py` 的 `daily()` / `sw_daily()` | AKShare → Tushare → 东财 |
| 券商研报 | `get_data('research_report', start, end)` | Tushare `report_rc`（代理） |
| 个股新闻 | `get_data('news', news_type='stock', symbol=code)` | AKShare `stock_news_em` + `news_cctv` |

研报评级打分表（`trader_research` 里）：

```
买入/强烈推荐/强推/买入-A = 3 ｜ 推荐 = 2.5 ｜ 增持/优于大市/跑赢行业 = 2
谨慎增持/谨慎推荐 = 1.5 ｜ 持有/中性 = 1 ｜ 减持/卖出 = 0
```

行业级评级要求 ≥3 篇研报才统计；个股级研报覆盖按 `ts_code` 聚合（篇数 + 平均评级）。

---

## 七、运行方式

### 7.1 本地单跑（不推飞书）

```bash
cd trade-agents
set -a && source .env && set +a
export PYTHONPATH="$(pwd):$(pwd)/tech/bin:$(pwd)/tech/smc:$(pwd)/data_hub"

/tmp/venv/bin/python trade_agents_ai.py            # 只生成报告，不推飞书
/tmp/venv/bin/python trade_agents_ai.py --push     # 生成报告 + 前5只个股缠论图 + 推飞书
```

### 7.2 分阶段调试

```bash
/tmp/venv/bin/python stage1_weekly_chan.py         # 只看阶段一：行业周线买点
/tmp/venv/bin/python stage2_drilldown.py           # 只看阶段二：个股下钻
```

### 7.3 cron 定时（周日 08:15）

```bash
/opt/data/venv/bin/python cron/trade_agents_weekly.py
```

`cron/trade_agents_weekly.py` 内部会：注入 Tushare token / TECH_WS / PYTHONPATH → source datahub `.env` → 调 `trade_agents_ai.py --push`，超时 1800s。

---

## 八、首次部署

1. **装依赖**：
   ```bash
   pip install -r requirements.txt
   ```
   需额外准备：中文字体（如 `wqy-zenhei.ttc`，否则 matplotlib 中文乱码）；`lark-cli`（旧版纯文本推送用，图文卡片走 `feishu_card.py` 不需要）。

2. **生成缓存**（`tech/.cache/`，本仓库不含）：
   ```bash
   /tmp/venv/bin/python tech/tools/bootstrap_cache.py
   ```
   产物：`sw_index_map.json`（申万行业→指数代码）、`sw_member_all.json`（行业→成分股）、`stock_names.csv`（代码→名称）。

3. **配置密钥**：`.env.example` → `.env` 填入 Tushare / DeepSeek / 飞书凭证。

4. **Anchor-lite 持仓快照**：`trade_agents_ai.py` 会读 `positions_snapshot.json`（组合层面仓位），放在 Anchor-lite 仓库根目录；不存在则跳过该段。

---

## 九、报告输出示例（节选）

```
📊 Trade Agents 多交易员周度分析（报告来自 hermes.DeepSeek）

【策略持仓】Anchor-lite 截至 2026-11-05：股票 7.17%、债券 92.83%（防御）
【基本面】🌙 家用电器：研报144篇 评级2.5 买入54%
【技术面】🌙 家用电器 ②一买@8157 ｜ 🌙 医药生物 ③二买@7167
【多空辩论】多头：行业买点共振 + 家电研报覆盖高；谨慎：组合低仓位 + 药店链利空
【共振结论】美的集团、达仁堂、爱博医疗、上海医药 四票「技术买点+基本面」共振
【操作建议】优先级1 可关注：美的/达仁堂/爱博/上海医药 ……
【风险提示】⭐ 苏泊尔净利-6.84% / 格力铜价挤压 / 药店链医保控费 ……
```

完整报告样例见最近一次运行结果：`/tmp/trade_agents_ai_YYYYMMDD.txt`。

---

## 十、与 Anchor-lite / techAnly 仓库的关系

| 仓库 | 关系 |
|------|------|
| `anchor-lite`（本仓库 `anchor-report` 分支） | 周报任务的编排 + 报告 + 技术分析选择引擎 + 数据层的**完整副本** |
| `baizhiwei9/anchor-lite`（`main` / `Anchor-tsmom` 分支） | Anchor-Lite 组合策略本体 + 动量（TSMom）回测 |
| `baizhiwei9/techAnly-summary`（公开）/ `tech-summary`（私有） | 技术分析引擎的独立仓库（本任务 `tech/` 即其一份快照） |
| `baizhiwei9/datahub-three-tier`（私有） | 数据层的独立仓库（本任务 `data_hub/` 即其一份快照） |

> 本分支把依赖的 `tech/` 和 `data_hub/` 一并打包，保证 `anchor-report` **开箱可跑、不依赖外部仓库**。
> 若后续上游 `techAnly-summary` / `datahub-three-tier` 有更新，需手动同步回本分支对应目录。

---

⚠️ **免责声明**：本任务输出的所有内容均为技术结构与基本面数据的程序化汇总，**不构成投资建议**。买卖决策请自行判断并控制仓位。
