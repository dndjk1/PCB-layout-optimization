# Week 2 Two-Stage Optimization Record

本文档记录本轮新增的两阶段优化算法、合法化 repair、基于 net 重心的移动策略、收敛曲线记录，以及一次 `small-1` 到 `small-4` 的实验结果。

## 1. 本轮新增功能

本轮在 `src/optimizer.py` 中新增了 `two_stage` 算法。该算法把优化过程拆成两个阶段：

1. **合法化 repair 阶段**：优先消除越界和元件间距违规。
2. **HPWL 优化阶段**：在尽量保持合法性的前提下，使用 net 重心引导移动，继续降低 HPWL。

同时新增了收敛记录结构 `ConvergenceRecord`，批量脚本 `scripts/run_optimizer.py` 会输出：

- `results/week2/optimization_metrics.csv`
- `results/week2/optimized_pl/two_stage/*.pl`
- `results/week2/layouts/two_stage/*.png`
- `results/week2/convergence/two_stage/*_history.csv`
- `results/week2/curves/two_stage/*_convergence.png`

## 2. 关键代码记录

### 2.1 算法入口

`optimize()` 增加了 `two_stage` 分支：

```python
if algorithm == "two_stage":
    return two_stage_optimize(dataset, config)
```

可用算法列表也更新为：

```python
def available_algorithms() -> list[str]:
    return ["annealing", "random", "two_stage"]
```

因此命令行脚本和 Web UI 中都会出现“两阶段优化”。后续版本中，`greedy` 贪心局部搜索由于合法化能力弱、容易停在局部最优，已经不再作为 UI 推荐算法展示。

### 2.2 两阶段优化流程

核心流程如下：

```python
repair_iter = max(1, config.max_iter // 2)
hpwl_iter = max(0, config.max_iter - repair_iter)

for iteration in range(repair_iter):
    legality = check_layout_legality(...)
    if legality.is_legal:
        break
    candidate = _repair_candidate(...)
    if _repair_rank(candidate) < _repair_rank(current):
        accept candidate

for offset in range(hpwl_iter):
    candidate[name] = _net_centroid_move(...)
    if current layout is legal:
        accept only legal HPWL-improving candidate
    else:
        accept lower-score candidate
```

这个设计解决了之前的一个核心问题：原来的算法只优化罚函数，不能保证从非法布局变成合法布局。现在 repair 阶段直接以“违规数量和违规程度”为优先目标。

### 2.3 合法化 repair

repair 阶段优先选择当前存在违规的元件：

```python
names = _violating_components(legality)
movable = [name for name in names if name in dataset.components and name in placements and not placements[name].fixed]
```

如果某个元件和其他元件距离不足，则尝试沿冲突方向推开：

```python
if abs(cx - ox) >= abs(cy - oy):
    dx += math.copysign(max(2.0, step * 0.35), cx - ox or rng.choice([-1.0, 1.0]))
else:
    dy += math.copysign(max(2.0, step * 0.35), cy - oy or rng.choice([-1.0, 1.0]))
```

repair 阶段的比较指标不是单纯 HPWL，而是：

```python
violation_count = len(gap_violations) + len(boundary_violations) + len(reference_violations) * 100
penalty = legality_penalty(...)
hpwl = total_hpwl(...)
return violation_count, penalty, hpwl
```

因此 repair 阶段会优先接受“让布局更合法”的移动。

### 2.4 基于 net 重心的移动

HPWL 阶段不再完全随机移动元件，而是计算与该元件相连的其他引脚位置，将元件向连接对象的平均位置移动：

```python
target_x = sum(point[0] for point in targets) / len(targets) - component.width / 2
target_y = sum(point[1] for point in targets) / len(targets) - component.height / 2

dx = clamp(target_x - placement.x, -step, step)
dy = clamp(target_y - placement.y, -step, step)
```

这相当于一个简化版力导向策略：连接越多的区域会对元件产生吸引，使局部连线更短。

### 2.5 收敛曲线记录

新增 `ConvergenceRecord`：

```python
@dataclass(frozen=True)
class ConvergenceRecord:
    iteration: int
    stage: str
    hpwl: float
    score: float
    gap_violations: int
    boundary_violations: int
    reference_violations: int
    is_legal: bool
```

每隔 `history_interval` 次迭代记录一次：

```python
_record_history(dataset, placements, board, config, history, iteration, stage)
```

`scripts/run_optimizer.py` 会把这些记录写成 CSV，并画出 HPWL 和间距违规数量的双轴曲线。

## 3. 本轮实验设置

运行命令：

```powershell
.venv\Scripts\python.exe scripts\run_optimizer.py --algorithm two_stage --max-iter 5000 --history-interval 100 --seed 21
```

实验对象：

- `small-1`
- `small-2`
- `small-3`
- `small-4`

## 4. 实验结果

| 数据集 | 初始 HPWL | 优化后 HPWL | HPWL 降低率 | 初始合法 | 优化后合法 | 初始间距违规 | 优化后间距违规 |
|---|---:|---:|---:|---|---|---:|---:|
| small-1 | 2625.0 | 2271.815 | 13.45% | False | True | 5 | 0 |
| small-2 | 2712.5 | 2284.292 | 15.79% | True | True | 0 | 0 |
| small-3 | 1781.0 | 1471.575 | 17.37% | False | True | 12 | 0 |
| small-4 | 1039.5 | 1207.561 | -16.17% | False | True | 18 | 0 |

本轮结果的重点是：**四个数据集全部变为合法布局**。这是之前基于罚函数的贪心/退火算法没有稳定做到的。

需要注意的是，`small-4` 的 HPWL 变大了。原因是 `small-4` 初始布局有 18 个间距违规，两阶段算法优先完成合法化 repair。合法化需要把重叠或过近元件推开，因此可能牺牲一部分线长。也就是说，`small-4` 的结果体现了“合法性优先”和“HPWL 最小化”之间的冲突。

## 5. 收敛曲线与布局图

### small-1

![small-1 convergence](../results/week2/curves/two_stage/small-1_two_stage_convergence.png)

![small-1 optimized layout](../results/week2/layouts/two_stage/small-1_two_stage_optimized_layout.png)

### small-2

![small-2 convergence](../results/week2/curves/two_stage/small-2_two_stage_convergence.png)

![small-2 optimized layout](../results/week2/layouts/two_stage/small-2_two_stage_optimized_layout.png)

### small-3

![small-3 convergence](../results/week2/curves/two_stage/small-3_two_stage_convergence.png)

![small-3 optimized layout](../results/week2/layouts/two_stage/small-3_two_stage_optimized_layout.png)

### small-4

![small-4 convergence](../results/week2/curves/two_stage/small-4_two_stage_convergence.png)

![small-4 optimized layout](../results/week2/layouts/two_stage/small-4_two_stage_optimized_layout.png)

## 6. 问题与分析

### 6.1 为什么 two_stage 能解决不合法问题

原来的算法使用：

```text
score = HPWL + 合法性罚分
```

这种方法只能让综合评分下降，不保证最终合法。新的 two_stage 在 repair 阶段直接比较：

```text
违规数量 -> 违规程度 -> HPWL
```

因此它会优先减少间距违规。实验中 `small-1 / small-3 / small-4` 的间距违规均降为 0。

### 6.2 为什么 small-4 的 HPWL 反而上升

`small-4` 初始 HPWL 很低，但初始布局不合法，包含 18 个间距违规。这说明部分线长优势来自过于紧密甚至非法的元件摆放。repair 阶段把元件推开后，线长自然可能增加。

因此对 `small-4`，当前结果可以解释为：

- 合法性显著改善：18 个间距违规降到 0
- HPWL 牺牲：1039.5 上升到 1207.561

这不是程序错误，而是合法化约束带来的代价。

### 6.3 为什么仍然比不上老师的 AutoDMP 样例

two_stage 已经加入 net 重心移动，但它仍然是启发式局部搜索，和 AutoDMP 类全局布局算法相比仍有差距：

- 没有连续优化模型
- 没有密度场约束
- 没有全局解析布局阶段
- 没有多尺度聚类
- 只做单元件局部移动，缺少大规模整体重排

因此它更适合作为课程项目中的可解释优化器，而不是工业级布局器。

## 7. 后续改进方向

后续可以继续增强：

1. 将 repair 阶段改为更系统的 legalization，例如按扫描线或网格空位放置。
2. 将 net 重心移动扩展为 force-directed，多轮计算所有元件受力。
3. 对 BGA 大芯片和小电阻/电容采用不同移动策略。
4. 增加多随机种子实验，选择每个数据集的最优结果。
5. 在 Web UI 中显示收敛曲线，并支持下载历史 CSV。

当前版本已经可以作为“第二周优化算法与问题记录”的正式材料。
