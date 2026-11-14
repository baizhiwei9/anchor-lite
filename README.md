# Anchor-Lite

宽基多资产组合策略**应用程序**：中美宏观锚 + 债券衰退切换 + 风格选股 + 期权风控。

## 怎么用

```bash
pip install -r requirements.txt   # numpy pandas scipy matplotlib
python3 app.py                    # 运行策略, 出 Markdown 报告 → reports/strategy_report.md
python3 app.py --print-only       # 只打印到终端
python3 app.py --out 我的报告.md   # 指定输出文件
```

`app.py` 是入口，`long2022.py` 是引擎，参数自动读 `VERSION.json` 的 `key_params`（冻结配置）。报告为纯 Markdown：核心指标 / 分年度收益 / 当前持仓 / 最新选股 / 运行配置。

## 目录结构

| 目录/文件 | 内容 |
|---|---|
| `app.py` | **应用程序入口**（跑策略出报告） |
| `long2022.py` | 主引擎（中美宏观锚 + 债券衰退切换 + 风格选股 + 期权风控） |
| `data_long/` | 全区间日线 + 宏观 + 期权偏度/QVIX（主程序数据） |
| `data/` | 宏观月度序列 macro_2026.json |
| `VERSION.json` | 冻结版本参数与脚本指纹 |
| `reports/` | HTML 报告归档（研究产物，非程序） |
| `backtests/` | 研究脚本归档（历史回测/因子/资金流等，非主程序） |
| `research_output/` | 研究中间产物（csv/json 缓存） |

> 主目录是应用程序；`reports/`、`backtests/`、`research_output/` 是研究归档，运行主程序不依赖它们。

## 环境与依赖

核心链路只用 `numpy / pandas`；`long2022.py` 本地缺 QVIX 缓存时会用 `akshare` 联网补齐。全流程不含任何大模型调用。

## 声明

> 研究分享，不构成投资建议。历史回测不代表未来表现。
