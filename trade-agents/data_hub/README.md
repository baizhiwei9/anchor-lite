# Data Hub — 统一数据采集层

> 三级降级数据源框架，为量化监控脚本提供稳定、可靠的数据获取能力。

## 核心特性

- **三级降级**：AKShare → Tushare → Web爬取，逐级兜底，数据宁慢勿空
- **全局串行**：Tushare 调用全局串行，间隔 ≥ 0.55s，避免触发频率限制
- **质量校验**：每级返回数据均做数量校验，不足自动降级
- **零LLM依赖**：纯Python实现，无需AI模型
- **无跨天缓存**：每次调用实时获取，保证数据时效性
- **明确日志**：所有失败有明确日志，数据缺失标 None，绝不造假

## 目录结构

```
data_hub/
├── __init__.py              # 统一入口：get_data() / get_data_result()
├── config.py                # 配置模块（优先级、超时、阈值、分时批次）
├── base.py                  # 基类（BaseDataSource / BaseCollector / DataResult / 异常）
├── data_requirements.md     # 需求调研报告
├── README.md                # 本文档
├── sources/                 # 数据源适配器（四级）
│   ├── __init__.py
│   ├── akshare_source.py    # Tier 1: AKShare
│   ├── tushare_source.py    # Tier 2: Tushare Pro
│   └── web_source.py        # Tier 3/4: 网页爬取（东财/新浪/交易所官网）
├── collectors/              # 数据采集器
│   ├── __init__.py
│   ├── index_components.py  # 指数成分股采集器
│   ├── option_data.py       # 期权数据采集器
│   ├── option_board.py      # 期权原始行情采集器（T型报价/标的行情/MO/现货快照）
│   └── stock_daily.py       # A股日线行情采集器
├── option_maps.py           # 期权看板↔标的ETF映射、默认指数/ETF代码集
└── tests/                   # 单元测试
    └── test_data_hub.py
```

## 快速开始

### 安装依赖

```bash
pip install akshare tushare pandas requests
```

### 配置 Tushare Token（可选）

```python
import os
os.environ["TUSHARE_TOKEN"] = "your_token_here"

# 或者运行时设置
from data_hub import TUSHARE_CONFIG
TUSHARE_CONFIG.token = "your_token_here"
```

### 基本用法

```python
from data_hub import get_data

# 1. 获取指数成分股
codes = get_data("index_components", index_code="000300.SH")
print(f"沪深300成分股: {len(codes)}只")

# 支持简称
codes = get_data("index_components", index_code="沪深300")

# 2. 获取期权数据
option = get_data("option_data", exchange="SSE", underlying="510300")
print(f"合约数量: {len(option['contracts'])}")

# 3. 获取A股日线（单只股票）
df = get_data("stock_daily", ts_code="000001.SZ",
              start_date="20260101", end_date="20260716")
print(f"K线数量: {len(df)}")

# 4. 获取全市场某日行情
df = get_data("stock_daily", trade_date="20260716")
print(f"全市场股票数: {len(df)}")
```

### 获取完整结果对象

```python
from data_hub import get_data_result

result = get_data_result("index_components", index_code="000300.SH")

print(f"成功: {result.success}")
print(f"数据源: {result.source} (Tier {result.source_tier})")
print(f"数据量: {result.data_count}")
print(f"时间戳: {result.timestamp}")
print(f"元数据: {result.meta}")
print(f"数据: {result.data[:5]}...")  # 前5只
```

## 数据类型详解

### 1. index_components — 指数成分股

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| index_code | str | ✅ | 指数代码（支持tushare格式/6位数字/简称） |

**支持的指数：**
| 简称 | Tushare代码 | 成分股数 |
|------|------------|---------|
| 沪深300 | 000300.SH | 300 |
| 中证500 | 000905.SH | 500 |
| 中证1000 | 000852.SH | 1000 |
| 创业板指 | 399006.SZ | 100 |
| 科创50 | 000688.SH | 50 |
| 上证50 | 000016.SH | 50 |
| 创业板50 | 399673.SZ | 50 |

**返回：** `list[str]` — tushare格式成分股代码列表（如 `['000001.SZ', '600000.SH', ...]`）

### 2. option_data — 期权数据

**参数：**
| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| exchange | str | ✅ | - | 交易所：SSE / SZSE / CFFEX |
| underlying | str | ❌ | None | 标的代码（如 510300、159915、MO） |
| trade_date | str | ❌ | 当日 | 交易日期（YYYYMMDD格式） |

**覆盖标的：**
| 交易所 | 标的代码 | 名称 |
|--------|---------|------|
| SSE | 510050 | 50ETF |
| SSE | 510300 | 300ETF（沪） |
| SSE | 510500 | 500ETF（沪） |
| SSE | 588000 | 科创50ETF |
| SZSE | 159919 | 300ETF（深） |
| SZSE | 159915 | 创业板ETF |
| CFFEX | MO | 中证1000股指期权 |

**返回：** `dict`
```python
{
    "contracts": pd.DataFrame,    # 合约基本信息
    "daily": pd.DataFrame,        # 日线行情
    "underlying_price": float,    # 标的现货价格（如有）
}
```

### 3. stock_daily — A股日线行情

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| ts_code | str | ❌ | tushare格式股票代码（如 000001.SZ），单只 |
| symbol | str | ❌ | 6位数字股票代码，单只 |
| trade_date | str | ❌ | 单日日期（YYYYMMDD），全市场模式 |
| start_date | str | ❌ | 开始日期（YYYYMMDD） |
| end_date | str | ❌ | 结束日期（YYYYMMDD） |
| adjust | str | ❌ | 复权方式：qfq(前复权)/hfq(后复权)/''(不复权) |

**两种模式：**
- **单只模式**：传 ts_code 或 symbol + 日期范围
- **全市场模式**：传 trade_date（不传 ts_code/symbol）

**返回：** `pd.DataFrame` — 包含列：ts_code, trade_date, open, high, low, close, vol, amount, pct_chg 等

### 4. 期权原始行情接口（业务脚本 akshare 直连的统一替代）

> 背景：`市场情绪监控` 等业务脚本原先直连 `akshare` 取期权T型报价与标的现货快照。
> 现全部收敛到 data_hub，data_hub 成为全系统唯一数据入口，并自动获得四级降级能力。
> **返回值与 akshare 原生函数列名/行数/数值完全一致**，上层业务解析逻辑零改动。

| 便捷函数 | 对应 akshare 原生函数 | 参数 | 返回 |
|---------|---------------------|------|------|
| `get_option_board()` | `option_finance_board` | `symbol`（看板名，如 `华夏上证50ETF期权`）、`end_month`（YYMM） | `DataFrame`：日期/合约交易代码/当前价/涨跌幅/前结价/行权价/数量 |
| `get_option_underlying_quote()` | `option_finance_sse_underlying` | `symbol`（看板名） | `DataFrame`：代码/名称/当前价/涨跌/涨跌幅/振幅/成交量(手)/成交额(万元)/更新日期 |
| `get_option_cffex_zz1000_list()` | `option_cffex_zz1000_list_sina` | - | `dict`：`{'中证1000指数': ['mo2611', ...]}` |
| `get_option_cffex_zz1000_spot()` | `option_cffex_zz1000_spot_sina` | `symbol`（如 `mo2611`） | `DataFrame`：中金所MO T型报价 |
| `get_index_spot_sina()` | `stock_zh_index_spot_sina` | `index_codes`（可选，如 `['sh000852']`，仅 Web 兜底使用） | `DataFrame`：代码(sh000852)/名称/最新价/... |
| `get_etf_spot_em()` | `fund_etf_spot_em` | `etf_codes`（可选，如 `['510050']`，仅 Web 兜底使用） | `DataFrame`：代码(510050，无前缀)/名称/最新价/... |

**降级说明：**

| 接口 | Tier1 AKShare | Tier2 Tushare | Tier3 Coze网关 | Tier4 Web |
|------|--------------|---------------|---------------|-----------|
| option_board | ✅ | - | - | - |
| option_underlying_quote | ✅（pandas 3.x 下上游报错） | - | - | ✅ 新浪 hq 兜底 |
| option_cffex_zz1000_list / _spot | ✅ | - | - | - |
| index_spot_sina | ✅ | - | - | ✅ 新浪 hq 兜底 |
| etf_spot_em | ✅（东财接口部分网络环境不可达） | - | - | ✅ 新浪 hq 兜底 |

> Web 兜底已与 Tushare 主链路交叉核验：50ETF/300ETF/500ETF/科创50ETF/创业板ETF
> 五个标的的新浪兜底价与 Tushare `fund_daily` 收盘价**完全一致**。
> `option_board` 暂未提供 Web 兜底（上交所官网期权行情接口已不可用），失败时返回空 DataFrame，
> 与改造前 akshare 返回空的行为一致。

## 便捷函数

```python
from data_hub import (
    get_index_components,     # 获取指数成分股
    get_top2000_stocks,       # 获取前2000只（沪深300+中证500+中证1000合并去重）
    get_option_data,          # 获取期权数据
    get_all_option_contracts, # 获取全部交易所期权合约
    get_stock_daily,          # 获取日线行情
    get_market_daily,         # 获取全市场某日行情
    # 期权原始行情（业务脚本 akshare 直连的统一替代）
    get_option_board,             # 期权T型报价
    get_option_underlying_quote,  # 期权标的当日行情
    get_option_cffex_zz1000_list, # 中金所MO合约月份列表
    get_option_cffex_zz1000_spot, # 中金所MO T型报价
    get_index_spot_sina,          # 新浪指数实时行情
    get_etf_spot_em,              # 东财ETF实时行情
)

# 示例：获取全市场前2000只
top2000 = get_top2000_stocks()
print(f"前2000只: {len(top2000)}只")
```

## 三级降级机制

```
请求数据
   │
   ▼
┌─────────────────┐
│  Tier 1: AKShare │  免费无限制，东方财富/新浪数据源
│  (首选)          │  调用间隔 ≥ 0.1s
└────────┬────────┘
         │ 失败 / 数据不足
         ▼
┌─────────────────┐
│  Tier 2: Tushare │  专业数据平台，质量高
│  (二级)          │  全局串行，间隔 ≥ 0.55s
└────────┬────────┘
         │ 失败 / 数据不足
         ▼
┌─────────────────┐
│  Tier 3: Web爬取  │  官网/财经网站爬取
│  (兜底)          │  仅作备用，速度较慢
└─────────────────┘
```

**降级触发条件：**
1. 数据源调用异常（网络错误、接口报错等）
2. 返回数据为空
3. 数据量不足（低于质量阈值）

**每级重试：** 默认重试 2 次（指数退避）

## 配置说明

```python
from data_hub import (
    TIMEOUT_CONFIG,       # 超时与重试配置
    QUALITY_THRESHOLDS,   # 数据质量阈值
    TUSHARE_CONFIG,       # Tushare配置
)

# 修改Tushare调用间隔
TIMEOUT_CONFIG.tushare_min_interval = 0.6  # 默认0.55s

# 修改指数成分股最少数量阈值
QUALITY_THRESHOLDS.min_index_components = 200  # 默认100

# 设置重试次数
TIMEOUT_CONFIG.max_retries = 3  # 默认2次
```

## 异常处理

```python
from data_hub import get_data_result, AllSourcesFailedError, DataHubError

try:
    result = get_data_result("index_components", index_code="000300.SH")
    # 处理成功结果
except AllSourcesFailedError as e:
    print(f"所有数据源均失败: {e}")
    print(f"各数据源失败原因: {e.failures}")
except DataHubError as e:
    print(f"DataHub错误: {e}")
except Exception as e:
    print(f"其他错误: {e}")
```

## 使用采集器类（高级用法）

```python
from data_hub import IndexComponentsCollector

# 自定义数据源优先级（比如只用 AKShare + Web）
collector = IndexComponentsCollector(
    source_priority=["akshare", "web"]
)

# 自定义 Tushare Token
collector = IndexComponentsCollector(tushare_token="your_token")

result = collector.collect(index_code="000300.SH")
```

## 扩展新的数据类型

1. 在 `sources/` 中为每个数据源添加对应的 `fetch_xxx` 方法
2. 在 `collectors/` 中创建新的采集器类，继承 `BaseCollector`
3. 在 `__init__.py` 的 `_COLLECTOR_REGISTRY` 中注册

```python
# 示例：添加新数据类型
from data_hub.base import BaseCollector
from data_hub.sources import AKShareSource, TushareSource

class MyDataCollector(BaseCollector):
    data_type = "my_data"
    min_data_count = 5

    def __init__(self, source_priority=None):
        super().__init__(source_priority=source_priority)
        self.register_source(AKShareSource())
        self.register_source(TushareSource())
```

## 设计原则

1. **宁慢勿空，绝不造假** — 宁可降级慢一点，也不返回虚假或估算数据
2. **明确失败** — 数据缺失返回 None/空 DataFrame，有明确日志
3. **全局协调** — Tushare 调用全局串行，避免多脚本并发触发频率限制
4. **即时新鲜** — 不做跨天缓存，保证每次调用都是最新数据
5. **纯Python** — 零LLM依赖，可离线运行（网络除外）

## 版本历史

- **v1.1.0** (2026-10-22)
  - 新增 6 个期权原始行情接口：`option_board` / `option_underlying_quote` /
    `option_cffex_zz1000_list` / `option_cffex_zz1000_spot` / `index_spot_sina` / `etf_spot_em`
  - 新增 `option_maps.py`（期权看板↔标的ETF映射、默认指数/ETF代码集）
  - Web 层新增新浪 hq 现货快照与期权标的行情兜底
  - `市场情绪监控` 的 `option_monitor.py` / `skew_percentile.py` 完成 akshare 直连收敛，
    data_hub 成为期权数据全系统唯一入口
- **v1.0.0** (2026-07-16)
  - 初始版本
  - 支持 3 类核心数据：指数成分股、期权数据、A股日线
  - 三级降级：AKShare → Tushare → Web爬取
  - 全局 Tushare 串行控制
  - 完整单元测试
