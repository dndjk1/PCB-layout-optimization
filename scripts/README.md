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

## `web_ui.py`

Starts a local browser interface for uploading a dataset zip or the three Bookshelf files.

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

