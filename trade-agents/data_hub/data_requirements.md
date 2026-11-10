# Data Hub 全量需求调研报告

> 调研时间：2026-07-16
> 调研范围：14个核心任务脚本的静态扫描
> 调研方法：import语句分析 + API调用提取（akshare/tushare/requests URL）

---

## 一、调研对象清单

| 编号 | 脚本名称 | 路径 | 代码量 | 核心功能 |
|------|----------|------|--------|----------|
| 1 | TOP200概念汇总 | `outputs/涨幅分析/top200_concept_report.py` | 845行 | 全市场涨幅前200只股票概念归类汇总 |
| 2 | 环流日报 | `环流日报/scripts/circulation_daily.py` | 1900行 | 全球流动性/债市/汇率/资金环流监控 |
| 3 | 风险日报 | `市场情绪监控/scripts/risk_forecast_daily.py` | 2091行 | 市场风险情绪预测与分级预警 |
| 4 | 多策略选股（缠论） | `长期计划/缠论主升浪/src/unified_screener.py` | 529行 | 缠论主升浪多策略统一选股器 |
| 5 | 结构因子日报 | `skills/structural-factor/main.py` | 415行 | A股5大指数结构因子AbsFactor/RelFactor计算 |
| 6 | 板块筛选日报 | `板块筛选v4_fast.py` | 331行 | 概念板块涨幅/资金流/强度筛选排名 |
| 7 | 市场综合监控 | `市场情绪监控/scripts/liquidity_risk_monitor.py` | 1467行 | 流动性风险综合监控（涨跌停/融资/北向等） |
| 8 | CFC结构因子 | `CFC结构因子/cfc_calculator_v2.py` | 1154行 | 跨指数拥挤度因子CDI计算 |
| 9 | 期权监控 | `市场情绪监控/scripts/option_monitor.py` | 956行 | 50/300/500/创业板/科创50/1000MO期权SKEW/GEX |
| 10 | 风格漂移 | `市场情绪监控/scripts/style_drift_monitor.py` | 1247行 | 期权隐含波动率风格漂移监控 |
| 11 | 库存信贷周期 | `库存信贷周期/scripts/cycle_monitor.py` | 2089行 | 宏观库存/信贷周期跟踪与拐点判断 |
| 12 | 热点概念TOP20 | `热点概念追踪/scripts/concept_tracker_top20.py` | 400行 | 近两周TOP20热点概念追踪 |
| 13 | 国债期货四因子 | `skills/bond-futures-four-factor/main.py` | 1416行 | 国债期货TL/TF/T四因子方向预测 |
| 14 | 中线偏好监控 | `codeact/scripts/midline_preference_monitor.py` | 811行 | 中线风格偏好综合监控（依赖CFC+期权） |

---

## 二、按数据类型归类统计

### 1. 指数成分股

**涉及任务：** CFC结构因子、风格漂移、结构因子日报、风险日报、市场综合监控、中线偏好监控（间接）

**使用指数：**
| 指数名称 | Tushare代码 | AKShare代码 | 使用脚本数 |
|----------|-------------|-------------|-----------|
| 沪深300 | 000300.SH | 000300 | 6 |
| 中证500 | 000905.SH | 000905 | 5 |
| 中证1000 | 000852.SH | 000852 | 5 |
| 创业板指 | 399006.SZ | 399006 | 5 |
| 科创50 | 000688.SH | 000688 | 4 |
| 上证50 | 000016.SH | 000016 | 3 |
| 创业板50 | 399673.SZ | 399673 | 1 |

**接口调用：**
- 一级（akshare）：`ak.index_stock_cons(symbol=xxx)`
- 二级（tushare）：`pro.index_weight(index_code=xxx)` → 取最新交易日成分

**日调用次数估算：** ~6-8次/天（每个指数1次，CFC全市场2000只需3个指数合并）

**优先级：** ⭐⭐⭐ 最高 — 多个核心策略的基础输入

---

### 2. A股日线行情

**涉及任务：** TOP200概念汇总、环流日报、风险日报、市场综合监控、CFC结构因子、热点概念TOP20、中线偏好监控、结构因子日报（间接）

**接口调用：**
- 二级（tushare）：`pro.daily(ts_code/trade_date/start_date/end_date)`、`pro.daily_basic(...)`
- 缠论选股：通过子进程调用akshare（`stock_zh_a_hist`系列）

**调用模式：**
- 按交易日全市场拉取：`pro.daily(trade_date=YYYYMMDD)` 约5500条/次
- 按个股范围+日期范围拉取：`pro.daily(ts_code=xxx, start_date=..., end_date=...)`
- 日线基础指标：`pro.daily_basic(trade_date=xxx, fields=...)`

**日调用次数估算：** ~15-20次/天（不同脚本不同时间窗口）

**优先级：** ⭐⭐⭐ 最高 — 所有量化分析的基础数据

---

### 3. 期权数据

**涉及任务：** 期权监控、风格漂移监控、中线偏好监控、风险日报（间接）

**标的合约：**
| 标的 | 交易所 | 代码 | 合约数量级 |
|------|--------|------|-----------|
| 50ETF | 上交所 | 510050 | ~100+ |
| 300ETF | 上交所 | 510300 | ~150+ |
| 300ETF | 深交所 | 159919 | ~100+ |
| 500ETF | 上交所 | 510500 | ~100+ |
| 创业板ETF | 深交所 | 159915 | ~80+ |
| 科创50ETF | 上交所 | 588000 | ~80+ |
| 中证1000股指期权 | 中金所 | MO | ~100+ |

**接口调用：**
- 一级（akshare）：
  - `ak.option_current_day_sse()` — 上交所当日全部期权
  - `ak.option_current_day_szse()` — 深交所当日全部期权
  - `ak.option_finance_board(symbol=xxx, end_month=xxx)` — 金融期权看板（补充）
  - `ak.option_cffex_zz1000_list_sina()` — 中金所1000期权合约列表
  - `ak.option_cffex_zz1000_spot_sina(symbol=xxx)` — 中金所1000期权行情

- 二级（tushare）：
  - `pro.opt_basic(exchange=xxx)` — 期权合约基本信息
  - `pro.opt_daily(exchange=xxx, start_date=..., end_date=...)` — 期权日线行情

- 三级（web）：
  - 上交所官网：`http://www.sse.com.cn/assortment/options/price/`
  - 深交所官网：`http://www.szse.cn/api/report/ShowReport?SHOWTYPE=JSON...`
  - 新浪财经：`https://stock.finance.sina.com.cn/futures/view/options.php?j_code=...`

**计算需求：** SKEW（偏度）、GEX（Gamma敞口）、IV（隐含波动率）、Put/Call Ratio

**日调用次数估算：** ~10-15次/天（合约列表 + 行情数据，按交易所/标的拆分）

**优先级：** ⭐⭐⭐ 最高 — 期权类监控的核心数据源

---

### 4. 指数日线行情

**涉及任务：** 环流日报、风险日报、市场综合监控、CFC结构因子、期权监控、风格漂移、中线偏好、结构因子日报

**接口调用：**
- 二级（tushare）：`pro.index_daily(ts_code=xxx, start_date=..., end_date=...)`
- 一级（akshare）：`ak.index_hist_sw(symbol=xxx)`、`ak.stock_zh_index_daily(symbol=xxx)`

**日调用次数估算：** ~8-10次/天

**优先级：** ⭐⭐ 高 — 指数行情是大盘基准，但获取简单

---

### 5. ETF/基金日线

**涉及任务：** 期权监控、风格漂移监控（ETF现货价格计算）

**接口调用：**
- 二级（tushare）：`pro.fund_daily(ts_code=xxx, start_date=..., end_date=...)`

**日调用次数估算：** ~5-6次/天（主要ETF标的）

**优先级：** ⭐⭐ 高 — 期权标的的现货价格依赖，但可以用指数近似替代

---

### 6. 期货数据（股指+国债）

**涉及任务：** 期权监控、国债期货四因子、风险日报、基差监控

**接口调用：**
- 二级（tushare）：`pro.fut_daily(ts_code=xxx, ...)`
- 一级（akshare）：`ak.futures_zh_hist_sina(symbol=xxx)`、`ak.get_cffex_rank_table()`

**品种：** IF/IC/IM（股指）、T/TF/TL（国债）

**日调用次数估算：** ~6-8次/天

**优先级：** ⭐⭐ 中高 — 期货策略相关，但使用脚本数较少

---

### 7. 资金流向/北向资金/融资融券

**涉及任务：** 市场综合监控、板块筛选日报、国债期货四因子、环流日报

**接口调用：**
- 二级（tushare）：`pro.moneyflow_hsgt()`、`pro.margin(trade_date=...)`
- 一级（akshare）：`ak.stock_hsgt_hist_em(symbol=xxx)`

**日调用次数估算：** ~4-6次/天

**优先级：** ⭐⭐ 中高 — 情绪面重要指标

---

### 8. 宏观数据（货币/利率/通胀/经济）

**涉及任务：** 环流日报、库存信贷周期、国债期货四因子、市场综合监控、期权监控

**接口调用（种类繁多）：**
- tushare：`pro.shibor()`、`pro.us_tycr`、`pro.yc_cb`、`pro.fx_daily`
- akshare：`ak.macro_china_shibor_all()`、`ak.macro_china_money_supply()`、`ak.macro_china_lpr()`、`ak.macro_china_ppi()`、`ak.macro_china_exports_yoy()`、`ak.macro_china_industrial_production_yoy()`、`ak.macro_rmb_loan()`、`ak.macro_china_shrzgm()`、`ak.macro_china_qyspjg()`
- 外部API：FRED（美联储经济数据）、HKMA（香港金管局）、TradingEconomics、纽约联储（SOFR/RRP）
- 官网爬取：央行（pbc.gov.cn）、统计局（stats.gov.cn）

**日调用次数估算：** ~20-25次/天（含外部API调用）

**优先级：** ⭐ 中 — 数量多但大部分是低频（月/季度）数据，每日波动小

---

### 9. 板块/概念数据

**涉及任务：** TOP200概念汇总、板块筛选日报、热点概念TOP20

**数据源：**
- 东方财富网页API：`push2delay.eastmoney.com`、`push2his.eastmoney.com`
- 同花顺概念板块（缠论选股）
- 无直接akshare/tushare接口（概念成分股接口不完整）

**日调用次数估算：** ~10-20次/天（板块列表+各板块K线+资金流）

**优先级：** ⭐⭐ 高 — 板块筛选是高频需求，但数据源复杂（网页爬取为主）

---

### 10. 涨跌停/市场情绪

**涉及任务：** 市场综合监控、风险日报

**接口调用：**
- 二级（tushare）：`pro.limit_list(trade_date=...)`
- 一级（akshare）：`ak.stock_a_high_low_statistics(symbol=xxx)`

**日调用次数估算：** ~2-3次/天

**优先级：** ⭐ 中 — 市场情绪辅助指标

---

### 11. 交易日历

**涉及任务：** 8个脚本使用（TOP200概念、环流日报、风险日报、市场综合监控、CFC、风格漂移、热点概念、中线偏好）

**接口调用：**
- 二级（tushare）：`pro.trade_cal(exchange='SSE', is_open='1', ...)`

**日调用次数估算：** ~8-10次/天（每个脚本独立调用）

**优先级：** ⭐⭐ 高 — 基础性工具数据，调用频繁但计算简单

---

### 12. 股票基础信息

**涉及任务：** TOP200概念汇总、热点概念TOP20

**接口调用：**
- 二级（tushare）：`pro.stock_basic(exchange='', list_status='L', ...)`

**日调用次数估算：** ~2-3次/天

**优先级：** ⭐ 中低 — 变动频率低，可缓存

---

## 三、日调用次数汇总

| 数据类型 | 估算日调用次数 | 使用脚本数 | 数据源现状 |
|----------|---------------|-----------|-----------|
| A股日线行情 | 15-20次 | 8 | Tushare为主，akshare备用 |
| 宏观数据 | 20-25次 | 5 | akshare为主，外部API补充 |
| 指数日线 | 8-10次 | 8 | Tushare为主 |
| 期权数据 | 10-15次 | 3+2间接 | akshare为主，tushare备用 |
| 交易日历 | 8-10次 | 8 | Tushare唯一稳定源 |
| 板块/概念 | 10-20次 | 3 | 东财网页爬取为主 |
| 指数成分股 | 6-8次 | 5 | Tushare为主，akshare备用 |
| 期货数据 | 6-8次 | 3 | akshare+sina为主 |
| 资金流向 | 4-6次 | 4 | Tushare为主 |
| ETF日线 | 5-6次 | 2 | Tushare为主 |
| 涨跌停 | 2-3次 | 2 | Tushare为主 |
| 股票基础信息 | 2-3次 | 2 | Tushare为主 |

**Tushare日均调用估算：** ~50-70次/天（免费版100次/分钟够用，但批量任务可能瞬时集中）

---

## 四、优先实现建议

### ✅ 第一优先级（本期实现）

1. **指数成分股** — 5个核心脚本依赖，是因子计算的基础
   - 三级降级：akshare → tushare → 官网爬取
   - 覆盖指数：沪深300、中证500、中证1000、创业板指、科创50、上证50

2. **期权数据** — 期权监控/风格漂移的核心，当前降级逻辑分散在各脚本
   - 三级降级：akshare → tushare → 官网/新浪爬取
   - 覆盖标的：50ETF/300ETF(沪+深)/500ETF/创业板ETF/科创50ETF/中证1000MO
   - 输出：合约基本信息 + 当日行情 + 隐含波动率

3. **A股日线行情** — 最基础最频繁调用的数据类型
   - 三级降级：akshare → tushare → 新浪/东财网页
   - 支持两种模式：按交易日全市场 / 按个股+日期范围

### 📋 第二优先级（下期实现）

4. 指数日线行情 — 实现简单但使用频率高
5. 交易日历 — 高频调用，可做本地缓存
6. ETF日线 — 期权计算的辅助数据

### 📦 第三优先级（后续迭代）

7. 资金流向/北向/融资融券 — 中等重要性
8. 期货数据 — 使用脚本数较少
9. 涨跌停统计 — 辅助指标
10. 股票基础信息 — 变动慢，可长期缓存

### ❌ 不纳入 data_hub

11. **宏观数据** — 来源过于分散（国内+国外+官网爬取），更新频率低（月/季），单独维护更合理
12. **板块/概念数据** — 数据源以网页爬取为主，接口稳定性差，适合独立模块
13. **缠论选股数据** — 通过子进程调用独立脚本，不适合纳入统一采集层

---

## 五、核心发现与设计启示

### 1. Tushare 是事实上的主力数据源
- 14个脚本中有10个直接使用 tushare
- 但免费版有100次/分钟限制，当前各脚本独立控制串行，缺乏全局协调
- **设计启示：** data_hub 必须全局串行控制 tushare 调用，间隔≥0.55s

### 2. 降级逻辑分散重复
- CFC结构因子中已经有 akshare ↔ tushare 的指数成分股降级
- 期权监控中也有独立的 akshare ↔ tushare 期权数据降级
- 风格漂移甚至做了四级降级（tushare → akshare → 上交所官网 → 新浪）
- **设计启示：** 统一三级降级框架，消除重复代码

### 3. 数据时效性要求高
- 所有脚本都是日报/监控类，要求当日最新数据
- 不做跨天缓存，保证数据新鲜度
- **设计启示：** 按"宁慢勿空"原则，允许降级但禁止造假

### 4. 数据质量校验标准不一
- 指数成分股：CFC要求≥10只才接受，有的脚本直接用空列表
- 期权数据：有的脚本接受部分合约，有的要求完整
- **设计启示：** 统一质量阈值配置，可按数据类型调整

---

## 六、本期实现范围确认

| 模块 | 文件 | 状态 |
|------|------|------|
| 配置模块 | `config.py` | ✅ 已完成 |
| 基类模块 | `base.py` | ✅ 已完成 |
| 数据源适配器-AKShare | `sources/akshare_source.py` | ⏳ 待实现 |
| 数据源适配器-Tushare | `sources/tushare_source.py` | ⏳ 待实现 |
| 数据源适配器-Web爬取 | `sources/web_source.py` | ⏳ 待实现 |
| 采集器-指数成分股 | `collectors/index_components.py` | ⏳ 待实现 |
| 采集器-期权数据 | `collectors/option_data.py` | ⏳ 待实现 |
| 采集器-A股日线 | `collectors/stock_daily.py` | ⏳ 待实现 |
| 统一入口 | `__init__.py` | ⏳ 待实现 |
| 单元测试 | `tests/test_data_hub.py` | ⏳ 待实现 |
| 使用文档 | `README.md` | ⏳ 待实现 |
