# 两阶段布局算法优化与 UI 测试入口说明

本文档记录本轮针对 `two_stage` 算法和 Web UI 测试入口的改动，方便后续继续调参、复现实验结果和撰写报告。

## 1. 本轮算法改动

### 1.1 大规模小器件槽位匹配

位置：`src/optimizer.py`

- `_min_cost_slot_assignment` 不再对 7 个以上的小器件组直接返回 `None`。
- 小规模组继续使用精确搜索。
- 大规模组新增 `_min_cost_flow_slot_assignment`，用最小费用流在“小器件 -> 候选槽位”之间做全局分配。
- 流匹配后会再做合法性校验；如果局部间距仍冲突，会进入局部合法化修复。

目标：

- 让大组小器件也能按目标 pin 和边缘槽位整体匹配。
- 减少以前大组只靠贪心回退导致的局部拥挤、顺序不稳定问题。

### 1.2 多行/多列边缘槽位

位置：`src/optimizer.py`

- `_pin_side_slot_candidates` 增加 `target_pin` 参数。
- 槽位生成从宏器件边缘平均铺开，改为围绕目标 pin 的投影位置生成多排/多列槽位。
- 每条边生成更多 ring，拥挤时允许使用相邻边的一部分候选槽位。

目标：

- 更接近“小器件围绕大芯片边缘成排”的样例风格。
- 给 min-cost-flow 提供足够多的合法候选位置。

### 1.3 大芯片拓扑搜索

位置：`src/optimizer.py`

- `_compact_macro_targets` 现在会实际调用 `_best_macro_topology_targets`。
- `_macro_topology_target_candidates` 对 2 到 4 个大芯片枚举排列顺序。
- 对候选槽位做镜像和坐标互换，扩展横排、竖排、三角、四角等拓扑。
- 评分综合考虑 HPWL、间距违规和宏器件簇紧凑度。

目标：

- 让 U1、U2、U3 等大器件先形成更合理的全局相对位置。
- 降低后续小器件布局的线长上限。

### 1.4 更严格 legalization

位置：`src/optimizer.py`

- `_grid_legalize` 增加冲突驱动的二次修复。
- 新增 `_legalization_repair_order`，优先移动小器件和中等器件，尽量把大芯片作为稳定锚点。
- 修复时按当前冲突数、器件类型、连接度排序，减少“修合法但破坏整体结构”的情况。

目标：

- 对布局结果做更稳定的合法化。
- 降低优化后仍出现重叠或间距违规的概率。

### 1.5 解析式优化参数自适应

位置：`src/optimizer.py`

- `analytical_nesterov_optimize` 在每轮迭代前统计当前 HPWL 改善率和合法性违规数量。
- 新增 `_adaptive_analytical_parameters`，动态调整 `gamma`、`learning_rate`、`momentum` 和 `density_weight`。
- 当间距或边界违规较多时，自动提高 density 权重、降低步长和 momentum，让布局先摊开。
- 当连续多轮 HPWL 改善很小，自动降低步长并略微提高 density 权重，减少震荡。
- 当 HPWL 明显改善且合法性较好时，允许步长和 momentum 稍微回升，加快收敛。

目标：

- 避免固定步长在拥挤布局中持续震荡。
- 让解析式优化能根据“线长改善”和“合法性压力”自动切换优化重心。

核心代码摘录：

```python
legality = check_layout_legality(dataset.components, current, dataset.nets, board=board, min_gap=config.min_gap)
current_hpwl = total_hpwl(dataset.nets, dataset.components, current)
improvement = (previous_hpwl - current_hpwl) / max(previous_hpwl, 1.0)

gamma, learning_rate, momentum, density_weight = _adaptive_analytical_parameters(
    span=span,
    base_gamma=max(1.0, span / 40.0),
    learning_rate=learning_rate,
    momentum=momentum,
    legality=legality,
    improvement=improvement,
    stagnant_iterations=stagnant_iterations,
    config=config,
)

gradients = _smooth_wirelength_gradients(dataset, current, gamma)
_add_density_and_boundary_gradients(dataset, current, board, config, gradients, density_weight=density_weight)
```

解释：

这段逻辑把解析式优化从“固定参数迭代”改成“状态反馈迭代”。如果当前布局不合法，算法会更强调 density 和边界修复；如果 HPWL 改善明显，则继续保留较大的移动能力；如果连续停滞，则自动收缩步长，避免来回跳动。

### 1.6 swap refinement 扩展

位置：`src/optimizer.py`

- `_slot_swap_refinement` 保留原有同组两两交换。
- 新增同边排序候选 `_same_side_sorted_candidate`，按目标 pin 在边缘方向上的顺序重排小器件。
- 新增三元轮换 `_three_cycle_candidate`，在同一边的小器件组内尝试 3 个器件的顺/逆向中心点轮换。
- 新增邻近组交换 `_neighbor_group_pairs`，在同一宏器件的相邻边之间尝试局部交换。
- 所有候选都通过 `_accept_refinement_candidate` 统一验收：必须合法，并且必须降低 HPWL。

目标：

- 修正小器件局部顺序错误。
- 降低连线交叉和边缘附近的 HPWL 偏高。
- 在不破坏合法性的前提下做更强的局部后处理。

核心代码摘录：

```python
sorted_candidate = _same_side_sorted_candidate(dataset, result, ordered, assignments)
result, current_hpwl, accepted_sort = _accept_refinement_candidate(
    dataset, result, sorted_candidate, current_hpwl, board, config
)

for triple in itertools.combinations(ordered[:18], 3):
    for direction in (1, -1):
        candidate = _three_cycle_candidate(dataset, result, triple, direction)
        result, current_hpwl, accepted_cycle = _accept_refinement_candidate(
            dataset, result, candidate, current_hpwl, board, config
        )

for group_a, group_b in _neighbor_group_pairs(groups):
    candidate[name_a], candidate[name_b] = _swap_component_centers(
        dataset, name_a, result[name_a], name_b, result[name_b]
    )
```

解释：

原来的 swap refinement 只尝试同组两个器件交换，能修小错，但遇到三个器件顺序整体错位、或者器件被分到相邻边角附近时，改善空间有限。本轮扩展后，算法先尝试整条边按 pin 顺序重排，再尝试三元轮换，最后尝试同一宏器件相邻边之间的局部交换。这样更符合 PCB 中“小器件沿芯片边缘按引脚顺序排布”的直觉。

## 2. 核心流程概述

两阶段优化主线：

```text
读取布局
-> 大芯片拓扑搜索
-> 小器件按宏引脚分配 side 和目标 pin
-> 多排/多列槽位生成
-> 小规模精确匹配或大规模 min-cost-flow 匹配
-> slot swap refinement 局部顺序修正
-> force-directed 连续微调
-> 冲突驱动 legalization
-> 孤立器件归队和布局居中
```

解析式优化主线：

```text
结构化 seed
-> smooth wirelength 梯度
-> density-grid / boundary 梯度
-> 根据违规数量和 HPWL 改善率调整参数
-> Nesterov momentum 更新
-> legalization 和 slot refinement
```

## 3. 新增测试覆盖

位置：`tests/test_optimizer.py`

- `test_pin_side_candidates_generate_multiple_rows_around_target_pin`
  - 验证目标 pin 附近会生成多排边缘槽位。
- `test_min_cost_slot_assignment_handles_large_groups`
  - 构造 9 个小器件的大组场景，验证大规模槽位匹配可返回合法结果。
- `test_adaptive_analytical_parameters_react_to_violations`
  - 验证解析式优化在存在间距违规时会提高 density 权重，并降低步长和 momentum。
- `test_same_side_sorted_candidate_reorders_by_target_pin_axis`
  - 验证同边排序候选会按目标 pin 顺序重排小器件中心点。

当前测试结果：

```text
15 passed
```

## 4. Web UI 测试入口

位置：`scripts/web_ui.py`

上传页新增“内置样本快速测试”入口，可以直接打开项目 `data/` 目录下的样本：

```text
small-1
small-2
small-3
small-4
small-5
```

运行方式：

```powershell
.\.venv\Scripts\python.exe scripts\web_ui.py --host 127.0.0.1 --port 8793
```

浏览器访问：

```text
http://127.0.0.1:8793/
```

也可以直接打开某个内置样本：

```text
http://127.0.0.1:8793/dataset?name=small-5
```

进入工作台后，建议优先点击：

```text
结构化两阶段混合优化
```

## 5. 快速验证记录

本轮对 `small-5` 做过一次两阶段优化冒烟测试：

```text
components=115 nets=199
initial=13908.500
optimized=3556.415
legal=True
gap=0 boundary=0 accepted=297
```

这个数值只代表当前 `max_iter=300`、`seed=0` 下的快速验证，不是最终报告用的完整跑批结果。

## 6. 后续可继续优化的方向

1. 给大芯片拓扑搜索加入局部旋转候选，但只对方向安全的封装启用。
2. 在 UI 中增加“一键跑 small-1 到 small-5 并导出 CSV”的批量测试按钮。
3. 为两阶段优化输出更多中间布局快照，方便报告展示算法过程。
