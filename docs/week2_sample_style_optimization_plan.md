# Week 2 Sample-Style Optimization Plan

本文档记录为继续接近老师样例布局效果而新增的优化思路、代码改动和当前实验结果。目标不是单纯降低 HPWL，而是让布局更接近 PCB 人工/专业布局器的风格：大芯片拓扑稳定，小器件按芯片边缘成组排列，未连接器件放在主簇周边，整体布局合法且紧凑。

## 1. 背景问题

上一版 `two_stage + hybrid polish` 已经能把 `small-5` 从初始不合法布局优化到合法布局，并将 HPWL 降到约 `3723.626`。但和老师样例的 `2882` 仍有差距，主要表现为：

1. 大芯片拓扑仍是启发式，缺少多候选比较。
2. 小器件虽然靠近对应芯片，但排列顺序还不够像样例那样贴边、整齐。
3. 对同一芯片同一侧的小器件，原来是逐个贪心找槽位，前面器件的选择会影响后面器件。

因此本轮继续做两个增强：

- 宏器件拓扑多候选生成与评分。
- 小器件槽位分配加入匹配式候选，与原贪心策略二选一。

## 2. 宏器件拓扑多候选

新增代码位于 `src/optimizer.py`：

```python
def _macro_topology_target_candidates(...):
    ...

def _best_macro_topology_targets(...):
    ...
```

候选拓扑包括：

- 两个宏器件：横排、竖排、对角排列。
- 三个宏器件：上二下一、下二上一、三角、横排、竖排。
- 四个宏器件：2x2、横排、竖排。

候选目标会转换成每个宏器件的左下角坐标：

```python
targets[name] = (
    clamp(center_x - component.width / 2),
    clamp(center_y - component.height / 2),
)
```

评分时会构造临时布局：

```python
trial = dict(placements)
for name, (x, y) in targets.items():
    trial[name] = replace(placements[name], x=x, y=y)
```

并比较：

```python
score = total_hpwl(...) + 10000 * gap_violations + compact_penalty
```

### 当前处理

实验发现，宏拓扑的“临时评分”与最终小器件放置后的质量并不完全一致。直接把 `_best_macro_topology_targets()` 作为默认策略时，`small-5` 的最终 HPWL 反而回升到约 `3953`。因此当前版本采取保护策略：

- 保留多候选拓扑生成与评分代码，作为后续实验入口。
- 默认 `two_stage` 仍使用之前更稳定的 `_compact_macro_topology_targets()`。
- 后续如果要真正启用宏拓扑枚举，应当把“小器件放置后的总 HPWL”也纳入候选评分，而不是只看宏器件目标阶段。

这一点很重要：宏拓扑不能只按宏器件之间的线长评分，因为 PCB 里大量 HPWL 来自小器件和芯片引脚之间的连接。

## 3. 小器件槽位匹配

本轮真正带来收益的是小器件槽位分配。新增代码：

```python
def _place_assigned_components_by_slot_matching(...):
    ...
```

原始流程是：对每个小器件按顺序调用 `_best_slot_near_target()`，找到第一个合法且距离目标较近的槽位。这个方法简单稳定，但容易出现“前面的器件占了后面更合适的槽位”的问题。

新流程先把已经能分配到芯片边缘的小器件按 `(macro_name, side)` 分组：

```python
groups.setdefault((macro_name, side), []).append(name)
```

然后每组同时构造两种候选：

1. **greedy candidate**：沿用原来的顺序贪心放置。
2. **matching candidate**：对同一组小器件按目标 pin 坐标排序，再在该边的候选槽位中寻找未使用的合法槽位。

匹配候选的费用为：

```python
abs(slot.x - target.x)
+ abs(slot.y - target.y)
+ 0.05 * movement_distance
```

最终不是盲目采用 matching，而是比较两种候选的综合评分：

```python
greedy_score = placement_score(...)
match_score = placement_score(...)
chosen = match if match_score <= greedy_score else greedy
```

这样做的好处是：

- 如果匹配策略更好，就采用更整齐的小器件边缘排列。
- 如果匹配策略反而让 HPWL 或合法性变差，就自动回退到原贪心策略。

## 4. 当前实验结果

在 `small-5` 上运行：

```text
algorithm: two_stage
max_iter: 3000
seed: 0
```

当前结果为：

```text
optimized HPWL: 3424.109
legal: True
gap violations: 0
```

对比前一版：

```text
two_stage + hybrid polish: 3723.626
slot matching v10:        3424.109
teacher sample:           2882.0
```

说明小器件槽位匹配确实让布局进一步接近样例效果。虽然还没有达到老师样例，但差距已经从约 `841.6` 缩小到约 `542.1`。

## 5. 后续改进方向

下一步建议继续做：

1. **真正启用宏拓扑多候选**  
   但评分必须包含小器件放置后的 HPWL，而不是只对宏器件位置打分。

2. **同一芯片同一侧的小器件 swap refinement**  
   在槽位匹配后，对同一边的小器件两两交换，如果合法且 HPWL 下降就接受。

3. **更接近最小费用匹配的全局分配**  
   当前 matching 是安全版贪心匹配。后续可以实现 Hungarian / min-cost flow，让每个小器件和每个槽位形成完整二分图。

4. **走线通道约束**  
   在大芯片之间、小器件排之间保留固定通道宽度，避免器件过度挤压。

5. **多 seed / 多候选最终选择**  
   对不同宏拓扑、不同随机种子分别运行，最终选择合法且 HPWL 最低的结果。

## 6. 结论

本轮最有效的改进是“小器件槽位匹配 + 贪心回退保护”。它没有破坏合法性，同时将 `small-5` 的 HPWL 从约 `3723.626` 进一步降到约 `3424.109`。这说明要接近样例布局，关键不只是继续增加随机优化次数，而是把 PCB 的结构规则显式编码进布局器中。

## 7. 平面旋转与旋转细化优化

后续又加入了平面内 90 度旋转能力。这里的旋转只考虑 PCB 平面内顺时针/逆时针旋转，不做镜像翻转。支持方向为：

```text
N -> E -> S -> W -> N
```

### 7.1 几何模型

新增文件：

```text
src/geometry.py
```

核心函数如下：

```python
BASE_ORIENTS = ("N", "E", "S", "W")

def rotate_orient(orient: str, direction: str) -> str:
    current = canonical_orient(orient)
    index = BASE_ORIENTS.index(current)
    if direction == "ccw":
        index -= 1
    else:
        index += 1
    return BASE_ORIENTS[index % len(BASE_ORIENTS)]
```

旋转后器件宽高需要跟随方向变化：

```python
def oriented_size(component, placement):
    orient = canonical_orient(placement.orient)
    if orient in {"E", "W"}:
        return component.height, component.width
    return component.width, component.height
```

引脚偏移也要旋转，否则图上的连线和 HPWL 计算会错误：

```python
def rotate_pin_offset(dx, dy, orient):
    if orient == "E":
        return dy, -dx
    if orient == "S":
        return -dx, -dy
    if orient == "W":
        return -dy, dx
    return dx, dy
```

为了让手动旋转更自然，旋转时保持器件中心不变：

```python
def rotated_about_center(component, placement, direction):
    old_w, old_h = oriented_size(component, placement)
    center_x = placement.x + old_w / 2
    center_y = placement.y + old_h / 2
    new_orient = rotate_orient(placement.orient, direction)
    new_w, new_h = oriented_size(component, Placement(..., new_orient, ...))
    return Placement(
        name=placement.name,
        x=center_x - new_w / 2,
        y=center_y - new_h / 2,
        orient=new_orient,
        fixed=placement.fixed,
    )
```

### 7.2 HPWL 与合法性同步旋转

原来的 HPWL 计算假设器件方向不变：

```python
x = placement.x + component.width / 2 + dx
y = placement.y + component.height / 2 + dy
```

现在改为：

```python
def pin_position(component, placement, dx, dy):
    width, height = oriented_size(component, placement)
    pin_dx, pin_dy = rotate_pin_offset(dx, dy, placement.orient)
    return placement.x + width / 2 + pin_dx, placement.y + height / 2 + pin_dy
```

合法性检查也从固定宽高改成方向相关宽高：

```python
def component_rect(component, placement):
    width, height = oriented_size(component, placement)
    return Rect(
        left=placement.x,
        bottom=placement.y,
        right=placement.x + width,
        top=placement.y + height,
    )
```

这样旋转后：

- SVG 中器件尺寸会改变；
- pin 点位置会改变；
- net 连线会连接到旋转后的 pin；
- HPWL 会重新计算；
- 间距/越界合法性也会重新计算。

### 7.3 Web UI 手动旋转

UI 中新增两个按钮：

```text
顺时针旋转
逆时针旋转
```

前端逻辑是选中器件后保持中心不变，更新 `orient`：

```javascript
function rotateSelected(direction) {
  const oldSize = componentSize(component, placement);
  const centerX = placement.x + oldSize.width / 2;
  const centerY = placement.y + oldSize.height / 2;
  placement.orient = rotateOrient(placement.orient, direction);
  const newSize = componentSize(component, placement);
  placement.x = centerX - newSize.width / 2;
  placement.y = centerY - newSize.height / 2;
  render();
}
```

保存和继续优化时，前端会把方向一起传给后端：

```javascript
payload[name] = {
  x: placement.x,
  y: placement.y,
  orient: placement.orient
};
```

后端 `placements_from_payload()` 会保留这个方向：

```python
orient=canonical_orient(str(item.get("orient", original.orient)))
```

因此用户手动旋转后的布局可以继续优化，也可以保存到 `.pl`。

### 7.4 旋转细化优化

新增算法入口：

```python
if algorithm == "rotation_refine":
    return rotation_refine_optimize(dataset, config)
```

UI 中显示为：

```text
旋转细化优化
```

它和随机旋转不同，是受控优化：

```python
def _rotation_refinement(dataset, placements, board, config, passes=1):
    for name in order:
        for direction in ("cw", "ccw"):
            rotated = rotated_about_center(component, base, direction)
            legality = check_layout_legality(...)
            if not legality.is_legal:
                continue
            score = placement_score(...)
            if score < best_score:
                accept rotation
```

关键约束：

1. 不移动器件，只改变方向。
2. 固定器件不旋转。
3. 大芯片默认不参与自动旋转，避免破坏宏拓扑和引脚朝向。
4. 旋转后必须合法。
5. 只有综合评分更低才接受。

完整算法包装为：

```python
def rotation_refine_optimize(dataset, config):
    placements = dict(dataset.placements)
    refined, accepted = _rotation_refinement(...)
    return _optimization_result(...)
```

### 7.5 实验观察

曾尝试把旋转直接加入随机移动和 force-directed 流程中，但效果不好：随机旋转会大量改变小器件方向，破坏原本较好的 pin 朝向，导致 `small-5` 的 HPWL 明显变差。

因此最终采用更安全的设计：

- 手动旋转：用户明确控制某个器件方向。
- 旋转细化：独立按钮，只接受合法且评分更好的旋转。

推荐使用流程：

```text
上传数据
-> 结构化两阶段混合优化
-> 必要时手动旋转局部器件
-> 旋转细化优化
-> 保存布局
```

测试中新增了旋转几何单元测试，验证：

- 顺时针旋转后 `N -> E`；
- 宽高正确交换；
- 器件中心保持不变；
- pin 偏移按方向正确旋转。

## 8. 小器件最小费用槽位分配与 Swap Refinement

为了继续接近样例中“小器件沿芯片边缘整齐排列”的效果，本轮在两阶段布局中增强了两个局部优化步骤：

1. 对同一芯片、同一边上的小器件做小规模槽位最小费用匹配。
2. 对已经放到槽位上的小器件做 swap refinement，尝试交换两个器件的位置，只保留合法且 HPWL 更低的交换。

对应代码位置：

```text
src/optimizer.py
_min_cost_slot_assignment()
_place_assigned_components_by_slot_matching()
_slot_swap_refinement()
_swap_component_centers()
```

### 8.1 小规模槽位最小费用匹配

原来的槽位放置主要依赖贪心选择：按目标位置排序后依次找可放置槽位。这种方法快，但早放置的器件可能占掉更适合后续器件的位置，导致局部排列不够整齐。

本轮新增 `_min_cost_slot_assignment()`，对数量较少的小器件组进行精确搜索：

```python
def _min_cost_slot_assignment(dataset, placements, ordered, slots, board, config, placed):
    candidates = []
    for component, target_x, target_y in ordered:
        # 为每个器件选出距离目标点最近的若干候选槽位
        ...

    def search(index, used_slots, current, total_cost, placed_now):
        # 分支限界搜索，寻找总费用最小的器件-槽位分配
        ...
```

费用由两部分组成：

```text
cost = 到目标 pin 附近位置的距离 + 0.05 * 相对原位置的移动距离
```

这样做的目的不是单纯把器件移动得最近，而是在“靠近连接目标”和“避免过大扰动”之间取平衡。

为了避免 Web UI 卡顿，该精确匹配只用于小规模组：

```python
if len(ordered) <= 7:
    exact = _min_cost_slot_assignment(...)
```

每个器件只保留前 6 个候选槽位。这样搜索规模被限制在可控范围内，适合当前课程数据集中的芯片边缘小器件组。

### 8.2 大规模组的安全回退

当同一边的小器件数量较多时，完整最小费用匹配会迅速变慢。因此目前采用混合策略：

```text
小组：分支限界最小费用槽位匹配
大组：原有 greedy / matching 候选方案对比
```

这种策略优先保证交互式界面不会长时间无响应，同时让最容易出错的小规模局部区域得到更精细的排列。

### 8.3 Swap Refinement

槽位分配完成后，新增 `_slot_swap_refinement()` 做二次局部细化。它会把小器件按照“所属大芯片 + 所在边”分组，然后尝试同组内两两交换中心位置：

```python
def _slot_swap_refinement(dataset, placements, board, config, macro_names, assignments, passes=1):
    for group in groups.values():
        for a, b in candidate_pairs:
            swapped = _swap_component_centers(dataset, best, a, b)
            legality = check_layout_legality(dataset.components, swapped, board, config.spacing)
            if not legality.is_legal:
                continue
            score = total_hpwl(dataset, swapped)
            if score < best_score:
                best = swapped
```

该步骤有三个约束：

1. 只交换器件中心，不改变器件尺寸和方向。
2. 交换后必须保持合法，不允许产生重叠或越界。
3. 只有 HPWL 变低才接受交换。

为了控制运行时间，每组最多检查前 24 个小器件，并限制候选交换对数量：

```python
limited = group[:24]
if checked >= 160:
    break
```

### 8.4 当前实验观察

本轮实现后，单元测试通过：

```text
11 passed
```

在 `small-5` 上使用 `two_stage`、`max_iter=3000`、`seed=0` 运行后，布局保持合法，间距违规则为 0。由于现在 HPWL 计算已经改为旋转感知版本，pin 坐标会随器件方向变化，所以新的 HPWL 数值不能直接和之前未考虑旋转 pin 的旧结果比较。

当前观察到的效果是：

- 小器件会更倾向于分布在对应芯片边缘。
- 同组小器件的局部顺序会被进一步调整。
- 未连接器件仍由后处理阶段统一放到主簇周边空白区域。
- 对大规模小器件组，目前仍需要更完整的 Hungarian 或 min-cost-flow 才能进一步逼近样例的规整程度。

后续可以继续增强：

1. 用真正的 Hungarian / min-cost-flow 替换大组回退策略。
2. 在费用函数中加入“同一边成排程度”奖励。
3. 对芯片四周小器件做行列约束，使结果更接近人工 PCB 摆放习惯。
4. 把大芯片之间的相对拓扑也纳入搜索，而不仅仅优化小器件槽位。

## 9. 旋转优化回退记录

后续测试发现，自动旋转并没有稳定改善当前数据集的优化结果。主要原因有两个：

1. `HPWL` 已经改为使用真实 pin 坐标，器件方向变化会直接改变 pin 位置，因此新旧 HPWL 数值口径不同，不能和早期未考虑旋转 pin 的结果直接比较。
2. 当前优化器还没有完整的封装、方向约束和布线方向模型。对小电阻、电容随机或局部旋转时，虽然器件中心没有移动，但 pin 朝向可能被破坏，导致局部连线反而变长。

因此本轮决定从主流程中移除自动旋转优化：

```text
删除 rotation_refine 算法入口
删除 Web UI 中的旋转细化优化按钮
删除 Web UI 中的手动顺/逆时针旋转按钮
```

底层仍保留 `.pl` 文件方向读取和方向感知显示能力，原因是原始数据可能已经包含器件方向。如果完全忽略方向，pin 显示和连线定位会再次出现偏差。

当前推荐主线恢复为：

```text
结构化两阶段布局
-> 小器件槽位匹配
-> swap refinement
-> legalization / postprocess
```

后续如果重新引入旋转，应该满足以下条件：

1. 只允许封装对称的器件旋转。
2. 大芯片方向由约束指定，不由优化器自由旋转。
3. 旋转代价必须同时考虑 pin 朝向、器件可焊性和局部成排规则。
4. 旋转应作为最后的合法局部搜索，而不是插入主优化循环随机尝试。
