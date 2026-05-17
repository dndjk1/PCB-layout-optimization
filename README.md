# PCB Layout Optimization Project

This project is organized for the PCB layout optimization assignment.

## Directory Layout

- `data/`: Stores the assignment datasets, grouped by dataset folder such as `small-1_original_artifacts/`.
- `docs/`: Assignment notes, original spec, and reference images.
- `src/`: Core Python modules for parsing data, computing HPWL, and checking layout legality.
- `scripts/`: Runnable scripts for weekly checks and experiments.
- `results/`: Generated metrics, optimized layouts, and figures.
- `reports/`: Report drafts and final report materials.
- `tests/`: Small tests for key functions.

## Week 1 Goal

Week 1 should build the basic pipeline:

1. Read `.nodes`, `.nets`, and `.pl` files.
2. Count components, nets, and pins.
3. Compute the initial HPWL for each dataset.
4. Check boundary and minimum-gap legality.
5. Prepare the first report sections.

After you receive the dataset folders, place them in `data/` and run:

```powershell
python scripts/week1_inspect.py
```

## Development Environment

This project uses a local virtual environment:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\week1_inspect.py
```

## Week 1 Scripts

Run all Week 1 checks and visualizations:

```powershell
.venv\Scripts\python.exe scripts\week1_all.py
```

Run only data inspection and HPWL/legal checks:

```powershell
.venv\Scripts\python.exe scripts\week1_inspect.py
```

Run only initial layout visualization:

```powershell
.venv\Scripts\python.exe scripts\visualize_initial_layouts.py
```

Start the local interactive upload UI:

```powershell
.venv\Scripts\python.exe scripts\web_ui.py
```

Generated Week 1 outputs:

- `results/week1/initial_metrics.csv`
- `results/week1/top_hpwl_nets.csv`
- `results/week1/legality_violations.csv`
- `results/initial_layouts/*.png`
- `results/initial_layouts/manifest.csv`
- `results/ui_uploads/`
- `results/ui_images/`

Reference materials now live under `docs/`, and the legacy teacher visualizer is kept as `scripts/legacy_visualize_pcb.py`.
