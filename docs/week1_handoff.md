# Week 1 Handoff

This file summarizes the completed Week 1 work and gives a clean starting workflow for Week 2.

## 1. Project Goal

The project solves a PCB component placement optimization task.

- Input: Bookshelf-style `.nodes`, `.nets`, `.pl`
- Objective: minimize total HPWL
- Constraints:
  - keep components inside the board region
  - keep at least 2 units of spacing in x or y between any two components
- Output:
  - optimized `.pl`
  - metrics tables
  - visualization images
  - report material

## 2. Current Directory Structure

```text
data/
  small-1_original_artifacts/
  small-2_original_artifacts/
  small-3_original_artifacts/
  small-4_original_artifacts/
  small-5_original_artifacts/
  small-5_optimized/
docs/
  README.md
  大作业说明.md
  reference_results/
reports/
  week1/
results/
  initial_layouts/
  ui_images/
  ui_uploads/
  week1/
scripts/
src/
tests/
```

## 3. Completed Week 1 Modules

### Data reading

Implemented in [src/pcb_data.py](/C:/Users/12925/Desktop/人工智能大作业/src/pcb_data.py).

- Parses `.nodes`, `.nets`, `.pl`
- Builds structured records for:
  - `Component`
  - `Pin`
  - `Net`
  - `Placement`
  - `Dataset`
- Recursively discovers complete datasets inside `data/`
- Supports canonical dataset names such as `small-1`

### HPWL computation

Implemented in [src/hpwl.py](/C:/Users/12925/Desktop/人工智能大作业/src/hpwl.py).

- Computes absolute pin coordinates
- Computes single-net HPWL
- Computes total HPWL
- Produces per-net HPWL detail
- Produces top HPWL nets for analysis

### Legality checking

Implemented in [src/legality.py](/C:/Users/12925/Desktop/人工智能大作业/src/legality.py).

- Infers board bounds from the current placement
- Checks boundary violations
- Checks minimum-gap violations
- Checks missing component references across files
- Produces a structured `LegalityResult`

### Visualization

Implemented in [src/visualization.py](/C:/Users/12925/Desktop/人工智能大作业/src/visualization.py).

- Draws components, pins, and net connections
- Saves PNG layout images
- Used by both scripts and the local Web UI

### Local Web UI

Implemented in [scripts/web_ui.py](/C:/Users/12925/Desktop/人工智能大作业/scripts/web_ui.py).

- Upload a dataset zip or `.nodes/.nets/.pl`
- Automatically parse data
- Compute HPWL and legality
- Render layout image directly in the browser

## 4. Main Scripts

[scripts/week1_inspect.py](/C:/Users/12925/Desktop/人工智能大作业/scripts/week1_inspect.py)

- Scans datasets in `data/`
- Computes initial HPWL
- Checks legality
- Writes CSV outputs under `results/week1/`

[scripts/visualize_initial_layouts.py](/C:/Users/12925/Desktop/人工智能大作业/scripts/visualize_initial_layouts.py)

- Generates initial placement images under `results/initial_layouts/`

[scripts/week1_all.py](/C:/Users/12925/Desktop/人工智能大作业/scripts/week1_all.py)

- Runs inspection and visualization in sequence

[scripts/make_sample_zip.py](/C:/Users/12925/Desktop/人工智能大作业/scripts/make_sample_zip.py)

- Creates a sample zip for the Web UI

[scripts/legacy_visualize_pcb.py](/C:/Users/12925/Desktop/人工智能大作业/scripts/legacy_visualize_pcb.py)

- Teacher-provided legacy visualizer
- Kept for reference only

## 5. Verification Workflow

Use the project virtual environment:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\week1_all.py
```

Useful single commands:

```powershell
.venv\Scripts\python.exe scripts\week1_inspect.py
.venv\Scripts\python.exe scripts\visualize_initial_layouts.py
.venv\Scripts\python.exe scripts\web_ui.py
```

## 6. Week 1 Outputs

Generated outputs already exist in:

- [results/week1/initial_metrics.csv](/C:/Users/12925/Desktop/人工智能大作业/results/week1/initial_metrics.csv)
- [results/week1/top_hpwl_nets.csv](/C:/Users/12925/Desktop/人工智能大作业/results/week1/top_hpwl_nets.csv)
- [results/week1/legality_violations.csv](/C:/Users/12925/Desktop/人工智能大作业/results/week1/legality_violations.csv)
- [results/initial_layouts](/C:/Users/12925/Desktop/人工智能大作业/results/initial_layouts)

Initial metrics:

```text
small-1: HPWL 2625.0, legal=False, gap violations=5
small-2: HPWL 2712.5, legal=True,  gap violations=0
small-3: HPWL 1781.0, legal=False, gap violations=12
small-4: HPWL 1039.5, legal=False, gap violations=18
small-5: HPWL 9075.0, legal=False, gap violations=35
```

Interpretation:

- The parser is working.
- HPWL computation is working.
- Legality checks are working.
- Several original layouts are not fully legal under the current 2-unit gap rule.

## 7. Important Notes

- The old `要求与数据` folder was removed.
- All active scripts now read from `data/`.
- Reference documents and images were moved into `docs/`.
- The repo has a local `.venv` for Python 3.12 and testing.
- The local Git repo already has a follow-up commit after the initial commit.

## 8. Recommended Week 2 Workflow

Week 2 should focus on producing actual optimized placements.

Suggested implementation order:

1. Add an optimizer module under `src/`, such as `src/optimizer.py`
2. Start with a simple baseline:
   - randomly pick a component
   - try a small translation
   - keep the move only if legality is preserved and HPWL improves
3. Add output writing for optimized `.pl`
4. Add a script such as `scripts/run_optimizer.py`
5. Record for each dataset:
   - initial HPWL
   - optimized HPWL
   - improvement ratio
   - runtime
6. Reuse `src/visualization.py` to generate before/after images
7. If time allows, upgrade the baseline into simulated annealing

## 9. Expected Week 2 Deliverables

By the end of Week 2, the next conversation should aim to produce:

- one runnable optimization script
- optimized `.pl` files for the scored datasets
- a result table comparing initial and optimized HPWL
- before/after layout images
- a report draft section describing the algorithm

