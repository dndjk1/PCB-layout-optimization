from __future__ import annotations

import argparse
import csv
from dataclasses import replace
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.artifacts import write_history, write_pl
from src.optimizer import available_algorithms, optimize
from src.pcb_data import Dataset, Placement, load_all_datasets
from src.visualization import visualize_dataset


DEFAULT_DATASETS = ["small-1", "small-2", "small-3", "small-4"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PCB placement optimization on assignment datasets.")
    parser.add_argument("--dataset", action="append", help="Dataset name to optimize, for example small-1.")
    parser.add_argument("--algorithm", action="append", default=None, help="Optimizer algorithm. Defaults to greedy.")
    parser.add_argument("--max-iter", type=int, default=10_000, help="Maximum iterations per dataset and algorithm.")
    parser.add_argument("--history-interval", type=int, default=100, help="Record convergence every N iterations.")
    parser.add_argument("--seed", type=int, default=0, help="Base random seed.")
    parser.add_argument("--seed-count", type=int, default=1, help="Run N seeds and keep the best result per dataset/algorithm.")
    parser.add_argument("--seed-step", type=int, default=1000, help="Seed increment between repeated runs.")
    parser.add_argument("--min-gap", type=float, default=2.0, help="Required component spacing.")
    parser.add_argument("--output-dir", default="results/week2", help="Directory for CSV, PL, and image outputs.")
    parser.add_argument("--dpi", type=int, default=180, help="Output image DPI.")
    parser.add_argument("--max-net-degree", type=int, default=20, help="Hide nets above this degree in visualizations.")
    parser.add_argument("--no-images", action="store_true", help="Skip optimized layout PNG generation.")
    parser.add_argument("--no-curves", action="store_true", help="Skip convergence curve PNG generation.")
    return parser.parse_args()


def optimized_dataset(dataset: Dataset, placements: dict[str, Placement]) -> Dataset:
    return replace(dataset, placements=placements)


def result_row(
    dataset: Dataset,
    algorithm: str,
    seed: int,
    result,
    pl_path: Path,
    image_path: Path | None,
    history_path: Path | None,
    curve_path: Path | None,
) -> dict[str, object]:
    return {
        "dataset": dataset.name,
        "algorithm": algorithm,
        "seed": seed,
        "components": len(dataset.components),
        "nets": len(dataset.nets),
        "pins": dataset.pin_count,
        "initial_hpwl": result.initial_hpwl,
        "optimized_hpwl": result.optimized_hpwl,
        "initial_score": result.initial_score,
        "optimized_score": result.optimized_score,
        "improvement": result.improvement,
        "improvement_percent": result.improvement_ratio * 100.0,
        "initial_legal": result.initial_legality.is_legal,
        "optimized_legal": result.optimized_legality.is_legal,
        "initial_gap_violations": len(result.initial_legality.gap_violations),
        "optimized_gap_violations": len(result.optimized_legality.gap_violations),
        "iterations": result.iterations,
        "accepted_moves": result.accepted_moves,
        "runtime_seconds": result.runtime_seconds,
        "optimized_pl": str(pl_path.relative_to(ROOT)),
        "optimized_image": str(image_path.relative_to(ROOT)) if image_path else "",
        "convergence_csv": str(history_path.relative_to(ROOT)) if history_path else "",
        "convergence_curve": str(curve_path.relative_to(ROOT)) if curve_path else "",
    }


def plot_history(path: Path, dataset_name: str, algorithm: str, history) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not history:
        return path

    iterations = [item.iteration for item in history]
    hpwl_values = [item.hpwl for item in history]
    gap_values = [item.gap_violations for item in history]

    fig, ax_hpwl = plt.subplots(figsize=(8, 4.5), dpi=160)
    ax_hpwl.plot(iterations, hpwl_values, color="#235f9c", linewidth=2, label="HPWL")
    ax_hpwl.set_xlabel("Iteration")
    ax_hpwl.set_ylabel("HPWL", color="#235f9c")
    ax_hpwl.tick_params(axis="y", labelcolor="#235f9c")
    ax_hpwl.grid(True, alpha=0.2, linewidth=0.5)

    ax_gap = ax_hpwl.twinx()
    ax_gap.step(iterations, gap_values, where="post", color="#c43d3d", linewidth=1.6, label="Gap violations")
    ax_gap.set_ylabel("Gap violations", color="#c43d3d")
    ax_gap.tick_params(axis="y", labelcolor="#c43d3d")

    fig.suptitle(f"{dataset_name} {algorithm} convergence")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    wanted = args.dataset or DEFAULT_DATASETS
    algorithms = args.algorithm or available_algorithms()
    output_dir = ROOT / args.output_dir
    pl_dir = output_dir / "optimized_pl"
    image_dir = output_dir / "layouts"
    history_dir = output_dir / "convergence"
    curve_dir = output_dir / "curves"

    datasets = [dataset for dataset in load_all_datasets([ROOT / "data"]) if dataset.name in set(wanted)]
    datasets.sort(key=lambda item: wanted.index(item.name) if item.name in wanted else len(wanted))
    if not datasets:
        print("No matching datasets found.")
        return 1

    rows: list[dict[str, object]] = []
    all_seed_rows: list[dict[str, object]] = []
    for dataset_index, dataset in enumerate(datasets):
        for algorithm_index, algorithm in enumerate(algorithms):
            seed_results = []
            for seed_index in range(max(1, args.seed_count)):
                run_seed = args.seed + dataset_index * 100 + algorithm_index + seed_index * args.seed_step
                print(f"Optimizing {dataset.name} with {algorithm} (seed={run_seed}, max_iter={args.max_iter})...", flush=True)
                result = optimize(
                    dataset,
                    algorithm=algorithm,
                    max_iter=args.max_iter,
                    seed=run_seed,
                    min_gap=args.min_gap,
                    history_interval=args.history_interval,
                )
                seed_results.append((run_seed, result))

                seed_suffix = f"seed{run_seed}"
                pl_path = pl_dir / algorithm / seed_suffix / f"{dataset.name}_{algorithm}_{seed_suffix}.pl"
                write_pl(pl_path, result.placements)
                image_path = None
                if not args.no_images:
                    image_path = image_dir / algorithm / seed_suffix / f"{dataset.name}_{algorithm}_{seed_suffix}_optimized_layout.png"
                    visualize_dataset(
                        optimized_dataset(dataset, result.placements),
                        output_path=image_path,
                        max_net_degree=args.max_net_degree,
                        dpi=args.dpi,
                        title=f"{dataset.name} {algorithm} optimized layout seed {run_seed}",
                    )
                history_path = history_dir / algorithm / seed_suffix / f"{dataset.name}_{algorithm}_{seed_suffix}_history.csv"
                write_history(history_path, dataset.name, algorithm, result.history)
                curve_path = None
                if not args.no_curves:
                    curve_path = curve_dir / algorithm / seed_suffix / f"{dataset.name}_{algorithm}_{seed_suffix}_convergence.png"
                    plot_history(curve_path, dataset.name, algorithm, result.history)
                all_seed_rows.append(result_row(dataset, algorithm, run_seed, result, pl_path, image_path, history_path, curve_path))
                print(
                    f"  HPWL {result.initial_hpwl:.3f} -> {result.optimized_hpwl:.3f} "
                    f"({result.improvement_ratio * 100.0:.2f}%), legal={result.optimized_legality.is_legal}",
                    flush=True,
                )

            best_seed, best_result = min(seed_results, key=lambda item: _result_rank(item[1]))
            best_suffix = f"best_seed{best_seed}"
            best_pl_path = pl_dir / algorithm / f"{dataset.name}_{algorithm}_best.pl"
            write_pl(best_pl_path, best_result.placements)
            best_image_path = None
            if not args.no_images:
                best_image_path = image_dir / algorithm / f"{dataset.name}_{algorithm}_best_optimized_layout.png"
                visualize_dataset(
                    optimized_dataset(dataset, best_result.placements),
                    output_path=best_image_path,
                    max_net_degree=args.max_net_degree,
                    dpi=args.dpi,
                    title=f"{dataset.name} {algorithm} best optimized layout",
                )
            best_history_path = history_dir / algorithm / f"{dataset.name}_{algorithm}_best_history.csv"
            write_history(best_history_path, dataset.name, algorithm, best_result.history)
            best_curve_path = None
            if not args.no_curves:
                best_curve_path = curve_dir / algorithm / f"{dataset.name}_{algorithm}_best_convergence.png"
                plot_history(best_curve_path, dataset.name, algorithm, best_result.history)
            rows.append(result_row(dataset, algorithm, best_seed, best_result, best_pl_path, best_image_path, best_history_path, best_curve_path))

    metrics_path = output_dir / "optimization_metrics.csv"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with metrics_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    all_seed_path = output_dir / "all_seed_metrics.csv"
    with all_seed_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(all_seed_rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(all_seed_rows)

    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    print(f"\nSaved: {metrics_path}")
    print(f"Saved: {all_seed_path}")
    return 0


def _result_rank(result) -> tuple[int, float, float]:
    return (
        0 if result.optimized_legality.is_legal else 1,
        result.optimized_score,
        result.optimized_hpwl,
    )


if __name__ == "__main__":
    raise SystemExit(main())
