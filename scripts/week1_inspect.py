from __future__ import annotations

import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.hpwl import top_hpwl_nets, total_hpwl
from src.legality import check_layout_legality
from src.pcb_data import Dataset, load_all_datasets


def inspect_dataset(dataset: Dataset) -> dict[str, object]:
    legality = check_layout_legality(dataset.components, dataset.placements, dataset.nets, min_gap=2.0)
    hpwl = total_hpwl(dataset.nets, dataset.components, dataset.placements)

    return {
        "dataset": dataset.name,
        "components": len(dataset.components),
        "nets": len(dataset.nets),
        "pins": dataset.pin_count,
        "initial_hpwl": hpwl,
        "is_legal": legality.is_legal,
        "reference_violations": len(legality.reference_violations),
        "boundary_violations": len(legality.boundary_violations),
        "gap_violations": len(legality.gap_violations),
        "source_dir": str(dataset.files.directory.relative_to(ROOT)),
    }


def legality_detail_rows(dataset: Dataset) -> list[dict[str, object]]:
    legality = check_layout_legality(dataset.components, dataset.placements, dataset.nets, min_gap=2.0)
    rows: list[dict[str, object]] = []

    for violation in legality.reference_violations:
        rows.append(
            {
                "dataset": dataset.name,
                "type": "reference",
                "component_a": violation.component,
                "component_b": "",
                "gap_x": "",
                "gap_y": "",
                "detail": violation.source,
            }
        )

    for violation in legality.boundary_violations:
        rows.append(
            {
                "dataset": dataset.name,
                "type": "boundary",
                "component_a": violation.component,
                "component_b": "",
                "gap_x": "",
                "gap_y": "",
                "detail": (
                    f"rect=({violation.rect.left},{violation.rect.bottom},"
                    f"{violation.rect.right},{violation.rect.top})"
                ),
            }
        )

    for violation in legality.gap_violations:
        rows.append(
            {
                "dataset": dataset.name,
                "type": "min_gap",
                "component_a": violation.component_a,
                "component_b": violation.component_b,
                "gap_x": violation.gap_x,
                "gap_y": violation.gap_y,
                "detail": "requires gap_x >= 2 or gap_y >= 2",
            }
        )

    return rows


def top_net_rows(dataset: Dataset, limit: int = 10) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rank, detail in enumerate(top_hpwl_nets(dataset.nets, dataset.components, dataset.placements, limit=limit), 1):
        rows.append(
            {
                "dataset": dataset.name,
                "rank": rank,
                "net": detail.net,
                "degree": detail.degree,
                "hpwl": detail.hpwl,
                "min_x": detail.min_x,
                "max_x": detail.max_x,
                "min_y": detail.min_y,
                "max_y": detail.max_y,
            }
        )
    return rows


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    datasets = load_all_datasets([ROOT / "data", ROOT / "要求与数据" / "data"])
    if not datasets:
        print("No complete datasets found. Put .nodes, .nets, and .pl files in data/.")
        return 1

    rows = [inspect_dataset(dataset) for dataset in datasets]
    top_rows = [row for dataset in datasets for row in top_net_rows(dataset)]
    legality_rows = [row for dataset in datasets for row in legality_detail_rows(dataset)]
    fieldnames = list(rows[0].keys())

    output_path = ROOT / "results" / "week1" / "initial_metrics.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    top_nets_path = ROOT / "results" / "week1" / "top_hpwl_nets.csv"
    with top_nets_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(top_rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(top_rows)

    legality_path = ROOT / "results" / "week1" / "legality_violations.csv"
    if legality_rows:
        legality_fields = list(legality_rows[0].keys())
    else:
        legality_fields = ["dataset", "type", "component_a", "component_b", "gap_x", "gap_y", "detail"]
    with legality_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=legality_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(legality_rows)

    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    print(f"\nSaved: {output_path}")
    print(f"Saved: {top_nets_path}")
    print(f"Saved: {legality_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
