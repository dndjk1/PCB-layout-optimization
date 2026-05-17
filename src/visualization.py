from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.lines import Line2D

from .hpwl import all_pin_positions
from .legality import infer_board
from .pcb_data import Dataset


def visualize_dataset(
    dataset: Dataset,
    output_path: Path,
    show_pins: bool = True,
    show_nets: bool = True,
    max_net_degree: int | None = 20,
    dpi: int = 180,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    board = infer_board(dataset.components, dataset.placements, margin=5.0)
    width = board.right - board.left
    height = board.top - board.bottom
    figure_width = 14.0
    figure_height = max(8.0, figure_width * height / max(width, 1.0))

    fig, ax = plt.subplots(figsize=(figure_width, figure_height), dpi=dpi)
    max_area = _max_component_area(dataset)
    pin_positions = _collect_pin_positions(dataset)

    if show_nets:
        _draw_nets(ax, dataset, pin_positions, max_net_degree=max_net_degree)

    _draw_components(ax, dataset, max_area=max_area)

    if show_pins:
        _draw_pins(ax, pin_positions)

    ax.set_xlim(board.left, board.right)
    ax.set_ylim(board.bottom, board.top)
    ax.set_aspect("equal")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_title(
        f"{dataset.name} initial layout\n"
        f"{len(dataset.components)} components | {len(dataset.nets)} nets | {dataset.pin_count} pins"
    )
    ax.grid(True, alpha=0.15, linewidth=0.4)
    ax.legend(handles=_legend_handles(), loc="upper right", fontsize=7, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path


def _max_component_area(dataset: Dataset) -> float:
    if not dataset.components:
        return 1.0
    return max(component.width * component.height for component in dataset.components.values())


def _collect_pin_positions(dataset: Dataset) -> Dict[Tuple[str, float, float], Tuple[float, float]]:
    positions: Dict[Tuple[str, float, float], Tuple[float, float]] = {}
    for net in dataset.nets:
        for position in all_pin_positions(net, dataset.components, dataset.placements):
            positions[(position.component, position.dx, position.dy)] = (position.x, position.y)
    return positions


def _draw_nets(
    ax,
    dataset: Dataset,
    pin_positions: Dict[Tuple[str, float, float], Tuple[float, float]],
    max_net_degree: int | None,
) -> None:
    cmap = matplotlib.colormaps.get_cmap("tab20")
    for index, net in enumerate(dataset.nets):
        if max_net_degree is not None and len(net.pins) > max_net_degree:
            continue

        coords = []
        for pin in net.pins:
            coord = pin_positions.get((pin.component, pin.dx, pin.dy))
            if coord is not None:
                coords.append(coord)

        if len(coords) < 2:
            continue

        color = cmap(index % 20)
        anchor = coords[0]
        for coord in coords[1:]:
            ax.plot(
                [anchor[0], coord[0]],
                [anchor[1], coord[1]],
                color=color,
                linewidth=0.7,
                alpha=0.55,
                zorder=1,
            )


def _draw_components(ax, dataset: Dataset, max_area: float) -> None:
    for name, placement in dataset.placements.items():
        component = dataset.components.get(name)
        if component is None:
            continue

        area_ratio = (component.width * component.height) / max(max_area, 1.0)
        if area_ratio > 0.15:
            facecolor, edgecolor, linewidth = "#fff7d6", "#8a6d3b", 1.8
        elif placement.fixed or component.terminal:
            facecolor, edgecolor, linewidth = "#ffd6d6", "#b03434", 1.2
        else:
            facecolor, edgecolor, linewidth = "#cfe8ff", "#356d9c", 1.0

        rect = patches.Rectangle(
            (placement.x, placement.y),
            component.width,
            component.height,
            linewidth=linewidth,
            edgecolor=edgecolor,
            facecolor=facecolor,
            alpha=0.9,
            zorder=2,
        )
        ax.add_patch(rect)

        fontsize = 5 if area_ratio < 0.05 else 7
        ax.text(
            placement.x + component.width / 2.0,
            placement.y + component.height / 2.0,
            name,
            ha="center",
            va="center",
            fontsize=fontsize,
            color="#222222",
            zorder=3,
        )


def _draw_pins(ax, pin_positions: Dict[Tuple[str, float, float], Tuple[float, float]]) -> None:
    for x, y in pin_positions.values():
        ax.plot(
            x,
            y,
            "o",
            markersize=2.8,
            markerfacecolor="white",
            markeredgecolor="#222222",
            markeredgewidth=0.7,
            zorder=4,
        )


def _legend_handles() -> list:
    return [
        patches.Patch(facecolor="#fff7d6", edgecolor="#8a6d3b", label="Large component"),
        patches.Patch(facecolor="#cfe8ff", edgecolor="#356d9c", label="Movable component"),
        patches.Patch(facecolor="#ffd6d6", edgecolor="#b03434", label="Fixed/terminal"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="white", markeredgecolor="#222222", label="Pin"),
        Line2D([0], [0], color="#888888", linewidth=1, label="Net connection"),
    ]

