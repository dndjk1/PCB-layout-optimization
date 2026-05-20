# PCB 布局优化项目当前工作流交接

本文档用于新开窗口后快速接续当前项目。当前项目目标是：读取 `.nodes`、`.nets`、`.pl` 或包含这些文件的 zip 数据集，进行 PCB 器件布局优化，并在 Web UI 中可视化查看、拖动、缩放、保存结果。

## 1. 当前运行方式

推荐从项目根目录运行：

```powershell
cd C:\Users\12925\Desktop\人工智能大作业
.\.venv\Scripts\python.exe scripts\web_ui.py --host 127.0.0.1 --port 8793
```

浏览器打开：

```text
http://127.0.0.1:8793/
```

如果旧页面缓存导致按钮或脚本不更新，可以换一个新端口，例如：

```powershell
.\.venv\Scripts\python.exe scripts\web_ui.py --host 127.0.0.1 --port 8794
```

## 2. 当前 Web UI 功能

当前 UI 已重写为基础稳定版，主要功能包括：

- 上传 zip，或同时上传 `.nodes`、`.nets`、`.pl` 三个文件。
- 解析后进入 PCB 布局工作台。
- 显示数据集名称、HPWL、合法性、间距违规数量。
- 支持左键拖动器件。
- 支持滚轮缩放。
- 支持中键或右键拖动画布。
- 支持放大、缩小、重置视图。
- 支持重置布局。
- 支持保存当前布局。
- 显示器件、引脚、net 连线和 PCB 点状背景。

当前 UI 不再提供旋转按钮，因为自动或手动旋转在当前数据集上没有稳定改善 HPWL。

## 3. 当前优化算法按钮

当前保留 4 个优化入口：

```text
结构化模拟退火优化
结构化随机搜索优化
结构化解析式 Nesterov 优化
结构化两阶段混合优化
```

对应 `src/optimizer.py` 中的算法：

```python
annealing
random
analytical
two_stage
```

其中目前最推荐继续调试和展示的是：

```text
结构化两阶段混合优化
```

## 4. 当前主要代码文件

```text
scripts/web_ui.py
```

Web UI 后端和前端模板，包含上传、解析、调用优化器、返回可视化数据、保存布局。

```text
src/optimizer.py
```

核心优化算法，包括模拟退火、随机搜索、解析式 Nesterov、两阶段优化、小器件槽位匹配、swap refinement、合法化和后处理。

```text
src/hpwl.py
```

HPWL 计算，目前使用真实 pin 坐标。

```text
src/legality.py
```

合法性检查，包括边界、间距和引用缺失。

```text
src/geometry.py
```

方向和几何辅助函数。虽然当前不再自动旋转器件，但仍保留方向感知能力，用来正确显示原始数据中的器件方向和 pin 位置。

```text
tests/test_hpwl.py
tests/test_optimizer.py
```

当前测试文件。

## 5. 当前两阶段优化主线

当前 `two_stage` 的整体流程可以理解为：

```text
读取初始布局
-> 识别大芯片和小器件
-> 生成结构化初始布局
-> 大芯片优先布局
-> 小器件按 pin/宏边缘分配
-> 小器件槽位匹配
-> 小器件 swap refinement
-> force-directed / Nesterov 类连续优化
-> legalization 修复非法重叠和间距问题
-> 未连接器件后处理，放到主簇周边空白区域
-> 将整体结果居中
-> 输出最终布局
```

当前重点是让结果更接近样例中“小器件围绕大芯片边缘成排分布”的效果。

## 6. 已完成的关键算法改进

### 6.1 结构化两阶段布局

两阶段优化先处理大芯片，再围绕大芯片引脚安排小器件，避免纯随机优化把器件拉成长链。

### 6.2 Legalization 修复

优化后会检查器件是否重叠、是否越界、是否违反最小间距，并尝试把非法器件推到空位。

### 6.3 未连接器件后处理

对于没有 net 连接的器件，先标记出来。主布局完成后，再把这些器件放到主器件簇周边空白区域，避免它们留在 PCB 边角。

### 6.4 Density-grid / Nesterov 思路

在解析式优化中加入了类似 RePlAce / DREAMPlace 的 density-grid 思路：用网格密度惩罚避免器件过度聚集，同时保留线长优化目标。

### 6.5 小器件最小费用槽位匹配

对同一芯片、同一边上的少量小器件，新增小规模最小费用匹配：

```text
目标：让小器件选择最合适的边缘槽位
约束：不能重叠，不能越界
费用：靠近目标 pin，同时避免过大扰动
```

目前小规模组使用精确搜索，大规模组仍使用安全回退，避免 UI 卡死。

### 6.6 小器件 swap refinement

槽位初步分配后，会尝试交换同组小器件的位置：

```text
如果交换后合法，并且 HPWL 更低，则接受交换。
```

这个步骤用于修正局部顺序不佳的问题。

### 6.7 旋转优化回退

曾尝试加入器件旋转优化，但结果不稳定。原因是当前 HPWL 已经按真实 pin 坐标计算，旋转会改变 pin 位置；小器件旋转后可能破坏原本较好的引脚朝向，导致 HPWL 反而升高。

因此当前已删除：

```text
rotation_refine 算法入口
UI 中的旋转优化按钮
UI 中的顺/逆时针旋转按钮
```

## 7. 当前报告文档

已经写过的报告/记录主要有：

```text
docs/week2_sample_style_optimization_plan.md
```

这是目前最重要的后续优化记录，包含：

- 样例效果差异分析。
- 两阶段优化思路。
- density-grid 增强。
- 旋转优化尝试和回退。
- 小器件槽位匹配。
- swap refinement。

其他历史记录：

```text
docs/week2_advanced_optimization_record.md
docs/week2_algorithm_issue_record.md
docs/week2_two_stage_optimization_record.md
```

## 8. 当前测试状态

最近一次测试结果：

```text
11 passed
```

运行方式：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 9. 当前已知问题

### 9.1 HPWL 数值前后口径变化

之前 HPWL 没有完整考虑器件方向和真实 pin 坐标；现在 HPWL 使用真实 pin 坐标，所以新旧结果不能直接比较。

### 9.2 小器件还不够像样例一样整齐

虽然已经加入槽位匹配和 swap refinement，但大规模小器件组还没有真正使用 Hungarian 或 min-cost-flow，因此局部规整程度仍不如老师给出的样例。

### 9.3 大芯片拓扑仍有优化空间

目前重点是把小器件围绕大芯片排列，但大芯片之间的相对位置、朝向和整体拓扑还可以继续优化。

### 9.4 Legalization 仍偏工程化

当前 legalization 能解决很多非法问题，但还不是完整工业布局器中的严格合法化算法。

## 10. 下一步建议

建议后续优先做以下几件事：

1. 为大规模小器件组实现真正的 Hungarian 或 min-cost-flow 槽位匹配。
2. 在槽位费用函数中加入“同一边成排程度”奖励，让小器件更整齐。
3. 对大芯片四周建立多行/多列槽位，而不是只靠单边最近位置。
4. 增加大芯片拓扑搜索，让 U1、U2、U3 等大器件先形成更合理的相对位置。
5. 增加更多样本自动跑批脚本，记录每个样本的 HPWL、合法性和截图。
6. 如果以后重新考虑旋转，必须只对封装对称、方向无关的小器件做受限旋转。

## 11. 新窗口接续提示词

新开窗口后可以直接说：

```text
请先阅读 docs/current_workflow_handoff.md 和 docs/week2_sample_style_optimization_plan.md，然后继续在 src/optimizer.py 中实现大规模小器件 Hungarian/min-cost-flow 槽位匹配，并更新报告。
```

也可以说：

```text
请根据 docs/current_workflow_handoff.md 接着优化 PCB 布局算法，优先让 small-5 的两阶段优化结果更接近样例中的小器件环绕大芯片成排效果。
```
