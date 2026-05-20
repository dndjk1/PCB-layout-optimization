from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from .pcb_data import Component, Net, Pin, Placement
from .geometry import oriented_size, rotate_pin_offset


@dataclass(frozen=True)
class PinPosition:
    net: str
    component: str
    x: float
    y: float
    dx: float
    dy: float


@dataclass(frozen=True)
class NetHPWL:
    net: str
    degree: int
    hpwl: float
    min_x: float
    max_x: float
    min_y: float
    max_y: float


def pin_position(component: Component, placement: Placement, dx: float, dy: float) -> Tuple[float, float]:
    """Calculate a pin's absolute coordinate from component lower-left coordinate."""
    width, height = oriented_size(component, placement)
    pin_dx, pin_dy = rotate_pin_offset(dx, dy, placement.orient)
    return placement.x + width / 2.0 + pin_dx, placement.y + height / 2.0 + pin_dy


def all_pin_positions(
    net: Net,
    components: Dict[str, Component],
    placements: Dict[str, Placement],
) -> List[PinPosition]:
    positions: List[PinPosition] = []
    for pin in net.pins:
        component, placement = _lookup_pin_owner(pin, components, placements)
        x, y = pin_position(component, placement, pin.dx, pin.dy)
        positions.append(PinPosition(net=net.name, component=pin.component, x=x, y=y, dx=pin.dx, dy=pin.dy))
    return positions


def net_hpwl_detail(
    net: Net,
    components: Dict[str, Component],
    placements: Dict[str, Placement],
) -> NetHPWL:
    positions = all_pin_positions(net, components, placements)
    if not positions:
        return NetHPWL(net=net.name, degree=0, hpwl=0.0, min_x=0.0, max_x=0.0, min_y=0.0, max_y=0.0)

    xs = [position.x for position in positions]
    ys = [position.y for position in positions]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    hpwl = (max_x - min_x) + (max_y - min_y)
    return NetHPWL(
        net=net.name,
        degree=len(positions),
        hpwl=hpwl,
        min_x=min_x,
        max_x=max_x,
        min_y=min_y,
        max_y=max_y,
    )


def net_hpwl(net: Net, components: Dict[str, Component], placements: Dict[str, Placement]) -> float:
    return net_hpwl_detail(net, components, placements).hpwl


def hpwl_by_net(
    nets: Iterable[Net],
    components: Dict[str, Component],
    placements: Dict[str, Placement],
) -> List[NetHPWL]:
    return [net_hpwl_detail(net, components, placements) for net in nets]


def total_hpwl(nets: Iterable[Net], components: Dict[str, Component], placements: Dict[str, Placement]) -> float:
    return sum(detail.hpwl for detail in hpwl_by_net(nets, components, placements))


def top_hpwl_nets(
    nets: Iterable[Net],
    components: Dict[str, Component],
    placements: Dict[str, Placement],
    limit: int = 10,
) -> List[NetHPWL]:
    details = hpwl_by_net(nets, components, placements)
    return sorted(details, key=lambda item: item.hpwl, reverse=True)[:limit]


def _lookup_pin_owner(
    pin: Pin,
    components: Dict[str, Component],
    placements: Dict[str, Placement],
) -> Tuple[Component, Placement]:
    if pin.component not in components:
        raise KeyError(f"Pin references unknown component in .nodes: {pin.component}")
    if pin.component not in placements:
        raise KeyError(f"Pin references component missing in .pl: {pin.component}")
    return components[pin.component], placements[pin.component]
