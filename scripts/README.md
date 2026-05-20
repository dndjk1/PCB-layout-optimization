# Scripts

## `week1_inspect.py`

Reads all discovered datasets, computes initial HPWL, checks layout legality, and writes CSV results under `results/week1/`.

## `visualize_initial_layouts.py`

Generates initial placement PNG images under `results/initial_layouts/`.

Useful options:

```powershell
.venv\Scripts\python.exe scripts\visualize_initial_layouts.py --dataset small-1
.venv\Scripts\python.exe scripts\visualize_initial_layouts.py --no-nets
.venv\Scripts\python.exe scripts\visualize_initial_layouts.py --max-net-degree 10
```

## `week1_all.py`

Runs `week1_inspect.py` and `visualize_initial_layouts.py` in sequence.

## `run_optimizer.py`

Runs the Week 2 optimizers on `small-1` through `small-4`, writes optimized `.pl` files, metrics, and optimized layout PNGs under `results/week2/`.

Available algorithms:

- `greedy`: greedy local search
- `annealing`: simulated annealing
- `random`: random-search baseline
- `two_stage`: legality repair followed by net-centroid HPWL optimization

```powershell
.venv\Scripts\python.exe scripts\run_optimizer.py --max-iter 10000
.venv\Scripts\python.exe scripts\run_optimizer.py --algorithm two_stage --max-iter 5000 --history-interval 100
.venv\Scripts\python.exe scripts\run_optimizer.py --algorithm annealing --max-iter 20000
.venv\Scripts\python.exe scripts\run_optimizer.py --dataset small-2 --max-iter 20000
.venv\Scripts\python.exe scripts\run_optimizer.py --no-images
```

The script also writes convergence history CSV files and convergence curve PNGs under `results/week2/convergence/` and `results/week2/curves/`.

## `web_ui.py`

Starts a local browser interface for uploading a dataset zip or the three Bookshelf files.
After upload, the page can run greedy local search, simulated annealing, or random search. The layout view is interactive: use the mouse wheel or toolbar to zoom, drag the empty canvas to pan, and drag components directly to make manual placement edits before continuing optimization. The current layout is shown as the primary large board, the original layout can be toggled on demand, and the current board can be opened in a full-screen viewer with HPWL and violation counts.

```powershell
.venv\Scripts\python.exe scripts\web_ui.py
```

Then open:

```text
http://127.0.0.1:8765
```

## `make_sample_zip.py`

Creates a sample upload zip from `small-1_original_artifacts`:

```powershell
.venv\Scripts\python.exe scripts\make_sample_zip.py
```
