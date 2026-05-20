# Week 2 Algorithm Issue Record

本文档记录第二周优化算法与交互界面实现过程中观察到的主要问题、对应代码原因，以及后续改进方向。它可以作为后续实验报告中“方法描述”“问题分析”“不足与改进”部分的草稿。

## 1. 当前实现概述

当前项目已经实现了三类优化算法，统一入口位于 `src/optimizer.py`：

- `greedy`：贪心局部搜索
- `annealing`：模拟退火
- `random`：随机搜索基线

优化入口代码如下：

```python
def optimize(
    dataset: Dataset,
    algorithm: AlgorithmName = "greedy",
    max_iter: int = 10_000,
    seed: int = 0,
    min_gap: float = 2.0,
    initial_step: float | None = None,
    legality_weight: float = 100_000.0,
    initial_temperature: float | None = None,
    cooling_rate: float = 0.995,
) -> OptimizationResult:
    config = OptimizationConfig(
        algorithm=algorithm,
        max_iter=max_iter,
        seed=seed,
        min_gap=min_gap,
        initial_step=initial_step,
        legality_weight=legality_weight,
        initial_temperature=initial_temperature,
        cooling_rate=cooling_rate,
    )
    if algorithm in {"greedy", "local_search"}:
        return greedy_local_search(dataset, config)
    if algorithm in {"annealing", "simulated_annealing"}:
        return simulated_annealing(dataset, config)
    if algorithm == "random":
        return random_search(dataset, config)
    else:
        raise ValueError(f"Unsupported optimizer algorithm: {algorithm}")
```

目前优化器的核心思想是：随机选择一个元件，尝试移动它，计算移动后的综合评分，如果综合评分变好则接受该移动。这个综合评分不是单纯的 HPWL，而是：

```python
score = hpwl + legality_weight * legality_penalty
```

对应实现如下：

```python
def placement_score(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board | None = None,
    min_gap: float = 2.0,
    legality_weight: float = 100_000.0,
) -> float:
    effective_board = board or infer_board(dataset.components, placements)
    hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    legality = check_layout_legality(
        dataset.components,
        placements,
        dataset.nets,
        board=effective_board,
        min_gap=min_gap,
    )
    return hpwl + legality_weight * legality_penalty(legality, min_gap=min_gap)
```

## 2. 问题一：当前算法效果比不上老师给出的 small-5 参考优化

### 2.1 现象

老师给出的 `small-5` 参考优化结果中，元件明显围绕三个 BGA 芯片聚集，长距离连线被大量缩短。这个效果类似全局布局器或 AutoDMP 类方法产生的结果。

当前实现的算法虽然能在部分数据集上降低 HPWL，但优化幅度不稳定，特别是在迭代次数较少或者初始布局不合法时，结果明显不如参考样例。

当前 `results/week2/optimization_metrics.csv` 仍是 smoke test 数据，只跑了 `small-2` 且迭代次数为 20，不能代表正式实验结果：

```text
small-2 greedy:    2712.5 -> 2703.46
small-2 annealing: 2712.5 -> 2712.5
small-2 random:    2712.5 -> 2655.15
```

这说明当前记录的结果只是功能验证，不是最终优化实验。

### 2.2 代码层面的原因

当前贪心局部搜索每一步只移动一个随机元件：

```python
name = rng.choice(movable_names)
candidate = dict(placements)
candidate[name] = _random_move(
    placement=placements[name],
    component_width=dataset.components[name].width,
    component_height=dataset.components[name].height,
    board=board,
    step=step,
    rng=rng,
)
```

这个搜索方式有几个限制：

1. 每次只移动一个元件，无法快速形成整体聚类。
2. 移动方向完全随机，没有利用 net 连接关系提供的梯度或吸引力。
3. 元件之间没有分组，例如没有识别“围绕 BGA 的相关电阻电容”。
4. 当前移动是局部扰动，缺少全局重排能力。
5. 没有学习或参考已有优化结果中的结构模式。

因此，该算法更像一个基础随机局部搜索，而老师样例 `small-5` 中的 AutoDMP 结果更像是全局布局算法的输出。AutoDMP 类方法通常会利用连续优化、密度约束、全局线长模型等信息，让大量元件整体向关键芯片附近收缩。当前算法没有这些机制，所以效果差距是预期内的。

### 2.3 模拟退火为什么也没有明显解决问题

模拟退火相比贪心搜索允许接受一部分变差移动：

```python
delta = candidate_score - current_score
if delta < 0 or rng.random() < math.exp(-delta / max(temperature, 1e-9)):
    placements = candidate
    current_score = candidate_score
```

这能缓解局部最优，但它仍然使用随机单元件移动作为候选解。也就是说，退火只是改变“是否接受移动”的策略，并没有改变“如何生成有价值移动”的能力。因此，如果候选移动本身大多质量不高，退火也很难达到参考布局那种全局重排效果。

## 3. 问题二：当前算法不能稳定解决非法布局

### 3.1 现象

第一周初始检查发现多个数据集原始布局本身不合法：

```text
small-1: legal=False, gap violations=5
small-2: legal=True,  gap violations=0
small-3: legal=False, gap violations=12
small-4: legal=False, gap violations=18
small-5: legal=False, gap violations=35
```

当前优化器会尝试降低综合评分，但不能保证最终布局一定合法。实际使用时经常出现：

- gap violations 减少，但没有降到 0
- HPWL 降低了，但布局仍不合法
- 或者为了减少违法罚分，HPWL 暂时变大

### 3.2 代码层面的原因

当前合法性处理采用罚函数：

```python
return hpwl + legality_weight * legality_penalty(legality, min_gap=min_gap)
```

其中间距违规罚分如下：

```python
for violation in legality.gap_violations:
    need_x = max(0.0, min_gap - violation.gap_x)
    need_y = max(0.0, min_gap - violation.gap_y)
    penalty += min(need_x, need_y)
```

这种方法的问题是：它把“合法性约束”变成了一个连续罚分项，而不是硬约束。只要综合评分下降，算法就可能接受一个仍然不合法的布局。

例如某次移动可能让违规程度从 18 降到 16，但仍然不合法。由于罚分下降，综合评分变好，算法会接受它。最终结果可能是“更接近合法”，但不是“完全合法”。

### 3.3 为什么不能简单要求每一步都合法

一个直觉做法是：只接受完全合法的移动。但这在当前数据上会遇到问题，因为多个数据集初始布局已经非法。如果从非法初始状态出发，并要求每一步候选布局必须完全合法，那么绝大多数候选移动都会被拒绝，算法很可能无法启动。

也就是说：

- 对合法初始布局，可以使用硬约束优化。
- 对非法初始布局，需要先做 repair 或 legalization。

当前代码没有独立的合法化阶段，所以非法布局不能稳定修复。

## 4. 问题三：HPWL 降低率和综合评分可能不一致

界面中展示的 HPWL 降低率定义为：

```text
HPWL降低率 = (initial_hpwl - optimized_hpwl) / initial_hpwl * 100%
```

对应 `OptimizationResult` 中的实现：

```python
@property
def improvement_ratio(self) -> float:
    if self.initial_hpwl == 0:
        return 0.0
    return self.improvement / self.initial_hpwl
```

但是算法实际优化的是综合评分：

```text
score = HPWL + 合法性罚分
```

因此在非法布局上会出现一个现象：综合评分下降，但 HPWL 降低率变小，甚至变成负数。

原因是某次移动可能牺牲一部分 HPWL，但显著减少间距违规。算法认为这是好移动，因为综合评分下降；界面只看 HPWL 时，则可能显示优化效果下降。

这不是计算错误，而是当前目标函数和展示指标不完全一致。

## 5. 当前算法适合在报告中如何表述

当前算法可以作为“基础优化框架”和“交互式优化系统”的第一版。建议在报告中如实表述：

1. 本项目实现了 Bookshelf 数据读取、HPWL 计算、合法性检查和可视化。
2. 优化部分实现了贪心局部搜索、模拟退火和随机搜索三种方法。
3. 当前算法使用罚函数将 HPWL 与合法性约束合并为一个综合评分。
4. 该方法实现简单，便于接入交互界面，也支持用户手动拖动元件后继续优化。
5. 但它属于随机局部搜索，缺少全局布局能力，因此与 AutoDMP 参考结果仍有差距。
6. 对初始非法布局，当前方法只能减少违规程度，不能保证最终完全合法。

## 6. 建议的后续改进方向

### 6.1 增加两阶段优化

建议将算法拆成两个阶段：

```text
阶段一：合法化
目标：优先消除越界和间距违规

阶段二：HPWL 优化
目标：在保持合法性的前提下降低 HPWL
```

合法化阶段可以只比较违规数量和违规程度，不优先考虑 HPWL。伪代码如下：

```text
while has_violation:
    select a violating component
    try candidate moves around it
    accept move if violation count decreases
```

HPWL 优化阶段再采用硬约束：

```text
if candidate is legal and candidate_hpwl < current_hpwl:
    accept candidate
```

### 6.2 加入基于 net 的移动方向

当前移动方向是随机的。可以改为让元件朝相连引脚的重心移动：

```text
target_x = average(x positions of connected pins)
target_y = average(y positions of connected pins)
move component toward target
```

这会比纯随机移动更接近布局优化中的力导向方法。

### 6.3 对大芯片和小元件采用不同策略

样例 `small-5` 的优化结果显示，小元件通常围绕 BGA 芯片聚集。因此可以：

- 固定或少量移动 BGA 大芯片
- 优先移动与 BGA 相连的小电阻、电容
- 对高连接度 net 给予更高优先级

### 6.4 记录收敛过程

后续实验应记录每隔固定迭代次数的：

- HPWL
- 综合 score
- gap violation 数量
- 是否合法

这样报告可以画收敛曲线，也能解释为什么某些数据集 HPWL 下降不明显。

## 7. 当前阶段结论

当前实现已经具备完整系统雏形：可以上传数据、可视化布局、手动拖拽元件、运行多种优化算法、继续优化并输出结果。但当前优化算法仍属于基础随机局部搜索，主要问题是：

1. 缺少全局重排能力，所以难以达到老师给出的 AutoDMP 样例效果。
2. 使用罚函数处理合法性，不能保证非法初始布局最终变合法。
3. 算法实际优化的是综合 score，而界面主要展示 HPWL，因此可能出现 score 改善但 HPWL 降低率变差的现象。

下一阶段应优先实现“合法化阶段”和“基于 net 重心的移动策略”，再正式运行 `small-1` 到 `small-4` 的完整实验。
