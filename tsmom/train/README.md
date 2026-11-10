# tsmom/train — 训练代码（可切换标的池）

本目录是从 STMOM 工程迁入的**训练代码**，用于重新训练 SLP / MLP / CNN / LSTM 四个模型，产出供 `tsmom/` 组合层使用的信号矩阵。**迁入时未执行训练**，只做静态校验。

## 文件

| 文件 | 作用 |
|---|---|
| `train_4models.py` | 四模型训练主脚本（PyTorch），随机搜索超参 + 多种子集成，输出 `sig_*.npz` / `metrics.json` |
| `stmom.py` | 特征工程：波动率归一化 lag 收益（1/21/63/126/252）+ 波动率归一化 MACD（8/24、16/48、32/96），张量 (N, L=5, F=8) |
| `check_data.py` | 数据与池校验（不依赖 PyTorch），报告 N/L/F/D、训练段与回测段样本数 |
| `train_pool71_reference.py` | 原工程的 16 模型池 + 72 规则池扩展脚本（参考实现，路径按原工程硬编码） |

## 资产（`tsmom/assets/`）

| 文件 | 内容 |
|---|---|
| `panel13.csv` | 13 只全球宽基 ETF 面板（2013-01-04 ~ 2026-09-23），对应原 13 池训练口径 |
| `pool_model.csv` | 16 只**模型池**面板（2016-08 前上市，可训练） |
| `pool_rules71.csv` | 72 只**规则池**面板（多数 ETF 上市晚，只能做规则回测） |
| `etf_pool_meta.json` | 池元数据：`model_pool`(16) / `rules_pool`(72) / listing / names / groups / 训练与回测区间 / dropped |
| `model_pool.json`、`rules_pool.json` | 上述两个池的纯代码列表，便于 `--pool-file` 直接引用 |

## 依赖

- 训练需要 **PyTorch**（`train_4models.py` 与 `train_pool71_reference.py` 顶部 `import torch`）；当前沙箱未安装，因此本次**未执行训练**，只做了 `py_compile` 与数据校验。
- `check_data.py` 只依赖 numpy / pandas，可在无 PyTorch 环境下运行。

## 切换到行业 ETF / 股票 ETF

池不再是写死的 13 只，改为三处可控：`--panel`（面板）、`--pool` / `--pool-file`（标的列表）、`--out`（输出目录）。张量维度按列数自适应（`D = N × L × F`），换池不需要改模型结构。

```bash
# 1) 先校验池与数据（无需 torch）
python tsmom/train/check_data.py \
  --panel tsmom/assets/pool_model.csv --pool-key model_pool

# 2) 用 16 只模型池训练（需要 torch）
python tsmom/train/train_4models.py \
  --panel tsmom/assets/pool_model.csv \
  --pool-file tsmom/assets/model_pool.json \
  --train-end 2021-12-31 --eval-start 2022-01-01 \
  --out tsmom/train/out_pool16

# 3) 复刻原 13 池（默认参数即原口径）
python tsmom/train/train_4models.py            # 等价于 --panel assets/panel13.csv --out tsmom/train/out
```

## 口径与实测校验结果

| 池 | 面板 | N | D=N×L×F | 训练段样本 | 回测段样本 |
|---|---|---|---|---|---|
| 原 13 池 | `panel13.csv` | 13 | 520 | 1059（2017-08-25~2021-12-31） | 1145（2022-01-04~2026-09-22） |
| 16 只模型池 | `pool_model.csv` | 16 | 640 | 776（2017-08-25~2021-12-28） | 921（2022-01-06~2026-09-21） |
| 72 只规则池 | `pool_rules71.csv` | 72 | 2880 | **0** | 312（2025-05-14~2026-09-21） |

72 只规则池训练段为 0，原因是多数 ETF 上市晚于 2021-12-31、且 72 只同时有数据的窗口只有 2025-05 之后——**该池只能做规则回测，不能训练模型**。这正是原扩展脚本拆成「16 只模型池训练 + 72 只规则池回测」的原因。

原工程 13 池完整训练耗时 **207 秒**（4 模型 × 24 组超参 × 5 种子，日志见原工程 `stmom_4models_run.log`），16 池样本更少，预计同量级或更快。

## 输出

`--out` 目录下：`sig_SLP.npz`、`sig_MLP.npz`、`sig_CNN.npz`、`sig_LSTM.npz`（各含 `dates`、`s`、`s0..sN` 种子信号）、`metrics.json`。这些文件可直接替换 `tsmom/assets/sig_*.npz`，供 `python tsmom/run_all.py` 回测。换池训练时注意同步更新 `tsmom/base.py` 里的 `POOL13` 与收益面板，否则组合层与信号维度不匹配。

## 已知限制

- 训练脚本的早停/超参搜索仍依赖固定随机种子（`torch.manual_seed(0)`），不同 torch 版本可能有数值差异。
- 未做 walk-forward 重训，训练窗口固定为 `--train-end` 之前的样本。
- 本目录迁入时未执行训练，模型指标以原工程 `stmom_4models_out/metrics.json` 为准。