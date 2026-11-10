# -*- coding: utf-8 -*-
"""四套组合：V1 为继承基线，V5 / V6 / V8 继承 V1 并只覆盖差异参数。

继承关系：
    TsmomEngine
        └── V1Ensemble        集成信号 0.7×MLP + 0.3×CNN，vol 15%，含成本 5bp
                ├── V5Vol18       仅覆盖 vol_target = 0.18
                ├── V6Tucker      仅开启 Tucker 三距离闸门
                └── V8RuleMix     仅覆盖信号为 MLP 与规则 A 各半

另附两套「实验口径」对照类（V5Vol18MP / V6TuckerMP）：继承关系不变，只把信号
显式回退为纯 MLP，用于复刻叠加大实验阶段的原始数字（V5 19.1%/−5.0%、V6 15.2%/−5.0%）。
"""
from .base import TsmomEngine


class V1Ensemble(TsmomEngine):
    """V1 继承基线：0.7×MLP + 0.3×CNN 集成信号。"""

    name = "V1 集成 0.7MLP+0.3CNN"
    signal = "mix"
    vol_target = 0.15


class V5Vol18(V1Ensemble):
    """V5：在 V1 基础上把目标波动由 15% 提到 18%（等价于放大仓位，信号仍是 V1 集成）。"""

    name = "V5 波动目标18%"
    vol_target = 0.18


class V6Tucker(V1Ensemble):
    """V6：在 V1 基础上叠加 Tucker 三模式距离闸门（z≥1.5 仓位×0.5，z≥2.5 清仓）。"""

    name = "V6 Tucker闸门"
    use_tucker = True
    tucker_cut, tucker_scale = 1.5, 0.5
    tucker_cut2, tucker_scale2 = 2.5, 0.0


class V8RuleMix(V1Ensemble):
    """V8：在 V1 基础上把信号替换为「MLP 与规则 A（13 池等权全仓）各半」。"""

    name = "V8 MLP与规则A各半"
    signal = "ruleA_mix"


# ---- 实验口径对照（信号回退纯 MLP，其余与对应类一致） ----
class V5Vol18MP(V5Vol18):
    name = "V5-lite 纯MLP+vol18%（实验口径）"
    signal = "MLP"


class V6TuckerMP(V6Tucker):
    name = "V6-lite 纯MLP+Tucker（实验口径）"
    signal = "MLP"


VARIANTS = [V1Ensemble, V5Vol18, V6Tucker, V8RuleMix]
EXPERIMENT_REFS = [V5Vol18MP, V6TuckerMP]