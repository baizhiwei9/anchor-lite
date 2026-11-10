# Anchor-tsmom 分支信息

| 项 | 值 |
|---|---|
| 仓库 | `anchor_lite`（本目录，2026-11-10 初始化本地 git） |
| 分支 | `Anchor-tsmom` |
| 基线提交 | `d7d5833` baseline: Anchor-Lite v1.1-20261109（228 个既有文件，未改动） |
| 分支提交 | `a04f4a4` feat(tsmom): V1 baseline + V5/V6/V8 variants + test report |
| 远程 | 无（本工程无远端仓库，未执行 push） |
| 数据依赖 | `tsmom/assets/` 内已固化 13 池收盘面板与四模型信号快照（截至 2026-09-22） |

## 分支内容

```
tsmom/__init__.py                        包导出
tsmom/base.py                            TsmomEngine：数据/信号/regime/波动率目标/成本/指标
tsmom/variants.py                        V1 继承基线 + V5/V6/V8 派生 + 两个实验口径对照类
tsmom/run_all.py                         统一驱动，跑七套并输出 results.json / nav.csv
tsmom/assets/panel13.csv                 13 只 ETF 收盘面板（2013-01-04 ~ 2026-09-23）
tsmom/assets/sig_{SLP,MLP,CNN,LSTM}.npz  四模型信号矩阵（1145×13）
tsmom/assets/tucker_distance.csv         Tucker 三距离 z（2021-02-18 ~ 2026-09-22）
tsmom/out/results.json                   本次测试结果（含逐年）
tsmom/out/nav.csv                        本次测试逐日净值
tsmom/TEST_REPORT.md                     测试报告（含口径、结论、限制）
```

## 继承关系

`TsmomEngine` → `V1Ensemble`（0.7×MLP + 0.3×CNN）→ `V5Vol18` / `V6Tucker` / `V8RuleMix`，派生类只覆盖类属性（`vol_target` / `use_tucker` / `signal`），计算路径与 V1 完全一致。

## 关键结果（含成本 5bp，2022-01-04~2026-09-22）

| 组合 | 年化 | 最大回撤 | 夏普 |
|---|---|---|---|
| V1 集成 | 16.4% | −3.6% | 2.15 |
| V5 波动目标 18% | 16.7% | −3.6% | 2.14 |
| V6 Tucker 闸门 | 14.4% | −3.6% | 2.00 |
| V8 MLP与规则A各半 | 23.6% | −8.9% | 1.84 |

实验口径对照：参考 MP 基座（纯 MLP+vol15%，无成本）+121.9% / 年化 19.2% / 回撤 −4.9% / 夏普 2.23，与原 STMOM 口径二报告一致；V5-lite 19.1%/−5.0%、V6-lite 15.2%/−5.0%。

## 复跑

```bash
cd anchor_lite
git checkout Anchor-tsmom
export PYTHONPATH=<workspace>/py_deps
python tsmom/run_all.py     # 末尾打印 TSMOM_BRANCH_DONE
```

## 校验记录

- 原有 323 个文件 md5 与改动前基线完全一致（0 处差异）。
- 连续两次运行 `run_all.py`，七套组合全部指标数值差异为 0。
- `outputs` 仅写 `tsmom/out/`，未触碰工程内其它目录。