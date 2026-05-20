from __future__ import annotations

import argparse
import csv
from dataclasses import replace
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.artifacts import format_number, safe_extract_zip, write_history, write_pl
from src.optimizer import available_algorithms, optimize
from src.pcb_data import Dataset, find_dataset_files, load_dataset, load_dataset_from_files
from src.visualization import visualize_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a PCB optimizer without Web UI and export static visualization artifacts."
    )
    parser.add_argument("--input", help="Zip file or folder containing .nodes/.nets/.pl files.")
    parser.add_argument("--dataset", default="small-1", help="Dataset name when --input is not provided.")
    parser.add_argument("--algorithm", default="two_stage", choices=available_algorithms())
    parser.add_argument("--max-iter", type=int, default=3000)
    parser.add_argument("--history-interval", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--min-gap", type=float, default=2.0)
    parser.add_argument("--output-dir", default="results/visual_runs")
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--max-net-degree", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pcb_visual_run_") as tmp:
        dataset = load_input_dataset(args, Path(tmp))
        run_dir = output_dir / f"{dataset.name}_{args.algorithm}_seed{args.seed}_iter{args.max_iter}"
        run_dir.mkdir(parents=True, exist_ok=True)

        initial_png = run_dir / "01_initial_layout.png"
        visualize_dataset(
            dataset,
            initial_png,
            dpi=args.dpi,
            max_net_degree=args.max_net_degree,
            title=f"{dataset.name} initial layout",
        )

        result = optimize(
            dataset,
            algorithm=args.algorithm,
            max_iter=args.max_iter,
            seed=args.seed,
            min_gap=args.min_gap,
            history_interval=args.history_interval,
        )
        optimized = replace(dataset, placements=result.placements)

        optimized_png = run_dir / "02_optimized_layout.png"
        visualize_dataset(
            optimized,
            optimized_png,
            dpi=args.dpi,
            max_net_degree=args.max_net_degree,
            title=f"{dataset.name} {args.algorithm} optimized layout",
        )

        pl_path = write_pl(run_dir / "optimized.pl", result.placements)
        metrics_csv = write_metrics(run_dir / "metrics.csv", dataset, args.algorithm, args.seed, result, pl_path)
        history_csv = write_history(run_dir / "history.csv", dataset.name, args.algorithm, result.history)
        curve_png = plot_history(run_dir / "03_convergence_curve.png", dataset.name, args.algorithm, result.history)
        comparison_png = plot_comparison(
            run_dir / "04_before_after_comparison.png",
            initial_png,
            optimized_png,
            curve_png,
            dataset.name,
            args.algorithm,
            result,
        )

        print("Non-UI visualization run complete.")
        print(f"Output directory: {run_dir}")
        print(f"Initial layout:   {initial_png}")
        print(f"Optimized layout: {optimized_png}")
        print(f"Comparison:       {comparison_png}")
        print(f"Convergence:      {curve_png}")
        print(f"Metrics CSV:      {metrics_csv}")
        print(f"History CSV:      {history_csv}")
        print(f"Optimized PL:     {pl_path}")
    return 0


def load_input_dataset(args: argparse.Namespace, tmp_dir: Path) -> Dataset:
    if not args.input:
        return load_dataset(ROOT / "data", args.dataset)

    source = Path(args.input)
    if not source.is_absolute():
        source = ROOT / source
    if not source.exists():
        raise FileNotFoundError(f"Input does not exist: {source}")

    if source.suffix.lower() == ".zip":
        extract_dir = tmp_dir / source.stem
        extract_dir.mkdir(parents=True, exist_ok=True)
        safe_extract_zip(source, extract_dir)
        matches = find_dataset_files(extract_dir, args.dataset if args.dataset else None)
    elif source.is_dir():
        matches = find_dataset_files(source, args.dataset if args.dataset else None)
    else:
        raise ValueError("--input must be a zip file or a directory.")

    if not matches:
        raise ValueError("No complete .nodes/.nets/.pl dataset found in input.")
    return load_dataset_from_files(matches[0])


def write_metrics(path: Path, dataset: Dataset, algorithm: str, seed: int, result, pl_path: Path) -> Path:
    row = {
        "dataset": dataset.name,
        "algorithm": algorithm,
        "seed": seed,
        "components": len(dataset.components),
        "nets": len(dataset.nets),
        "pins": dataset.pin_count,
        "initial_hpwl": result.initial_hpwl,
        "optimized_hpwl": result.optimized_hpwl,
        "improvement_percent": result.improvement_ratio * 100.0,
        "initial_legal": result.initial_legality.is_legal,
        "optimized_legal": result.optimized_legality.is_legal,
        "initial_gap_violations": len(result.initial_legality.gap_violations),
        "optimized_gap_violations": len(result.optimized_legality.gap_violations),
        "iterations": result.iterations,
        "accepted_moves": result.accepted_moves,
        "runtime_seconds": result.runtime_seconds,
        "optimized_pl": str(pl_path.relative_to(ROOT)),
    }
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(row), lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
    return path


def plot_history(path: Path, dataset_name: str, algorithm: str, history) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax_hpwl = plt.subplots(figsize=(8, 4.5), dpi=160)
    if history:
        iterations = [item.iteration for item in history]
        hpwl_values = [item.hpwl for item in history]
        gap_values = [item.gap_violations for item in history]
        ax_hpwl.plot(iterations, hpwl_values, color="#235f9c", linewidth=2, label="HPWL")
        ax_gap = ax_hpwl.twinx()
        ax_gap.step(iterations, gap_values, where="post", color="#bf4f2f", linewidth=1.5, label="Gap violations")
        ax_gap.set_ylabel("Gap violations", color="#bf4f2f")
        ax_gap.tick_params(axis="y", labelcolor="#bf4f2f")
    ax_hpwl.set_title(f"{dataset_name} {algorithm} convergence")
    ax_hpwl.set_xlabel("Iteration")
    ax_hpwl.set_ylabel("HPWL", color="#235f9c")
    ax_hpwl.tick_params(axis="y", labelcolor="#235f9c")
    ax_hpwl.grid(True, alpha=0.25, linewidth=0.5)
    fig.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def plot_comparison(
    path: Path,
    initial_png: Path,
    optimized_png: Path,
    curve_png: Path,
    dataset_name: str,
    algorithm: str,
    result,
) -> Path:
    fig, axes = plt.subplots(2, 2, figsize=(16, 11), dpi=150)
    axes = axes.flatten()
    for ax, image_path, title in [
        (axes[0], initial_png, "Initial layout"),
        (axes[1], optimized_png, "Optimized layout"),
        (axes[2], curve_png, "Convergence curve"),
    ]:
        ax.imshow(mpimg.imread(image_path))
        ax.set_title(title)
        ax.axis("off")

    axes[3].axis("off")
    summary = [
        f"Dataset: {dataset_name}",
        f"Algorithm: {algorithm}",
        f"Initial HPWL: {format_number(result.initial_hpwl)}",
        f"Optimized HPWL: {format_number(result.optimized_hpwl)}",
        f"Improvement: {format_number(result.improvement_ratio * 100.0)}%",
        f"Initial legal: {result.initial_legality.is_legal}",
        f"Optimized legal: {result.optimized_legality.is_legal}",
        f"Initial gap violations: {len(result.initial_legality.gap_violations)}",
        f"Optimized gap violations: {len(result.optimized_legality.gap_violations)}",
        f"Iterations: {result.iterations}",
        f"Runtime: {format_number(result.runtime_seconds)} s",
    ]
    axes[3].text(0.02, 0.98, "\n".join(summary), va="top", ha="left", fontsize=13)
    fig.suptitle("PCB Placement Non-UI Visualization Run", fontsize=16)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


if __name__ == "__main__":
    raise SystemExit(main())
