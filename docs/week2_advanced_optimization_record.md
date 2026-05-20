# Week 2 Advanced Optimization Record

本文档记录本轮继续增强后的优化器：系统 legalization、force-directed 多轮移动、器件类型差异化策略、多随机种子实验，以及 Web UI 收敛曲线展示功能。文档中的图片均已生成并保存在 `results/week2/` 下，可作为后续正式报告截图材料。

## 1. 本轮完成内容

本轮完成了五项增强：

1. 将 repair 阶段升级为更系统的 grid/scan legalization。
2. 将 net 重心移动扩展为 force-directed 多轮受力移动。
3. 对大芯片、小型无源器件和中型器件采用不同移动策略。
4. 增加多随机种子实验，自动选择每个数据集的最优结果。
5. 在 Web UI 中显示收敛曲线，并支持下载 history CSV。

## 2. 系统 Legalization

原先的 repair 阶段是随机挑选违规元件并尝试推开。这个方法能减少违规，但不够系统。本轮新增 `_grid_legalize()`，使用扫描线/网格空位思想重排非法元件：

```python
def _grid_legalize(dataset, placements, board, config):
    ordered = _legalization_order(dataset, placements)
    placed = {}
    result = dict(placements)

    for name in ordered:
        original = placements[name]
        component = dataset.components[name]
        if original.fixed:
            placed[name] = original
            result[name] = original
            continue
        if _fits_against_placed(dataset, original, component, placed, board, config.min_gap):
            placed[name] = original
            result[name] = original
            continue

        replacement = _find_grid_slot(dataset, result, placed, name, board, config)
        placed[name] = replacement
        result[name] = replacement

    return result
```

该方法按器件优先级依次放置：

- 固定器件优先
- BGA/大芯片优先
- 高连接度器件优先
- 其他器件随后放置

每放置一个器件时，会检查它与已经放置器件是否满足最小间距：

```python
def _fits_against_placed(dataset, placement, component, placed, board, min_gap):
    rect = component_rect(component, placement)
    if rect.left < board.left or rect.bottom < board.bottom or rect.right > board.right or rect.top > board.top:
        return False
    for other_name, other_placement in placed.items():
        other_component = dataset.components.get(other_name)
        if not has_min_gap(rect, component_rect(other_component, other_placement), min_gap=min_gap):
            return False
    return True
```

如果当前位置不合法，则 `_find_grid_slot()` 会扫描候选网格位置，并优先选择靠近 net 重心和原位置的合法空位。

## 3. Force-Directed 多轮移动

原来的 HPWL 阶段只把器件向相连 net 的重心移动。本轮扩展为 force-directed：

- 相连 net 对器件产生吸引力
- 过近器件产生排斥力
- 根据器件类型限制最大移动步长

核心代码：

```python
for net in dataset.nets:
    own_pins = [pin for pin in net.pins if pin.component == name]
    if not own_pins:
        continue
    target_x = average(connected_pin_x)
    target_y = average(connected_pin_y)
    force_x += (target_x - center_x) * weight
    force_y += (target_y - center_y) * weight

for other_name, other_placement in placements.items():
    if distance_sq < min_distance * min_distance:
        scale = (min_distance * min_distance - distance_sq) / distance_sq
        force_x += delta_x * scale * 2.0
        force_y += delta_y * scale * 2.0
```

这比单纯随机移动更有方向性，也比只看一个重心点更稳，因为它同时考虑了线长吸引和近邻排斥。

## 4. 器件类型差异化策略

本轮新增 `_component_kind()`：

```python
def _component_kind(dataset, name):
    component = dataset.components[name]
    max_area = max(item.width * item.height for item in dataset.components.values())
    area = component.width * component.height
    if area >= max_area * 0.15 or name.upper().startswith("U"):
        return "large"
    if name.upper().startswith(("R", "C", "L")) and area <= max_area * 0.08:
        return "passive"
    return "medium"
```

移动策略：

- `large`：BGA/大芯片，移动幅度较小，避免大芯片频繁扰动整体结构。
- `passive`：电阻、电容、电感等小器件，允许较大移动，更容易围绕核心芯片重新排列。
- `medium`：中型器件，使用中等步长。

force-directed 阶段的步长控制：

```python
if kind == "large":
    max_step = max(config.min_step, step * 0.25)
elif kind == "passive":
    max_step = step * 1.25
else:
    max_step = step * 0.8
```

## 5. 多随机种子实验

`scripts/run_optimizer.py` 新增：

```powershell
--seed-count
--seed-step
```

本轮实验命令：

```powershell
.venv\Scripts\python.exe scripts\run_optimizer.py --algorithm two_stage --max-iter 5000 --history-interval 100 --seed 31 --seed-count 3
```

脚本会对每个数据集运行 3 个 seed，并按以下规则选择最优：

```python
def _result_rank(result):
    return (
        0 if result.optimized_legality.is_legal else 1,
        result.optimized_score,
        result.optimized_hpwl,
    )
```

即：优先选择合法结果，其次选择综合 score 更低的结果，最后比较 HPWL。

所有 seed 的结果写入：

```text
results/week2/all_seed_metrics.csv
```

每个数据集最终选出的最佳结果写入：

```text
results/week2/optimization_metrics.csv
```

## 6. 多 Seed 最优实验结果

![two-stage best result table](../results/week2/summary/two_stage_best_results_table.png)

| 数据集 | 最优 seed | 初始 HPWL | 优化后 HPWL | 降低率 | 初始合法 | 优化后合法 | 间距违规 |
|---|---:|---:|---:|---:|---|---|---:|
| small-1 | 31 | 2625.0 | 1895.132 | 27.80% | False | True | 5 -> 0 |
| small-2 | 1131 | 2712.5 | 2056.471 | 24.19% | True | True | 0 -> 0 |
| small-3 | 2231 | 1781.0 | 1278.875 | 28.19% | False | True | 12 -> 0 |
| small-4 | 2331 | 1039.5 | 1091.117 | -4.97% | False | True | 18 -> 0 |

相比上一版 two-stage，本轮结果有明显提升：

- `small-1`：从约 13.45% 提升到 27.80%
- `small-2`：从约 15.79% 提升到 24.19%
- `small-3`：从约 17.37% 提升到 28.19%
- `small-4`：仍因 legalization 牺牲 HPWL，但从 -16.17% 改善到 -4.97%

## 7. 收敛曲线记录

收敛曲线拼图如下：

![two-stage convergence montage](../results/week2/summary/two_stage_best_convergence_montage.png)

单独曲线文件：

- `results/week2/curves/two_stage/small-1_two_stage_best_convergence.png`
- `results/week2/curves/two_stage/small-2_two_stage_best_convergence.png`
- `results/week2/curves/two_stage/small-3_two_stage_best_convergence.png`
- `results/week2/curves/two_stage/small-4_two_stage_best_convergence.png`

对应历史 CSV：

- `results/week2/convergence/two_stage/small-1_two_stage_best_history.csv`
- `results/week2/convergence/two_stage/small-2_two_stage_best_history.csv`
- `results/week2/convergence/two_stage/small-3_two_stage_best_history.csv`
- `results/week2/convergence/two_stage/small-4_two_stage_best_history.csv`

## 8. 优化布局截图记录

优化后布局拼图如下：

![two-stage layout montage](../results/week2/summary/two_stage_best_layout_montage.png)

单独布局图：

- `results/week2/layouts/two_stage/small-1_two_stage_best_optimized_layout.png`
- `results/week2/layouts/two_stage/small-2_two_stage_best_optimized_layout.png`
- `results/week2/layouts/two_stage/small-3_two_stage_best_optimized_layout.png`
- `results/week2/layouts/two_stage/small-4_two_stage_best_optimized_layout.png`

## 9. Web UI 增强

Web UI 已增加以下功能：

- 优化完成后显示收敛曲线
- 支持下载本次优化 history CSV
- 支持 `two_stage` 两阶段优化按钮
- 手动拖动元件后，仍可从当前布局继续 two-stage 优化

后端接口 `process_optimization()` 返回：

```python
"history": history_payload(result.history)
```

前端用该历史数据绘制 SVG 收敛曲线，并提供：

```text
下载历史 CSV
```

## 10. 当前问题

虽然本轮增强后效果明显提高，但仍有问题：

1. `small-4` 的 HPWL 仍然略高于初始值。
   - 原因是初始布局有 18 个间距违规，合法化会推开器件，导致线长增加。
   - 本轮已将损失从 -16.17% 改善到 -4.97%。

2. grid legalization 是启发式扫描，不是严格最优。
   - 它能稳定找到合法位置，但不保证全局最小 HPWL。

3. force-directed 仍是局部算法。
   - 没有连续优化、密度场、全局解析布局等工业布局器机制。

## 11. 当前结论

本轮优化完成后，算法已经从“随机局部搜索”升级为“合法化优先 + 力导向优化 + 多 seed 选择”的可解释启发式布局器。

最重要的改进是：

- 四个评分数据集最终全部合法。
- `small-1 / small-2 / small-3` 都取得了 24% 到 28% 的 HPWL 降低。
- `small-4` 虽然 HPWL 略升，但合法性从 18 个间距违规修复到 0，且相较上一版损失显著降低。

该版本已经适合作为正式报告中的主要算法结果。

## 12. 面向 small-5 参考样例的进一步改进记录

在对比老师给出的 `small-5` 参考结果后，发现上一版 two-stage 虽然能保证合法，但结构仍不像 PCB 实际布局：大芯片容易形成过长的横向链，小电阻/电容也没有稳定围绕相关芯片和公共电源网络成簇。因此本轮继续加入 PCB 结构先验。

### 12.1 改进思路

参考样例的主要特征是：

1. `U1/U2/U3` 这类大芯片先形成紧凑拓扑。
2. 小型 R/C/L 器件围绕相关芯片的引脚边缘排布。
3. 对没有直接连接大芯片的公共小网络，例如电源/控制网络，应把同一 net 上的小器件聚成局部小团。
4. 合法化之后再做 HPWL 局部精修，而不是只靠随机移动或模拟退火。

因此本轮将 `two_stage` 前半段改成：

```text
pin-aware macro placement
-> small-net clustering
-> grid legalization
-> slot-based HPWL refinement
-> force-directed local refinement
```

### 12.2 宏器件优先布局

新增 `_pin_aware_initial_placement()`。它先识别大芯片，再为大芯片生成紧凑拓扑位置：

```python
macro_names = [
    name
    for name in _macro_order(dataset, placements)
    if name in movable and name in dataset.components and name in placements
]

macro_targets = _compact_macro_targets(dataset, placements, macro_names, board)
```

对于 3 个大芯片的常见 PCB 结构，使用“三角形”紧凑拓扑，而不是简单保留原始横向距离：

```python
slots = [
    (-pitch_x * 0.62, pitch_y * 0.28),
    ( pitch_x * 0.62, pitch_y * 0.28),
    (0.0, -pitch_y * 0.72),
]
```

这样做的目的不是硬编码最终坐标，而是给后续合法化和局部优化一个更接近参考布局的结构起点。

### 12.3 引脚方向感知的小器件槽位

旧版只看 net 重心，无法知道小器件应该放在芯片哪一侧。本轮根据 pin offset 判断引脚方向：

```python
def _pin_side(component, dx, dy):
    nx = dx / max(component.width / 2, 1e-9)
    ny = dy / max(component.height / 2, 1e-9)
    if abs(nx) >= abs(ny):
        return "left" if nx < 0 else "right"
    return "bottom" if ny < 0 else "top"
```

随后在对应芯片边缘生成候选槽位：

```python
if side == "left":
    x = macro_placement.x - gap - component.width - ring * (component.width + gap)
elif side == "right":
    x = macro_placement.x + macro_component.width + gap + ring * (component.width + gap)
```

这样 R/C/L 不再随机散布，而是优先贴近它们实际连接的大芯片引脚边。

### 12.4 小网络聚类

`small-5` 中有一些公共网络本身不直接连接大芯片，例如 `NET_3V3_CTRL`。上一版会把这些小器件分散到多个区域，导致该 net HPWL 很大。本轮新增 `_place_small_net_clusters()`：

```python
cluster_nets = sorted(
    [
        net
        for net in dataset.nets
        if len(non_macro_pins) >= 3
        and not any(pin.component in macro_set for pin in net.pins)
    ],
    key=lambda net: -len(net.pins),
)
```

对这类小网络，先计算一个局部中心，再把网络内器件按小网格聚成一团：

```python
offsets = _cluster_offsets(ordered, dataset, config)
target = (
    center[0] + offset_x - component.width / 2,
    center[1] + offset_y - component.height / 2,
)
```

这一步对 `small-5` 的提升比较明显，因为参考结果中很多 R/C 网络本来就是成组摆放。

### 12.5 基于槽位的 HPWL 局部精修

在合法化后，新增 `_local_hpwl_slot_refinement()`。它逐个尝试把小器件移动到当前 net 重心附近的合法槽位，只有总 HPWL 下降才接受：

```python
target = _component_net_centroid(dataset, result, name)
candidate = _best_slot_near_target(...)

trial[name] = candidate
if legality.is_legal and hpwl < current_hpwl:
    result = trial
```

这一步解决了“布局已经合法，但线长仍偏长”的问题。

### 12.6 small-5 本轮结果

使用命令：

```powershell
.venv\Scripts\python.exe scripts\visualize_optimizer_run.py --input data\small-5_original_artifacts.zip --dataset small-5 --algorithm two_stage --max-iter 3000 --history-interval 500
```

得到结果：

| 数据集 | 算法 | 初始 HPWL | 优化后 HPWL | 降低率 | 初始合法 | 优化后合法 | 间距违规 |
|---|---|---:|---:|---:|---|---|---:|
| small-5 | two_stage | 9075.0 | 3725.559 | 58.95% | False | True | 35 -> 0 |

生成文件：

- `results/visual_runs/small-5_two_stage_seed0_iter3000/01_initial_layout.png`
- `results/visual_runs/small-5_two_stage_seed0_iter3000/02_optimized_layout.png`
- `results/visual_runs/small-5_two_stage_seed0_iter3000/03_convergence_curve.png`
- `results/visual_runs/small-5_two_stage_seed0_iter3000/04_before_after_comparison.png`
- `results/visual_runs/small-5_two_stage_seed0_iter3000/metrics.csv`
- `results/visual_runs/small-5_two_stage_seed0_iter3000/history.csv`

![small-5 before after comparison](../results/visual_runs/small-5_two_stage_seed0_iter3000/04_before_after_comparison.png)

### 12.7 与参考样例仍存在的差距

本轮结果已经从 `9075.0 -> 3725.559`，明显优于上一版 two-stage 的约 `5608.897`，但仍未达到老师样例的 `2882.0`。主要原因是：

1. 当前宏器件拓扑仍是启发式，不是严格全局最优。
2. 小器件槽位只考虑局部候选，不能像专业布局器一样同时优化所有器件位置。
3. 公共小网络聚类会改善局部 HPWL，但多个网络之间存在冲突，仍可能出现局部折中。
4. 当前没有显式考虑走线通道、pin access、器件朝向翻转等 PCB 细节。

下一步若继续追近参考样例，可以优先做：

- 对宏器件拓扑做多候选枚举，选择总 HPWL 最低且合法的宏布局。
- 对每个大芯片四周槽位做二分图匹配或最小费用匹配，而不是贪心放置。
- 增加小器件 swap/refinement：允许同一槽位组内的 R/C 两两交换，进一步降低 HPWL。
- 把 `small-net clustering` 从一次性聚类改成多轮聚类与合法化迭代。

## 13. 多样本运行截图记录

为对比代码改进后的整体效果，本轮使用新版 `two_stage` 对 `small-1` 到 `small-5` 均运行了一次非 UI 可视化实验。统一设置如下：

```powershell
.venv\Scripts\python.exe scripts\visualize_optimizer_run.py --dataset small-1 --algorithm two_stage --max-iter 3000 --history-interval 500 --output-dir results/visual_runs_after_improvement
.venv\Scripts\python.exe scripts\visualize_optimizer_run.py --dataset small-2 --algorithm two_stage --max-iter 3000 --history-interval 500 --output-dir results/visual_runs_after_improvement
.venv\Scripts\python.exe scripts\visualize_optimizer_run.py --dataset small-3 --algorithm two_stage --max-iter 3000 --history-interval 500 --output-dir results/visual_runs_after_improvement
.venv\Scripts\python.exe scripts\visualize_optimizer_run.py --dataset small-4 --algorithm two_stage --max-iter 3000 --history-interval 500 --output-dir results/visual_runs_after_improvement
.venv\Scripts\python.exe scripts\visualize_optimizer_run.py --dataset small-5 --algorithm two_stage --max-iter 3000 --history-interval 500 --output-dir results/visual_runs_after_improvement
```

### 13.1 汇总指标

汇总 CSV：

```text
results/visual_runs_after_improvement/summary/two_stage_after_improvement_metrics.csv
```

汇总表截图：

![two-stage after improvement table](../results/visual_runs_after_improvement/summary/two_stage_after_improvement_table.png)

| 数据集 | 初始 HPWL | 优化后 HPWL | HPWL 变化 | 初始合法 | 优化后合法 | 间距违规 |
|---|---:|---:|---:|---|---|---:|
| small-1 | 2625.0 | 2154.566 | 17.92% 降低 | False | True | 5 -> 0 |
| small-2 | 2712.5 | 1388.672 | 48.80% 降低 | True | True | 0 -> 0 |
| small-3 | 1781.0 | 981.719 | 44.88% 降低 | False | True | 12 -> 0 |
| small-4 | 1039.5 | 1348.876 | 29.76% 增加 | False | True | 18 -> 0 |
| small-5 | 9075.0 | 3725.559 | 58.95% 降低 | False | True | 35 -> 0 |

从结果看，新版 pin-aware / clustering 策略对 `small-2`、`small-3`、`small-5` 的收益明显；`small-5` 从不合法的 `9075.0` 降到合法的 `3725.559`。`small-4` 仍是主要问题样本，算法优先消除了 18 个间距违规，但 HPWL 被合法化推开导致上升。

### 13.2 前后布局对比截图

以下拼图包含每个样本的初始布局、优化后布局、收敛曲线和关键指标：

![two-stage after improvement comparison montage](../results/visual_runs_after_improvement/summary/two_stage_after_improvement_comparison_montage.png)

单独文件路径如下：

- `results/visual_runs_after_improvement/small-1_two_stage_seed0_iter3000/04_before_after_comparison.png`
- `results/visual_runs_after_improvement/small-2_two_stage_seed0_iter3000/04_before_after_comparison.png`
- `results/visual_runs_after_improvement/small-3_two_stage_seed0_iter3000/04_before_after_comparison.png`
- `results/visual_runs_after_improvement/small-4_two_stage_seed0_iter3000/04_before_after_comparison.png`
- `results/visual_runs_after_improvement/small-5_two_stage_seed0_iter3000/04_before_after_comparison.png`

### 13.3 收敛曲线截图

![two-stage after improvement convergence montage](../results/visual_runs_after_improvement/summary/two_stage_after_improvement_convergence_montage.png)

每个样本均输出了 history CSV：

- `results/visual_runs_after_improvement/small-1_two_stage_seed0_iter3000/history.csv`
- `results/visual_runs_after_improvement/small-2_two_stage_seed0_iter3000/history.csv`
- `results/visual_runs_after_improvement/small-3_two_stage_seed0_iter3000/history.csv`
- `results/visual_runs_after_improvement/small-4_two_stage_seed0_iter3000/history.csv`
- `results/visual_runs_after_improvement/small-5_two_stage_seed0_iter3000/history.csv`

### 13.4 对代码改进效果的观察

本轮多样本实验说明：

1. 新版 `two_stage` 对包含明显芯片-外围器件结构的数据更有效，尤其是 `small-5`。
2. 引脚方向感知槽位能让 R/C/L 更靠近对应芯片边缘。
3. 小网络聚类能缓解公共网络上的小器件分散问题。
4. slot-based refinement 可以在合法化后继续压低 HPWL。
5. 对 `small-4` 这类初始违法较多且布局空间紧张的样本，仍需要更强的合法化代价控制或多候选选择，否则合法化会牺牲较多线长。

因此，当前版本适合作为“结构先验启发式布局”的阶段性结果；下一步需要在 `small-4` 上加入多 seed / 多候选宏布局选择，并在最终结果选择时优先比较“合法 + HPWL 最低”的组合。

## 14. Hybrid Polish：结合模拟退火和随机搜索基线

在前面版本中，`two_stage` 主要负责生成结构上更像 PCB 的布局：先做宏器件拓扑、引脚方向感知小器件槽位、合法化 repair，再做 force-directed 局部移动。这个流程的优点是稳定、合法性较好；不足是后期容易进入局部最优，尤其是某些小器件已经合法但仍有微调空间时，单纯 force-directed 不一定能继续降低 HPWL。

因此本轮把模拟退火和随机搜索基线改造成 `two_stage` 后端的保守 polish 阶段，而不是让它们独立替代两阶段算法。新的流程为：

```text
pin-aware initial placement
-> grid legalization
-> force-directed local movement
-> hybrid polish: simulated annealing + random perturbation
-> postprocess
-> delayed disconnected placement
-> final centering
```

关键代码位于 `src/optimizer.py` 的 `_hybrid_baseline_polish()`。其核心原则是：

1. 从 `two_stage` 已经得到的布局出发，而不是重新随机生成布局。
2. 模拟退火只使用小步长扰动，避免把芯片和外围器件结构打散。
3. 随机搜索只作为补充扰动，候选位置限制在当前布局包围盒附近。
4. 候选布局必须保持合法；非法候选直接丢弃。
5. 最终只返回 HPWL 更低的布局，否则保留原 `two_stage` 结果。

关键代码片段如下：

```python
def _hybrid_baseline_polish(dataset, placements, board, config):
    current = dict(placements)
    current_hpwl = total_hpwl(dataset.nets, dataset.components, current)
    best = dict(current)
    best_hpwl = current_hpwl

    for _ in range(anneal_iter):
        candidate[name] = _random_move(...)
        legality = check_layout_legality(...)
        if not legality.is_legal:
            continue
        hpwl = total_hpwl(...)
        if delta < 0 or rng.random() < math.exp(-delta / temperature):
            accept candidate

    for _ in range(random_iter):
        candidate[name] = local_random_or_bbox_random_move(...)
        if legal and hpwl < best_hpwl:
            best = candidate

    if best_hpwl < original_hpwl:
        return best, accepted
    return placements, 0
```

这一步与普通模拟退火不同：普通模拟退火允许接受较差解以跳出局部最优；这里为了保护已经合法且结构较好的 PCB 布局，只把模拟退火作为“候选生成器”，最终结果仍由合法性和 HPWL 控制。随机搜索也不再作为完全随机 baseline，而是变成局部随机扰动，用来发现 force-directed 没有尝试到的小步改进。

在 `small-5` 上验证，本轮 hybrid polish 将上一版结果：

```text
HPWL 3725.559 -> 3723.626
合法性 True
间距违规 0
未连接器件 R96_3 仍放置在主簇周边
```

收益不算大，但说明该阶段可以在不破坏合法性和结构的前提下继续压低 HPWL。它更适合作为后处理 polish，而不是主优化器。

## 15. 删除贪心局部搜索基线

早期 UI 中保留了 `greedy` 贪心局部搜索按钮，用于和模拟退火、随机搜索作对比。但实际实验发现，贪心算法存在两个明显问题：

1. 它只接受立即变好的移动，容易停在局部最优。
2. 当布局初始状态存在重叠或间距违规时，贪心对合法化的修复能力弱，容易出现“HPWL 下降但布局仍不合法”的结果。

因此本轮从 `available_algorithms()` 中移除了 `greedy`：

```python
def available_algorithms() -> list[str]:
    return ["annealing", "random", "two_stage"]
```

Web UI 后续只显示：

- `annealing`：模拟退火基线
- `random`：随机搜索基线
- `two_stage`：当前主算法，内部已经结合 hybrid polish

底层 `greedy_local_search()` 函数暂时保留，作为历史代码和兼容入口，避免影响旧脚本；但它不再作为推荐算法出现在 UI 和实验流程中。

## 16. 新增解析式 Nesterov 全局布局实验

在继续调研更先进的布局算法时，本轮参考了 VLSI/PCB placement 中常见的 analytical placement 思路：

- NVIDIA Research 的 DREAMPlace 将解析式全局布局建模成类似深度学习训练的问题，用 PyTorch/GPU 加速 wirelength 和 density 计算。
- OpenROAD 的 global placement 模块基于 RePlAce，属于现代解析式全局布局路线。
- NVIDIA 开源的 Cypress 项目把 DREAMPlace/RePlAce 这类 VLSI-inspired 方法迁移到 PCB placement，说明该路线对 PCB 器件布局也有参考价值。

参考链接：

- `https://research.nvidia.com/publication/2019-06_dreamplace-deep-learning-toolkit-enabled-gpu-acceleration-modern-vlsi-placement`
- `https://openroad.readthedocs.io/en/latest/main/src/gpl/README.html`
- `https://github.com/NVlabs/Cypress`

这些工具完整实现较重，依赖 GPU、PyTorch/CUDA 或完整 EDA 数据格式，不能直接放进当前课程项目。因此本轮实现了一个轻量版 `analytical` 算法，用来验证解析式布局思想能否接入当前 `.nodes/.nets/.pl` 流程。

### 16.1 算法思想

新增算法名为 `analytical`，在 UI 中显示为“解析式 Nesterov 优化”。它的流程是：

```text
smooth wirelength gradient
-> density / boundary repulsion
-> Nesterov momentum continuous movement
-> grid legalization
-> slot refinement
-> postprocess
```

其中 wirelength 使用 log-sum-exp 的平滑近似来计算梯度。对每条 net，令 pin 坐标为 `x_i, y_i`，使用：

```text
smooth_hpwl_x = gamma * log(sum(exp(x_i / gamma)))
              + gamma * log(sum(exp(-x_i / gamma)))
```

这样可以近似 `max(x_i) - min(x_i)`，并且能对每个器件位置求梯度。实现代码位于 `_smooth_wirelength_gradients()`：

```python
gx = exp_x_pos[index] / sum_x_pos - exp_x_neg[index] / sum_x_neg
gy = exp_y_pos[index] / sum_y_pos - exp_y_neg[index] / sum_y_neg
```

随后加入简单的 density / boundary 梯度，避免器件重叠和越界：

```python
def _add_density_and_boundary_gradients(...):
    if rect.left < board.left:
        gx -= (board.left - rect.left) / repulsion_scale
    if rect.right > board.right:
        gx += (rect.right - board.right) / repulsion_scale
    ...
```

连续优化部分采用 Nesterov / momentum 风格更新：

```python
vx = momentum * vx - learning_rate * gx
vy = momentum * vy - learning_rate * gy
candidate[name] = replace(
    current[name],
    x=clamp(current[name].x + vx),
    y=clamp(current[name].y + vy),
)
```

最后仍接入当前项目已有的：

- `_grid_legalize()`
- `_local_hpwl_slot_refinement()`
- `_postprocess_layout()`

这样可以把连续布局结果转换成合法 PCB 布局。

### 16.2 与 two_stage 的关系

当前 `analytical` 还不是主算法，原因是它只实现了轻量版本，没有完整 RePlAce/DREAMPlace 的局部密度网格、电势场求解、动态步长、routability-driven inflation 等模块。因此它的结果稳定性和质量暂时不如 `two_stage`。

在 `small-5` 上的一次验证结果为：

```text
algorithm: analytical
initial HPWL: 9075.0
optimized HPWL: 8587.287
legal: True
gap violations: 0
```

该结果说明解析式方法能够接入当前数据格式，并能产生合法布局，但 HPWL 明显不如 `two_stage + hybrid polish` 的约 `3723.626`。因此后续更合理的方向不是让 `analytical` 直接替代 `two_stage`，而是把它作为：

1. `two_stage` 之前的全局预布局器；
2. 或者作为宏器件拓扑候选生成器；
3. 或者继续扩展成更接近 RePlAce/DREAMPlace 的 density-grid analytical placer。

### 16.3 UI 与脚本入口

`available_algorithms()` 已更新为：

```python
def available_algorithms() -> list[str]:
    return ["annealing", "random", "analytical", "two_stage"]
```

Web UI 中对应按钮为：

- `模拟退火优化`
- `随机搜索优化`
- `解析式 Nesterov 优化`
- `结构化两阶段混合优化`

其中 `解析式 Nesterov 优化` 是本轮新增的先进算法实验入口。

## 17. Density-Grid Analytical Placement 增强

在 `analytical` 能跑通后，本轮继续向 RePlAce / DREAMPlace 的 density-grid 方向增强。完整的 RePlAce/DREAMPlace 不只优化线长，还会把布局区域划分成很多 bin，统计每个 bin 的面积占用率，并对超过目标密度的区域施加 density penalty。这样可以避免器件全部被 wirelength 梯度拉到同一区域，导致严重重叠。

当前项目实现了一个轻量版 density-grid 梯度，代码位置为：

```text
src/optimizer.py
- _add_density_grid_gradients()
- _add_density_and_boundary_gradients()
```

### 17.1 核心流程

新的 `analytical` 梯度由三部分组成：

```text
total gradient
= smooth wirelength gradient
+ density-grid overflow gradient
+ boundary / pairwise repulsion gradient
```

其中 density-grid 部分先根据板框大小和器件数量自动设置 bin 数量：

```python
base_bins = max(4, min(18, int(math.sqrt(len(dataset.components)) * 1.6)))
bins_x = max(4, min(24, int(base_bins * math.sqrt(aspect))))
bins_y = max(4, min(24, int(base_bins / max(0.5, math.sqrt(aspect)))))
```

然后统计每个 bin 的占用密度：

```python
overlap_x = max(0.0, min(rect.right, bin_right) - max(rect.left, bin_left))
overlap_y = max(0.0, min(rect.top, bin_top) - max(rect.bottom, bin_bottom))
density[ix][iy] += (overlap_x * overlap_y) / bin_area
```

目标密度根据总器件面积和板面积自适应设置：

```python
target_density = clamp((total_area / board_area) * 1.25, 0.25, 0.82)
```

如果某个 bin 的密度超过目标密度，则对位于该 bin 附近的器件产生排斥力：

```python
overflow = density[bx][by] - target_density
if overflow > 0:
    gx += density_weight * overflow * dx / distance
    gy += density_weight * overflow * dy / distance
```

这一步对应 RePlAce/DREAMPlace 中的 density penalty 思想：拥挤区域的器件会被推向周围较空的位置。

### 17.2 与完整 RePlAce/DREAMPlace 的差别

当前实现仍是课程项目级的轻量近似，和完整算法有明显差距：

1. 完整 RePlAce 使用更严格的 electrostatic density model，并通过 Poisson 方程/电势场计算密度梯度；当前实现只是按 bin overflow 近似排斥。
2. 完整 DREAMPlace 使用 GPU/PyTorch 并行计算大量 cell 的梯度；当前数据规模较小，直接用 Python 循环即可。
3. 完整算法会动态调节 wirelength 和 density 权重；当前 `density_weight = 0.65` 较保守，避免过度推散 PCB 结构。
4. 完整算法还有 routability-driven inflation、macro handling、detailed placement 等步骤；当前仍依赖已有 `_grid_legalize()` 和 `_local_hpwl_slot_refinement()` 收尾。

因此，本轮增强的意义主要是把 `analytical` 从“只优化线长”的连续布局，推进到“线长 + 密度”的解析式布局框架，为后续实现更接近 RePlAce/DREAMPlace 的版本打基础。

### 17.3 当前实验观察

在 `small-5` 上运行：

```text
algorithm: analytical
initial HPWL: 9075.0
optimized HPWL: 8587.287
legal: True
gap violations: 0
```

与上一版 lightweight analytical 相比，当前指标基本接近，说明 density-grid 没有破坏合法化流程，但权重仍偏保守。后续可以继续尝试：

- 增大 `density_weight`，观察拥挤区域是否进一步展开；
- 按不同阶段动态提高 density 权重；
- 用网格电势场代替简单 overflow 排斥；
- 将 analytical 结果作为 `two_stage` 的宏器件初始候选，而不是独立最终结果。

## 18. 结构化 Seed 接入所有优化算法

在前面实验中可以看到，单独的模拟退火、随机搜索和解析式 Nesterov 都存在一个共同问题：如果直接从原始 `.pl` 布局开始搜索，算法需要先花大量迭代处理宏器件位置、重叠、非法间距和孤立器件问题，搜索效率较低。`two_stage` 的优势则是先利用 PCB 结构先验，快速得到一个更像样的初始布局。

因此本轮将 `two_stage` 中的结构化初始布局抽成公共预处理 `_structured_seed_placement()`，接入到另外三个算法中：

- `annealing`：结构化 seed + 模拟退火
- `random`：结构化 seed + 随机搜索
- `analytical`：结构化 seed + density-grid Nesterov
- `two_stage`：保留完整两阶段混合流程

UI 按钮也更新为：

- `结构化模拟退火优化`
- `结构化随机搜索优化`
- `结构化解析式 Nesterov 优化`
- `结构化两阶段混合优化`

### 18.1 公共结构化 Seed 流程

公共 seed 函数位于 `src/optimizer.py`：

```python
def _structured_seed_placement(dataset, placements, board, config, rng):
    structured = _pin_aware_initial_placement(...)
    legalized = _grid_legalize(...)
    refined, refine_accepts = _local_hpwl_slot_refinement(...)
    finalized, finalize_moves = _finalize_structured_seeded_result(...)
    return result, accepted
```

它包含四个步骤：

1. `pin-aware initial placement`：先按宏器件和引脚方向生成 PCB 风格初始布局。
2. `grid legalization`：用网格扫描空位修复重叠、越界和间距违规。
3. `slot refinement`：在合法基础上尝试槽位局部 HPWL 优化。
4. `structured finalize`：处理未连接器件并整体居中。

每个候选阶段都通过 `_structured_seed_is_better()` 判断是否接受：

```python
if after_rank < before_rank:
    return True
if after_rank > before_rank:
    return False
return after_score <= before_score + max(1e-6, abs(before_score) * 0.02)
```

也就是说，优先比较合法化/违规修复效果；如果合法性等级相同，再比较综合 score，并允许极小范围的结构化调整。

### 18.2 为什么还保留各自算法

接入结构化 seed 后，这三个算法不再是纯 baseline，而是变成“结构化初始化 + 各自搜索策略”：

- 模拟退火负责在结构化布局附近接受少量扰动，尝试跳出局部最优。
- 随机搜索负责产生简单候选，作为低成本对照。
- 解析式 Nesterov 负责结合 wirelength/density 梯度做连续优化。

这种设计比“每个算法都从原始布局随机开始”更符合 PCB 场景，因为 PCB 组件布局不是完全随机的点集优化，而是有明显的大芯片、引脚方向、小器件环绕和未连接器件后放置规则。

### 18.3 快速验证结果

在 `small-5` 上做一次快速验证，`annealing/random/analytical` 使用 `max_iter=800`，`two_stage` 使用 `max_iter=1500`：

| 算法 | 优化后 HPWL | 合法性 | 间距违规 | 说明 |
|---|---:|---|---:|---|
| 结构化模拟退火 | 4030.492 | True | 0 | seed 后退火搜索 |
| 结构化随机搜索 | 4030.492 | True | 0 | seed 后随机扰动 |
| 结构化解析式 Nesterov | 3830.591 | True | 0 | seed + density-grid analytical |
| 结构化两阶段混合优化 | 3782.542 | True | 0 | 1500 次快速验证 |

可以看到，结构化 seed 显著改善了 `analytical` 的结果：上一版单独 analytical 在 `small-5` 上约为 `8587.287`，接入结构化 seed 后快速验证达到约 `3830.591`。这说明结构先验对该任务非常重要。

后续如果需要进一步提高，可以把 `two_stage` 和 `analytical` 做成更紧密的交替流程：

```text
structured seed
-> analytical density-grid global smoothing
-> pin-aware slot refinement
-> legalization
-> hybrid polish
```
