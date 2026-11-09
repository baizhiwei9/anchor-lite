# Anchor-Lite

宽基多资产组合策略工程：**中美宏观锚 + 债券衰退切换 + 风格选股 + 期权风控**。覆盖 2022–2026 全区间回测、年度重置熔断、5% 回撤约束下的配置寻优与渐进调仓对照。冻结版本记录见 `VERSION.json`（v1.1-20261109）。

## 核心脚本（`VERSION.json` 登记）

| 脚本 | 职责 |
|---|---|
| `long2022.py` | 全区间引擎：宏观锚 / 债券衰退切换 / 风格选股 / 期权风控；被其它脚本以模块方式导入 |
| `style_regime.py` | 风格与 regime 判定，输出到 `style_out/` |
| `factor_table.py` | 行业因子表构建 |
| `flow_factors.py` | 资金流因子 |
| `mf_backtest.py` | 多因子强弱打分回测（含平滑与得分加权变体） |
| `xgb_backtest.py` | XGBoost walk-forward 行业打分走查（可选，需 `xgboost`） |
| `gradual.py` | 渐进调仓方案 |
| `ind2026.py` | 2026 年行业口径（模块） |
| `ext_pool.py` | 扩展标的池回测 |

其余脚本为研究过程中的诊断、参数扫描与报告构建脚本，一并保留以便接手继续修改；部分脚本依赖本仓库内的数据快照，个别依赖原运行环境的其它工程（已在文件内以环境变量形式标注）。

## 数据

| 目录 | 内容 |
|---|---|
| `data_long/` | 全区间日线：宽基、债券、黄金、原油、QVIX、期权偏度等 |
| `data2026/` | 2026 年行业与宽基日线（33 个品种，`*_full.csv`） |
| `data_signals/` | 信号序列快照 |
| `style_out/` | 风格判定输出 |
| `data/` | 宏观月度序列 `macro_2026.json` |
| `*.html` | 各口径回测报告（含 5% 回撤约束方案、年度重置熔断、归因报告等） |

## 环境与依赖

```bash
pip install -r requirements.txt
```

核心链路只用 `numpy / pandas / scipy / matplotlib`；`long2022.py` 在本地缺 QVIX 缓存时会用 `akshare` 联网补齐；`xgb_backtest.py` 需要 `xgboost`。**全流程不含任何大模型调用**，XGBoost 为本地训练，可按需跳过。

## 运行示例（已实测）

```bash
python factor_table.py       # 行业因子得分
python mf_backtest.py        # 多因子打分回测（前 N 名 × 平滑窗口）
python ext_pool.py           # 扩展池逐日权重与收益
python style_regime.py       # 风格/regime 判定，写入 style_out/
python -c "import long2022"  # 以模块方式加载全区间引擎（10 个资产，2020-01 起）
```

## 移植说明

- 已移除原运行环境注入的共享依赖目录（`py_deps`），依赖一律走 `pip`。
- 宏观数据路径默认取仓库内 `data/macro_2026.json`，可用环境变量 `MACRO_JSON` 覆盖；`macro_unified.py` 复用外部宏观工程，路径由 `MACRO_MONTHLY_DIR` 指定。
- 3 个非核心出图脚本需要中文字体 `Noto Sans SC`，默认读取 `~/.fonts/NotoSansSC-Regular.ttf`；缺字体时这些脚本不可用，不影响核心回测。
- 数据为历史快照，更新数据请复用各脚本内的取数逻辑或另行补齐 CSV。

## 交接要点

- 改策略口径优先动 `long2022.py`（引擎）与 `VERSION.json`（参数与脚本指纹登记）。
- 复跑结果请与 `*.html` 报告中的口径对齐（成本统一单边 5bp、当日收盘决策次日结算）。
- `VERSION.json` 记录了各脚本的 sha256 前 16 位与字节数，便于核验改动。