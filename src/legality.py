from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from .pcb_data import Component, Net, Placement
from .geometry import oriented_size


@dataclass(frozen=True)
class Rect:
    left: float
    bottom: float
    right: float
    top: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom


@dataclass(frozen=True)
class Board:
    left: float
    bottom: float
    right: float
    top: float


@dataclass(frozen=True)
class BoundaryViolation:
    component: str
    rect: Rect
    board: Board


@dataclass(frozen=True)
class GapViolation:
    component_a: str
    component_b: str
    gap_x: float
    gap_y: float


@dataclass(frozen=True)
class ReferenceViolation:
    component: str
    source: str


@dataclass(frozen=True)
class LegalityResult:
    board: Board
    boundary_violations: List[BoundaryViolation]
    gap_violations: List[GapViolation]
    reference_violations: List[ReferenceViolation]

    @property
    def is_legal(self) -> bool:
        return not self.boundary_violations and not self.gap_violations and not self.reference_violations


def component_rect(component: Component, placement: Placement) -> Rect:
    width, height = oriented_size(component, placement)
    return Rect(
        left=placement.x,
        bottom=placement.y,
        right=placement.x + width,
        top=placement.y + height,
    )


def build_rects(components: Dict[str, Component], placements: Dict[str, Placement]) -> Dict[str, Rect]:
    rects: Dict[str, Rect] = {}
    for name, placement in placements.items():
        if name in components:
            rects[name] = component_rect(components[name], placement)
    return rects


def infer_board(components: Dict[str, Component], placements: Dict[str, Placement], margin: float = 0.0) -> Board:
    rects = list(build_rects(components, placements).values())
    if not rects:
        raise ValueError("Cannot infer board bounds from an empty layout.")
    return Board(
        left=min(rect.left for rect in rects) - margin,
        bottom=min(rect.bottom for rect in rects) - margin,
        right=max(rect.right for rect in rects) + margin,
        top=max(rect.top for rect in rects) + margin,
    )


def check_boundary(
    components: Dict[str, Component],
    placements: Dict[str, Placement],
    board: Board,
) -> List[BoundaryViolation]:
    violations: List[BoundaryViolation] = []
    for name, rect in build_rects(components, placements).items():
        if rect.left < board.left or rect.bottom < board.bottom or rect.right > board.right or rect.top > board.top:
            violations.append(BoundaryViolation(component=name, rect=rect, board=board))
    return violations


def rectangle_gaps(a: Rect, b: Rect) -> Tuple[float, float]:
    """Return assignment-style gaps; negative values mean projections overlap."""
    gap_x = max(a.left, b.left) - min(a.right, b.right)
    gap_y = max(a.bottom, b.bottom) - min(a.top, b.top)
    return gap_x, gap_y


def has_min_gap(a: Rect, b: Rect, min_gap: float = 2.0) -> bool:
    gap_x, gap_y = rectangle_gaps(a, b)
    return gap_x >= min_gap or gap_y >= min_gap


def check_min_gap(
    components: Dict[str, Component],
    placements: Dict[str, Placement],
    min_gap: float = 2.0,
) -> List[GapViolation]:
    rects = build_rects(components, placements)
    names = sorted(rects)
    violations: List[GapViolation] = []
    for i, name_a in enumerate(names):
        rect_a = rects[name_a]
        for name_b in names[i + 1 :]:
            rect_b = rects[name_b]
            gap_x, gap_y = rectangle_gaps(rect_a, rect_b)
            if gap_x < min_gap and gap_y < min_gap:
                violations.append(GapViolation(name_a, name_b, gap_x, gap_y))
    return violations


def missing_references(
    components: Dict[str, Component],
    placements: Dict[str, Placement],
    referenced_components: Iterable[str],
) -> List[str]:
    known = set(components) & set(placements)
    return sorted(set(referenced_components) - known)


def check_references(
    components: Dict[str, Component],
    placements: Dict[str, Placement],
    nets: Iterable[Net],
) -> List[ReferenceViolation]:
    violations: List[ReferenceViolation] = []
    known_components = set(components)
    known_placements = set(placements)

    for name in sorted(known_components - known_placements):
        violations.append(ReferenceViolation(component=name, source="missing in .pl"))
    for name in sorted(known_placements - known_components):
        violations.append(ReferenceViolation(component=name, source="missing in .nodes"))

    for net in nets:
        for pin in net.pins:
            if pin.component not in known_components:
                violations.append(ReferenceViolation(component=pin.component, source=f"{net.name} missing in .nodes"))
            elif pin.component not in known_placements:
                violations.append(ReferenceViolation(component=pin.component, source=f"{net.name} missing in .pl"))

    return violations


def check_layout_legality(
    components: Dict[str, Component],
    placements: Dict[str, Placement],
    nets: Iterable[Net],
    board: Board | None = None,
    min_gap: float = 2.0,
) -> LegalityResult:
    effective_board = board if board is not None else infer_board(components, placements)
    return LegalityResult(
        board=effective_board,
        boundary_violations=check_boundary(components, placements, effective_board),
        gap_violations=check_min_gap(components, placements, min_gap=min_gap),
        reference_violations=check_references(components, placements, nets),
    )
