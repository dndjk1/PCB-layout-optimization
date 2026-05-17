from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pcb_data import load_all_datasets
from src.visualization import visualize_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate initial PCB layout visualizations.")
    parser.add_argument("--dataset", action="append", help="Dataset name to render, for example small-1.")
    parser.add_argument("--output-dir", default="results/initial_layouts", help="Directory for PNG outputs.")
    parser.add_argument("--dpi", type=int, default=180, help="Output image DPI.")
    parser.add_argument("--max-net-degree", type=int, default=20, help="Hide nets above this degree.")
    parser.add_argument("--no-pins", action="store_true", help="Hide pin markers.")
    parser.add_argument("--no-nets", action="store_true", help="Hide net connections.")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    datasets = load_all_datasets([ROOT / "data", ROOT / "要求与数据" / "data"])
    if args.dataset:
        wanted = set(args.dataset)
        datasets = [dataset for dataset in datasets if dataset.name in wanted]

    if not datasets:
        print("No datasets found to visualize.")
        return 1

    output_dir = ROOT / args.output_dir
    rows = []
    for dataset in datasets:
        output_path = output_dir / f"{dataset.name}_initial_layout.png"
        visualize_dataset(
            dataset,
            output_path=output_path,
            show_pins=not args.no_pins,
            show_nets=not args.no_nets,
            max_net_degree=args.max_net_degree,
            dpi=args.dpi,
        )
        rows.append(
            {
                "dataset": dataset.name,
                "components": len(dataset.components),
                "nets": len(dataset.nets),
                "pins": dataset.pin_count,
                "image": str(output_path.relative_to(ROOT)),
            }
        )
        print(f"Saved: {output_path}")

    manifest_path = output_dir / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_step(args: list[str]) -> None:
    command = [sys.executable, *args]
    print("$ " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    run_step(["scripts/week1_inspect.py"])
    run_step(["scripts/visualize_initial_layouts.py"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
