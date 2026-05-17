from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Dict, Iterable, List, Sequence


@dataclass(frozen=True)
class Component:
    name: str
    width: float
    height: float
    terminal: bool = False


@dataclass(frozen=True)
class Pin:
    component: str
    dx: float
    dy: float
    pin_type: str = "I"


@dataclass(frozen=True)
class Net:
    name: str
    pins: List[Pin]
    declared_degree: int | None = None


@dataclass(frozen=True)
class Placement:
    name: str
    x: float
    y: float
    orient: str
    fixed: bool = False


@dataclass(frozen=True)
class DatasetFiles:
    name: str
    directory: Path
    nodes_path: Path
    nets_path: Path
    pl_path: Path


@dataclass(frozen=True)
class Dataset:
    name: str
    files: DatasetFiles
    components: Dict[str, Component]
    nets: List[Net]
    placements: Dict[str, Placement]

    @property
    def pin_count(self) -> int:
        return sum(len(net.pins) for net in self.nets)


def _clean_lines(path: Path) -> Iterable[str]:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("UCLA"):
            continue
        yield line


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _strip_inline_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _canonical_dataset_name(stem: str) -> str:
    match = re.match(r"(small-\d+)", stem)
    return match.group(1) if match else stem


def parse_nodes(path: Path) -> Dict[str, Component]:
    """Read a Bookshelf .nodes file into component definitions."""
    components: Dict[str, Component] = {}

    for raw_line in _clean_lines(path):
        line = _strip_inline_comment(raw_line)
        parts = line.split()
        if len(parts) < 3 or not _is_number(parts[1]) or not _is_number(parts[2]):
            continue

        name = parts[0]
        width = float(parts[1])
        height = float(parts[2])
        terminal = any(part.lower() == "terminal" for part in parts[3:])
        components[name] = Component(name=name, width=width, height=height, terminal=terminal)

    return components


def parse_nets(path: Path) -> List[Net]:
    """Read a Bookshelf .nets file into net and pin records."""
    nets: List[Net] = []
    current_name: str | None = None
    current_degree: int | None = None
    current_pins: List[Pin] = []

    def finish_current_net() -> None:
        if current_name is not None:
            nets.append(Net(name=current_name, pins=list(current_pins), declared_degree=current_degree))

    for raw_line in _clean_lines(path):
        line = _strip_inline_comment(raw_line)
        parts = line.replace(":", " : ").split()
        if not parts or parts[0] in {"NumNets", "NumPins"}:
            continue

        if parts[0] == "NetDegree":
            finish_current_net()
            current_degree = int(parts[2]) if len(parts) >= 3 and _is_number(parts[2]) else None
            current_name = parts[3] if len(parts) >= 4 else f"unnamed_net_{len(nets)}"
            current_pins = []
            continue

        if current_name is None:
            continue

        if len(parts) >= 5 and parts[2] == ":" and _is_number(parts[3]) and _is_number(parts[4]):
            current_pins.append(
                Pin(
                    component=parts[0],
                    pin_type=parts[1],
                    dx=float(parts[3]),
                    dy=float(parts[4]),
                )
            )

    finish_current_net()
    return nets


def parse_pl(path: Path) -> Dict[str, Placement]:
    """Read a Bookshelf .pl file into initial component placements."""
    placements: Dict[str, Placement] = {}

    for raw_line in _clean_lines(path):
        line = _strip_inline_comment(raw_line)
        parts = line.replace(":", " : ").split()
        if len(parts) < 5 or not _is_number(parts[1]) or not _is_number(parts[2]):
            continue

        name = parts[0]
        x = float(parts[1])
        y = float(parts[2])
        orient = parts[4] if parts[3] == ":" else parts[3]
        fixed = any(part.upper() == "/FIXED" for part in parts)
        placements[name] = Placement(name=name, x=x, y=y, orient=orient, fixed=fixed)

    return placements


def find_dataset_files(root: Path, dataset_name: str | None = None) -> List[DatasetFiles]:
    """Recursively discover complete .nodes/.nets/.pl dataset triples."""
    root = Path(root)
    groups: Dict[Path, Dict[str, Path]] = {}

    for path in root.rglob("*"):
        if path.suffix.lower() not in {".nodes", ".nets", ".pl"}:
            continue
        groups.setdefault(path.parent, {})[path.suffix.lower()[1:]] = path

    datasets: List[DatasetFiles] = []
    for directory, files in groups.items():
        if not {"nodes", "nets", "pl"}.issubset(files):
            continue
        raw_name = files["nodes"].stem
        name = _canonical_dataset_name(raw_name)
        if dataset_name is not None and name != dataset_name and raw_name != dataset_name:
            continue
        datasets.append(
            DatasetFiles(
                name=name,
                directory=directory,
                nodes_path=files["nodes"],
                nets_path=files["nets"],
                pl_path=files["pl"],
            )
        )

    return sorted(datasets, key=lambda item: (item.name, str(item.directory)))


def discover_dataset_names(data_dir: Path) -> List[str]:
    return sorted({files.name for files in find_dataset_files(data_dir)})


def load_dataset_from_files(files: DatasetFiles) -> Dataset:
    components = parse_nodes(files.nodes_path)
    nets = parse_nets(files.nets_path)
    placements = parse_pl(files.pl_path)
    return Dataset(
        name=files.name,
        files=files,
        components=components,
        nets=nets,
        placements=placements,
    )


def load_dataset(data_dir: Path, name: str) -> Dataset:
    """Load one dataset by canonical name, such as small-1 or small-5."""
    direct = Path(data_dir)
    direct_files = DatasetFiles(
        name=_canonical_dataset_name(name),
        directory=direct,
        nodes_path=direct / f"{name}.nodes",
        nets_path=direct / f"{name}.nets",
        pl_path=direct / f"{name}.pl",
    )
    if direct_files.nodes_path.exists() and direct_files.nets_path.exists() and direct_files.pl_path.exists():
        return load_dataset_from_files(direct_files)

    matches = find_dataset_files(direct, name)
    if not matches:
        raise FileNotFoundError(f"No complete dataset named {name!r} found under {data_dir}")
    if len(matches) > 1:
        original = [item for item in matches if "original" in item.directory.name]
        matches = original or matches

    return load_dataset_from_files(matches[0])


def load_all_datasets(search_roots: Sequence[Path], include_optimized: bool = False) -> List[Dataset]:
    """Load every discovered dataset from one or more roots."""
    found: Dict[str, DatasetFiles] = {}

    for root in search_roots:
        if not Path(root).exists():
            continue
        for files in find_dataset_files(Path(root)):
            is_optimized = "optimized" in files.directory.name.lower()
            if is_optimized and not include_optimized:
                continue
            found.setdefault(files.name, files)

    return [load_dataset_from_files(files) for _, files in sorted(found.items())]


def dataset_summary(dataset: Dataset) -> dict[str, object]:
    return {
        "dataset": dataset.name,
        "components": len(dataset.components),
        "nets": len(dataset.nets),
        "pins": dataset.pin_count,
        "directory": str(dataset.files.directory),
    }

