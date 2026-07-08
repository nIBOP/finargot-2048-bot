from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal


Board = tuple[tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int]]
Direction = Literal["UP", "DOWN", "LEFT", "RIGHT"]
Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]

SIZE = 4
DIRECTIONS: tuple[Direction, ...] = ("UP", "DOWN", "LEFT", "RIGHT")
DEFAULT_URL = "https://battlepass.ru/special/dark_carnival#dc-games"
POWER_VALUES = {1 << i for i in range(1, 18)}

CORNER_POSITIONS: dict[Corner, tuple[int, int]] = {
    "top-left": (0, 0),
    "top-right": (0, SIZE - 1),
    "bottom-left": (SIZE - 1, 0),
    "bottom-right": (SIZE - 1, SIZE - 1),
}

AVOID_MOVES: dict[Corner, tuple[Direction, ...]] = {
    "top-left": ("DOWN",),
    "top-right": ("DOWN",),
    "bottom-left": ("UP",),
    "bottom-right": ("UP",),
}


@dataclass(frozen=True)
class SolverConfig:
    depth: int = 3
    tile_encoding: Literal["auto", "value", "rank"] = "auto"
    corner: Corner = "bottom-right"
    spawn_twos_probability: float = 0.9
    empty_weight: float = 320.0
    monotonicity_weight: float = 90.0
    smoothness_weight: float = 22.0
    merge_weight: float = 160.0
    corner_weight: float = 1200.0
    snake_weight: float = 18.0
    avoid_move_penalty: float = 15000.0
    max_tile_move_penalty: float = 8500.0
    gain_weight: float = 1.0
    time_limit_ms: int = 120
    strict_corner: bool = True


@dataclass(frozen=True)
class Decision:
    move: Direction | None
    value: float
    depth: int
    valid_moves: tuple[Direction, ...]
    elapsed_ms: float


class SearchTimeout(Exception):
    pass


def as_board(matrix: Iterable[Iterable[int]]) -> Board:
    rows = tuple(tuple(int(v) for v in row) for row in matrix)
    if len(rows) != SIZE or any(len(row) != SIZE for row in rows):
        raise ValueError("Board must be a 4x4 matrix")
    return rows  # type: ignore[return-value]


def empty_board() -> Board:
    return as_board(((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)))


def merge_line(line: Iterable[int]) -> tuple[tuple[int, int, int, int], int]:
    values = [int(v) for v in line if int(v) != 0]
    merged: list[int] = []
    gain = 0
    index = 0

    while index < len(values):
        current = values[index]
        if index + 1 < len(values) and current == values[index + 1]:
            new_value = current * 2
            merged.append(new_value)
            gain += new_value
            index += 2
        else:
            merged.append(current)
            index += 1

    merged.extend([0] * (SIZE - len(merged)))
    return tuple(merged[:SIZE]), gain  # type: ignore[return-value]


def simulate_move(board: Board, direction: Direction) -> tuple[Board, int, bool]:
    rows = [list(row) for row in board]
    gain = 0

    if direction == "LEFT":
        next_rows = []
        for row in rows:
            merged, row_gain = merge_line(row)
            next_rows.append(list(merged))
            gain += row_gain
    elif direction == "RIGHT":
        next_rows = []
        for row in rows:
            merged, row_gain = merge_line(reversed(row))
            next_rows.append(list(reversed(merged)))
            gain += row_gain
    elif direction == "UP":
        next_rows = [[0] * SIZE for _ in range(SIZE)]
        for col in range(SIZE):
            merged, col_gain = merge_line(rows[row][col] for row in range(SIZE))
            gain += col_gain
            for row in range(SIZE):
                next_rows[row][col] = merged[row]
    elif direction == "DOWN":
        next_rows = [[0] * SIZE for _ in range(SIZE)]
        for col in range(SIZE):
            merged, col_gain = merge_line(rows[row][col] for row in reversed(range(SIZE)))
            merged = tuple(reversed(merged))
            gain += col_gain
            for row in range(SIZE):
                next_rows[row][col] = merged[row]
    else:
        raise ValueError(f"Unknown direction: {direction}")

    next_board = as_board(next_rows)
    return next_board, gain, next_board != board


def valid_moves(board: Board) -> tuple[Direction, ...]:
    return tuple(direction for direction in DIRECTIONS if simulate_move(board, direction)[2])


def policy_moves(moves: tuple[Direction, ...], config: SolverConfig) -> tuple[Direction, ...]:
    if not config.strict_corner or len(moves) <= 1:
        return moves

    preferred = tuple(move for move in moves if move not in AVOID_MOVES[config.corner])
    return preferred or moves


def empty_cells(board: Board) -> tuple[tuple[int, int], ...]:
    return tuple((r, c) for r in range(SIZE) for c in range(SIZE) if board[r][c] == 0)


def set_cell(board: Board, row: int, col: int, value: int) -> Board:
    rows = [list(line) for line in board]
    rows[row][col] = value
    return as_board(rows)


def tile_log(value: int) -> float:
    return math.log2(value) if value > 0 else 0.0


@lru_cache(maxsize=None)
def snake_path(corner: Corner) -> tuple[tuple[int, int], ...]:
    vertical = range(SIZE) if corner.startswith("top") else range(SIZE - 1, -1, -1)
    start_from_right = corner.endswith("right")
    path: list[tuple[int, int]] = []

    for offset, row in enumerate(vertical):
        if (offset % 2 == 0) == start_from_right:
            cols = range(SIZE - 1, -1, -1)
        else:
            cols = range(SIZE)
        path.extend((row, col) for col in cols)

    return tuple(path)


def target_corner_value(board: Board, corner: Corner) -> int:
    row, col = CORNER_POSITIONS[corner]
    return board[row][col]


def max_tile_positions(board: Board) -> tuple[int, tuple[tuple[int, int], ...]]:
    max_tile = max(max(row) for row in board)
    return max_tile, tuple((r, c) for r in range(SIZE) for c in range(SIZE) if board[r][c] == max_tile)


def distance_to_corner(row: int, col: int, corner: Corner) -> int:
    target_row, target_col = CORNER_POSITIONS[corner]
    return abs(row - target_row) + abs(col - target_col)


def count_merge_potential(board: Board) -> int:
    merges = 0
    for row in range(SIZE):
        for col in range(SIZE):
            value = board[row][col]
            if value == 0:
                continue
            if col + 1 < SIZE and board[row][col + 1] == value:
                merges += 1
            if row + 1 < SIZE and board[row + 1][col] == value:
                merges += 1
    return merges


def smoothness_score(board: Board) -> float:
    penalty = 0.0
    for row in range(SIZE):
        for col in range(SIZE):
            value = board[row][col]
            if value == 0:
                continue
            current = tile_log(value)
            if col + 1 < SIZE and board[row][col + 1]:
                penalty += abs(current - tile_log(board[row][col + 1]))
            if row + 1 < SIZE and board[row + 1][col]:
                penalty += abs(current - tile_log(board[row + 1][col]))
    return -penalty


def monotonicity_score(board: Board) -> float:
    total = 0.0
    for row in range(SIZE):
        values = [tile_log(board[row][col]) for col in range(SIZE)]
        total += max(sum(values[i] - values[i + 1] for i in range(SIZE - 1)), sum(values[i + 1] - values[i] for i in range(SIZE - 1)))
    for col in range(SIZE):
        values = [tile_log(board[row][col]) for row in range(SIZE)]
        total += max(sum(values[i] - values[i + 1] for i in range(SIZE - 1)), sum(values[i + 1] - values[i] for i in range(SIZE - 1)))
    return total


def corner_score(board: Board, corner: Corner) -> float:
    max_tile, positions = max_tile_positions(board)
    if max_tile == 0:
        return 0.0

    target = CORNER_POSITIONS[corner]
    if target in positions:
        return tile_log(max_tile) * 2.5

    best_distance = min(distance_to_corner(row, col, corner) for row, col in positions)
    return -tile_log(max_tile) * (1.0 + best_distance)


def snake_score(board: Board, corner: Corner) -> float:
    score = 0.0
    path = snake_path(corner)
    path_len = len(path)
    for index, (row, col) in enumerate(path):
        rank = path_len - index
        value = board[row][col]
        score += tile_log(value) * (rank * rank)
    return score


def move_policy_adjustment(before: Board, after: Board, direction: Direction, config: SolverConfig, legal_count: int) -> float:
    penalty = 0.0

    if legal_count > 1 and direction in AVOID_MOVES[config.corner]:
        penalty += config.avoid_move_penalty

    before_max, before_positions = max_tile_positions(before)
    if before_max > 0:
        target = CORNER_POSITIONS[config.corner]
        before_locked = target in before_positions
        after_max, after_positions = max_tile_positions(after)

        if before_locked and target not in after_positions:
            penalty += config.max_tile_move_penalty + tile_log(before_max) * config.corner_weight
        elif after_max == before_max and target not in after_positions:
            best_distance = min(distance_to_corner(row, col, config.corner) for row, col in after_positions)
            penalty += best_distance * tile_log(before_max) * 420.0

    return -penalty


def evaluate_board(board: Board, config: SolverConfig = SolverConfig()) -> float:
    if not valid_moves(board):
        return -1_000_000_000.0

    empty_count = len(empty_cells(board))
    return (
        empty_count * config.empty_weight
        + monotonicity_score(board) * config.monotonicity_weight
        + smoothness_score(board) * config.smoothness_weight
        + count_merge_potential(board) * config.merge_weight
        + corner_score(board, config.corner) * config.corner_weight
        + snake_score(board, config.corner) * config.snake_weight
    )


def choose_best_move(board: Board, config: SolverConfig = SolverConfig()) -> Decision:
    start = time.perf_counter()
    deadline = start + (max(config.time_limit_ms, 1) / 1000.0)
    moves = valid_moves(board)
    root_moves = policy_moves(moves, config)

    if not moves:
        return Decision(None, evaluate_board(board, config), config.depth, moves, 0.0)

    def check_deadline() -> None:
        if time.perf_counter() > deadline:
            raise SearchTimeout()

    @lru_cache(maxsize=200_000)
    def player_value(current: Board, depth: int) -> float:
        check_deadline()
        moves_now = valid_moves(current)
        considered_moves = policy_moves(moves_now, config)
        if depth <= 0 or not moves_now:
            return evaluate_board(current, config)

        best = -float("inf")
        for move in considered_moves:
            moved_board, gain, moved = simulate_move(current, move)
            if not moved:
                continue
            value = (
                config.gain_weight * gain
                + chance_value(moved_board, depth - 1)
                + move_policy_adjustment(current, moved_board, move, config, len(moves_now))
            )
            if value > best:
                best = value
        return best if best != -float("inf") else evaluate_board(current, config)

    @lru_cache(maxsize=200_000)
    def chance_value(current: Board, depth: int) -> float:
        check_deadline()
        cells = empty_cells(current)
        if depth <= 0 or not cells:
            return evaluate_board(current, config)

        total = 0.0
        probability_per_cell = 1.0 / len(cells)
        for row, col in cells:
            total += probability_per_cell * config.spawn_twos_probability * player_value(set_cell(current, row, col, 2), depth)
            total += probability_per_cell * (1.0 - config.spawn_twos_probability) * player_value(set_cell(current, row, col, 4), depth)
        return total

    best_move: Direction | None = None
    best_value = -float("inf")

    for move in root_moves:
        try:
            moved_board, gain, _ = simulate_move(board, move)
            value = (
                config.gain_weight * gain
                + chance_value(moved_board, max(config.depth - 1, 0))
                + move_policy_adjustment(board, moved_board, move, config, len(moves))
            )
        except SearchTimeout:
            break

        if value > best_value:
            best_value = value
            best_move = move

    if best_move is None:
        for move in root_moves:
            moved_board, gain, _ = simulate_move(board, move)
            value = (
                config.gain_weight * gain
                + evaluate_board(moved_board, config)
                + move_policy_adjustment(board, moved_board, move, config, len(moves))
            )
            if value > best_value:
                best_value = value
                best_move = move

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return Decision(best_move, best_value, config.depth, moves, elapsed_ms)


def normalize_tile_number(raw: int, encoding: Literal["auto", "value", "rank"]) -> int:
    if raw <= 0:
        return 0
    if encoding == "value":
        return raw
    if encoding == "rank":
        return 1 << raw

    if raw in POWER_VALUES:
        return raw
    if 1 <= raw <= 16:
        return 1 << raw
    return raw


def first_int(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def class_tile_value(class_name: str | None) -> int | None:
    if not class_name:
        return None
    patterns = (
        r"(?:^|\s)(?:tile|value|level|rank|item)[_-]?(\d+)(?:\s|$)",
        r"(?:^|\s)g2048__[a-z_-]*?(\d+)(?:\s|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, class_name, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def parse_cell_payload(payload: dict[str, Any], encoding: Literal["auto", "value", "rank"] = "auto") -> int:
    preferred_keys = ("data-value", "data-tile", "data-level", "data-rank", "aria-label", "title", "text")

    attrs = payload.get("attrs") or {}
    for key in preferred_keys:
        raw = attrs.get(key) if key != "text" else payload.get("text")
        number = first_int(str(raw)) if raw is not None else None
        if number is not None:
            return normalize_tile_number(number, encoding)

    for image in payload.get("images") or []:
        for key in ("alt", "title", "aria-label", "data-value", "data-level", "data-rank"):
            number = first_int(str(image.get(key))) if image.get(key) is not None else None
            if number is not None:
                return normalize_tile_number(number, encoding)

    number = class_tile_value(payload.get("className"))
    if number is not None:
        return normalize_tile_number(number, encoding)

    for image in payload.get("images") or []:
        number = class_tile_value(image.get("className"))
        if number is not None:
            return normalize_tile_number(number, encoding)

    return 0


def board_from_payloads(payloads: list[dict[str, Any]], encoding: Literal["auto", "value", "rank"] = "auto") -> Board | None:
    if len(payloads) < SIZE * SIZE:
        return None

    values = [parse_cell_payload(payload, encoding) for payload in payloads[: SIZE * SIZE]]
    rows = [values[index : index + SIZE] for index in range(0, SIZE * SIZE, SIZE)]
    return as_board(rows)


def format_board(board: Board) -> str:
    width = max(4, len(str(max(max(row) for row in board))))
    return "\n".join(" ".join(f"{value:{width}d}" for value in row) for row in board)


class Selenium2048Bot:
    def __init__(self, url: str, encoding: Literal["auto", "value", "rank"], browser: Literal["firefox"] = "firefox") -> None:
        self.url = url
        self.encoding = encoding
        self.browser = browser
        self.driver = self._create_driver()

    def _create_driver(self) -> Any:
        try:
            from selenium import webdriver
            from selenium.webdriver.firefox.service import Service
            from webdriver_manager.firefox import GeckoDriverManager
        except ImportError as exc:
            raise RuntimeError(
                "Missing browser dependencies. Install them with: "
                "python -m pip install selenium webdriver-manager"
            ) from exc

        if self.browser != "firefox":
            raise ValueError("Only Firefox is configured in this bot version")

        options = webdriver.FirefoxOptions()
        options.log.level = "fatal"
        return webdriver.Firefox(service=Service(GeckoDriverManager().install()), options=options)

    def open_game(self) -> None:
        self.driver.get(self.url)
        print("\n[bot] Firefox opened.")
        print("[bot] Log in, open the game, press Play, then return here.")
        input("[bot] Press ENTER when the 4x4 board is visible...")

    def read_cell_payloads(self) -> list[dict[str, Any]]:
        script = """
        return Array.from(document.querySelectorAll('.g2048__cell')).slice(0, 16).map((cell, index) => {
          const attrs = {};
          for (const attr of cell.attributes) attrs[attr.name] = attr.value;
          const images = Array.from(cell.querySelectorAll('img')).map((img) => {
            const imgAttrs = {};
            for (const attr of img.attributes) imgAttrs[attr.name] = attr.value;
            imgAttrs.className = img.className || '';
            return imgAttrs;
          });
          return {
            index,
            text: cell.innerText || '',
            className: cell.className || '',
            attrs,
            images,
            html: (cell.innerHTML || '').slice(0, 240),
          };
        });
        """
        return self.driver.execute_script(script)

    def read_board(self) -> Board | None:
        try:
            return board_from_payloads(self.read_cell_payloads(), self.encoding)
        except Exception:
            return None

    def execute_move(self, direction: Direction) -> None:
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys

        key_map = {
            "UP": Keys.ARROW_UP,
            "DOWN": Keys.ARROW_DOWN,
            "LEFT": Keys.ARROW_LEFT,
            "RIGHT": Keys.ARROW_RIGHT,
        }

        game_grid = self.driver.find_element(By.CLASS_NAME, "g2048__grid")
        self.driver.execute_script("arguments[0].click();", game_grid)
        ActionChains(self.driver).send_keys(key_map[direction]).perform()

    def close(self) -> None:
        self.driver.quit()


def write_jsonl(log_path: Path | None, payload: dict[str, Any]) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def run_bot(args: argparse.Namespace) -> int:
    config = SolverConfig(
        depth=args.depth,
        tile_encoding=args.tile_encoding,
        corner=args.corner,
        time_limit_ms=args.time_limit_ms,
        strict_corner=not args.relaxed_corner,
    )
    delay_min, delay_max = args.delay
    log_path = Path(args.log_dir) / f"run-{int(time.time())}.jsonl" if args.log_dir else None

    bot = Selenium2048Bot(args.url, args.tile_encoding)
    try:
        bot.open_game()
        move_number = 0

        while args.max_moves <= 0 or move_number < args.max_moves:
            board = bot.read_board()
            if board is None:
                print("[bot] Waiting for board...")
                time.sleep(1.0)
                continue

            decision = choose_best_move(board, config)
            if args.debug:
                print("\n" + format_board(board))
                print(
                    f"[solver] move={decision.move} value={decision.value:.2f} "
                    f"valid={decision.valid_moves} elapsed={decision.elapsed_ms:.1f}ms"
                )

            write_jsonl(
                log_path,
                {
                    "move_number": move_number,
                    "board": board,
                    "decision": decision.__dict__,
                    "timestamp": time.time(),
                },
            )

            if decision.move is None:
                print("[bot] No legal moves. Game is probably over.")
                return 0

            if args.dry_run:
                print(f"[dry-run] would play {decision.move}")
                return 0

            bot.execute_move(decision.move)
            move_number += 1

            delay = random.uniform(delay_min, delay_max)
            print(f"[bot] move={move_number} played={decision.move} delay={delay:.2f}s value={decision.value:.2f}")
            time.sleep(delay)

        print(f"[bot] Reached max moves: {args.max_moves}")
        return 0
    finally:
        if args.keep_browser_open:
            print("[bot] Browser left open by request.")
        else:
            bot.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Solver bot for the g2048 browser mini-game.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--depth", type=int, default=3, help="Expectimax depth in player moves.")
    parser.add_argument("--time-limit-ms", type=int, default=120, help="Soft time budget per decision.")
    parser.add_argument("--tile-encoding", choices=("auto", "value", "rank"), default="auto")
    parser.add_argument("--corner", choices=("top-left", "top-right", "bottom-left", "bottom-right"), default="bottom-right")
    parser.add_argument("--relaxed-corner", action="store_true", help="Allow moves away from the target row when the solver wants them.")
    parser.add_argument("--delay", type=float, nargs=2, default=(0.08, 0.18), metavar=("MIN", "MAX"))
    parser.add_argument("--max-moves", type=int, default=0, help="0 means unlimited.")
    parser.add_argument("--log-dir", default="runs")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-browser-open", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.delay[0] < 0 or args.delay[1] < args.delay[0]:
        raise SystemExit("--delay must be two values: MIN MAX, where 0 <= MIN <= MAX")
    return run_bot(args)


if __name__ == "__main__":
    raise SystemExit(main())
