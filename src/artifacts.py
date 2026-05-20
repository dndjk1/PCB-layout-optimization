from __future__ import annotations

import csv
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

from .pcb_data import Placement


HISTORY_FIELDNAMES = [
    "dataset",
    "algorithm",
    "iteration",
    "stage",
    "hpwl",
    "score",
    "gap_violations",
    "boundary_violations",
    "reference_violations",
    "is_legal",
]


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def write_pl(path: Path, placements: dict[str, Placement]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        fp.write("UCLA pl 1.0\n\n")
        for name in sorted(placements):
            placement = placements[name]
            line = f"{placement.name} {format_number(placement.x)} {format_number(placement.y)} : {placement.orient}"
            if placement.fixed:
                line += " /FIXED"
            fp.write(line + "\n")
    return path


def safe_extract_zip(
    zip_path: Path,
    target_dir: Path,
    unsafe_path_message: str = "Unsafe path in zip: {name}",
) -> None:
    target_root = target_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            destination = (target_dir / member.filename).resolve()
            if target_root not in destination.parents and destination != target_root:
                raise ValueError(unsafe_path_message.format(name=member.filename))
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def write_history(path: Path, dataset_name: str, algorithm: str, history: Iterable[object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=HISTORY_FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        for item in history:
            writer.writerow(
                {
                    "dataset": dataset_name,
                    "algorithm": algorithm,
                    "iteration": item.iteration,
                    "stage": item.stage,
                    "hpwl": item.hpwl,
                    "score": item.score,
                    "gap_violations": item.gap_violations,
                    "boundary_violations": item.boundary_violations,
                    "reference_violations": item.reference_violations,
                    "is_legal": item.is_legal,
                }
            )
    return path
