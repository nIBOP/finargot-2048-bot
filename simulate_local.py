from __future__ import annotations

import argparse
import csv
import random
import statistics
import time
from collections import Counter
from pathlib import Path

import main


def add_random_tile(board: main.Board, rng: random.Random) -> main.Board:
    cells = main.empty_cells(board)
    if not cells:
        return board
    row, col = rng.choice(cells)
    value = 4 if rng.random() < 0.1 else 2
    return main.set_cell(board, row, col, value)


def play_game(
    seed: int,
    config: main.SolverConfig,
    max_moves: int,
    solver=None,
    progress_every: int = 0,
) -> dict[str, object]:
    rng = random.Random(seed)
    board = main.empty_board()
    board = add_random_tile(add_random_tile(board, rng), rng)
    score = 0
    decisions_ms: list[float] = []
    depths: Counter[int] = Counter()
    requested_depths: Counter[int] = Counter()
    time_budgets: Counter[int] = Counter()
    moves: Counter[str | None] = Counter()

    started = time.perf_counter()
    move_number = 0

    while move_number < max_moves:
        turn_config = main.million_mode_config(board, config)
        decision = solver.choose_best_move(board, turn_config) if solver else main.choose_best_move(board, turn_config)
        decisions_ms.append(decision.elapsed_ms)
        depths[decision.depth] += 1
        requested_depths[turn_config.depth] += 1
        time_budgets[turn_config.time_limit_ms] += 1
        moves[decision.move] += 1

        if decision.move is None:
            break

        next_board, gain, moved = main.simulate_move(board, decision.move)
        if not moved:
            break

        score += gain
        board = add_random_tile(next_board, rng)
        move_number += 1

        if progress_every > 0 and move_number % progress_every == 0:
            flat_progress = [value for row in board for value in row]
            print(
                f"  progress seed={seed} moves={move_number} "
                f"score={score} max_tile={max(flat_progress)} empty={flat_progress.count(0)} "
                f"last_ms={decision.elapsed_ms:.1f} depth={decision.depth}/{turn_config.depth}",
                flush=True,
            )

    flat = [value for row in board for value in row]
    elapsed = time.perf_counter() - started

    return {
        "seed": seed,
        "score": score,
        "max_tile": max(flat),
        "moves_played": move_number,
        "empty": flat.count(0),
        "sum_tiles": sum(flat),
        "elapsed_s": elapsed,
        "avg_decision_ms": statistics.mean(decisions_ms) if decisions_ms else 0.0,
        "p95_decision_ms": statistics.quantiles(decisions_ms, n=20)[18] if len(decisions_ms) >= 20 else max(decisions_ms, default=0.0),
        "depths": dict(depths),
        "requested_depths": dict(requested_depths),
        "time_budgets": dict(time_budgets),
        "moves": {str(move): count for move, count in moves.items()},
        "final_board": board,
    }


def summarize(results: list[dict[str, object]]) -> str:
    scores = [int(result["score"]) for result in results]
    tiles = [int(result["max_tile"]) for result in results]
    move_counts = [int(result["moves_played"]) for result in results]
    avg_ms = [float(result["avg_decision_ms"]) for result in results]
    p95_ms = [float(result["p95_decision_ms"]) for result in results]
    tile_counts = Counter(tiles)

    return (
        f"games={len(results)} "
        f"score_avg={statistics.mean(scores):.0f} score_min={min(scores)} score_max={max(scores)} "
        f"tile_counts={dict(sorted(tile_counts.items()))} "
        f"moves_avg={statistics.mean(move_counts):.0f} "
        f"decision_avg_ms={statistics.mean(avg_ms):.1f} decision_p95_ms={statistics.mean(p95_ms):.1f}"
    )


def write_csv(path: Path, results: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "seed",
        "score",
        "max_tile",
        "moves_played",
        "empty",
        "sum_tiles",
        "elapsed_s",
        "avg_decision_ms",
        "p95_decision_ms",
        "depths",
        "requested_depths",
        "time_budgets",
        "moves",
        "final_board",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({field: result[field] for field in fields})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local 2048 simulations without browser automation.")
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--max-moves", type=int, default=5000)
    parser.add_argument("--depth", type=int, default=8)
    parser.add_argument("--time-limit-ms", type=int, default=80)
    parser.add_argument("--cprob-threshold", type=float, default=0.0004)
    parser.add_argument("--fast-snake-weight", type=float, default=7.5)
    parser.add_argument("--fixed-depth", action="store_true")
    parser.add_argument("--legacy-solver", action="store_true")
    parser.add_argument("--solver-backend", choices=("python", "java", "rust", "tdl"), default="python")
    parser.add_argument("--tdl-network", default="auto")
    parser.add_argument("--tdl-search", default="3p")
    parser.add_argument("--strict-corner", action="store_true")
    parser.add_argument("--million-mode", action="store_true")
    parser.add_argument("--progress-every", type=int, default=0)
    parser.add_argument("--csv", default="")
    return parser


def main_cli() -> int:
    args = build_parser().parse_args()
    config = main.SolverConfig(
        depth=args.depth,
        time_limit_ms=args.time_limit_ms,
        cprob_threshold=args.cprob_threshold,
        fast_snake_weight=args.fast_snake_weight,
        adaptive_depth=not args.fixed_depth,
        fast_solver=not args.legacy_solver,
        strict_corner=args.strict_corner,
        million_mode=args.million_mode,
    )

    results = []
    base_dir = Path(__file__).resolve().parent
    if args.solver_backend == "rust":
        solver = main.RustSolverClient(base_dir / "target" / "release" / "solver2048.exe")
    elif args.solver_backend == "java":
        solver = main.JavaSolverClient(base_dir)
    elif args.solver_backend == "tdl":
        tdl_network, tdl_model = main.resolve_tdl_model(base_dir, args.tdl_network)
        solver = main.TDLSolverClient(
            base_dir / "external" / "TDL2048" / "tdl2048.exe",
            tdl_model,
            network=tdl_network,
            search=args.tdl_search,
        )
    else:
        solver = None
    started = time.perf_counter()
    try:
        for index in range(args.games):
            seed = args.seed_start + index
            result = play_game(seed, config, args.max_moves, solver, args.progress_every)
            results.append(result)
            print(
                f"{index + 1:04d}/{args.games:04d} seed={seed} "
                f"score={result['score']} max_tile={result['max_tile']} "
                f"moves={result['moves_played']} avg_ms={float(result['avg_decision_ms']):.1f}",
                flush=True,
            )
    finally:
        if solver is not None:
            solver.close()

    print(summarize(results))
    print(f"elapsed_total_s={time.perf_counter() - started:.1f}")

    if args.csv:
        write_csv(Path(args.csv), results)
        print(f"csv={args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
