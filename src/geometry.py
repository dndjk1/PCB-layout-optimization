from __future__ import annotations

from .pcb_data import Component, Placement


BASE_ORIENTS = ("N", "E", "S", "W")


def canonical_orient(orient: str) -> str:
    value = (orient or "N").upper()
    if value in BASE_ORIENTS:
        return value
    # Bookshelf may contain mirrored orientations. Keep the rotation part only
    # because this project supports planar 90-degree rotation, not mirroring.
    for item in BASE_ORIENTS:
        if value.endswith(item):
            return item
    return "N"


def rotate_orient(orient: str, direction: str) -> str:
    current = canonical_orient(orient)
    index = BASE_ORIENTS.index(current)
    if direction == "ccw":
        index -= 1
    else:
        index += 1
    return BASE_ORIENTS[index % len(BASE_ORIENTS)]


def oriented_size(component: Component, placement: Placement) -> tuple[float, float]:
    orient = canonical_orient(placement.orient)
    if orient in {"E", "W"}:
        return component.height, component.width
    return component.width, component.height


def rotate_pin_offset(dx: float, dy: float, orient: str) -> tuple[float, float]:
    orient = canonical_orient(orient)
    if orient == "E":
        return dy, -dx
    if orient == "S":
        return -dx, -dy
    if orient == "W":
        return -dy, dx
    return dx, dy


def rotated_about_center(component: Component, placement: Placement, direction: str) -> Placement:
    old_w, old_h = oriented_size(component, placement)
    center_x = placement.x + old_w / 2
    center_y = placement.y + old_h / 2
    new_orient = rotate_orient(placement.orient, direction)
    new_w, new_h = oriented_size(component, Placement(placement.name, placement.x, placement.y, new_orient, placement.fixed))
    return Placement(
        name=placement.name,
        x=center_x - new_w / 2,
        y=center_y - new_h / 2,
        orient=new_orient,
        fixed=placement.fixed,
    )
