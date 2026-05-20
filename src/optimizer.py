from __future__ import annotations

from dataclasses import dataclass, field, replace
import itertools
import math
import random
import time
from typing import Dict, Literal

from .hpwl import pin_position, total_hpwl
from .legality import Board, LegalityResult, component_rect, has_min_gap, check_layout_legality, infer_board
from .pcb_data import Dataset, Placement
from .geometry import oriented_size, rotate_pin_offset


AlgorithmName = Literal["greedy", "local_search", "annealing", "simulated_annealing", "random", "analytical", "two_stage"]


@dataclass(frozen=True)
class OptimizationConfig:
    algorithm: AlgorithmName = "greedy"
    max_iter: int = 10_000
    seed: int = 0
    min_gap: float = 2.0
    initial_step: float | None = None
    min_step: float = 1.0
    legality_weight: float = 100_000.0
    initial_temperature: float | None = None
    cooling_rate: float = 0.995
    history_interval: int = 100


@dataclass(frozen=True)
class ConvergenceRecord:
    iteration: int
    stage: str
    hpwl: float
    score: float
    gap_violations: int
    boundary_violations: int
    reference_violations: int
    is_legal: bool


@dataclass(frozen=True)
class OptimizationResult:
    algorithm: str
    placements: Dict[str, Placement]
    initial_hpwl: float
    optimized_hpwl: float
    initial_score: float
    optimized_score: float
    initial_legality: LegalityResult
    optimized_legality: LegalityResult
    iterations: int
    accepted_moves: int
    runtime_seconds: float
    board: Board
    history: list[ConvergenceRecord] = field(default_factory=list)

    @property
    def improvement(self) -> float:
        return self.initial_hpwl - self.optimized_hpwl

    @property
    def improvement_ratio(self) -> float:
        if self.initial_hpwl == 0:
            return 0.0
        return self.improvement / self.initial_hpwl


def optimize(
    dataset: Dataset,
    algorithm: AlgorithmName = "greedy",
    max_iter: int = 10_000,
    seed: int = 0,
    min_gap: float = 2.0,
    initial_step: float | None = None,
    legality_weight: float = 100_000.0,
    initial_temperature: float | None = None,
    cooling_rate: float = 0.995,
    history_interval: int = 100,
) -> OptimizationResult:
    """Run a placement optimizer with a stable interface for scripts and UI."""
    config = OptimizationConfig(
        algorithm=algorithm,
        max_iter=max_iter,
        seed=seed,
        min_gap=min_gap,
        initial_step=initial_step,
        legality_weight=legality_weight,
        initial_temperature=initial_temperature,
        cooling_rate=cooling_rate,
        history_interval=history_interval,
    )
    if algorithm in {"greedy", "local_search"}:
        return greedy_local_search(dataset, config)
    if algorithm in {"annealing", "simulated_annealing"}:
        return simulated_annealing(dataset, config)
    if algorithm == "random":
        return random_search(dataset, config)
    if algorithm == "analytical":
        return analytical_nesterov_optimize(dataset, config)
    if algorithm == "two_stage":
        return two_stage_optimize(dataset, config)
    else:
        raise ValueError(f"Unsupported optimizer algorithm: {algorithm}")


def available_algorithms() -> list[str]:
    return ["annealing", "random", "analytical", "two_stage"]


def greedy_local_search(dataset: Dataset, config: OptimizationConfig | None = None) -> OptimizationResult:
    """Improve placement by keeping random translations that reduce penalized score."""
    config = config or OptimizationConfig()
    started_at = time.perf_counter()
    rng = random.Random(config.seed)
    board = infer_board(dataset.components, dataset.placements)

    movable_names = [
        name
        for name, placement in sorted(dataset.placements.items())
        if name in dataset.components and not placement.fixed
    ]
    if not movable_names:
        return _unchanged_result(dataset, config, board, started_at)

    placements = dict(dataset.placements)
    initial_hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    initial_legality = check_layout_legality(
        dataset.components,
        placements,
        dataset.nets,
        board=board,
        min_gap=config.min_gap,
    )
    current_score = placement_score(
        dataset,
        placements,
        board=board,
        min_gap=config.min_gap,
        legality_weight=config.legality_weight,
    )
    initial_score = current_score
    best_placements = dict(placements)
    best_score = current_score
    accepted = 0
    history: list[ConvergenceRecord] = []
    _record_history(dataset, placements, board, config, history, 0, "greedy")

    step = config.initial_step if config.initial_step is not None else _default_step(board)
    for iteration in range(max(0, config.max_iter)):
        if iteration and iteration % 1000 == 0:
            step = max(config.min_step, step * 0.85)

        name = rng.choice(movable_names)
        candidate = dict(placements)
        candidate[name] = _random_move(
            placement=placements[name],
            component_width=dataset.components[name].width,
            component_height=dataset.components[name].height,
            board=board,
            step=step,
            rng=rng,
        )
        candidate_score = placement_score(
            dataset,
            candidate,
            board=board,
            min_gap=config.min_gap,
            legality_weight=config.legality_weight,
        )

        if candidate_score < current_score:
            placements = candidate
            current_score = candidate_score
            accepted += 1
            if candidate_score < best_score:
                best_score = candidate_score
                best_placements = dict(candidate)
        if _should_record(iteration + 1, config):
            _record_history(dataset, placements, board, config, history, iteration + 1, "greedy")

    optimized_hpwl = total_hpwl(dataset.nets, dataset.components, best_placements)
    optimized_legality = check_layout_legality(
        dataset.components,
        best_placements,
        dataset.nets,
        board=board,
        min_gap=config.min_gap,
    )
    return OptimizationResult(
        algorithm=config.algorithm,
        placements=best_placements,
        initial_hpwl=initial_hpwl,
        optimized_hpwl=optimized_hpwl,
        initial_score=initial_score,
        optimized_score=best_score,
        initial_legality=initial_legality,
        optimized_legality=optimized_legality,
        iterations=max(0, config.max_iter),
        accepted_moves=accepted,
        runtime_seconds=time.perf_counter() - started_at,
        board=board,
        history=history,
    )


def simulated_annealing(dataset: Dataset, config: OptimizationConfig | None = None) -> OptimizationResult:
    """Run simulated annealing over random component translations."""
    config = config or OptimizationConfig(algorithm="annealing")
    started_at = time.perf_counter()
    rng = random.Random(config.seed)
    board = infer_board(dataset.components, dataset.placements)

    movable_names = _movable_names(dataset)
    if not movable_names:
        return _unchanged_result(dataset, config, board, started_at)

    placements = dict(dataset.placements)
    initial_hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    initial_legality = check_layout_legality(
        dataset.components,
        placements,
        dataset.nets,
        board=board,
        min_gap=config.min_gap,
    )
    current_score = placement_score(
        dataset,
        placements,
        board=board,
        min_gap=config.min_gap,
        legality_weight=config.legality_weight,
    )
    initial_score = current_score
    best_placements = dict(placements)
    best_score = current_score
    accepted = 0
    history: list[ConvergenceRecord] = []
    _record_history(dataset, placements, board, config, history, 0, "annealing")

    seeded, seed_accepts = _structured_seed_placement(dataset, placements, board, config, rng)
    if seed_accepts:
        placements = seeded
        current_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        best_placements = dict(placements)
        best_score = current_score
        accepted += seed_accepts
    _record_history(dataset, placements, board, config, history, 0, "structured_seed")

    step = config.initial_step if config.initial_step is not None else _default_step(board) * 1.5
    temperature = config.initial_temperature if config.initial_temperature is not None else max(1.0, current_score * 0.05)
    cooling_rate = _clamp(config.cooling_rate, 0.90, 0.9999)

    for iteration in range(max(0, config.max_iter)):
        if iteration and iteration % 1000 == 0:
            step = max(config.min_step, step * 0.9)

        name = rng.choice(movable_names)
        candidate = dict(placements)
        candidate[name] = _random_move(
            placement=placements[name],
            component_width=dataset.components[name].width,
            component_height=dataset.components[name].height,
            board=board,
            step=step,
            rng=rng,
        )
        candidate_score = placement_score(
            dataset,
            candidate,
            board=board,
            min_gap=config.min_gap,
            legality_weight=config.legality_weight,
        )

        delta = candidate_score - current_score
        if delta < 0 or rng.random() < math.exp(-delta / max(temperature, 1e-9)):
            placements = candidate
            current_score = candidate_score
            accepted += 1
            if candidate_score < best_score:
                best_score = candidate_score
                best_placements = dict(candidate)

        temperature = max(1e-6, temperature * cooling_rate)
        if _should_record(iteration + 1, config):
            _record_history(dataset, placements, board, config, history, iteration + 1, "annealing")

    best_placements, final_seed_moves = _finalize_structured_seeded_result(dataset, best_placements, board, config)
    if final_seed_moves:
        best_score = placement_score(dataset, best_placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += final_seed_moves
    _record_history(dataset, best_placements, board, config, history, config.max_iter, "structured_finalize")

    return _optimization_result(
        dataset=dataset,
        algorithm=config.algorithm,
        placements=best_placements,
        initial_hpwl=initial_hpwl,
        initial_score=initial_score,
        initial_legality=initial_legality,
        optimized_score=best_score,
        iterations=max(0, config.max_iter),
        accepted_moves=accepted,
        runtime_seconds=time.perf_counter() - started_at,
        board=board,
        min_gap=config.min_gap,
        history=history,
    )


def random_search(dataset: Dataset, config: OptimizationConfig | None = None) -> OptimizationResult:
    """Use independent random relocations as a simple comparison baseline."""
    config = config or OptimizationConfig(algorithm="random")
    started_at = time.perf_counter()
    rng = random.Random(config.seed)
    board = infer_board(dataset.components, dataset.placements)

    movable_names = _movable_names(dataset)
    if not movable_names:
        return _unchanged_result(dataset, config, board, started_at)

    placements = dict(dataset.placements)
    initial_hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    initial_legality = check_layout_legality(
        dataset.components,
        placements,
        dataset.nets,
        board=board,
        min_gap=config.min_gap,
    )
    initial_score = placement_score(
        dataset,
        placements,
        board=board,
        min_gap=config.min_gap,
        legality_weight=config.legality_weight,
    )
    best_placements = dict(placements)
    best_score = initial_score
    accepted = 0
    history: list[ConvergenceRecord] = []
    _record_history(dataset, best_placements, board, config, history, 0, "random")

    seeded, seed_accepts = _structured_seed_placement(dataset, placements, board, config, rng)
    if seed_accepts:
        best_placements = seeded
        best_score = placement_score(dataset, best_placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += seed_accepts
    _record_history(dataset, best_placements, board, config, history, 0, "structured_seed")

    for _ in range(max(0, config.max_iter)):
        name = rng.choice(movable_names)
        candidate = dict(best_placements)
        candidate[name] = _random_absolute_placement(
            placement=best_placements[name],
            component_width=dataset.components[name].width,
            component_height=dataset.components[name].height,
            board=board,
            rng=rng,
        )
        candidate_score = placement_score(
            dataset,
            candidate,
            board=board,
            min_gap=config.min_gap,
            legality_weight=config.legality_weight,
        )
        if candidate_score < best_score:
            best_score = candidate_score
            best_placements = dict(candidate)
            accepted += 1
        if _should_record(_ + 1, config):
            _record_history(dataset, best_placements, board, config, history, _ + 1, "random")

    best_placements, final_seed_moves = _finalize_structured_seeded_result(dataset, best_placements, board, config)
    if final_seed_moves:
        best_score = placement_score(dataset, best_placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += final_seed_moves
    _record_history(dataset, best_placements, board, config, history, config.max_iter, "structured_finalize")

    return _optimization_result(
        dataset=dataset,
        algorithm=config.algorithm,
        placements=best_placements,
        initial_hpwl=initial_hpwl,
        initial_score=initial_score,
        initial_legality=initial_legality,
        optimized_score=best_score,
        iterations=max(0, config.max_iter),
        accepted_moves=accepted,
        runtime_seconds=time.perf_counter() - started_at,
        board=board,
        min_gap=config.min_gap,
        history=history,
    )


def analytical_nesterov_optimize(dataset: Dataset, config: OptimizationConfig | None = None) -> OptimizationResult:
    """Lightweight analytical placement using smooth wirelength gradients and Nesterov momentum."""
    config = config or OptimizationConfig(algorithm="analytical")
    started_at = time.perf_counter()
    board = infer_board(dataset.components, dataset.placements)
    movable_names = _movable_names(dataset)
    if not movable_names:
        return _unchanged_result(dataset, config, board, started_at)

    placements = dict(dataset.placements)
    initial_hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    initial_legality = check_layout_legality(dataset.components, placements, dataset.nets, board=board, min_gap=config.min_gap)
    initial_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)

    current = dict(placements)
    best_placements = dict(current)
    best_score = initial_score
    accepted = 0
    history: list[ConvergenceRecord] = []
    _record_history(dataset, current, board, config, history, 0, "analytical")

    rng = random.Random(config.seed + 211)
    seeded, seed_accepts = _structured_seed_placement(dataset, placements, board, config, rng)
    if seed_accepts:
        current = seeded
        best_placements = dict(current)
        best_score = placement_score(dataset, best_placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += seed_accepts
    _record_history(dataset, current, board, config, history, 0, "structured_seed")

    span = max(board.right - board.left, board.top - board.bottom)
    gamma = max(1.0, span / 40.0)
    learning_rate = config.initial_step if config.initial_step is not None else max(config.min_step, span / 80.0)
    velocity = {name: (0.0, 0.0) for name in movable_names}
    momentum = 0.86
    previous_hpwl = total_hpwl(dataset.nets, dataset.components, current)
    stagnant_iterations = 0

    for iteration in range(max(0, config.max_iter)):
        legality = check_layout_legality(dataset.components, current, dataset.nets, board=board, min_gap=config.min_gap)
        current_hpwl = total_hpwl(dataset.nets, dataset.components, current)
        improvement = (previous_hpwl - current_hpwl) / max(previous_hpwl, 1.0)
        if improvement < 1e-4:
            stagnant_iterations += 1
        else:
            stagnant_iterations = 0
        gamma, learning_rate, momentum, density_weight = _adaptive_analytical_parameters(
            span=span,
            base_gamma=max(1.0, span / 40.0),
            learning_rate=learning_rate,
            momentum=momentum,
            legality=legality,
            improvement=improvement,
            stagnant_iterations=stagnant_iterations,
            config=config,
        )
        previous_hpwl = current_hpwl

        gradients = _smooth_wirelength_gradients(dataset, current, gamma)
        _add_density_and_boundary_gradients(dataset, current, board, config, gradients, density_weight=density_weight)
        candidate = dict(current)

        for name in movable_names:
            component = dataset.components[name]
            gx, gy = gradients.get(name, (0.0, 0.0))
            norm = math.hypot(gx, gy)
            if norm > 1.0:
                gx /= norm
                gy /= norm
            vx, vy = velocity[name]
            vx = momentum * vx - learning_rate * gx
            vy = momentum * vy - learning_rate * gy
            velocity[name] = (vx, vy)
            candidate[name] = replace(
                current[name],
                x=_clamp(current[name].x + vx, board.left, board.right - component.width),
                y=_clamp(current[name].y + vy, board.bottom, board.top - component.height),
            )

        candidate_score = placement_score(dataset, candidate, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        if candidate_score <= best_score:
            current = candidate
            best_placements = dict(candidate)
            best_score = candidate_score
            accepted += 1
        else:
            current = candidate

        learning_rate = max(config.min_step, learning_rate * 0.99)
        if _should_record(iteration + 1, config):
            _record_history(dataset, best_placements, board, config, history, iteration + 1, "analytical")

    legalized = _grid_legalize(dataset, best_placements, board, config)
    if placement_score(dataset, legalized, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight) <= best_score:
        best_placements = legalized
        best_score = placement_score(dataset, best_placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += 1
    _record_history(dataset, best_placements, board, config, history, config.max_iter, "analytical_legalize")

    refined, refine_accepts = _local_hpwl_slot_refinement(dataset, best_placements, board, config, passes=1)
    if refine_accepts:
        best_placements = refined
        best_score = placement_score(dataset, best_placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += refine_accepts
    _record_history(dataset, best_placements, board, config, history, config.max_iter, "analytical_refine")

    postprocessed, postprocess_accepts = _postprocess_layout(dataset, best_placements, board, config)
    if postprocess_accepts:
        best_placements = postprocessed
        best_score = placement_score(dataset, best_placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += postprocess_accepts
    _record_history(dataset, best_placements, board, config, history, config.max_iter, "analytical_postprocess")

    best_placements, final_seed_moves = _finalize_structured_seeded_result(dataset, best_placements, board, config)
    if final_seed_moves:
        best_score = placement_score(dataset, best_placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += final_seed_moves
    _record_history(dataset, best_placements, board, config, history, config.max_iter, "structured_finalize")

    return _optimization_result(
        dataset=dataset,
        algorithm=config.algorithm,
        placements=best_placements,
        initial_hpwl=initial_hpwl,
        initial_score=initial_score,
        initial_legality=initial_legality,
        optimized_score=best_score,
        iterations=max(0, config.max_iter),
        accepted_moves=accepted,
        runtime_seconds=time.perf_counter() - started_at,
        board=board,
        min_gap=config.min_gap,
        history=history,
    )


def two_stage_optimize(dataset: Dataset, config: OptimizationConfig | None = None) -> OptimizationResult:
    """Place macro components first, arrange small parts around pins, then refine."""
    config = config or OptimizationConfig(algorithm="two_stage")
    started_at = time.perf_counter()
    rng = random.Random(config.seed)
    board = infer_board(dataset.components, dataset.placements)
    movable_names = _movable_names(dataset)
    if not movable_names:
        return _unchanged_result(dataset, config, board, started_at)
    disconnected_names = _disconnected_component_names(dataset, dataset.placements)

    placements = dict(dataset.placements)
    initial_hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    initial_legality = check_layout_legality(dataset.components, placements, dataset.nets, board=board, min_gap=config.min_gap)
    initial_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
    current_score = initial_score
    accepted = 0
    history: list[ConvergenceRecord] = []
    _record_history(dataset, placements, board, config, history, 0, "initial")

    structured = _pin_aware_initial_placement(dataset, placements, board, config, rng)
    if _repair_rank(dataset, structured, board, config) <= _repair_rank(dataset, placements, board, config):
        placements = structured
        current_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += 1
    _record_history(dataset, placements, board, config, history, 0, "pin_aware")

    legalized = _grid_legalize(dataset, placements, board, config)
    legalized_score = placement_score(dataset, legalized, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
    if _repair_rank(dataset, legalized, board, config) < _repair_rank(dataset, placements, board, config):
        placements = legalized
        current_score = legalized_score
        accepted += 1
    _record_history(dataset, placements, board, config, history, 0, "legalize")

    best_placements = dict(placements)
    best_score = current_score
    repair_iter = max(1, config.max_iter // 2)
    hpwl_iter = max(0, config.max_iter - repair_iter)
    step = config.initial_step if config.initial_step is not None else _default_step(board)

    for iteration in range(repair_iter):
        legality = check_layout_legality(dataset.components, placements, dataset.nets, board=board, min_gap=config.min_gap)
        if legality.is_legal:
            break
        candidate = _repair_candidate(dataset, placements, board, legality, step, rng, config)
        candidate_score = placement_score(dataset, candidate, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        if _repair_rank(dataset, candidate, board, config) < _repair_rank(dataset, placements, board, config):
            placements = candidate
            current_score = candidate_score
            accepted += 1
            if candidate_score < best_score:
                best_score = candidate_score
                best_placements = dict(candidate)
        if _should_record(iteration + 1, config):
            _record_history(dataset, placements, board, config, history, iteration + 1, "repair")

    if not history or history[-1].iteration != repair_iter:
        _record_history(dataset, placements, board, config, history, repair_iter, "repair")

    refined, refine_accepts = _local_hpwl_slot_refinement(dataset, placements, board, config, passes=2)
    if refine_accepts:
        placements = refined
        current_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        accepted += refine_accepts
        if current_score < best_score:
            best_score = current_score
            best_placements = dict(placements)
    _record_history(dataset, placements, board, config, history, repair_iter, "slot_refine")

    iteration = repair_iter
    ordered_names = _movement_order(dataset, placements)
    while iteration < config.max_iter:
        for name in ordered_names:
            if iteration >= config.max_iter:
                break
            iteration += 1
            candidate = dict(placements)
            candidate[name] = _force_directed_move(dataset, placements, name, board, step, rng, config)
            candidate_legality = check_layout_legality(dataset.components, candidate, dataset.nets, board=board, min_gap=config.min_gap)
            candidate_score = placement_score(dataset, candidate, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)

            current_legal = check_layout_legality(dataset.components, placements, dataset.nets, board=board, min_gap=config.min_gap)
            if current_legal.is_legal:
                accept = candidate_legality.is_legal and total_hpwl(dataset.nets, dataset.components, candidate) < total_hpwl(dataset.nets, dataset.components, placements)
            else:
                accept = candidate_score < current_score

            if accept:
                placements = candidate
                current_score = candidate_score
                accepted += 1
                if candidate_score < best_score:
                    best_score = candidate_score
                    best_placements = dict(candidate)
            if _should_record(iteration, config):
                _record_history(dataset, placements, board, config, history, iteration, "force")
        step = max(config.min_step, step * 0.92)

    polished, polish_accepts = _hybrid_baseline_polish(dataset, placements, board, config)
    if polish_accepts:
        placements = polished
        accepted += polish_accepts
        current_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        best_score = current_score
        best_placements = dict(placements)
    _record_history(dataset, placements, board, config, history, config.max_iter, "hybrid_polish")

    postprocessed, postprocess_accepts = _postprocess_layout(dataset, placements, board, config)
    if postprocess_accepts:
        placements = postprocessed
        accepted += postprocess_accepts
        current_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        best_score = current_score
        best_placements = dict(placements)
    _record_history(dataset, placements, board, config, history, config.max_iter, "postprocess")

    delayed, delayed_moves = _place_marked_disconnected_components_near_layout(
        dataset,
        placements,
        board,
        config,
        disconnected_names,
    )
    if delayed_moves:
        placements = delayed
        accepted += delayed_moves
        current_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        best_score = current_score
        best_placements = dict(placements)
    _record_history(dataset, placements, board, config, history, config.max_iter, "disconnected_place")

    centered_final, centered_final_moves = _center_layout_on_board(dataset, placements, board, config)
    if centered_final_moves and _postprocess_is_acceptable(dataset, placements, centered_final, board, config, allow_equal_score=True):
        placements = centered_final
        accepted += centered_final_moves
        current_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        best_score = current_score
        best_placements = dict(placements)
    _record_history(dataset, placements, board, config, history, config.max_iter, "final_center")

    _record_history(dataset, placements, board, config, history, config.max_iter, "final")
    final_score = placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
    if final_score < best_score:
        best_score = final_score
        best_placements = dict(placements)

    return _optimization_result(
        dataset=dataset,
        algorithm=config.algorithm,
        placements=best_placements,
        initial_hpwl=initial_hpwl,
        initial_score=initial_score,
        initial_legality=initial_legality,
        optimized_score=best_score,
        iterations=max(0, config.max_iter),
        accepted_moves=accepted,
        runtime_seconds=time.perf_counter() - started_at,
        board=board,
        min_gap=config.min_gap,
        history=history,
    )


def placement_score(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board | None = None,
    min_gap: float = 2.0,
    legality_weight: float = 100_000.0,
) -> float:
    effective_board = board or infer_board(dataset.components, placements)
    hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    legality = check_layout_legality(
        dataset.components,
        placements,
        dataset.nets,
        board=effective_board,
        min_gap=min_gap,
    )
    return hpwl + legality_weight * legality_penalty(legality, min_gap=min_gap)


def legality_penalty(legality: LegalityResult, min_gap: float = 2.0) -> float:
    penalty = float(len(legality.reference_violations)) * 10_000.0

    for violation in legality.boundary_violations:
        rect = violation.rect
        board = violation.board
        penalty += max(0.0, board.left - rect.left)
        penalty += max(0.0, board.bottom - rect.bottom)
        penalty += max(0.0, rect.right - board.right)
        penalty += max(0.0, rect.top - board.top)

    for violation in legality.gap_violations:
        need_x = max(0.0, min_gap - violation.gap_x)
        need_y = max(0.0, min_gap - violation.gap_y)
        penalty += min(need_x, need_y)

    return penalty


def _random_move(
    placement: Placement,
    component_width: float,
    component_height: float,
    board: Board,
    step: float,
    rng: random.Random,
) -> Placement:
    dx = rng.uniform(-step, step)
    dy = rng.uniform(-step, step)
    x = _clamp(placement.x + dx, board.left, board.right - component_width)
    y = _clamp(placement.y + dy, board.bottom, board.top - component_height)
    return replace(placement, x=x, y=y)


def _random_absolute_placement(
    placement: Placement,
    component_width: float,
    component_height: float,
    board: Board,
    rng: random.Random,
) -> Placement:
    x = rng.uniform(board.left, max(board.left, board.right - component_width))
    y = rng.uniform(board.bottom, max(board.bottom, board.top - component_height))
    return replace(placement, x=x, y=y)


def _smooth_wirelength_gradients(
    dataset: Dataset,
    placements: Dict[str, Placement],
    gamma: float,
) -> dict[str, tuple[float, float]]:
    gradients = {name: (0.0, 0.0) for name in placements if name in dataset.components}
    for net in dataset.nets:
        pins = [
            pin
            for pin in net.pins
            if pin.component in dataset.components and pin.component in placements
        ]
        if len(pins) < 2:
            continue

        xs: list[float] = []
        ys: list[float] = []
        for pin in pins:
            component = dataset.components[pin.component]
            placement = placements[pin.component]
            x, y = pin_position(component, placement, pin.dx, pin.dy)
            xs.append(x)
            ys.append(y)

        max_x = max(xs)
        min_x = min(xs)
        max_y = max(ys)
        min_y = min(ys)
        exp_x_pos = [math.exp(_clamp((x - max_x) / gamma, -50.0, 50.0)) for x in xs]
        exp_x_neg = [math.exp(_clamp((min_x - x) / gamma, -50.0, 50.0)) for x in xs]
        exp_y_pos = [math.exp(_clamp((y - max_y) / gamma, -50.0, 50.0)) for y in ys]
        exp_y_neg = [math.exp(_clamp((min_y - y) / gamma, -50.0, 50.0)) for y in ys]
        sum_x_pos = sum(exp_x_pos) or 1.0
        sum_x_neg = sum(exp_x_neg) or 1.0
        sum_y_pos = sum(exp_y_pos) or 1.0
        sum_y_neg = sum(exp_y_neg) or 1.0

        for index, pin in enumerate(pins):
            gx = exp_x_pos[index] / sum_x_pos - exp_x_neg[index] / sum_x_neg
            gy = exp_y_pos[index] / sum_y_pos - exp_y_neg[index] / sum_y_neg
            old_x, old_y = gradients[pin.component]
            gradients[pin.component] = (old_x + gx, old_y + gy)
    return gradients


def _adaptive_analytical_parameters(
    span: float,
    base_gamma: float,
    learning_rate: float,
    momentum: float,
    legality: LegalityResult,
    improvement: float,
    stagnant_iterations: int,
    config: OptimizationConfig,
) -> tuple[float, float, float, float]:
    violation_count = len(legality.gap_violations) + len(legality.boundary_violations)
    density_weight = 0.65
    gamma = base_gamma
    next_learning_rate = learning_rate
    next_momentum = momentum

    if violation_count:
        density_weight = min(2.2, 0.85 + violation_count * 0.08)
        next_learning_rate = max(config.min_step, learning_rate * 0.82)
        next_momentum = max(0.62, momentum * 0.94)
        gamma = min(span / 18.0, base_gamma * 1.35)
    elif stagnant_iterations >= 8:
        density_weight = 0.9
        next_learning_rate = max(config.min_step, learning_rate * 0.76)
        next_momentum = max(0.66, momentum * 0.96)
        gamma = min(span / 20.0, base_gamma * 1.2)
    elif improvement > 0.01:
        next_learning_rate = min(max(config.min_step, span / 45.0), learning_rate * 1.04)
        next_momentum = min(0.92, momentum + 0.01)
        density_weight = 0.5
        gamma = max(1.0, base_gamma * 0.9)
    else:
        next_momentum = min(0.9, momentum + 0.002)

    return gamma, next_learning_rate, next_momentum, density_weight


def _add_density_and_boundary_gradients(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    gradients: dict[str, tuple[float, float]],
    density_weight: float = 0.65,
) -> None:
    names = [name for name in placements if name in dataset.components and not placements[name].fixed]
    _add_density_grid_gradients(dataset, placements, board, config, gradients, names, density_weight=density_weight)
    repulsion_scale = max(1.0, config.min_gap)
    for index, name in enumerate(names):
        component = dataset.components[name]
        rect = component_rect(component, placements[name])
        gx, gy = gradients.get(name, (0.0, 0.0))
        if rect.left < board.left:
            gx -= (board.left - rect.left) / repulsion_scale
        if rect.right > board.right:
            gx += (rect.right - board.right) / repulsion_scale
        if rect.bottom < board.bottom:
            gy -= (board.bottom - rect.bottom) / repulsion_scale
        if rect.top > board.top:
            gy += (rect.top - board.top) / repulsion_scale

        cx = rect.left + (rect.right - rect.left) / 2
        cy = rect.bottom + (rect.top - rect.bottom) / 2
        for other in names[index + 1 :]:
            other_component = dataset.components[other]
            other_rect = component_rect(other_component, placements[other])
            gap_x = max(other_rect.left - rect.right, rect.left - other_rect.right, 0.0)
            gap_y = max(other_rect.bottom - rect.top, rect.bottom - other_rect.top, 0.0)
            if gap_x >= config.min_gap or gap_y >= config.min_gap:
                continue

            other_cx = other_rect.left + (other_rect.right - other_rect.left) / 2
            other_cy = other_rect.bottom + (other_rect.top - other_rect.bottom) / 2
            dx = cx - other_cx
            dy = cy - other_cy
            if abs(dx) >= abs(dy):
                push = (config.min_gap - gap_x + 1.0) / repulsion_scale
                direction = 1.0 if dx >= 0 else -1.0
                gx += direction * push
                ogx, ogy = gradients.get(other, (0.0, 0.0))
                gradients[other] = (ogx - direction * push, ogy)
            else:
                push = (config.min_gap - gap_y + 1.0) / repulsion_scale
                direction = 1.0 if dy >= 0 else -1.0
                gy += direction * push
                ogx, ogy = gradients.get(other, (0.0, 0.0))
                gradients[other] = (ogx, ogy - direction * push)
        gradients[name] = (gx, gy)


def _add_density_grid_gradients(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    gradients: dict[str, tuple[float, float]],
    movable_names: list[str],
    density_weight: float = 0.65,
) -> None:
    if not movable_names:
        return

    board_width = max(1.0, board.right - board.left)
    board_height = max(1.0, board.top - board.bottom)
    aspect = board_width / board_height
    base_bins = max(4, min(18, int(math.sqrt(len(dataset.components)) * 1.6)))
    bins_x = max(4, min(24, int(base_bins * math.sqrt(aspect))))
    bins_y = max(4, min(24, int(base_bins / max(0.5, math.sqrt(aspect)))))
    bin_w = board_width / bins_x
    bin_h = board_height / bins_y
    bin_area = max(1.0, bin_w * bin_h)

    total_area = sum(component.width * component.height for component in dataset.components.values())
    board_area = max(1.0, board_width * board_height)
    target_density = _clamp((total_area / board_area) * 1.25, 0.25, 0.82)
    density = [[0.0 for _ in range(bins_y)] for _ in range(bins_x)]
    rects = {
        name: component_rect(dataset.components[name], placement)
        for name, placement in placements.items()
        if name in dataset.components
    }

    for rect in rects.values():
        ix0 = max(0, int((rect.left - board.left) / bin_w))
        ix1 = min(bins_x - 1, int((rect.right - board.left) / bin_w))
        iy0 = max(0, int((rect.bottom - board.bottom) / bin_h))
        iy1 = min(bins_y - 1, int((rect.top - board.bottom) / bin_h))
        for ix in range(ix0, ix1 + 1):
            bin_left = board.left + ix * bin_w
            bin_right = bin_left + bin_w
            overlap_x = max(0.0, min(rect.right, bin_right) - max(rect.left, bin_left))
            if overlap_x <= 0:
                continue
            for iy in range(iy0, iy1 + 1):
                bin_bottom = board.bottom + iy * bin_h
                bin_top = bin_bottom + bin_h
                overlap_y = max(0.0, min(rect.top, bin_top) - max(rect.bottom, bin_bottom))
                if overlap_y > 0:
                    density[ix][iy] += (overlap_x * overlap_y) / bin_area

    spread_radius = 1
    for name in movable_names:
        rect = rects[name]
        cx = rect.left + (rect.right - rect.left) / 2
        cy = rect.bottom + (rect.top - rect.bottom) / 2
        ix = max(0, min(bins_x - 1, int((cx - board.left) / bin_w)))
        iy = max(0, min(bins_y - 1, int((cy - board.bottom) / bin_h)))
        gx, gy = gradients.get(name, (0.0, 0.0))
        for bx in range(max(0, ix - spread_radius), min(bins_x, ix + spread_radius + 1)):
            for by in range(max(0, iy - spread_radius), min(bins_y, iy + spread_radius + 1)):
                overflow = density[bx][by] - target_density
                if overflow <= 0:
                    continue
                bin_cx = board.left + (bx + 0.5) * bin_w
                bin_cy = board.bottom + (by + 0.5) * bin_h
                dx = cx - bin_cx
                dy = cy - bin_cy
                distance = max(1.0, math.hypot(dx, dy))
                if distance <= 1.0:
                    dx = (ix - bx) or 1.0
                    dy = (iy - by) or 1.0
                    distance = math.hypot(dx, dy)
                gx += density_weight * overflow * dx / distance
                gy += density_weight * overflow * dy / distance
        gradients[name] = (gx, gy)


def _repair_candidate(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    legality: LegalityResult,
    step: float,
    rng: random.Random,
    config: OptimizationConfig,
) -> Dict[str, Placement]:
    candidate = dict(placements)
    names = _violating_components(legality)
    movable = [name for name in names if name in dataset.components and name in placements and not placements[name].fixed]
    if not movable:
        movable = _movable_names(dataset)
    name = rng.choice(movable)

    if rng.random() < 0.65:
        candidate[name] = _push_from_violations(dataset, placements, name, board, legality, step, rng)
    else:
        candidate[name] = _random_move(
            placement=placements[name],
            component_width=dataset.components[name].width,
            component_height=dataset.components[name].height,
            board=board,
            step=step * 1.5,
            rng=rng,
        )
    return candidate


def _push_from_violations(
    dataset: Dataset,
    placements: Dict[str, Placement],
    name: str,
    board: Board,
    legality: LegalityResult,
    step: float,
    rng: random.Random,
) -> Placement:
    placement = placements[name]
    component = dataset.components[name]
    dx = rng.uniform(-step * 0.2, step * 0.2)
    dy = rng.uniform(-step * 0.2, step * 0.2)

    for violation in legality.gap_violations:
        other = None
        if violation.component_a == name:
            other = violation.component_b
        elif violation.component_b == name:
            other = violation.component_a
        if other is None or other not in placements or other not in dataset.components:
            continue
        other_placement = placements[other]
        other_component = dataset.components[other]
        cx = placement.x + component.width / 2
        cy = placement.y + component.height / 2
        ox = other_placement.x + other_component.width / 2
        oy = other_placement.y + other_component.height / 2
        if abs(cx - ox) >= abs(cy - oy):
            dx += math.copysign(max(2.0, step * 0.35), cx - ox or rng.choice([-1.0, 1.0]))
        else:
            dy += math.copysign(max(2.0, step * 0.35), cy - oy or rng.choice([-1.0, 1.0]))

    x = _clamp(placement.x + dx, board.left, board.right - component.width)
    y = _clamp(placement.y + dy, board.bottom, board.top - component.height)
    return replace(placement, x=x, y=y)


def _net_centroid_move(
    dataset: Dataset,
    placements: Dict[str, Placement],
    name: str,
    board: Board,
    step: float,
    rng: random.Random,
) -> Placement:
    placement = placements[name]
    component = dataset.components[name]
    targets: list[tuple[float, float]] = []
    for net in dataset.nets:
        if not any(pin.component == name for pin in net.pins):
            continue
        for pin in net.pins:
            if pin.component == name or pin.component not in dataset.components or pin.component not in placements:
                continue
            other_component = dataset.components[pin.component]
            other_placement = placements[pin.component]
            targets.append((other_placement.x + other_component.width / 2 + pin.dx, other_placement.y + other_component.height / 2 + pin.dy))

    if not targets:
        return _random_move(placement, component.width, component.height, board, step, rng)

    target_x = sum(point[0] for point in targets) / len(targets) - component.width / 2
    target_y = sum(point[1] for point in targets) / len(targets) - component.height / 2
    dx = _clamp(target_x - placement.x, -step, step) + rng.uniform(-step * 0.15, step * 0.15)
    dy = _clamp(target_y - placement.y, -step, step) + rng.uniform(-step * 0.15, step * 0.15)
    x = _clamp(placement.x + dx, board.left, board.right - component.width)
    y = _clamp(placement.y + dy, board.bottom, board.top - component.height)
    return replace(placement, x=x, y=y)


def _force_directed_move(
    dataset: Dataset,
    placements: Dict[str, Placement],
    name: str,
    board: Board,
    step: float,
    rng: random.Random,
    config: OptimizationConfig,
) -> Placement:
    placement = placements[name]
    component = dataset.components[name]
    kind = _component_kind(dataset, name)
    force_x = 0.0
    force_y = 0.0

    for net in dataset.nets:
        own_pins = [pin for pin in net.pins if pin.component == name]
        if not own_pins:
            continue
        targets: list[tuple[float, float]] = []
        for pin in net.pins:
            if pin.component == name or pin.component not in dataset.components or pin.component not in placements:
                continue
            other_component = dataset.components[pin.component]
            other_placement = placements[pin.component]
            targets.append((other_placement.x + other_component.width / 2 + pin.dx, other_placement.y + other_component.height / 2 + pin.dy))
        if not targets:
            continue
        target_x = sum(point[0] for point in targets) / len(targets)
        target_y = sum(point[1] for point in targets) / len(targets)
        center_x = placement.x + component.width / 2
        center_y = placement.y + component.height / 2
        weight = min(3.0, 1.0 + len(targets) / 4.0)
        force_x += (target_x - center_x) * weight
        force_y += (target_y - center_y) * weight

    center_x = placement.x + component.width / 2
    center_y = placement.y + component.height / 2
    for other_name, other_placement in placements.items():
        if other_name == name or other_name not in dataset.components:
            continue
        other_component = dataset.components[other_name]
        other_center_x = other_placement.x + other_component.width / 2
        other_center_y = other_placement.y + other_component.height / 2
        delta_x = center_x - other_center_x
        delta_y = center_y - other_center_y
        distance_sq = max(delta_x * delta_x + delta_y * delta_y, 1.0)
        min_distance = (max(component.width, component.height) + max(other_component.width, other_component.height)) / 2.0 + config.min_gap
        if distance_sq < min_distance * min_distance:
            scale = (min_distance * min_distance - distance_sq) / distance_sq
            force_x += delta_x * scale * 2.0
            force_y += delta_y * scale * 2.0

    if kind == "large":
        max_step = max(config.min_step, step * 0.25)
    elif kind == "passive":
        max_step = step * 1.25
    else:
        max_step = step * 0.8

    dx = _clamp(force_x * 0.08, -max_step, max_step) + rng.uniform(-max_step * 0.08, max_step * 0.08)
    dy = _clamp(force_y * 0.08, -max_step, max_step) + rng.uniform(-max_step * 0.08, max_step * 0.08)
    x = _clamp(placement.x + dx, board.left, board.right - component.width)
    y = _clamp(placement.y + dy, board.bottom, board.top - component.height)
    return replace(placement, x=x, y=y)


def _grid_legalize(dataset: Dataset, placements: Dict[str, Placement], board: Board, config: OptimizationConfig) -> Dict[str, Placement]:
    ordered = _legalization_order(dataset, placements)
    placed: Dict[str, Placement] = {}
    result = dict(placements)

    for name in ordered:
        if name not in dataset.components or name not in placements:
            continue
        original = placements[name]
        component = dataset.components[name]
        if original.fixed:
            placed[name] = original
            result[name] = original
            continue
        if _fits_against_placed(dataset, original, component, placed, board, config.min_gap):
            placed[name] = original
            result[name] = original
            continue

        replacement = _find_grid_slot(dataset, result, placed, name, board, config)
        placed[name] = replacement
        result[name] = replacement

    for _ in range(2):
        legality = check_layout_legality(dataset.components, result, dataset.nets, board=board, min_gap=config.min_gap)
        if legality.is_legal:
            break
        current_rank = _repair_rank(dataset, result, board, config)
        moved = False
        for name in _legalization_repair_order(dataset, result, legality):
            if name not in dataset.components or name not in result or result[name].fixed:
                continue
            placed_without = {
                other_name: other_placement
                for other_name, other_placement in result.items()
                if other_name != name and other_name in dataset.components
            }
            replacement = _find_grid_slot(dataset, result, placed_without, name, board, config)
            if replacement == result[name]:
                continue
            candidate = dict(result)
            candidate[name] = replacement
            candidate_rank = _repair_rank(dataset, candidate, board, config)
            if candidate_rank <= current_rank:
                result = candidate
                current_rank = candidate_rank
                moved = True
        if not moved:
            break

    return result


def _local_hpwl_slot_refinement(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    passes: int = 2,
) -> tuple[Dict[str, Placement], int]:
    result = dict(placements)
    accepted = 0
    current_hpwl = total_hpwl(dataset.nets, dataset.components, result)
    order = [name for name in _movement_order(dataset, result) if _component_kind(dataset, name) != "large"]
    for _ in range(max(1, passes)):
        improved = False
        for name in order:
            if name not in dataset.components or name not in result or result[name].fixed:
                continue
            component = dataset.components[name]
            target = _component_net_centroid(dataset, result, name)
            if target is None:
                continue
            target_xy = (target[0] - component.width / 2, target[1] - component.height / 2)
            placed = {other: placement for other, placement in result.items() if other != name}
            candidates = _local_grid_candidates(component, target_xy[0], target_xy[1], board, config)
            candidates.extend(_local_grid_candidates(component, result[name].x, result[name].y, board, config)[:12])
            candidate = _best_slot_near_target(
                dataset=dataset,
                original=result[name],
                component=component,
                placed=placed,
                board=board,
                config=config,
                target=target_xy,
                extra_candidates=candidates,
            )
            if candidate == result[name]:
                continue
            trial = dict(result)
            trial[name] = candidate
            legality = check_layout_legality(dataset.components, trial, dataset.nets, board=board, min_gap=config.min_gap)
            if not legality.is_legal:
                continue
            hpwl = total_hpwl(dataset.nets, dataset.components, trial)
            if hpwl + 1e-9 < current_hpwl:
                result = trial
                current_hpwl = hpwl
                accepted += 1
                improved = True
        if not improved:
            break
    return result, accepted


def _postprocess_layout(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
) -> tuple[Dict[str, Placement], int]:
    result = dict(placements)
    accepted = 0

    with_isolated, isolated_moves = _place_isolated_components_near_cluster(dataset, result, board, config)
    if isolated_moves and _postprocess_is_acceptable(dataset, result, with_isolated, board, config):
        result = with_isolated
        accepted += isolated_moves

    centered, centered_moves = _center_layout_on_board(dataset, result, board, config)
    if centered_moves and _postprocess_is_acceptable(dataset, result, centered, board, config, allow_equal_score=True):
        result = centered
        accepted += centered_moves

    return result, accepted


def _place_isolated_components_near_cluster(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
) -> tuple[Dict[str, Placement], int]:
    degrees = _component_degrees(dataset)
    graph_groups = _component_graph_groups(dataset)
    group_size = {
        name: len(group)
        for group in graph_groups
        for name in group
    }
    main_group = max(graph_groups, key=len, default=set())
    isolated = [
        name
        for name in sorted(placements)
        if name in dataset.components
        and not placements[name].fixed
        and (
            degrees.get(name, 0) <= 1
            or group_size.get(name, 1) == 1
            or (name not in main_group and group_size.get(name, 1) <= 2)
            or _component_net_centroid(dataset, placements, name) is None
        )
    ]
    if not isolated:
        return _pull_far_outliers_to_cluster(dataset, placements, board, config)

    anchor_names = [
        name
        for name in placements
        if name in dataset.components and name not in isolated
    ]
    if not anchor_names:
        return placements, 0

    result = dict(placements)
    placed = {name: placement for name, placement in result.items() if name not in isolated and name in dataset.components}
    bbox = _placement_bbox(dataset, placed)
    moved = 0
    for name in isolated:
        component = dataset.components[name]
        original = result[name]
        candidates = _cluster_perimeter_candidates(component, bbox, board, config)
        target = (bbox[2] + config.min_gap, bbox[1])
        candidate = _best_slot_near_target(
            dataset=dataset,
            original=original,
            component=component,
            placed=placed,
            board=board,
            config=config,
            target=target,
            extra_candidates=candidates,
        )
        if candidate != original:
            result[name] = candidate
            placed[name] = candidate
            bbox = _placement_bbox(dataset, placed)
            moved += 1

    result, outlier_moves = _pull_far_outliers_to_cluster(dataset, result, board, config)
    return result, moved + outlier_moves


def _structured_seed_placement(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    rng: random.Random,
) -> tuple[Dict[str, Placement], int]:
    result = dict(placements)
    accepted = 0

    structured = _pin_aware_initial_placement(dataset, result, board, config, rng)
    if _structured_seed_is_better(dataset, result, structured, board, config):
        result = structured
        accepted += 1

    legalized = _grid_legalize(dataset, result, board, config)
    if _structured_seed_is_better(dataset, result, legalized, board, config):
        result = legalized
        accepted += 1

    refined, refine_accepts = _local_hpwl_slot_refinement(dataset, result, board, config, passes=1)
    if refine_accepts and _structured_seed_is_better(dataset, result, refined, board, config):
        result = refined
        accepted += refine_accepts

    finalized, finalize_moves = _finalize_structured_seeded_result(dataset, result, board, config)
    if finalize_moves:
        result = finalized
        accepted += finalize_moves
    return result, accepted


def _finalize_structured_seeded_result(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
) -> tuple[Dict[str, Placement], int]:
    result = dict(placements)
    accepted = 0

    disconnected = _disconnected_component_names(dataset, dataset.placements)
    with_disconnected, disconnected_moves = _place_marked_disconnected_components_near_layout(
        dataset,
        result,
        board,
        config,
        disconnected,
    )
    if disconnected_moves and _postprocess_is_acceptable(dataset, result, with_disconnected, board, config, allow_equal_score=True):
        result = with_disconnected
        accepted += disconnected_moves

    centered, centered_moves = _center_layout_on_board(dataset, result, board, config)
    if centered_moves and _postprocess_is_acceptable(dataset, result, centered, board, config, allow_equal_score=True):
        result = centered
        accepted += centered_moves
    return result, accepted


def _structured_seed_is_better(
    dataset: Dataset,
    before: Dict[str, Placement],
    after: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
) -> bool:
    before_rank = _repair_rank(dataset, before, board, config)
    after_rank = _repair_rank(dataset, after, board, config)
    if after_rank < before_rank:
        return True
    if after_rank > before_rank:
        return False
    before_score = placement_score(dataset, before, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
    after_score = placement_score(dataset, after, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
    return after_score <= before_score + max(1e-6, abs(before_score) * 0.02)


def _hybrid_baseline_polish(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
) -> tuple[Dict[str, Placement], int]:
    """Use annealing/random baselines as a conservative legal HPWL polish pass."""
    movable = [
        name
        for name, placement in placements.items()
        if name in dataset.components and not placement.fixed
    ]
    if not movable:
        return placements, 0

    rng = random.Random(config.seed + 97)
    current = dict(placements)
    current_hpwl = total_hpwl(dataset.nets, dataset.components, current)
    current_score = placement_score(dataset, current, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
    best = dict(current)
    best_hpwl = current_hpwl
    accepted = 0

    base_step = config.initial_step if config.initial_step is not None else _default_step(board)
    step = max(config.min_step, base_step * 0.12)
    temperature = config.initial_temperature if config.initial_temperature is not None else max(1.0, current_hpwl * 0.01)
    cooling_rate = _clamp(config.cooling_rate, 0.92, 0.9995)
    anneal_iter = max(50, config.max_iter // 6)
    random_iter = max(30, config.max_iter // 12)

    for _ in range(anneal_iter):
        name = rng.choice(movable)
        candidate = dict(current)
        candidate[name] = _random_move(
            placement=current[name],
            component_width=dataset.components[name].width,
            component_height=dataset.components[name].height,
            board=board,
            step=step,
            rng=rng,
        )
        legality = check_layout_legality(dataset.components, candidate, dataset.nets, board=board, min_gap=config.min_gap)
        if not legality.is_legal:
            temperature = max(1e-6, temperature * cooling_rate)
            continue
        hpwl = total_hpwl(dataset.nets, dataset.components, candidate)
        delta = hpwl - current_hpwl
        if delta < 0 or rng.random() < math.exp(-delta / max(temperature, 1e-9)):
            current = candidate
            current_hpwl = hpwl
            current_score = placement_score(dataset, current, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
            accepted += 1
            if hpwl < best_hpwl:
                best = dict(candidate)
                best_hpwl = hpwl
        temperature = max(1e-6, temperature * cooling_rate)

    cluster_bbox = _placement_bbox(dataset, best)
    for _ in range(random_iter):
        name = rng.choice(movable)
        component = dataset.components[name]
        original = best[name]
        if rng.random() < 0.7:
            candidate_placement = _random_move(
                placement=original,
                component_width=component.width,
                component_height=component.height,
                board=board,
                step=step * 2.0,
                rng=rng,
            )
        else:
            x_low = max(board.left, cluster_bbox[0] - step)
            x_high = min(board.right - component.width, cluster_bbox[2] + step)
            y_low = max(board.bottom, cluster_bbox[1] - step)
            y_high = min(board.top - component.height, cluster_bbox[3] + step)
            if x_high < x_low or y_high < y_low:
                continue
            x = rng.uniform(x_low, x_high)
            y = rng.uniform(y_low, y_high)
            candidate_placement = replace(original, x=x, y=y)

        candidate = dict(best)
        candidate[name] = candidate_placement
        legality = check_layout_legality(dataset.components, candidate, dataset.nets, board=board, min_gap=config.min_gap)
        if not legality.is_legal:
            continue
        hpwl = total_hpwl(dataset.nets, dataset.components, candidate)
        if hpwl + 1e-9 < best_hpwl:
            best = candidate
            best_hpwl = hpwl
            current_score = placement_score(dataset, best, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
            cluster_bbox = _placement_bbox(dataset, best)
            accepted += 1

    if best_hpwl + 1e-9 < total_hpwl(dataset.nets, dataset.components, placements):
        return best, accepted
    if current_score < placement_score(dataset, placements, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight):
        return best, accepted
    return placements, 0


def _disconnected_component_names(dataset: Dataset, placements: Dict[str, Placement]) -> list[str]:
    neighbors = _component_neighbors(dataset)
    return [
        name
        for name in sorted(placements)
        if name in dataset.components
        and not placements[name].fixed
        and not neighbors.get(name)
    ]


def _component_neighbors(dataset: Dataset) -> dict[str, set[str]]:
    neighbors: dict[str, set[str]] = {name: set() for name in dataset.components}
    for net in dataset.nets:
        names = sorted({pin.component for pin in net.pins if pin.component in dataset.components})
        if len(names) < 2:
            continue
        for index, name in enumerate(names):
            neighbors[name].update(names[:index])
            neighbors[name].update(names[index + 1 :])
    return neighbors


def _place_marked_disconnected_components_near_layout(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    disconnected_names: list[str],
) -> tuple[Dict[str, Placement], int]:
    names = [
        name
        for name in sorted(set(disconnected_names))
        if name in dataset.components
        and name in placements
        and not placements[name].fixed
    ]
    if not names:
        return placements, 0

    anchor_names = [
        name
        for name in placements
        if name in dataset.components and name not in names
    ]
    if not anchor_names:
        return placements, 0

    result = dict(placements)
    placed = {name: result[name] for name in anchor_names}
    bbox = _placement_bbox(dataset, placed)
    moved = 0

    names.sort(key=lambda name: dataset.components[name].width * dataset.components[name].height, reverse=True)
    for name in names:
        component = dataset.components[name]
        original = result[name]
        target = _nearest_bbox_perimeter_target(component, _component_center(dataset, name, original), bbox, board)
        candidates = _expanded_cluster_blank_candidates(component, bbox, board, config)
        candidate = _best_legal_slot_near_target(
            dataset=dataset,
            original=original,
            component=component,
            placed=placed,
            board=board,
            config=config,
            target=target,
            candidates=candidates,
        )
        if candidate is None:
            continue
        result[name] = candidate
        placed[name] = candidate
        bbox = _placement_bbox(dataset, placed)
        if candidate != original:
            moved += 1
    return result, moved


def _best_legal_slot_near_target(
    dataset: Dataset,
    original: Placement,
    component,
    placed: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    target: tuple[float, float],
    candidates: list[tuple[float, float]],
) -> Placement | None:
    target_x, target_y = target
    best: Placement | None = None
    best_cost = float("inf")
    for x, y in _candidate_slots([(target_x, target_y), *candidates], component, board):
        candidate = replace(original, x=x, y=y)
        if not _fits_against_placed(dataset, candidate, component, placed, board, config.min_gap):
            continue
        cost = abs(x - target_x) + abs(y - target_y)
        if cost < best_cost:
            best = candidate
            best_cost = cost
    return best


def _expanded_cluster_blank_candidates(component, bbox: tuple[float, float, float, float], board: Board, config: OptimizationConfig) -> list[tuple[float, float]]:
    left, bottom, right, top = bbox
    gap = max(config.min_gap, 2.0)
    step_x = max(component.width + gap, 4.0)
    step_y = max(component.height + gap, 4.0)
    max_ring = int(max(board.right - board.left, board.top - board.bottom) / max(step_x, step_y)) + 4
    candidates: list[tuple[float, float]] = []
    for ring in range(max_ring + 1):
        offset_x = gap + ring * step_x
        offset_y = gap + ring * step_y
        x_values = _scan_values(left - offset_x, right + offset_x, step_x)
        y_values = _scan_values(bottom - offset_y, top + offset_y, step_y)
        for y in y_values:
            candidates.append((right + offset_x, y))
            candidates.append((left - component.width - offset_x, y))
        for x in x_values:
            candidates.append((x, top + offset_y))
            candidates.append((x, bottom - component.height - offset_y))
    return candidates


def _component_graph_groups(dataset: Dataset) -> list[set[str]]:
    neighbors = _component_neighbors(dataset)
    groups: list[set[str]] = []
    remaining = set(neighbors)
    while remaining:
        start = remaining.pop()
        group = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in neighbors[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    group.add(neighbor)
                    stack.append(neighbor)
        groups.append(group)
    return groups


def _pull_far_outliers_to_cluster(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
) -> tuple[Dict[str, Placement], int]:
    movable = [
        name
        for name, placement in placements.items()
        if name in dataset.components and not placement.fixed
    ]
    if len(movable) < 4:
        return placements, 0

    centers = {name: _component_center(dataset, name, placements[name]) for name in movable}
    board_span = max(board.right - board.left, board.top - board.bottom)
    far_threshold = max(board_span * 0.18, config.min_gap * 8)

    outliers: list[str] = []
    for name in movable:
        nearest = min(
            (
                math.hypot(centers[name][0] - centers[other][0], centers[name][1] - centers[other][1])
                for other in movable
                if other != name
            ),
            default=0.0,
        )
        if nearest > far_threshold:
            outliers.append(name)

    if not outliers:
        return placements, 0

    anchors = [name for name in movable if name not in outliers]
    if not anchors:
        return placements, 0

    result = dict(placements)
    placed = {
        name: placement
        for name, placement in result.items()
        if name in dataset.components and name not in outliers
    }
    bbox = _placement_bbox(dataset, {name: result[name] for name in anchors})
    moved = 0
    for name in sorted(outliers):
        component = dataset.components[name]
        original = result[name]
        candidates = _cluster_perimeter_candidates(component, bbox, board, config)
        target = _nearest_bbox_perimeter_target(component, centers[name], bbox, board)
        candidate = _best_slot_near_target(
            dataset=dataset,
            original=original,
            component=component,
            placed=placed,
            board=board,
            config=config,
            target=target,
            extra_candidates=candidates,
        )
        if candidate != original:
            result[name] = candidate
            placed[name] = candidate
            bbox = _placement_bbox(dataset, {item: result[item] for item in anchors + sorted(outliers) if item in result})
            moved += 1
    return result, moved


def _component_center(dataset: Dataset, name: str, placement: Placement) -> tuple[float, float]:
    component = dataset.components[name]
    width, height = oriented_size(component, placement)
    return placement.x + width / 2, placement.y + height / 2


def _nearest_bbox_perimeter_target(
    component,
    center: tuple[float, float],
    bbox: tuple[float, float, float, float],
    board: Board,
) -> tuple[float, float]:
    left, bottom, right, top = bbox
    cx, cy = center
    target_x = _clamp(cx, left, right)
    target_y = _clamp(cy, bottom, top)
    distances = [
        (abs(cx - left), left - component.width, target_y),
        (abs(cx - right), right, target_y),
        (abs(cy - bottom), target_x, bottom - component.height),
        (abs(cy - top), target_x, top),
    ]
    _, x, y = min(distances, key=lambda item: item[0])
    return (
        _clamp(x, board.left, board.right - component.width),
        _clamp(y, board.bottom, board.top - component.height),
    )


def _center_layout_on_board(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
) -> tuple[Dict[str, Placement], int]:
    movable = [
        name
        for name, placement in placements.items()
        if name in dataset.components and not placement.fixed
    ]
    fixed = [
        name
        for name, placement in placements.items()
        if name in dataset.components and placement.fixed
    ]
    if not movable or fixed:
        return placements, 0

    bbox = _placement_bbox(dataset, {name: placements[name] for name in movable})
    left, bottom, right, top = bbox
    layout_width = right - left
    layout_height = top - bottom
    if layout_width <= 0 or layout_height <= 0:
        return placements, 0

    target_left = board.left + ((board.right - board.left) - layout_width) / 2
    target_bottom = board.bottom + ((board.top - board.bottom) - layout_height) / 2
    dx = target_left - left
    dy = target_bottom - bottom
    dx = _clamp(dx, board.left - left, board.right - right)
    dy = _clamp(dy, board.bottom - bottom, board.top - top)
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return placements, 0

    result = dict(placements)
    for name in movable:
        result[name] = replace(placements[name], x=placements[name].x + dx, y=placements[name].y + dy)
    return result, len(movable)


def _postprocess_is_acceptable(
    dataset: Dataset,
    before: Dict[str, Placement],
    after: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    allow_equal_score: bool = False,
) -> bool:
    legality = check_layout_legality(dataset.components, after, dataset.nets, board=board, min_gap=config.min_gap)
    if not legality.is_legal:
        return False
    before_score = placement_score(dataset, before, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
    after_score = placement_score(dataset, after, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
    if allow_equal_score:
        return after_score <= before_score + 1e-6
    return after_score <= before_score + max(1e-6, abs(before_score) * 1e-9)


def _placement_bbox(dataset: Dataset, placements: Dict[str, Placement]) -> tuple[float, float, float, float]:
    rects = [
        component_rect(dataset.components[name], placement)
        for name, placement in placements.items()
        if name in dataset.components
    ]
    if not rects:
        return 0.0, 0.0, 0.0, 0.0
    return (
        min(rect.left for rect in rects),
        min(rect.bottom for rect in rects),
        max(rect.right for rect in rects),
        max(rect.top for rect in rects),
    )


def _cluster_perimeter_candidates(component, bbox: tuple[float, float, float, float], board: Board, config: OptimizationConfig) -> list[tuple[float, float]]:
    left, bottom, right, top = bbox
    gap = max(config.min_gap, 2.0)
    step_x = max(component.width + gap, 4.0)
    step_y = max(component.height + gap, 4.0)
    candidates: list[tuple[float, float]] = []
    x_values = _scan_values(left - step_x, right + step_x, step_x)
    y_values = _scan_values(bottom - step_y, top + step_y, step_y)
    for ring in range(4):
        offset_x = gap + ring * step_x
        offset_y = gap + ring * step_y
        for y in y_values:
            candidates.append((_clamp(right + offset_x, board.left, board.right - component.width), _clamp(y, board.bottom, board.top - component.height)))
            candidates.append((_clamp(left - component.width - offset_x, board.left, board.right - component.width), _clamp(y, board.bottom, board.top - component.height)))
        for x in x_values:
            candidates.append((_clamp(x, board.left, board.right - component.width), _clamp(top + offset_y, board.bottom, board.top - component.height)))
            candidates.append((_clamp(x, board.left, board.right - component.width), _clamp(bottom - component.height - offset_y, board.bottom, board.top - component.height)))
    return candidates


def _pin_aware_initial_placement(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    rng: random.Random,
) -> Dict[str, Placement]:
    """Build a PCB-like starting point: compact macros, then slot passives near macro pins."""
    result = dict(placements)
    movable = {name for name in _movable_names(dataset)}
    macro_names = [
        name
        for name in _macro_order(dataset, placements)
        if name in movable and name in dataset.components and name in placements
    ]
    if not macro_names:
        return result

    placed: Dict[str, Placement] = {
        name: placement
        for name, placement in placements.items()
        if name in dataset.components and (placement.fixed or name not in movable)
    }

    macro_targets = _compact_macro_targets(dataset, placements, macro_names, board)
    for name in macro_names:
        component = dataset.components[name]
        original = placements[name]
        target_x, target_y = macro_targets.get(name, (original.x, original.y))
        candidate = _best_slot_near_target(
            dataset=dataset,
            original=original,
            component=component,
            placed=placed,
            board=board,
            config=config,
            target=(target_x, target_y),
            extra_candidates=_macro_slot_candidates(component, target_x, target_y, board, config),
        )
        result[name] = candidate
        placed[name] = candidate

    assignments = _macro_pin_assignments(dataset, result, macro_names)
    clustered = _place_small_net_clusters(dataset, result, placed, macro_names, assignments, board, config)
    non_macros = [
        name
        for name in _movement_order(dataset, result)
        if name not in macro_names and name not in clustered and name in movable and name in dataset.components and name in result
    ]
    matched = _place_assigned_components_by_slot_matching(
        dataset=dataset,
        result=result,
        placed=placed,
        names=[name for name in non_macros if name in assignments],
        assignments=assignments,
        board=board,
        config=config,
    )
    for name in non_macros:
        if name in matched:
            continue
        component = dataset.components[name]
        original = result[name]
        assignment = assignments.get(name)
        if assignment is None:
            centroid = _component_net_centroid(dataset, result, name)
            if centroid is None:
                target = (original.x, original.y)
                extra_candidates: list[tuple[float, float]] = []
            else:
                target = (centroid[0] - component.width / 2, centroid[1] - component.height / 2)
                extra_candidates = _local_grid_candidates(component, target[0], target[1], board, config)
        else:
            macro_name, side, target_pin = assignment
            macro_component = dataset.components[macro_name]
            macro_placement = result[macro_name]
            target = (target_pin[0] - component.width / 2, target_pin[1] - component.height / 2)
            extra_candidates = _pin_side_slot_candidates(
                component=component,
                macro_component=macro_component,
                macro_placement=macro_placement,
                side=side,
                board=board,
                config=config,
                target_pin=target_pin,
            )
        candidate = _best_slot_near_target(
            dataset=dataset,
            original=original,
            component=component,
            placed=placed,
            board=board,
            config=config,
            target=target,
            extra_candidates=extra_candidates,
        )
        result[name] = candidate
        placed[name] = candidate

    result, _ = _slot_swap_refinement(dataset, result, board, config, macro_names, assignments, passes=1)
    return result


def _macro_order(dataset: Dataset, placements: Dict[str, Placement]) -> list[str]:
    degrees = _component_degrees(dataset)
    return sorted(
        [
            name
            for name in placements
            if name in dataset.components and _component_kind(dataset, name) == "large"
        ],
        key=lambda name: (-degrees.get(name, 0), -(dataset.components[name].width * dataset.components[name].height), name),
    )


def _compact_macro_targets(
    dataset: Dataset,
    placements: Dict[str, Placement],
    macro_names: list[str],
    board: Board,
) -> dict[str, tuple[float, float]]:
    if 2 <= len(macro_names) <= 8:
        searched = _best_macro_topology_targets(dataset, placements, macro_names, board)
        if searched:
            return searched

    centers = [
        (
            placements[name].x + dataset.components[name].width / 2,
            placements[name].y + dataset.components[name].height / 2,
        )
        for name in macro_names
    ]
    centroid_x = sum(point[0] for point in centers) / len(centers)
    centroid_y = sum(point[1] for point in centers) / len(centers)

    max_macro_span = max(max(dataset.components[name].width, dataset.components[name].height) for name in macro_names)
    scale = 0.18 if len(macro_names) >= 3 else 0.35
    max_offset = max_macro_span * max(1.4, len(macro_names) ** 0.5)

    targets: dict[str, tuple[float, float]] = {}
    for name, (center_x, center_y) in zip(macro_names, centers):
        component = dataset.components[name]
        target_center_x = centroid_x + _clamp((center_x - centroid_x) * scale, -max_offset, max_offset)
        target_center_y = centroid_y + _clamp((center_y - centroid_y) * scale, -max_offset, max_offset)
        targets[name] = (
            _clamp(target_center_x - component.width / 2, board.left, board.right - component.width),
            _clamp(target_center_y - component.height / 2, board.bottom, board.top - component.height),
        )
    return targets


def _best_macro_topology_targets(
    dataset: Dataset,
    placements: Dict[str, Placement],
    macro_names: list[str],
    board: Board,
) -> dict[str, tuple[float, float]]:
    candidates = _macro_topology_target_candidates(dataset, placements, macro_names, board)
    if not candidates:
        return {}
    best_targets = candidates[0]
    best_score = float("inf")
    for targets in candidates:
        trial = dict(placements)
        for name, (x, y) in targets.items():
            trial[name] = replace(placements[name], x=x, y=y)
        legality = check_layout_legality(dataset.components, trial, dataset.nets, board=board, min_gap=2.0)
        macro_bbox = _placement_bbox(dataset, {name: trial[name] for name in macro_names})
        compact_penalty = (macro_bbox[2] - macro_bbox[0]) + (macro_bbox[3] - macro_bbox[1])
        shape_penalty = _macro_topology_shape_penalty(dataset, trial, macro_names)
        score = (
            total_hpwl(dataset.nets, dataset.components, trial)
            + 10_000.0 * len(legality.gap_violations)
            + 2.5 * compact_penalty
            + shape_penalty
        )
        if score < best_score:
            best_score = score
            best_targets = targets
    return best_targets


def _macro_topology_target_candidates(
    dataset: Dataset,
    placements: Dict[str, Placement],
    macro_names: list[str],
    board: Board,
) -> list[dict[str, tuple[float, float]]]:
    centers = [
        (
            placements[name].x + dataset.components[name].width / 2,
            placements[name].y + dataset.components[name].height / 2,
        )
        for name in macro_names
    ]
    centroid_x = sum(point[0] for point in centers) / len(centers)
    centroid_y = sum(point[1] for point in centers) / len(centers)
    max_w = max(dataset.components[name].width for name in macro_names)
    max_h = max(dataset.components[name].height for name in macro_names)
    pitch_x = max_w + max(16.0, max_w * 0.22)
    pitch_y = max_h + max(16.0, max_h * 0.22)
    if len(macro_names) <= 6:
        orders = [list(order) for order in itertools.permutations(macro_names)]
    else:
        orders = [_macro_topology_order(macro_names), sorted(macro_names, key=_natural_macro_key)]
    unique_orders: list[list[str]] = []
    for order in orders:
        if order not in unique_orders:
            unique_orders.append(order)

    slot_sets: list[list[tuple[float, float]]] = []
    if len(macro_names) == 2:
        slot_sets = [
            [(-pitch_x / 2, 0.0), (pitch_x / 2, 0.0)],
            [(0.0, -pitch_y / 2), (0.0, pitch_y / 2)],
            [(-pitch_x * 0.35, pitch_y * 0.35), (pitch_x * 0.35, -pitch_y * 0.35)],
        ]
    elif len(macro_names) == 3:
        slot_sets = [
            [(-pitch_x * 0.62, pitch_y * 0.28), (pitch_x * 0.62, pitch_y * 0.28), (0.0, -pitch_y * 0.72)],
            [(-pitch_x * 0.62, -pitch_y * 0.28), (pitch_x * 0.62, -pitch_y * 0.28), (0.0, pitch_y * 0.72)],
            [(-pitch_x * 0.72, 0.0), (0.0, pitch_y * 0.5), (pitch_x * 0.72, 0.0)],
            [(-pitch_x * 0.72, 0.0), (0.0, -pitch_y * 0.5), (pitch_x * 0.72, 0.0)],
            [(-pitch_x, 0.0), (0.0, 0.0), (pitch_x, 0.0)],
            [(0.0, -pitch_y), (0.0, 0.0), (0.0, pitch_y)],
        ]
    elif len(macro_names) == 4:
        slot_sets = [
            [(-pitch_x * 0.58, pitch_y * 0.42), (pitch_x * 0.58, pitch_y * 0.42), (-pitch_x * 0.58, -pitch_y * 0.42), (pitch_x * 0.58, -pitch_y * 0.42)],
            [(-pitch_x * 1.1, 0.0), (-pitch_x * 0.35, 0.0), (pitch_x * 0.35, 0.0), (pitch_x * 1.1, 0.0)],
            [(0.0, -pitch_y * 1.1), (0.0, -pitch_y * 0.35), (0.0, pitch_y * 0.35), (0.0, pitch_y * 1.1)],
        ]
    else:
        slot_sets = _macro_grid_slot_sets(len(macro_names), pitch_x, pitch_y)

    expanded_slot_sets: list[list[tuple[float, float]]] = []
    seen_slot_sets: set[tuple[tuple[float, float], ...]] = set()
    for slots in slot_sets:
        for swap_xy in (False, True):
            for sign_x, sign_y in ((1.0, 1.0), (-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0)):
                transformed = [
                    (sign_x * (offset_y if swap_xy else offset_x), sign_y * (offset_x if swap_xy else offset_y))
                    for offset_x, offset_y in slots
                ]
                key = tuple((round(x, 6), round(y, 6)) for x, y in transformed)
                if key in seen_slot_sets:
                    continue
                seen_slot_sets.add(key)
                expanded_slot_sets.append(transformed)

    candidates: list[dict[str, tuple[float, float]]] = []
    for order in unique_orders:
        for slots in expanded_slot_sets:
            targets: dict[str, tuple[float, float]] = {}
            for name, (offset_x, offset_y) in zip(order, slots):
                component = dataset.components[name]
                center_x = centroid_x + offset_x
                center_y = centroid_y + offset_y
                targets[name] = (
                    _clamp(center_x - component.width / 2, board.left, board.right - component.width),
                    _clamp(center_y - component.height / 2, board.bottom, board.top - component.height),
                )
            candidates.append(targets)
    candidates.append(_compact_macro_topology_targets(dataset, placements, macro_names, board))
    return candidates


def _macro_grid_slot_sets(count: int, pitch_x: float, pitch_y: float) -> list[list[tuple[float, float]]]:
    columns = math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns)
    grid: list[tuple[float, float]] = []
    for row in range(rows):
        for col in range(columns):
            if len(grid) >= count:
                break
            grid.append(((col - (columns - 1) / 2) * pitch_x, ((rows - 1) / 2 - row) * pitch_y))

    ring_radius_x = pitch_x * max(1.0, count / 5.0)
    ring_radius_y = pitch_y * max(1.0, count / 5.0)
    ring = [
        (
            math.cos(2 * math.pi * index / count) * ring_radius_x,
            math.sin(2 * math.pi * index / count) * ring_radius_y,
        )
        for index in range(count)
    ]

    row_slots = [((index - (count - 1) / 2) * pitch_x, 0.0) for index in range(count)]
    col_slots = [(0.0, ((count - 1) / 2 - index) * pitch_y) for index in range(count)]
    return [grid, ring, row_slots, col_slots]


def _macro_topology_shape_penalty(
    dataset: Dataset,
    placements: Dict[str, Placement],
    macro_names: list[str],
) -> float:
    centers = [_component_center(dataset, name, placements[name]) for name in macro_names]
    if len(centers) < 2:
        return 0.0
    xs = [point[0] for point in centers]
    ys = [point[1] for point in centers]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    span = max(width, height, 1.0)
    short_span = min(width, height)
    aspect_penalty = max(0.0, span / max(short_span, 1.0) - 2.4) * span
    if len(centers) == 3:
        area2 = abs(
            centers[0][0] * (centers[1][1] - centers[2][1])
            + centers[1][0] * (centers[2][1] - centers[0][1])
            + centers[2][0] * (centers[0][1] - centers[1][1])
        )
        triangle_area_ratio = area2 / max(span * span, 1.0)
        if triangle_area_ratio < 0.16:
            aspect_penalty += (0.16 - triangle_area_ratio) * span * 9.0
    return aspect_penalty


def _compact_macro_topology_targets(
    dataset: Dataset,
    placements: Dict[str, Placement],
    macro_names: list[str],
    board: Board,
) -> dict[str, tuple[float, float]]:
    centers = [
        (
            placements[name].x + dataset.components[name].width / 2,
            placements[name].y + dataset.components[name].height / 2,
        )
        for name in macro_names
    ]
    centroid_x = sum(point[0] for point in centers) / len(centers)
    centroid_y = sum(point[1] for point in centers) / len(centers)
    max_w = max(dataset.components[name].width for name in macro_names)
    max_h = max(dataset.components[name].height for name in macro_names)
    pitch_x = max_w + max(16.0, max_w * 0.22)
    pitch_y = max_h + max(16.0, max_h * 0.22)

    if len(macro_names) == 2:
        slots = [(-pitch_x / 2, 0.0), (pitch_x / 2, 0.0)]
    elif len(macro_names) == 3:
        slots = [(-pitch_x * 0.62, pitch_y * 0.28), (pitch_x * 0.62, pitch_y * 0.28), (0.0, -pitch_y * 0.72)]
    else:
        slots = [(-pitch_x * 0.58, pitch_y * 0.42), (pitch_x * 0.58, pitch_y * 0.42), (-pitch_x * 0.58, -pitch_y * 0.42), (pitch_x * 0.58, -pitch_y * 0.42)]

    ordered = _macro_topology_order(macro_names)
    targets: dict[str, tuple[float, float]] = {}
    for name, (offset_x, offset_y) in zip(ordered, slots):
        component = dataset.components[name]
        center_x = centroid_x + offset_x
        center_y = centroid_y + offset_y
        targets[name] = (
            _clamp(center_x - component.width / 2, board.left, board.right - component.width),
            _clamp(center_y - component.height / 2, board.bottom, board.top - component.height),
        )
    return targets


def _macro_topology_order(macro_names: list[str]) -> list[str]:
    natural = sorted(macro_names, key=_natural_macro_key)
    if len(natural) == 3 and all(name.upper().startswith("U") for name in natural):
        # Common PCB topology for a three-ASIC cluster: two upper anchors and one lower bridge.
        return [natural[1], natural[0], natural[2]]
    return natural


def _natural_macro_key(name: str) -> tuple[str, int, str]:
    prefix = "".join(char for char in name if not char.isdigit())
    digits = "".join(char for char in name if char.isdigit())
    return prefix, int(digits or 0), name


def _macro_pin_assignments(
    dataset: Dataset,
    placements: Dict[str, Placement],
    macro_names: list[str],
) -> dict[str, tuple[str, str, tuple[float, float]]]:
    macro_set = set(macro_names)
    scores: dict[str, dict[str, list[tuple[float, float, str]]]] = {}
    for net in dataset.nets:
        macro_pins = [pin for pin in net.pins if pin.component in macro_set]
        if not macro_pins:
            continue
        for pin in net.pins:
            if pin.component in macro_set or pin.component not in dataset.components:
                continue
            bucket = scores.setdefault(pin.component, {})
            for macro_pin in macro_pins:
                macro_name = macro_pin.component
                if macro_name not in placements or macro_name not in dataset.components:
                    continue
                macro_component = dataset.components[macro_name]
                macro_placement = placements[macro_name]
                pin_x, pin_y = pin_position(macro_component, macro_placement, macro_pin.dx, macro_pin.dy)
                rotated_dx, rotated_dy = rotate_pin_offset(macro_pin.dx, macro_pin.dy, macro_placement.orient)
                side = _pin_side(macro_component, macro_placement, rotated_dx, rotated_dy)
                bucket.setdefault(macro_name, []).append((pin_x, pin_y, side))

    assignments: dict[str, tuple[str, str, tuple[float, float]]] = {}
    for name, macro_hits in scores.items():
        best_macro = max(macro_hits, key=lambda macro: (len(macro_hits[macro]), macro))
        hits = macro_hits[best_macro]
        avg_x = sum(item[0] for item in hits) / len(hits)
        avg_y = sum(item[1] for item in hits) / len(hits)
        side = _dominant_side([item[2] for item in hits])
        assignments[name] = (best_macro, side, (avg_x, avg_y))
    return assignments


def _place_small_net_clusters(
    dataset: Dataset,
    result: Dict[str, Placement],
    placed: Dict[str, Placement],
    macro_names: list[str],
    assignments: dict[str, tuple[str, str, tuple[float, float]]],
    board: Board,
    config: OptimizationConfig,
) -> set[str]:
    macro_set = set(macro_names)
    clustered: set[str] = set()
    macro_centers = [
        (
            result[name].x + dataset.components[name].width / 2,
            result[name].y + dataset.components[name].height / 2,
        )
        for name in macro_names
        if name in result and name in dataset.components
    ]
    default_center = (
        sum(point[0] for point in macro_centers) / len(macro_centers),
        sum(point[1] for point in macro_centers) / len(macro_centers),
    ) if macro_centers else ((board.left + board.right) / 2, (board.bottom + board.top) / 2)

    cluster_nets = sorted(
        [
            net
            for net in dataset.nets
            if len([pin for pin in net.pins if pin.component not in macro_set and pin.component in dataset.components]) >= 3
            and not any(pin.component in macro_set for pin in net.pins)
        ],
        key=lambda net: -len(net.pins),
    )
    for net in cluster_nets:
        members = [
            pin.component
            for pin in net.pins
            if pin.component in dataset.components
            and pin.component in result
            and pin.component not in macro_set
            and pin.component not in clustered
            and not result[pin.component].fixed
        ]
        # Preserve order but remove duplicate components.
        members = list(dict.fromkeys(members))
        if len(members) < 3:
            continue

        target_points = [assignments[name][2] for name in members if name in assignments]
        if target_points:
            center = (
                sum(point[0] for point in target_points) / len(target_points),
                sum(point[1] for point in target_points) / len(target_points),
            )
        else:
            center = default_center

        ordered = sorted(
            members,
            key=lambda name: (0 if _component_kind(dataset, name) == "passive" else 1, -(dataset.components[name].width * dataset.components[name].height), name),
        )
        offsets = _cluster_offsets(ordered, dataset, config)
        placed_this_cluster: set[str] = set()
        for name, (offset_x, offset_y) in zip(ordered, offsets):
            component = dataset.components[name]
            original = result[name]
            target = (center[0] + offset_x - component.width / 2, center[1] + offset_y - component.height / 2)
            candidate = _best_slot_near_target(
                dataset=dataset,
                original=original,
                component=component,
                placed=placed,
                board=board,
                config=config,
                target=target,
                extra_candidates=_local_grid_candidates(component, target[0], target[1], board, config),
            )
            result[name] = candidate
            placed[name] = candidate
            placed_this_cluster.add(name)
        clustered.update(placed_this_cluster)
    return clustered


def _place_assigned_components_by_slot_matching(
    dataset: Dataset,
    result: Dict[str, Placement],
    placed: Dict[str, Placement],
    names: list[str],
    assignments: dict[str, tuple[str, str, tuple[float, float]]],
    board: Board,
    config: OptimizationConfig,
) -> set[str]:
    matched: set[str] = set()
    groups: dict[tuple[str, str], list[str]] = {}
    for name in names:
        assignment = assignments.get(name)
        if assignment is None:
            continue
        macro_name, side, _ = assignment
        groups.setdefault((macro_name, side), []).append(name)

    for (macro_name, side), group_names in sorted(groups.items()):
        if macro_name not in result or macro_name not in dataset.components:
            continue
        macro_component = dataset.components[macro_name]
        macro_placement = result[macro_name]
        ordered = sorted(
            group_names,
            key=lambda name: (
                assignments[name][2][1] if side in {"left", "right"} else assignments[name][2][0],
                name,
            ),
        )
        slots_by_name: dict[str, list[tuple[float, float]]] = {}
        for name in ordered:
            component = dataset.components[name]
            slots_by_name[name] = _pin_side_slot_candidates(
                component,
                macro_component,
                macro_placement,
                side,
                board,
                config,
                target_pin=assignments[name][2],
            )

        base_result = dict(result)
        base_placed = dict(placed)

        greedy_result = dict(base_result)
        greedy_placed = dict(base_placed)
        for name in ordered:
            component = dataset.components[name]
            original = greedy_result[name]
            target_pin = assignments[name][2]
            target = (target_pin[0] - component.width / 2, target_pin[1] - component.height / 2)
            chosen = _best_slot_near_target(
                dataset=dataset,
                original=original,
                component=component,
                placed=greedy_placed,
                board=board,
                config=config,
                target=target,
                extra_candidates=slots_by_name[name],
            )
            greedy_result[name] = chosen
            greedy_placed[name] = chosen

        match_result = dict(base_result)
        match_placed = dict(base_placed)
        min_cost = _min_cost_slot_assignment(
            dataset=dataset,
            base_result=base_result,
            base_placed=base_placed,
            ordered=ordered,
            assignments=assignments,
            slots_by_name=slots_by_name,
            board=board,
            config=config,
        )
        if min_cost is None:
            match_placed = dict(base_placed)
            used_slots: set[tuple[float, float]] = set()
            for name in ordered:
                component = dataset.components[name]
                original = match_result[name]
                target_pin = assignments[name][2]
                target = (target_pin[0] - component.width / 2, target_pin[1] - component.height / 2)
                ranked = sorted(
                    slots_by_name[name],
                    key=lambda slot: abs(slot[0] - target[0]) + abs(slot[1] - target[1]) + 0.05 * (abs(slot[0] - original.x) + abs(slot[1] - original.y)),
                )
                chosen: Placement | None = None
                for x, y in ranked:
                    key = (round(x, 6), round(y, 6))
                    if key in used_slots:
                        continue
                    candidate = replace(original, x=x, y=y)
                    if _fits_against_placed(dataset, candidate, component, match_placed, board, config.min_gap):
                        chosen = candidate
                        used_slots.add(key)
                        break
                if chosen is None:
                    chosen = _best_slot_near_target(
                        dataset=dataset,
                        original=original,
                        component=component,
                        placed=match_placed,
                        board=board,
                        config=config,
                        target=target,
                        extra_candidates=slots_by_name[name],
                    )
                match_result[name] = chosen
                match_placed[name] = chosen
        else:
            match_result, match_placed = min_cost

        greedy_score = placement_score(dataset, greedy_result, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        match_score = placement_score(dataset, match_result, board=board, min_gap=config.min_gap, legality_weight=config.legality_weight)
        chosen_result, chosen_placed = (match_result, match_placed) if match_score <= greedy_score else (greedy_result, greedy_placed)
        for name in ordered:
            result[name] = chosen_result[name]
            placed[name] = chosen_placed[name]
            matched.add(name)
    return matched


def _min_cost_slot_assignment(
    dataset: Dataset,
    base_result: Dict[str, Placement],
    base_placed: Dict[str, Placement],
    ordered: list[str],
    assignments: dict[str, tuple[str, str, tuple[float, float]]],
    slots_by_name: dict[str, list[tuple[float, float]]],
    board: Board,
    config: OptimizationConfig,
) -> tuple[Dict[str, Placement], Dict[str, Placement]] | None:
    if not ordered:
        return None

    candidate_lists: dict[str, list[tuple[float, Placement]]] = {}
    exact_search = len(ordered) <= 7
    candidate_limit = 6 if exact_search else min(28, max(12, len(ordered) + 8))
    for name in ordered:
        component = dataset.components[name]
        original = base_result[name]
        target_pin = assignments[name][2]
        target = (target_pin[0] - component.width / 2, target_pin[1] - component.height / 2)
        ranked: list[tuple[float, Placement]] = []
        for x, y in _candidate_slots(slots_by_name.get(name, []), component, board):
            cost = _slot_assignment_cost(dataset, base_result, name, x, y, original, target, assignments, config)
            ranked.append((cost, replace(original, x=x, y=y)))
        ranked.sort(key=lambda item: item[0])
        candidate_lists[name] = ranked[: min(candidate_limit, len(ranked))]
        if not candidate_lists[name]:
            return None

    if not exact_search:
        return _min_cost_flow_slot_assignment(
            dataset=dataset,
            base_result=base_result,
            base_placed=base_placed,
            ordered=ordered,
            candidate_lists=candidate_lists,
            board=board,
            config=config,
        )

    search_order = sorted(ordered, key=lambda name: (len(candidate_lists[name]), name))
    best_cost = float("inf")
    best_result: Dict[str, Placement] | None = None

    def search(index: int, current_result: Dict[str, Placement], current_placed: Dict[str, Placement], used: set[tuple[float, float]], cost_so_far: float) -> None:
        nonlocal best_cost, best_result
        if cost_so_far >= best_cost:
            return
        if index >= len(search_order):
            best_cost = cost_so_far
            best_result = dict(current_result)
            return

        name = search_order[index]
        component = dataset.components[name]
        for cost, candidate in candidate_lists[name]:
            key = (round(candidate.x, 6), round(candidate.y, 6))
            if key in used:
                continue
            if not _fits_against_placed(dataset, candidate, component, current_placed, board, config.min_gap):
                continue
            next_result = dict(current_result)
            next_placed = dict(current_placed)
            next_used = set(used)
            next_result[name] = candidate
            next_placed[name] = candidate
            next_used.add(key)
            search(index + 1, next_result, next_placed, next_used, cost_so_far + cost)

    search(0, dict(base_result), dict(base_placed), set(), 0.0)
    if best_result is None:
        return None

    best_placed = dict(base_placed)
    for name in ordered:
        best_placed[name] = best_result[name]
    return best_result, best_placed


def _slot_assignment_cost(
    dataset: Dataset,
    placements: Dict[str, Placement],
    name: str,
    x: float,
    y: float,
    original: Placement,
    target: tuple[float, float],
    assignments: dict[str, tuple[str, str, tuple[float, float]]],
    config: OptimizationConfig,
) -> float:
    component = dataset.components[name]
    cost = abs(x - target[0]) + abs(y - target[1])
    cost += 0.03 * (abs(x - original.x) + abs(y - original.y))
    assignment = assignments.get(name)
    if assignment is None:
        return cost

    macro_name, side, _ = assignment
    if macro_name not in dataset.components or macro_name not in placements:
        return cost
    macro_component = dataset.components[macro_name]
    macro_placement = placements[macro_name]
    gap = max(config.min_gap, 2.0)
    if side == "left":
        depth = macro_placement.x - (x + component.width)
        side_miss = max(0.0, gap - depth)
        row_index = max(0.0, (depth - gap) / max(component.width + gap, 1.0))
        axis_delta = abs(y - target[1])
    elif side == "right":
        depth = x - (macro_placement.x + macro_component.width)
        side_miss = max(0.0, gap - depth)
        row_index = max(0.0, (depth - gap) / max(component.width + gap, 1.0))
        axis_delta = abs(y - target[1])
    elif side == "bottom":
        depth = macro_placement.y - (y + component.height)
        side_miss = max(0.0, gap - depth)
        row_index = max(0.0, (depth - gap) / max(component.height + gap, 1.0))
        axis_delta = abs(x - target[0])
    else:
        depth = y - (macro_placement.y + macro_component.height)
        side_miss = max(0.0, gap - depth)
        row_index = max(0.0, (depth - gap) / max(component.height + gap, 1.0))
        axis_delta = abs(x - target[0])
    cost += 0.12 * axis_delta
    cost += row_index * max(component.width, component.height, gap)
    cost += side_miss * 20.0
    return cost


def _min_cost_flow_slot_assignment(
    dataset: Dataset,
    base_result: Dict[str, Placement],
    base_placed: Dict[str, Placement],
    ordered: list[str],
    candidate_lists: dict[str, list[tuple[float, Placement]]],
    board: Board,
    config: OptimizationConfig,
) -> tuple[Dict[str, Placement], Dict[str, Placement]] | None:
    slot_keys: list[tuple[float, float]] = []
    slot_index: dict[tuple[float, float], int] = {}
    edge_costs: dict[tuple[int, int], tuple[float, Placement]] = {}
    for component_index, name in enumerate(ordered):
        for cost, placement in candidate_lists[name]:
            key = (round(placement.x, 6), round(placement.y, 6))
            if key not in slot_index:
                slot_index[key] = len(slot_keys)
                slot_keys.append(key)
            edge_costs[(component_index, slot_index[key])] = (cost, placement)

    source = 0
    component_offset = 1
    slot_offset = component_offset + len(ordered)
    sink = slot_offset + len(slot_keys)
    graph: list[list[list[int]]] = [[] for _ in range(sink + 1)]

    def add_edge(src: int, dst: int, capacity: int, cost: int) -> None:
        graph[src].append([dst, len(graph[dst]), capacity, cost])
        graph[dst].append([src, len(graph[src]) - 1, 0, -cost])

    for index in range(len(ordered)):
        add_edge(source, component_offset + index, 1, 0)
    for slot in range(len(slot_keys)):
        add_edge(slot_offset + slot, sink, 1, 0)
    for (component_index, slot), (cost, _) in edge_costs.items():
        add_edge(component_offset + component_index, slot_offset + slot, 1, int(round(cost * 1000.0)))

    flow = 0
    target_flow = len(ordered)
    while flow < target_flow:
        distance = [math.inf] * len(graph)
        previous: list[tuple[int, int] | None] = [None] * len(graph)
        in_queue = [False] * len(graph)
        distance[source] = 0.0
        queue = [source]
        in_queue[source] = True
        cursor = 0
        while cursor < len(queue):
            node = queue[cursor]
            cursor += 1
            in_queue[node] = False
            for edge_index, edge in enumerate(graph[node]):
                dst, _, capacity, cost = edge
                if capacity <= 0:
                    continue
                next_dist = distance[node] + cost
                if next_dist < distance[dst]:
                    distance[dst] = next_dist
                    previous[dst] = (node, edge_index)
                    if not in_queue[dst]:
                        queue.append(dst)
                        in_queue[dst] = True
        if previous[sink] is None:
            return None
        node = sink
        while node != source:
            prev_node, edge_index = previous[node]
            edge = graph[prev_node][edge_index]
            edge[2] -= 1
            graph[node][edge[1]][2] += 1
            node = prev_node
        flow += 1

    result = dict(base_result)
    assigned: dict[str, Placement] = {}
    for component_index, name in enumerate(ordered):
        component_node = component_offset + component_index
        chosen_slot: int | None = None
        for edge in graph[component_node]:
            dst, _, capacity, _ = edge
            if slot_offset <= dst < sink and capacity == 0:
                chosen_slot = dst - slot_offset
                break
        if chosen_slot is None:
            return None
        _, placement = edge_costs[(component_index, chosen_slot)]
        result[name] = placement
        assigned[name] = placement

    placed = dict(base_placed)
    for name in ordered:
        component = dataset.components[name]
        placement = assigned[name]
        if not _fits_against_placed(dataset, placement, component, placed, board, config.min_gap):
            repaired = _grid_legalize(dataset, result, board, config)
            legality = check_layout_legality(dataset.components, repaired, dataset.nets, board=board, min_gap=config.min_gap)
            if not legality.is_legal:
                return None
            repaired_placed = dict(base_placed)
            for repaired_name in ordered:
                repaired_placed[repaired_name] = repaired[repaired_name]
            return repaired, repaired_placed
        placed[name] = placement
    return result, placed


def _slot_swap_refinement(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    macro_names: list[str],
    assignments: dict[str, tuple[str, str, tuple[float, float]]],
    passes: int = 1,
) -> tuple[Dict[str, Placement], int]:
    result = dict(placements)
    accepted = 0
    current_hpwl = total_hpwl(dataset.nets, dataset.components, result)
    groups: dict[tuple[str, str], list[str]] = {}
    macro_set = set(macro_names)
    for name, assignment in assignments.items():
        if name in macro_set or name not in result or name not in dataset.components or result[name].fixed:
            continue
        macro_name, side, _ = assignment
        groups.setdefault((macro_name, side), []).append(name)

    for _ in range(max(1, passes)):
        improved = False
        for _, group in sorted(groups.items()):
            ordered = sorted(group, key=lambda name: (dataset.components[name].width * dataset.components[name].height, name))
            if len(ordered) < 2:
                continue
            if len(ordered) > 24:
                ordered = ordered[:24]
            sorted_candidate = _same_side_sorted_candidate(dataset, result, ordered, assignments)
            result, current_hpwl, accepted_sort = _accept_refinement_candidate(
                dataset, result, sorted_candidate, current_hpwl, board, config
            )
            if accepted_sort:
                accepted += 1
                improved = True
                break

            checked_pairs = 0
            for index, name_a in enumerate(ordered):
                for name_b in ordered[index + 1 :]:
                    checked_pairs += 1
                    if checked_pairs > 160:
                        break
                    candidate = dict(result)
                    candidate[name_a], candidate[name_b] = _swap_component_centers(dataset, name_a, result[name_a], name_b, result[name_b])
                    result, current_hpwl, accepted_swap = _accept_refinement_candidate(
                        dataset, result, candidate, current_hpwl, board, config
                    )
                    if accepted_swap:
                        accepted += 1
                        improved = True
                        break
                if improved:
                    break
                if checked_pairs > 160:
                    break
            if improved:
                break

            checked_cycles = 0
            for triple in itertools.combinations(ordered[:18], 3):
                checked_cycles += 1
                if checked_cycles > 80:
                    break
                for direction in (1, -1):
                    candidate = _three_cycle_candidate(dataset, result, triple, direction)
                    result, current_hpwl, accepted_cycle = _accept_refinement_candidate(
                        dataset, result, candidate, current_hpwl, board, config
                    )
                    if accepted_cycle:
                        accepted += 1
                        improved = True
                        break
                if improved:
                    break
            if improved:
                break

        if not improved:
            checked_neighbor_pairs = 0
            for group_a, group_b in _neighbor_group_pairs(groups):
                candidates_a = sorted(groups[group_a], key=lambda name: (dataset.components[name].width * dataset.components[name].height, name))[:14]
                candidates_b = sorted(groups[group_b], key=lambda name: (dataset.components[name].width * dataset.components[name].height, name))[:14]
                for name_a in candidates_a:
                    for name_b in candidates_b:
                        checked_neighbor_pairs += 1
                        if checked_neighbor_pairs > 160:
                            break
                        candidate = dict(result)
                        candidate[name_a], candidate[name_b] = _swap_component_centers(dataset, name_a, result[name_a], name_b, result[name_b])
                        result, current_hpwl, accepted_neighbor = _accept_refinement_candidate(
                            dataset, result, candidate, current_hpwl, board, config
                        )
                        if accepted_neighbor:
                            accepted += 1
                            improved = True
                            break
                    if improved or checked_neighbor_pairs > 160:
                        break
                if improved or checked_neighbor_pairs > 160:
                    break
        if not improved:
            break
    return result, accepted


def _accept_refinement_candidate(
    dataset: Dataset,
    current: Dict[str, Placement],
    candidate: Dict[str, Placement],
    current_hpwl: float,
    board: Board,
    config: OptimizationConfig,
) -> tuple[Dict[str, Placement], float, bool]:
    if candidate == current:
        return current, current_hpwl, False
    legality = check_layout_legality(dataset.components, candidate, dataset.nets, board=board, min_gap=config.min_gap)
    if not legality.is_legal:
        return current, current_hpwl, False
    hpwl = total_hpwl(dataset.nets, dataset.components, candidate)
    if hpwl + 1e-9 < current_hpwl:
        return candidate, hpwl, True
    return current, current_hpwl, False


def _same_side_sorted_candidate(
    dataset: Dataset,
    placements: Dict[str, Placement],
    ordered: list[str],
    assignments: dict[str, tuple[str, str, tuple[float, float]]],
) -> Dict[str, Placement]:
    if len(ordered) < 3:
        return placements
    side = assignments[ordered[0]][1]
    axis = 1 if side in {"left", "right"} else 0
    names_by_pin = sorted(ordered, key=lambda name: (assignments[name][2][axis], name))
    centers = sorted((_component_center(dataset, name, placements[name]) for name in ordered), key=lambda point: point[axis])
    candidate = dict(placements)
    for name, center in zip(names_by_pin, centers):
        component = dataset.components[name]
        width, height = oriented_size(component, placements[name])
        candidate[name] = replace(placements[name], x=center[0] - width / 2, y=center[1] - height / 2)
    return candidate


def _three_cycle_candidate(
    dataset: Dataset,
    placements: Dict[str, Placement],
    names: tuple[str, str, str],
    direction: int,
) -> Dict[str, Placement]:
    centers = [_component_center(dataset, name, placements[name]) for name in names]
    rotated_centers = [centers[2], centers[0], centers[1]] if direction > 0 else [centers[1], centers[2], centers[0]]
    candidate = dict(placements)
    for name, center in zip(names, rotated_centers):
        component = dataset.components[name]
        width, height = oriented_size(component, placements[name])
        candidate[name] = replace(placements[name], x=center[0] - width / 2, y=center[1] - height / 2)
    return candidate


def _neighbor_group_pairs(groups: dict[tuple[str, str], list[str]]) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    adjacent = {
        "left": {"top", "bottom"},
        "right": {"top", "bottom"},
        "top": {"left", "right"},
        "bottom": {"left", "right"},
    }
    keys = sorted(groups)
    pairs: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for index, group_a in enumerate(keys):
        macro_a, side_a = group_a
        for group_b in keys[index + 1 :]:
            macro_b, side_b = group_b
            if macro_a != macro_b:
                continue
            if side_a == side_b or side_b in adjacent.get(side_a, set()):
                pairs.append((group_a, group_b))
    return pairs


def _swap_component_centers(
    dataset: Dataset,
    name_a: str,
    placement_a: Placement,
    name_b: str,
    placement_b: Placement,
) -> tuple[Placement, Placement]:
    component_a = dataset.components[name_a]
    component_b = dataset.components[name_b]
    center_a = _component_center(dataset, name_a, placement_a)
    center_b = _component_center(dataset, name_b, placement_b)
    width_a, height_a = oriented_size(component_a, placement_a)
    width_b, height_b = oriented_size(component_b, placement_b)
    return (
        replace(placement_a, x=center_b[0] - width_a / 2, y=center_b[1] - height_a / 2),
        replace(placement_b, x=center_a[0] - width_b / 2, y=center_a[1] - height_b / 2),
    )


def _cluster_offsets(names: list[str], dataset: Dataset, config: OptimizationConfig) -> list[tuple[float, float]]:
    if not names:
        return []
    avg_w = sum(dataset.components[name].width for name in names) / len(names)
    avg_h = sum(dataset.components[name].height for name in names) / len(names)
    pitch_x = max(avg_w + config.min_gap, 5.0)
    pitch_y = max(avg_h + config.min_gap, 5.0)
    columns = max(1, math.ceil(math.sqrt(len(names))))
    rows = math.ceil(len(names) / columns)
    offsets: list[tuple[float, float]] = []
    for index in range(len(names)):
        row = index // columns
        col = index % columns
        offsets.append(((col - (columns - 1) / 2) * pitch_x, ((rows - 1) / 2 - row) * pitch_y))
    return offsets


def _pin_side(component, placement: Placement, dx: float, dy: float) -> str:
    width, height = oriented_size(component, placement)
    nx = dx / max(width / 2, 1e-9)
    ny = dy / max(height / 2, 1e-9)
    if abs(nx) >= abs(ny):
        return "left" if nx < 0 else "right"
    return "bottom" if ny < 0 else "top"


def _dominant_side(sides: list[str]) -> str:
    order = {"left": 0, "right": 1, "top": 2, "bottom": 3}
    return max(sorted(set(sides), key=lambda side: order[side]), key=sides.count)


def _pin_side_slot_candidates(
    component,
    macro_component,
    macro_placement: Placement,
    side: str,
    board: Board,
    config: OptimizationConfig,
    target_pin: tuple[float, float] | None = None,
) -> list[tuple[float, float]]:
    gap = max(config.min_gap, 2.0)
    candidates: list[tuple[float, float]] = []
    if side in {"left", "right"}:
        pitch = max(component.height + gap, 4.0)
        count = max(7, int(math.ceil((macro_component.height + 4 * pitch) / pitch)))
        axis_center = target_pin[1] if target_pin is not None else macro_placement.y + macro_component.height / 2
        y_start = axis_center - (count - 1) * pitch / 2
        for ring in range(6):
            if side == "left":
                x = macro_placement.x - gap - component.width - ring * (component.width + gap)
            else:
                x = macro_placement.x + macro_component.width + gap + ring * (component.width + gap)
            for index in range(count):
                y = y_start + index * pitch
                candidates.append((_clamp(x, board.left, board.right - component.width), _clamp(y, board.bottom, board.top - component.height)))
    else:
        pitch = max(component.width + gap, 4.0)
        count = max(7, int(math.ceil((macro_component.width + 4 * pitch) / pitch)))
        axis_center = target_pin[0] if target_pin is not None else macro_placement.x + macro_component.width / 2
        x_start = axis_center - (count - 1) * pitch / 2
        for ring in range(6):
            if side == "bottom":
                y = macro_placement.y - gap - component.height - ring * (component.height + gap)
            else:
                y = macro_placement.y + macro_component.height + gap + ring * (component.height + gap)
            for index in range(count):
                x = x_start + index * pitch
                candidates.append((_clamp(x, board.left, board.right - component.width), _clamp(y, board.bottom, board.top - component.height)))

    # Also allow adjacent sides as escape routes when one side is crowded.
    if side in {"left", "right"}:
        candidates.extend(_pin_side_slot_candidates(component, macro_component, macro_placement, "top", board, config, target_pin=target_pin)[:12])
        candidates.extend(_pin_side_slot_candidates(component, macro_component, macro_placement, "bottom", board, config, target_pin=target_pin)[:12])
    return candidates


def _macro_slot_candidates(component, target_x: float, target_y: float, board: Board, config: OptimizationConfig) -> list[tuple[float, float]]:
    gap = max(config.min_gap, 4.0)
    span = max(component.width, component.height) + gap
    candidates = [(target_x, target_y)]
    for radius in range(1, 6):
        for dx, dy in ((radius, 0), (-radius, 0), (0, radius), (0, -radius), (radius, radius), (-radius, radius), (radius, -radius), (-radius, -radius)):
            candidates.append((
                _clamp(target_x + dx * span, board.left, board.right - component.width),
                _clamp(target_y + dy * span, board.bottom, board.top - component.height),
            ))
    return candidates


def _local_grid_candidates(component, target_x: float, target_y: float, board: Board, config: OptimizationConfig) -> list[tuple[float, float]]:
    step = max(config.min_gap, min(max(component.width, 1.0), max(component.height, 1.0), 8.0))
    candidates: list[tuple[float, float]] = []
    for radius in range(0, 7):
        for ix in range(-radius, radius + 1):
            for iy in range(-radius, radius + 1):
                if radius and abs(ix) != radius and abs(iy) != radius:
                    continue
                candidates.append((
                    _clamp(target_x + ix * step, board.left, board.right - component.width),
                    _clamp(target_y + iy * step, board.bottom, board.top - component.height),
                ))
    return candidates


def _best_slot_near_target(
    dataset: Dataset,
    original: Placement,
    component,
    placed: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    target: tuple[float, float],
    extra_candidates: list[tuple[float, float]],
) -> Placement:
    target_x, target_y = target
    candidates = list(extra_candidates)
    candidates.append((target_x, target_y))
    candidates.append((original.x, original.y))
    best_fallback = replace(
        original,
        x=_clamp(target_x, board.left, board.right - component.width),
        y=_clamp(target_y, board.bottom, board.top - component.height),
    )
    best_fallback_cost = float("inf")
    for x, y in _candidate_slots(candidates, component, board):
        candidate = replace(original, x=x, y=y)
        cost = abs(x - target_x) + abs(y - target_y) + 0.05 * (abs(x - original.x) + abs(y - original.y))
        if cost < best_fallback_cost:
            best_fallback = candidate
            best_fallback_cost = cost
        if _fits_against_placed(dataset, candidate, component, placed, board, config.min_gap):
            return candidate
    return best_fallback


def _candidate_slots(
    candidates: list[tuple[float, float]],
    component,
    board: Board,
) -> list[tuple[float, float]]:
    slots: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for x, y in candidates:
        x = _clamp(x, board.left, board.right - component.width)
        y = _clamp(y, board.bottom, board.top - component.height)
        key = (round(x, 6), round(y, 6))
        if key in seen:
            continue
        seen.add(key)
        slots.append((x, y))
    return slots


def _find_grid_slot(
    dataset: Dataset,
    placements: Dict[str, Placement],
    placed: Dict[str, Placement],
    name: str,
    board: Board,
    config: OptimizationConfig,
) -> Placement:
    original = placements[name]
    component = dataset.components[name]
    centroid = _component_net_centroid(dataset, placements, name)
    if centroid is None:
        target_x, target_y = original.x, original.y
    else:
        target_x, target_y = centroid[0] - component.width / 2, centroid[1] - component.height / 2

    candidates: list[tuple[float, float, float]] = []
    grid_step = max(config.min_gap, min(max(component.width, 1.0), max(component.height, 1.0), 8.0))
    x_values = _scan_values(board.left, board.right - component.width, grid_step)
    y_values = _scan_values(board.bottom, board.top - component.height, grid_step)
    anchor_points = [
        (original.x, original.y),
        (target_x, target_y),
        (board.left, board.bottom),
        (board.right - component.width, board.top - component.height),
    ]
    for x, y in anchor_points:
        candidates.append((abs(x - target_x) + abs(y - target_y), _clamp(x, board.left, board.right - component.width), _clamp(y, board.bottom, board.top - component.height)))
    for x in x_values:
        for y in y_values:
            distance = abs(x - target_x) + abs(y - target_y) + 0.1 * (abs(x - original.x) + abs(y - original.y))
            candidates.append((distance, x, y))

    for _, x, y in sorted(candidates, key=lambda item: item[0]):
        candidate = replace(original, x=x, y=y)
        if _fits_against_placed(dataset, candidate, component, placed, board, config.min_gap):
            return candidate
    return original


def _fits_against_placed(
    dataset: Dataset,
    placement: Placement,
    component,
    placed: Dict[str, Placement],
    board: Board,
    min_gap: float,
) -> bool:
    rect = component_rect(component, placement)
    if rect.left < board.left or rect.bottom < board.bottom or rect.right > board.right or rect.top > board.top:
        return False
    for other_name, other_placement in placed.items():
        other_component = dataset.components.get(other_name)
        if other_component is None:
            continue
        if not has_min_gap(rect, component_rect(other_component, other_placement), min_gap=min_gap):
            return False
    return True


def _scan_values(low: float, high: float, step: float) -> list[float]:
    if high < low:
        return [low]
    values: list[float] = []
    current = low
    while current <= high:
        values.append(current)
        current += step
    if not values or values[-1] != high:
        values.append(high)
    return values


def _component_net_centroid(dataset: Dataset, placements: Dict[str, Placement], name: str) -> tuple[float, float] | None:
    targets: list[tuple[float, float]] = []
    for net in dataset.nets:
        if not any(pin.component == name for pin in net.pins):
            continue
        for pin in net.pins:
            if pin.component == name or pin.component not in dataset.components or pin.component not in placements:
                continue
            component = dataset.components[pin.component]
            placement = placements[pin.component]
            targets.append(pin_position(component, placement, pin.dx, pin.dy))
    if not targets:
        return None
    return sum(point[0] for point in targets) / len(targets), sum(point[1] for point in targets) / len(targets)


def _legalization_order(dataset: Dataset, placements: Dict[str, Placement]) -> list[str]:
    degrees = _component_degrees(dataset)
    return sorted(
        placements,
        key=lambda name: (
            0 if placements[name].fixed else 1,
            0 if _component_kind(dataset, name) == "large" else 1,
            -degrees.get(name, 0),
            name,
        ),
    )


def _legalization_repair_order(
    dataset: Dataset,
    placements: Dict[str, Placement],
    legality: LegalityResult,
) -> list[str]:
    degrees = _component_degrees(dataset)
    conflict_count = {name: 0 for name in placements}
    for violation in legality.gap_violations:
        conflict_count[violation.component_a] = conflict_count.get(violation.component_a, 0) + 1
        conflict_count[violation.component_b] = conflict_count.get(violation.component_b, 0) + 1
    for violation in legality.boundary_violations:
        conflict_count[violation.component] = conflict_count.get(violation.component, 0) + 2
    kind_order = {"passive": 0, "medium": 1, "large": 2}
    return sorted(
        _violating_components(legality),
        key=lambda name: (
            1 if name in placements and placements[name].fixed else 0,
            kind_order.get(_component_kind(dataset, name), 3) if name in dataset.components else 3,
            -conflict_count.get(name, 0),
            -degrees.get(name, 0),
            name,
        ),
    )


def _movement_order(dataset: Dataset, placements: Dict[str, Placement]) -> list[str]:
    degrees = _component_degrees(dataset)
    movable = [name for name in placements if name in dataset.components and not placements[name].fixed]
    return sorted(
        movable,
        key=lambda name: (
            0 if _component_kind(dataset, name) == "passive" else 1,
            0 if _component_kind(dataset, name) == "medium" else 1,
            2 if _component_kind(dataset, name) == "large" else 0,
            -degrees.get(name, 0),
            name,
        ),
    )


def _component_degrees(dataset: Dataset) -> dict[str, int]:
    degrees = {name: 0 for name in dataset.components}
    for net in dataset.nets:
        for pin in net.pins:
            if pin.component in degrees:
                degrees[pin.component] += 1
    return degrees


def _component_kind(dataset: Dataset, name: str) -> str:
    component = dataset.components[name]
    max_area = max((item.width * item.height for item in dataset.components.values()), default=1.0)
    area = component.width * component.height
    if area >= max_area * 0.15 or name.upper().startswith("U"):
        return "large"
    if name.upper().startswith(("R", "C", "L")) and area <= max_area * 0.08:
        return "passive"
    return "medium"


def _violating_components(legality: LegalityResult) -> list[str]:
    names: set[str] = set()
    for violation in legality.boundary_violations:
        names.add(violation.component)
    for violation in legality.gap_violations:
        names.add(violation.component_a)
        names.add(violation.component_b)
    for violation in legality.reference_violations:
        names.add(violation.component)
    return sorted(names)


def _repair_rank(dataset: Dataset, placements: Dict[str, Placement], board: Board, config: OptimizationConfig) -> tuple[float, float, float]:
    legality = check_layout_legality(dataset.components, placements, dataset.nets, board=board, min_gap=config.min_gap)
    violation_count = len(legality.gap_violations) + len(legality.boundary_violations) + len(legality.reference_violations) * 100
    penalty = legality_penalty(legality, min_gap=config.min_gap)
    hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    return float(violation_count), penalty, hpwl


def _movable_names(dataset: Dataset) -> list[str]:
    return [
        name
        for name, placement in sorted(dataset.placements.items())
        if name in dataset.components and not placement.fixed
    ]


def _default_step(board: Board) -> float:
    span = max(board.right - board.left, board.top - board.bottom)
    return max(1.0, span / 20.0)


def _clamp(value: float, low: float, high: float) -> float:
    if high < low:
        return low
    return min(max(value, low), high)


def _should_record(iteration: int, config: OptimizationConfig) -> bool:
    interval = max(1, config.history_interval)
    return iteration == 0 or iteration % interval == 0 or iteration == config.max_iter


def _record_history(
    dataset: Dataset,
    placements: Dict[str, Placement],
    board: Board,
    config: OptimizationConfig,
    history: list[ConvergenceRecord],
    iteration: int,
    stage: str,
) -> None:
    hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    legality = check_layout_legality(dataset.components, placements, dataset.nets, board=board, min_gap=config.min_gap)
    score = hpwl + config.legality_weight * legality_penalty(legality, min_gap=config.min_gap)
    history.append(
        ConvergenceRecord(
            iteration=iteration,
            stage=stage,
            hpwl=hpwl,
            score=score,
            gap_violations=len(legality.gap_violations),
            boundary_violations=len(legality.boundary_violations),
            reference_violations=len(legality.reference_violations),
            is_legal=legality.is_legal,
        )
    )


def _unchanged_result(dataset: Dataset, config: OptimizationConfig, board: Board, started_at: float) -> OptimizationResult:
    placements = dict(dataset.placements)
    hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    legality = check_layout_legality(
        dataset.components,
        placements,
        dataset.nets,
        board=board,
        min_gap=config.min_gap,
    )
    score = hpwl + config.legality_weight * legality_penalty(legality, min_gap=config.min_gap)
    return OptimizationResult(
        algorithm=config.algorithm,
        placements=placements,
        initial_hpwl=hpwl,
        optimized_hpwl=hpwl,
        initial_score=score,
        optimized_score=score,
        initial_legality=legality,
        optimized_legality=legality,
        iterations=0,
        accepted_moves=0,
        runtime_seconds=time.perf_counter() - started_at,
        board=board,
        history=[],
    )


def _optimization_result(
    dataset: Dataset,
    algorithm: str,
    placements: Dict[str, Placement],
    initial_hpwl: float,
    initial_score: float,
    initial_legality: LegalityResult,
    optimized_score: float,
    iterations: int,
    accepted_moves: int,
    runtime_seconds: float,
    board: Board,
    min_gap: float,
    history: list[ConvergenceRecord] | None = None,
) -> OptimizationResult:
    optimized_hpwl = total_hpwl(dataset.nets, dataset.components, placements)
    optimized_legality = check_layout_legality(
        dataset.components,
        placements,
        dataset.nets,
        board=board,
        min_gap=min_gap,
    )
    return OptimizationResult(
        algorithm=algorithm,
        placements=placements,
        initial_hpwl=initial_hpwl,
        optimized_hpwl=optimized_hpwl,
        initial_score=initial_score,
        optimized_score=optimized_score,
        initial_legality=initial_legality,
        optimized_legality=optimized_legality,
        iterations=iterations,
        accepted_moves=accepted_moves,
        runtime_seconds=runtime_seconds,
        board=board,
        history=history or [],
    )
