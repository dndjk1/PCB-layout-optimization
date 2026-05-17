from src.legality import Board, check_boundary, check_layout_legality, check_min_gap, has_min_gap
from src.pcb_data import Component, Net, Pin, Placement


def test_min_gap_is_legal_when_one_axis_has_enough_space():
    components = {
        "A": Component("A", 10, 10),
        "B": Component("B", 10, 10),
    }
    placements = {
        "A": Placement("A", 0, 0, "N"),
        "B": Placement("B", 12, 0, "N"),
    }

    assert check_min_gap(components, placements, min_gap=2) == []


def test_min_gap_reports_overlap_or_too_close_pair():
    components = {
        "A": Component("A", 10, 10),
        "B": Component("B", 10, 10),
    }
    placements = {
        "A": Placement("A", 0, 0, "N"),
        "B": Placement("B", 11, 0, "N"),
    }

    violations = check_min_gap(components, placements, min_gap=2)

    assert len(violations) == 1
    assert violations[0].component_a == "A"
    assert violations[0].component_b == "B"
    assert violations[0].gap_x == 1


def test_boundary_violation_detects_component_outside_board():
    components = {"A": Component("A", 10, 10)}
    placements = {"A": Placement("A", 5, 5, "N")}
    board = Board(left=0, bottom=0, right=12, top=12)

    violations = check_boundary(components, placements, board)

    assert len(violations) == 1
    assert violations[0].component == "A"


def test_layout_legality_detects_missing_pin_reference():
    components = {"A": Component("A", 10, 10)}
    placements = {"A": Placement("A", 0, 0, "N")}
    nets = [Net("N1", [Pin("A", 0, 0), Pin("B", 0, 0)])]

    result = check_layout_legality(components, placements, nets)

    assert not result.is_legal
    assert result.reference_violations[0].component == "B"

