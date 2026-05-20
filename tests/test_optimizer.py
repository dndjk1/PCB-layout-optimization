from pathlib import Path

from src.legality import Board, check_layout_legality
from src.optimizer import (
    OptimizationConfig,
    _adaptive_analytical_parameters,
    _min_cost_slot_assignment,
    _pin_side_slot_candidates,
    _same_side_sorted_candidate,
    available_algorithms,
    optimize,
    placement_score,
)
from src.pcb_data import Component, Dataset, DatasetFiles, Net, Pin, Placement


def make_dataset(placements):
    return Dataset(
        name="toy",
        files=DatasetFiles(
            name="toy",
            directory=Path("."),
            nodes_path=Path("toy.nodes"),
            nets_path=Path("toy.nets"),
            pl_path=Path("toy.pl"),
        ),
        components={
            "A": Component("A", 10, 10),
            "B": Component("B", 10, 10),
            "C": Component("C", 10, 10),
        },
        nets=[
            Net("N1", [Pin("A", 0, 0), Pin("B", 0, 0)]),
            Net("N2", [Pin("B", 0, 0), Pin("C", 0, 0)]),
        ],
        placements=placements,
    )


def test_optimizer_keeps_all_components_and_board_bounds():
    dataset = make_dataset(
        {
            "A": Placement("A", 0, 0, "N"),
            "B": Placement("B", 80, 0, "N"),
            "C": Placement("C", 160, 0, "N"),
        }
    )

    result = optimize(dataset, max_iter=300, seed=2, initial_step=20)

    assert set(result.placements) == set(dataset.placements)
    for name, placement in result.placements.items():
        component = dataset.components[name]
        assert result.board.left <= placement.x
        assert result.board.bottom <= placement.y
        assert placement.x + component.width <= result.board.right
        assert placement.y + component.height <= result.board.top


def test_optimizer_improves_penalized_score_on_simple_layout():
    dataset = make_dataset(
        {
            "A": Placement("A", 0, 0, "N"),
            "B": Placement("B", 80, 0, "N"),
            "C": Placement("C", 160, 0, "N"),
        }
    )
    initial_score = placement_score(dataset, dataset.placements)

    result = optimize(dataset, max_iter=500, seed=3, initial_step=30)

    assert result.optimized_score < initial_score
    assert result.accepted_moves > 0


def test_available_algorithms_run_on_simple_layout():
    dataset = make_dataset(
        {
            "A": Placement("A", 0, 0, "N"),
            "B": Placement("B", 80, 0, "N"),
            "C": Placement("C", 160, 0, "N"),
        }
    )

    for algorithm in available_algorithms():
        result = optimize(dataset, algorithm=algorithm, max_iter=20, seed=7)

        assert result.algorithm == algorithm
        assert set(result.placements) == set(dataset.placements)


def test_two_stage_repairs_simple_overlap():
    dataset = make_dataset(
        {
            "A": Placement("A", 0, 0, "N"),
            "B": Placement("B", 0, 0, "N"),
            "C": Placement("C", 40, 0, "N"),
        }
    )

    result = optimize(dataset, algorithm="two_stage", max_iter=800, seed=12, initial_step=8)

    assert not result.initial_legality.is_legal
    assert result.optimized_legality.is_legal
    assert result.history


def test_pin_side_candidates_generate_multiple_rows_around_target_pin():
    component = Component("R1", 4, 4)
    macro = Component("U1", 40, 60)
    macro_placement = Placement("U1", 50, 40, "N")
    board = Board(0, 0, 120, 120)

    slots = _pin_side_slot_candidates(
        component,
        macro,
        macro_placement,
        "left",
        board,
        OptimizationConfig(min_gap=2),
        target_pin=(50, 70),
    )

    assert len({x for x, _ in slots}) >= 4
    assert min(abs(y - 70) for _, y in slots) <= component.height + 2


def test_min_cost_slot_assignment_handles_large_groups():
    components = {"U1": Component("U1", 40, 60)}
    placements = {"U1": Placement("U1", 60, 40, "N")}
    assignments = {}
    for index in range(9):
        name = f"R{index}"
        components[name] = Component(name, 4, 4)
        placements[name] = Placement(name, 5, 5 + index, "N")
        assignments[name] = ("U1", "left", (60, 48 + index * 5))
    dataset = Dataset(
        name="large_slot_group",
        files=DatasetFiles("large_slot_group", Path("."), Path("a.nodes"), Path("a.nets"), Path("a.pl")),
        components=components,
        nets=[],
        placements=placements,
    )
    board = Board(0, 0, 130, 130)
    config = OptimizationConfig(min_gap=2)
    ordered = [f"R{index}" for index in range(9)]
    slots_by_name = {
        name: _pin_side_slot_candidates(
            components[name],
            components["U1"],
            placements["U1"],
            "left",
            board,
            config,
            target_pin=assignments[name][2],
        )
        for name in ordered
    }

    result = _min_cost_slot_assignment(
        dataset=dataset,
        base_result=dict(placements),
        base_placed={"U1": placements["U1"]},
        ordered=ordered,
        assignments=assignments,
        slots_by_name=slots_by_name,
        board=board,
        config=config,
    )

    assert result is not None
    assigned, _ = result
    assert len({(assigned[name].x, assigned[name].y) for name in ordered}) == len(ordered)
    assert check_layout_legality(components, assigned, [], board=board, min_gap=2).is_legal


def test_adaptive_analytical_parameters_react_to_violations():
    dataset = make_dataset(
        {
            "A": Placement("A", 0, 0, "N"),
            "B": Placement("B", 0, 0, "N"),
            "C": Placement("C", 40, 0, "N"),
        }
    )
    board = Board(0, 0, 80, 80)
    legality = check_layout_legality(dataset.components, dataset.placements, dataset.nets, board=board, min_gap=2)

    _, learning_rate, momentum, density_weight = _adaptive_analytical_parameters(
        span=80,
        base_gamma=2,
        learning_rate=4,
        momentum=0.86,
        legality=legality,
        improvement=0,
        stagnant_iterations=0,
        config=OptimizationConfig(min_gap=2),
    )

    assert density_weight > 0.65
    assert learning_rate < 4
    assert momentum < 0.86


def test_same_side_sorted_candidate_reorders_by_target_pin_axis():
    components = {name: Component(name, 4, 4) for name in ("R1", "R2", "R3")}
    placements = {
        "R1": Placement("R1", 0, 30, "N"),
        "R2": Placement("R2", 0, 10, "N"),
        "R3": Placement("R3", 0, 20, "N"),
    }
    assignments = {
        "R1": ("U1", "left", (0, 10)),
        "R2": ("U1", "left", (0, 30)),
        "R3": ("U1", "left", (0, 20)),
    }
    dataset = Dataset(
        name="sort",
        files=DatasetFiles("sort", Path("."), Path("a.nodes"), Path("a.nets"), Path("a.pl")),
        components=components,
        nets=[],
        placements=placements,
    )

    candidate = _same_side_sorted_candidate(dataset, placements, ["R1", "R2", "R3"], assignments)

    assert candidate["R1"].y < candidate["R3"].y < candidate["R2"].y
