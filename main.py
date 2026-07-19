from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal


Board = tuple[tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int]]
Direction = Literal["UP", "DOWN", "LEFT", "RIGHT"]
Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]

SIZE = 4
DIRECTIONS: tuple[Direction, ...] = ("UP", "DOWN", "LEFT", "RIGHT")
DEFAULT_URL = "https://battlepass.ru/special/dark_carnival#dc-games"
DEFAULT_TDL_SEARCH = "7p limit=7p,7p,6p,6p,6p,5p,5p,5p,4p,4p,4p,3p"
DEFAULT_TDL_CACHE = "256M"
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
    empty_weight: float = 850.0
    monotonicity_weight: float = 135.0
    smoothness_weight: float = 38.0
    merge_weight: float = 220.0
    corner_weight: float = 950.0
    snake_weight: float = 3.5
    avoid_move_penalty: float = 15000.0
    max_tile_move_penalty: float = 3500.0
    gain_weight: float = 1.0
    time_limit_ms: int = 120
    strict_corner: bool = False
    fast_solver: bool = True
    adaptive_depth: bool = True
    cprob_threshold: float = 0.00005
    fast_snake_weight: float = 0.0
    million_mode: bool = False


@dataclass(frozen=True)
class Decision:
    move: Direction | None
    value: float
    depth: int
    valid_moves: tuple[Direction, ...]
    elapsed_ms: float


@dataclass(frozen=True)
class RhythmPause:
    seconds: float
    phase: str
    reason: str


class RhythmController:
    def __init__(
        self,
        profile: Literal["off", "balanced", "human"],
        legacy_delay: tuple[float, float],
        legacy_rest_every: int,
        legacy_rest_delay: tuple[float, float],
    ) -> None:
        self.profile = profile
        self.legacy_delay = legacy_delay
        self.legacy_rest_every = legacy_rest_every
        self.legacy_rest_delay = legacy_rest_delay
        self._startup_used = False
        self._next_micro = self._schedule_micro(0)
        self._next_medium = self._schedule_medium(0)
        self._next_long = self._schedule_long(0)

    def startup_pause(self) -> RhythmPause:
        if self._startup_used:
            return RhythmPause(0.0, "none", "startup_already_used")
        self._startup_used = True
        if self.profile == "human":
            return RhythmPause(random.uniform(2.0, 8.0), "startup", "first_visible_board")
        if self.profile == "balanced":
            return RhythmPause(random.uniform(0.8, 2.5), "startup", "first_visible_board")
        return RhythmPause(0.0, "off", "legacy_no_startup_pause")

    def plan_after_move(
        self,
        board: Board,
        decision: Decision,
        move_number: int,
        force_loss: bool,
    ) -> RhythmPause:
        if self.profile == "off":
            return self._legacy_pause(move_number)

        if force_loss:
            seconds = random.uniform(0.7, 2.2)
            return RhythmPause(seconds, "force_loss", "safe_finish_rhythm")

        seconds = self._base_pause()
        phase = "normal"
        reasons = ["base"]

        empty_count = len(empty_cells(board))
        valid_count = len(decision.valid_moves)
        if empty_count <= 4 or valid_count <= 2:
            add = self._thinking_pause()
            seconds += add
            phase = "think"
            reasons.append(f"tight_board_empty={empty_count}_valid={valid_count}")

        scheduled = self._scheduled_pause(move_number)
        if scheduled.seconds > 0:
            seconds += scheduled.seconds
            phase = scheduled.phase
            reasons.append(scheduled.reason)

        return RhythmPause(round(seconds, 3), phase, "+".join(reasons))

    def _legacy_pause(self, move_number: int) -> RhythmPause:
        delay_min, delay_max = self.legacy_delay
        seconds = random.uniform(delay_min, delay_max)
        phase = "legacy"
        reason = "legacy_delay"
        if self.legacy_rest_every > 0 and move_number % self.legacy_rest_every == 0:
            rest_min, rest_max = self.legacy_rest_delay
            seconds += random.uniform(rest_min, rest_max)
            phase = "legacy_rest"
            reason = f"legacy_rest_every_{self.legacy_rest_every}"
        return RhythmPause(round(seconds, 3), phase, reason)

    def _base_pause(self) -> float:
        if self.profile == "human":
            return self._clamp(random.lognormvariate(math.log(0.72), 0.42), 0.35, 1.6)
        return self._clamp(random.lognormvariate(math.log(0.45), 0.35), 0.22, 1.05)

    def _thinking_pause(self) -> float:
        if self.profile == "human":
            return random.uniform(0.6, 2.8)
        return random.uniform(0.25, 1.3)

    def _scheduled_pause(self, move_number: int) -> RhythmPause:
        if move_number >= self._next_long:
            self._next_long = self._schedule_long(move_number)
            self._next_medium = self._schedule_medium(move_number)
            self._next_micro = self._schedule_micro(move_number)
            if self.profile == "human":
                return RhythmPause(random.uniform(90.0, 240.0), "long", "long_break")
            return RhythmPause(random.uniform(45.0, 120.0), "long", "long_break")
        if move_number >= self._next_medium:
            self._next_medium = self._schedule_medium(move_number)
            self._next_micro = self._schedule_micro(move_number)
            if self.profile == "human":
                return RhythmPause(random.uniform(18.0, 70.0), "medium", "medium_break")
            return RhythmPause(random.uniform(8.0, 24.0), "medium", "medium_break")
        if move_number >= self._next_micro:
            self._next_micro = self._schedule_micro(move_number)
            if self.profile == "human":
                return RhythmPause(random.uniform(2.0, 7.0), "micro", "micro_break")
            return RhythmPause(random.uniform(1.2, 4.0), "micro", "micro_break")
        return RhythmPause(0.0, "normal", "no_scheduled_break")

    def _schedule_micro(self, move_number: int) -> int:
        if self.profile == "human":
            return move_number + random.randint(18, 65)
        return move_number + random.randint(40, 100)

    def _schedule_medium(self, move_number: int) -> int:
        if self.profile == "human":
            return move_number + random.randint(220, 650)
        return move_number + random.randint(500, 1000)

    def _schedule_long(self, move_number: int) -> int:
        if self.profile == "human":
            return move_number + random.randint(2500, 5000)
        return move_number + random.randint(6000, 9000)

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))


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


def board_max_tile(board: Board) -> int:
    return max(max(row) for row in board)


def board_empty_count(board: Board) -> int:
    return sum(1 for row in board for value in row if value == 0)


def million_mode_config(board: Board, config: SolverConfig) -> SolverConfig:
    if not config.million_mode:
        return config

    max_tile = board_max_tile(board)
    empty_count = board_empty_count(board)

    if max_tile < 512:
        depth, time_ms, threshold = 7, 14, 0.006
    elif max_tile < 2048:
        depth, time_ms, threshold = 9, 28, 0.002
    elif max_tile < 4096:
        depth, time_ms, threshold = 10, 40, 0.001
    elif max_tile < 8192:
        depth, time_ms, threshold = 12, 55, 0.0005
    elif max_tile < 16384:
        depth, time_ms, threshold = 14, 90, 0.0003
    elif max_tile < 32768:
        depth, time_ms, threshold = 16, 160, 0.00015
    else:
        depth, time_ms, threshold = 20, 450, 0.00005

    if empty_count <= 2 and max_tile >= 16384:
        depth += 2
        time_ms = int(time_ms * 1.8)
        threshold *= 0.6
    elif empty_count <= 4 and max_tile >= 8192:
        depth += 1
        time_ms = int(time_ms * 1.4)
        threshold *= 0.75
    elif empty_count <= 4 and max_tile >= 4096:
        depth += 1
        time_ms = int(time_ms * 1.25)
        threshold *= 0.8
    elif empty_count >= 8 and max_tile < 4096:
        time_ms = int(time_ms * 0.75)

    return replace(
        config,
        depth=min(depth, 24),
        time_limit_ms=min(max(time_ms, 20), 1500),
        cprob_threshold=max(threshold, 0.000002),
        adaptive_depth=False,
        strict_corner=False,
    )


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
    totals = [0.0, 0.0, 0.0, 0.0]

    for row in range(SIZE):
        values = [tile_log(board[row][col]) for col in range(SIZE)]
        for index in range(SIZE - 1):
            current = values[index]
            next_value = values[index + 1]
            if current > next_value:
                totals[0] += next_value - current
            elif next_value > current:
                totals[1] += current - next_value

    for col in range(SIZE):
        values = [tile_log(board[row][col]) for row in range(SIZE)]
        for index in range(SIZE - 1):
            current = values[index]
            next_value = values[index + 1]
            if current > next_value:
                totals[2] += next_value - current
            elif next_value > current:
                totals[3] += current - next_value

    return max(totals[0], totals[1]) + max(totals[2], totals[3])


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


def force_loss_board_score(board: Board, config: SolverConfig = SolverConfig()) -> float:
    moves = valid_moves(board)
    if not moves:
        return -1_000_000_000.0

    max_tile = max(board_max_tile(board), 2)
    return (
        board_empty_count(board) * 2200.0
        + len(moves) * 1800.0
        + count_merge_potential(board) * 900.0
        + monotonicity_score(board) * 25.0
        + smoothness_score(board) * 8.0
        + corner_score(board, config.corner) * 30.0
        + tile_log(max_tile) * 25.0
    )


def expected_force_loss_score(board: Board, config: SolverConfig = SolverConfig()) -> float:
    cells = empty_cells(board)
    if not cells:
        return force_loss_board_score(board, config)

    total = 0.0
    for row, col in cells:
        total += 0.9 * force_loss_board_score(set_cell(board, row, col, 2), config)
        total += 0.1 * force_loss_board_score(set_cell(board, row, col, 4), config)
    return total / len(cells)


def choose_force_loss_move(board: Board, config: SolverConfig = SolverConfig()) -> Decision:
    moves = valid_moves(board)
    if not moves:
        return Decision(None, -1_000_000_000.0, 0, (), 0.0)

    start = time.perf_counter()
    scored: list[tuple[float, Direction]] = []
    for move in moves:
        moved_board, gain, _ = simulate_move(board, move)
        value = expected_force_loss_score(moved_board, config) + gain * 0.35
        scored.append((value, move))

    scored.sort(key=lambda item: item[0])
    best_value = scored[0][0]
    near_worst = [move for value, move in scored if value <= best_value + 450.0]
    move = random.choice(near_worst)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return Decision(move, best_value, 0, moves, elapsed_ms)


ROW_MASK = 0xFFFF
COL_MASK = 0x000F000F000F000F
MASK64 = 0xFFFFFFFFFFFFFFFF

ROW_LEFT_TABLE: list[int] = []
ROW_RIGHT_TABLE: list[int] = []
COL_UP_TABLE: list[int] = []
COL_DOWN_TABLE: list[int] = []
HEUR_SCORE_TABLE: list[float] = []
EMPTY_COUNT_TABLE: list[int] = []
DISTINCT_MASK_TABLE: list[int] = []
SNAKE_SCORE_TABLES: list[list[list[float]]] = []
BITBOARD_TABLES_READY = False

BITBOARD_DIRECTIONS: tuple[Direction, ...] = ("UP", "DOWN", "LEFT", "RIGHT")
DIRECTION_TO_INT: dict[Direction, int] = {"UP": 0, "DOWN": 1, "LEFT": 2, "RIGHT": 3}
INT_TO_DIRECTION: tuple[Direction, ...] = ("UP", "DOWN", "LEFT", "RIGHT")

SCORE_LOST_PENALTY = 200000.0
SCORE_MONOTONICITY_POWER = 4.0
SCORE_MONOTONICITY_WEIGHT = 47.0
SCORE_SUM_POWER = 3.5
SCORE_SUM_WEIGHT = 11.0
SCORE_MERGES_WEIGHT = 700.0
SCORE_EMPTY_WEIGHT = 270.0
SCORE_SNAKE_WEIGHT = 7.5


def reverse_row(row: int) -> int:
    return ((row >> 12) | ((row >> 4) & 0x00F0) | ((row << 4) & 0x0F00) | ((row << 12) & 0xF000)) & ROW_MASK


def unpack_col(row: int) -> int:
    return (row | (row << 12) | (row << 24) | (row << 36)) & COL_MASK


def transpose_bitboard(board: int) -> int:
    a1 = board & 0xF0F00F0FF0F00F0F
    a2 = board & 0x0000F0F00000F0F0
    a3 = board & 0x0F0F00000F0F0000
    a = a1 | ((a2 << 12) & MASK64) | (a3 >> 12)
    b1 = a & 0xFF00FF0000FF00FF
    b2 = a & 0x00FF00FF00000000
    b3 = a & 0x00000000FF00FF00
    return (b1 | (b2 >> 24) | ((b3 << 24) & MASK64)) & MASK64


def row_to_values(row: int) -> list[int]:
    return [(row >> (4 * index)) & 0xF for index in range(SIZE)]


def values_to_row(values: Iterable[int]) -> int:
    row = 0
    for index, value in enumerate(values):
        row |= (int(value) & 0xF) << (4 * index)
    return row & ROW_MASK


def snake_weight_grid(corner: Corner) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int]]:
    weights = [[0] * SIZE for _ in range(SIZE)]
    path = snake_path(corner)
    for index, (row, col) in enumerate(path):
        weight = SIZE * SIZE - index
        weights[row][col] = weight * weight
    return tuple(tuple(row) for row in weights)  # type: ignore[return-value]


def move_row_left_values(values: list[int]) -> list[int]:
    non_zero = [value for value in values if value != 0]
    result: list[int] = []
    index = 0

    while index < len(non_zero):
        current = non_zero[index]
        if index + 1 < len(non_zero) and current == non_zero[index + 1]:
            result.append(min(current + 1, 0xF))
            index += 2
        else:
            result.append(current)
            index += 1

    result.extend([0] * (SIZE - len(result)))
    return result[:SIZE]


def init_bitboard_tables() -> None:
    global ROW_LEFT_TABLE, ROW_RIGHT_TABLE, COL_UP_TABLE, COL_DOWN_TABLE
    global HEUR_SCORE_TABLE, EMPTY_COUNT_TABLE, DISTINCT_MASK_TABLE, BITBOARD_TABLES_READY

    if BITBOARD_TABLES_READY:
        return

    ROW_LEFT_TABLE = [0] * 65536
    ROW_RIGHT_TABLE = [0] * 65536
    COL_UP_TABLE = [0] * 65536
    COL_DOWN_TABLE = [0] * 65536
    HEUR_SCORE_TABLE = [0.0] * 65536
    EMPTY_COUNT_TABLE = [0] * 65536
    DISTINCT_MASK_TABLE = [0] * 65536

    for row in range(65536):
        line = row_to_values(row)

        empty = 0
        merges = 0
        previous = 0
        counter = 0
        rank_sum = 0.0
        distinct_mask = 0

        for rank in line:
            rank_sum += rank**SCORE_SUM_POWER
            if rank == 0:
                empty += 1
            else:
                distinct_mask |= 1 << rank
                if previous == rank:
                    counter += 1
                elif counter > 0:
                    merges += 1 + counter
                    counter = 0
                previous = rank
        if counter > 0:
            merges += 1 + counter

        monotonicity_left = 0.0
        monotonicity_right = 0.0
        for index in range(1, SIZE):
            previous_rank = line[index - 1]
            current_rank = line[index]
            if previous_rank > current_rank:
                monotonicity_left += previous_rank**SCORE_MONOTONICITY_POWER - current_rank**SCORE_MONOTONICITY_POWER
            else:
                monotonicity_right += current_rank**SCORE_MONOTONICITY_POWER - previous_rank**SCORE_MONOTONICITY_POWER

        HEUR_SCORE_TABLE[row] = (
            SCORE_LOST_PENALTY
            + SCORE_EMPTY_WEIGHT * empty
            + SCORE_MERGES_WEIGHT * merges
            - SCORE_MONOTONICITY_WEIGHT * min(monotonicity_left, monotonicity_right)
            - SCORE_SUM_WEIGHT * rank_sum
        )
        EMPTY_COUNT_TABLE[row] = empty
        DISTINCT_MASK_TABLE[row] = distinct_mask

        result = values_to_row(move_row_left_values(line))
        reverse_original = reverse_row(row)
        reverse_result = reverse_row(result)

        ROW_LEFT_TABLE[row] = row ^ result
        ROW_RIGHT_TABLE[reverse_original] = reverse_original ^ reverse_result
        COL_UP_TABLE[row] = unpack_col(row) ^ unpack_col(result)
        COL_DOWN_TABLE[reverse_original] = unpack_col(reverse_original) ^ unpack_col(reverse_result)

    BITBOARD_TABLES_READY = True


def init_snake_score_tables() -> None:
    global SNAKE_SCORE_TABLES

    if SNAKE_SCORE_TABLES:
        return

    snake_grids = [snake_weight_grid(corner) for corner in ("top-left", "top-right", "bottom-left", "bottom-right")]
    tables = [[[0.0] * 65536 for _ in range(SIZE)] for _ in snake_grids]
    for row in range(65536):
        line = row_to_values(row)
        for grid_index, grid in enumerate(snake_grids):
            for board_row in range(SIZE):
                tables[grid_index][board_row][row] = sum(
                    (rank**2) * grid[board_row][col]
                    for col, rank in enumerate(line)
                    if rank
                )
    SNAKE_SCORE_TABLES = tables


def board_to_bitboard(board: Board) -> int:
    packed = 0
    for row in range(SIZE):
        for col in range(SIZE):
            value = board[row][col]
            rank = 0 if value <= 0 else value.bit_length() - 1
            packed |= (rank & 0xF) << (4 * (row * SIZE + col))
    return packed & MASK64


def bitboard_to_board(board: int) -> Board:
    rows: list[list[int]] = []
    for row in range(SIZE):
        values = []
        for col in range(SIZE):
            rank = (board >> (4 * (row * SIZE + col))) & 0xF
            values.append(0 if rank == 0 else 1 << rank)
        rows.append(values)
    return as_board(rows)


def bitboard_count_empty(board: int) -> int:
    init_bitboard_tables()
    return bitboard_count_empty_raw(board)


def bitboard_count_empty_raw(board: int) -> int:
    return (
        EMPTY_COUNT_TABLE[(board >> 0) & ROW_MASK]
        + EMPTY_COUNT_TABLE[(board >> 16) & ROW_MASK]
        + EMPTY_COUNT_TABLE[(board >> 32) & ROW_MASK]
        + EMPTY_COUNT_TABLE[(board >> 48) & ROW_MASK]
    )


def bitboard_count_distinct(board: int) -> int:
    init_bitboard_tables()
    return bitboard_count_distinct_raw(board)


def bitboard_count_distinct_raw(board: int) -> int:
    mask = (
        DISTINCT_MASK_TABLE[(board >> 0) & ROW_MASK]
        | DISTINCT_MASK_TABLE[(board >> 16) & ROW_MASK]
        | DISTINCT_MASK_TABLE[(board >> 32) & ROW_MASK]
        | DISTINCT_MASK_TABLE[(board >> 48) & ROW_MASK]
    )
    return mask.bit_count()


def bitboard_score_heuristic(board: int, snake_weight: float = SCORE_SNAKE_WEIGHT) -> float:
    init_bitboard_tables()
    return bitboard_score_heuristic_raw(board, snake_weight)


def bitboard_score_heuristic_raw(board: int, snake_weight: float = SCORE_SNAKE_WEIGHT) -> float:
    transposed = transpose_bitboard(board)
    base_score = (
        HEUR_SCORE_TABLE[(board >> 0) & ROW_MASK]
        + HEUR_SCORE_TABLE[(board >> 16) & ROW_MASK]
        + HEUR_SCORE_TABLE[(board >> 32) & ROW_MASK]
        + HEUR_SCORE_TABLE[(board >> 48) & ROW_MASK]
        + HEUR_SCORE_TABLE[(transposed >> 0) & ROW_MASK]
        + HEUR_SCORE_TABLE[(transposed >> 16) & ROW_MASK]
        + HEUR_SCORE_TABLE[(transposed >> 32) & ROW_MASK]
        + HEUR_SCORE_TABLE[(transposed >> 48) & ROW_MASK]
    )
    if snake_weight == 0.0:
        return base_score

    init_snake_score_tables()
    rows = (
        (board >> 0) & ROW_MASK,
        (board >> 16) & ROW_MASK,
        (board >> 32) & ROW_MASK,
        (board >> 48) & ROW_MASK,
    )
    snake_score = max(
        table[0][rows[0]] + table[1][rows[1]] + table[2][rows[2]] + table[3][rows[3]]
        for table in SNAKE_SCORE_TABLES
    )
    return base_score + snake_weight * snake_score


def bitboard_execute_move(board: int, move: Direction) -> int:
    init_bitboard_tables()
    return bitboard_execute_move_raw(board, DIRECTION_TO_INT[move])


def bitboard_execute_move_raw(board: int, move: int) -> int:
    result = board

    if move == 0:
        transposed = transpose_bitboard(board)
        result ^= (COL_UP_TABLE[(transposed >> 0) & ROW_MASK] << 0) & MASK64
        result ^= (COL_UP_TABLE[(transposed >> 16) & ROW_MASK] << 4) & MASK64
        result ^= (COL_UP_TABLE[(transposed >> 32) & ROW_MASK] << 8) & MASK64
        result ^= (COL_UP_TABLE[(transposed >> 48) & ROW_MASK] << 12) & MASK64
    elif move == 1:
        transposed = transpose_bitboard(board)
        result ^= (COL_DOWN_TABLE[(transposed >> 0) & ROW_MASK] << 0) & MASK64
        result ^= (COL_DOWN_TABLE[(transposed >> 16) & ROW_MASK] << 4) & MASK64
        result ^= (COL_DOWN_TABLE[(transposed >> 32) & ROW_MASK] << 8) & MASK64
        result ^= (COL_DOWN_TABLE[(transposed >> 48) & ROW_MASK] << 12) & MASK64
    elif move == 2:
        result ^= (ROW_LEFT_TABLE[(board >> 0) & ROW_MASK] << 0) & MASK64
        result ^= (ROW_LEFT_TABLE[(board >> 16) & ROW_MASK] << 16) & MASK64
        result ^= (ROW_LEFT_TABLE[(board >> 32) & ROW_MASK] << 32) & MASK64
        result ^= (ROW_LEFT_TABLE[(board >> 48) & ROW_MASK] << 48) & MASK64
    elif move == 3:
        result ^= (ROW_RIGHT_TABLE[(board >> 0) & ROW_MASK] << 0) & MASK64
        result ^= (ROW_RIGHT_TABLE[(board >> 16) & ROW_MASK] << 16) & MASK64
        result ^= (ROW_RIGHT_TABLE[(board >> 32) & ROW_MASK] << 32) & MASK64
        result ^= (ROW_RIGHT_TABLE[(board >> 48) & ROW_MASK] << 48) & MASK64
    else:
        raise ValueError(f"Unknown direction: {move}")

    return result & MASK64


def bitboard_all_moves_raw(board: int) -> tuple[int, int, int, int]:
    transposed = transpose_bitboard(board)

    up = board
    up ^= (COL_UP_TABLE[(transposed >> 0) & ROW_MASK] << 0) & MASK64
    up ^= (COL_UP_TABLE[(transposed >> 16) & ROW_MASK] << 4) & MASK64
    up ^= (COL_UP_TABLE[(transposed >> 32) & ROW_MASK] << 8) & MASK64
    up ^= (COL_UP_TABLE[(transposed >> 48) & ROW_MASK] << 12) & MASK64

    down = board
    down ^= (COL_DOWN_TABLE[(transposed >> 0) & ROW_MASK] << 0) & MASK64
    down ^= (COL_DOWN_TABLE[(transposed >> 16) & ROW_MASK] << 4) & MASK64
    down ^= (COL_DOWN_TABLE[(transposed >> 32) & ROW_MASK] << 8) & MASK64
    down ^= (COL_DOWN_TABLE[(transposed >> 48) & ROW_MASK] << 12) & MASK64

    left = board
    left ^= (ROW_LEFT_TABLE[(board >> 0) & ROW_MASK] << 0) & MASK64
    left ^= (ROW_LEFT_TABLE[(board >> 16) & ROW_MASK] << 16) & MASK64
    left ^= (ROW_LEFT_TABLE[(board >> 32) & ROW_MASK] << 32) & MASK64
    left ^= (ROW_LEFT_TABLE[(board >> 48) & ROW_MASK] << 48) & MASK64

    right = board
    right ^= (ROW_RIGHT_TABLE[(board >> 0) & ROW_MASK] << 0) & MASK64
    right ^= (ROW_RIGHT_TABLE[(board >> 16) & ROW_MASK] << 16) & MASK64
    right ^= (ROW_RIGHT_TABLE[(board >> 32) & ROW_MASK] << 32) & MASK64
    right ^= (ROW_RIGHT_TABLE[(board >> 48) & ROW_MASK] << 48) & MASK64

    return up & MASK64, down & MASK64, left & MASK64, right & MASK64


def bitboard_empty_tiles(board: int) -> list[int]:
    return [index for index in range(SIZE * SIZE) if ((board >> (4 * index)) & 0xF) == 0]


def choose_best_move_fast(board: Board, config: SolverConfig = SolverConfig()) -> Decision:
    init_bitboard_tables()
    start = time.perf_counter()
    deadline = start + (max(config.time_limit_ms, 1) / 1000.0)
    packed = board_to_bitboard(board)

    root_next_boards = bitboard_all_moves_raw(packed)
    move_ints = tuple(move for move, next_board in enumerate(root_next_boards) if next_board != packed)
    moves = tuple(INT_TO_DIRECTION[move] for move in move_ints)
    if not moves:
        return Decision(None, -1_000_000_000.0, config.depth, (), 0.0)

    root_moves = tuple(DIRECTION_TO_INT[move] for move in policy_moves(moves, config))

    nodes = 0

    def check_deadline() -> None:
        nonlocal nodes
        nodes += 1
        if nodes % 2048 == 0 and time.perf_counter() > deadline:
            raise SearchTimeout()

    cache: dict[tuple[int, int], float] = {}

    def tile_node(current: int, moves_left: int, cprob: float) -> float:
        check_deadline()
        if cprob < config.cprob_threshold or moves_left <= 0:
            return bitboard_score_heuristic_raw(current, config.fast_snake_weight)

        key = (current, moves_left)
        cached = cache.get(key)
        if cached is not None:
            return cached

        num_open = bitboard_count_empty_raw(current)
        if num_open == 0:
            value = move_node(current, moves_left, cprob)
            cache[key] = value
            return value

        cell_probability = cprob / num_open
        total = 0.0
        tmp = current
        tile_2 = 1
        while tile_2 <= 0x1000000000000000:
            if (tmp & 0xF) == 0:
                total += 0.9 * move_node(current | tile_2, moves_left - 1, cell_probability * 0.9)
                total += 0.1 * move_node(current | (tile_2 << 1), moves_left - 1, cell_probability * 0.1)
            tmp >>= 4
            tile_2 <<= 4

        value = total / num_open
        cache[key] = value
        return value

    def move_node(current: int, moves_left: int, cprob: float) -> float:
        check_deadline()
        best = -float("inf")
        for next_board in bitboard_all_moves_raw(current):
            if next_board != current:
                best = max(best, tile_node(next_board, moves_left, cprob))
        return best if best != -float("inf") else -1_000_000_000.0

    def run_search(depth_limit: int) -> tuple[Direction | None, float]:
        moves_left = max(depth_limit - 1, 0)

        best_move: Direction | None = None
        best_value = -float("inf")
        for move in root_moves:
            next_board = root_next_boards[move]
            if next_board == packed:
                continue
            value = tile_node(next_board, moves_left, 1.0)
            if value > best_value:
                best_value = value
                best_move = INT_TO_DIRECTION[move]
        return best_move, best_value

    completed_depth = 0
    best_move: Direction | None = None
    best_value = -float("inf")
    if config.adaptive_depth:
        max_depth = min(max(config.depth, 1), max(3, bitboard_count_distinct_raw(packed) - 2))
    else:
        max_depth = max(config.depth, 1)
    min_depth = min(3, max_depth)

    for depth_limit in range(1, max_depth + 1):
        if depth_limit < min_depth and time.perf_counter() > deadline:
            break
        try:
            depth_move, depth_value = run_search(depth_limit)
        except SearchTimeout:
            break
        if depth_move is not None:
            best_move = depth_move
            best_value = depth_value
            completed_depth = depth_limit

    if best_move is None:
        for move in root_moves:
            next_board = root_next_boards[move]
            if next_board == packed:
                continue
            value = bitboard_score_heuristic_raw(next_board, config.fast_snake_weight)
            if value > best_value:
                best_value = value
                best_move = INT_TO_DIRECTION[move]
        completed_depth = 0

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return Decision(best_move, best_value, completed_depth, moves, elapsed_ms)


def choose_best_move_legacy(board: Board, config: SolverConfig = SolverConfig()) -> Decision:
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
    completed_depth = 0

    for search_depth in range(1, max(config.depth, 1) + 1):
        depth_best_move: Direction | None = None
        depth_best_value = -float("inf")

        try:
            for move in root_moves:
                moved_board, gain, _ = simulate_move(board, move)
                value = (
                    config.gain_weight * gain
                    + chance_value(moved_board, search_depth - 1)
                    + move_policy_adjustment(board, moved_board, move, config, len(moves))
                )

                if value > depth_best_value:
                    depth_best_value = value
                    depth_best_move = move
        except SearchTimeout:
            break

        if depth_best_move is not None:
            best_move = depth_best_move
            best_value = depth_best_value
            completed_depth = search_depth

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
        completed_depth = 0

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return Decision(best_move, best_value, completed_depth, moves, elapsed_ms)


def choose_best_move(board: Board, config: SolverConfig = SolverConfig()) -> Decision:
    if config.fast_solver:
        return choose_best_move_fast(board, config)
    return choose_best_move_legacy(board, config)


class TDLSolverClient:
    def __init__(
        self,
        executable: Path | str,
        model_path: Path | str,
        network: str = "4x6patt",
        search: str = DEFAULT_TDL_SEARCH,
        cache: str = DEFAULT_TDL_CACHE,
        cache_peek: bool = True,
    ) -> None:
        exe_path = Path(executable)
        weights_path = Path(model_path)
        if not exe_path.exists():
            raise RuntimeError(f"TDL2048 executable not found: {exe_path}")
        if not weights_path.exists():
            raise RuntimeError(f"TDL2048 model not found: {weights_path}")

        env = os.environ.copy()
        msys_paths = [Path("C:/msys64/ucrt64/bin"), Path("C:/msys64/usr/bin")]
        env["PATH"] = os.pathsep.join(str(path) for path in msys_paths if path.exists()) + os.pathsep + env.get("PATH", "")

        command = [
            str(exe_path),
            "--protocol",
            "-n",
            network,
            "-i",
            str(weights_path),
            "-S",
            search,
        ]
        if cache:
            command.extend(("-c", cache))
            if cache_peek:
                command.append("peek")

        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("Failed to open TDL solver pipes")

        started = time.perf_counter()
        while True:
            if time.perf_counter() - started > 20:
                self.close()
                raise RuntimeError("TDL solver did not become ready before timeout")
            line = self.process.stdout.readline()
            if not line:
                self.close()
                raise RuntimeError("TDL solver returned no startup output")
            if line.strip() == "READY":
                break

    def choose_best_move(self, board: Board, config: SolverConfig) -> Decision:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("TDL solver process is closed")

        packed = board_to_bitboard(board)
        started = time.perf_counter()
        self.process.stdin.write(f"SOLVE {packed:016x}\n")
        self.process.stdin.flush()

        line = self.process.stdout.readline().strip()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if not line:
            raise RuntimeError("TDL solver returned no response")
        parts = line.split()
        if parts[0] != "OK" or len(parts) < 6:
            raise RuntimeError(f"TDL solver error: {line}")

        move = None if parts[1] == "NONE" else parts[1]
        if move is not None and move not in DIRECTIONS:
            raise RuntimeError(f"TDL solver returned invalid move: {move}")

        return Decision(
            move,  # type: ignore[arg-type]
            float(parts[3]),
            int(parts[2]),
            valid_moves(board),
            float(parts[4]) if parts[4] else elapsed_ms,
        )

    def close(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.write("QUIT\n")
                self.process.stdin.flush()
        except Exception:
            pass
        try:
            self.process.wait(timeout=2)
        except Exception:
            self.process.kill()


def resolve_tdl_model(base_dir: Path, network: str) -> tuple[str, Path]:
    model_dir = base_dir / "external" / "TDL2048"
    candidates = [network] if network != "auto" else ["8x6patt", "7x6patt", "6x6patt", "5x6patt", "4x6patt"]
    for candidate in candidates:
        path = model_dir / f"{candidate}.w"
        if path.exists():
            return candidate, path
    fallback = candidates[0]
    return fallback, model_dir / f"{fallback}.w"


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


BrowserName = Literal["firefox", "chrome", "edge"]


class Selenium2048Bot:
    def __init__(self, url: str, encoding: Literal["auto", "value", "rank"], browser: BrowserName = "firefox") -> None:
        self.url = url
        self.encoding = encoding
        self.browser = browser
        self.driver = self._create_driver()

    def _create_driver(self) -> Any:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service as ChromeService
            from selenium.webdriver.edge.service import Service as EdgeService
            from selenium.webdriver.firefox.service import Service as FirefoxService
            from webdriver_manager.chrome import ChromeDriverManager
            from webdriver_manager.microsoft import EdgeChromiumDriverManager
            from webdriver_manager.firefox import GeckoDriverManager
        except ImportError as exc:
            raise RuntimeError(
                "Missing browser dependencies. Install them with: "
                "python -m pip install selenium webdriver-manager"
            ) from exc

        if self.browser == "chrome":
            options = webdriver.ChromeOptions()
            options.add_argument("--disable-blink-features=AutomationControlled")
            profile_dir = Path("runs/chrome_profile").resolve()
            profile_dir.mkdir(parents=True, exist_ok=True)
            options.add_argument(f"--user-data-dir={profile_dir}")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
            options.add_experimental_option("detach", True)
            options.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})
            return webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        if self.browser == "edge":
            options = webdriver.EdgeOptions()
            options.add_argument("--disable-blink-features=AutomationControlled")
            return webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()), options=options)

        options = webdriver.FirefoxOptions()
        options.log.level = "fatal"
        firefox_path = Path("C:/Program Files/Mozilla Firefox/firefox.exe")
        if firefox_path.exists():
            options.binary_location = str(firefox_path)
        return webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), options=options)

    def open_game(self) -> None:
        self.driver.get(self.url)
        print(f"\n[bot] {self.browser.title()} открыт.")
        print("[bot] Что делать в браузере:")
        print("[bot]   1. Если сайт просит вход - войдите в BattlePass.")
        print("[bot]   2. Откройте миниигру 2048 и нажмите Play/Продолжить.")
        print("[bot]   3. Когда появится поле 4x4 с плитками, бот начнет играть сам.")
        print("[bot]   4. Во время игры не закрывайте браузер и не нажимайте стрелки вручную.")
        last_notice = 0.0
        while True:
            board = self.read_board()
            if board is not None and any(value for row in board for value in row):
                print("[bot] Поле найдено, начинаю автопроход.")
                return
            now = time.perf_counter()
            if now - last_notice >= 5.0:
                print("[bot] Жду видимое поле 4x4. Если бот долго ждет - нажмите Play/Продолжить в открытом Chrome.")
                last_notice = now
            time.sleep(0.25)

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

    def wait_for_board_change(self, previous: Board, timeout: float) -> Board | None:
        deadline = time.perf_counter() + timeout
        latest: Board | None = None
        while time.perf_counter() < deadline:
            latest = self.read_board()
            if latest is not None and latest != previous:
                return latest
            time.sleep(0.04)
        return latest

    def close(self) -> None:
        self.driver.quit()


def write_jsonl(log_path: Path | None, payload: dict[str, Any]) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=True) + "\n"
    for _ in range(5):
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            return
        except PermissionError:
            time.sleep(0.05)
    print(f"[bot] Warning: could not write log line to {log_path}")


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")[:80] or "snapshot"


def capture_browser_snapshot(
    bot: Selenium2048Bot,
    log_dir: str | None,
    reason: str,
    move_number: int,
    board: Board | None,
    decision: Decision | None,
    estimated_score: int,
) -> Path | None:
    if not log_dir:
        return None

    diag_dir = Path(log_dir) / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    prefix = diag_dir / f"{int(time.time())}-{safe_filename(reason)}-move-{move_number}"
    driver = bot.driver

    meta: dict[str, Any] = {
        "reason": reason,
        "move_number": move_number,
        "estimated_score": estimated_score,
        "board": board,
        "decision": decision.__dict__ if decision else None,
        "timestamp": time.time(),
    }

    for key, getter in (
        ("url", lambda: driver.current_url),
        ("title", lambda: driver.title),
        ("window_handles", lambda: driver.window_handles),
    ):
        try:
            meta[key] = getter()
        except Exception as exc:
            meta[f"{key}_error"] = repr(exc)

    try:
        driver.save_screenshot(str(prefix.with_suffix(".png")))
        meta["screenshot"] = str(prefix.with_suffix(".png"))
    except Exception as exc:
        meta["screenshot_error"] = repr(exc)

    try:
        prefix.with_suffix(".html").write_text(driver.page_source, encoding="utf-8")
        meta["html"] = str(prefix.with_suffix(".html"))
    except Exception as exc:
        meta["html_error"] = repr(exc)

    script = """
    const visibleText = (el) => (el.innerText || el.textContent || '').trim().slice(0, 500);
    const describe = (el) => ({
      tag: el.tagName,
      id: el.id || '',
      className: el.className || '',
      text: visibleText(el),
      disabled: !!el.disabled,
      aria: el.getAttribute('aria-label') || '',
      role: el.getAttribute('role') || '',
      html: (el.outerHTML || '').slice(0, 700),
    });
    const interesting = [
      ...document.querySelectorAll('button,a,[role="button"],input[type="button"],input[type="submit"]'),
      ...document.querySelectorAll('[class*="2048"],[class*="g2048"],[class*="game"],[class*="score"],[class*="modal"],[class*="result"],[class*="over"],[class*="submit"]')
    ];
    return {
      readyState: document.readyState,
      url: location.href,
      title: document.title,
      bodyText: (document.body ? document.body.innerText : '').slice(0, 20000),
      localStorageKeys: Object.keys(localStorage || {}).slice(0, 200),
      sessionStorageKeys: Object.keys(sessionStorage || {}).slice(0, 200),
      interesting: interesting.slice(0, 160).map(describe),
    };
    """
    try:
        meta["dom"] = driver.execute_script(script)
    except Exception as exc:
        meta["dom_error"] = repr(exc)

    for log_name in ("browser", "performance"):
        try:
            logs = driver.get_log(log_name)
            log_path = prefix.with_name(prefix.name + f"-{log_name}.json")
            log_path.write_text(json.dumps(logs[-2000:], ensure_ascii=True, indent=2), encoding="utf-8")
            meta[f"{log_name}_log"] = str(log_path)
            meta[f"{log_name}_log_count"] = len(logs)
        except Exception as exc:
            meta[f"{log_name}_log_error"] = repr(exc)

    summary_path = prefix.with_suffix(".json")
    summary_path.write_text(json.dumps(meta, ensure_ascii=True, indent=2), encoding="utf-8")
    print(f"[bot] Diagnostic snapshot saved: {summary_path}")
    return summary_path


def hold_browser_after_end(bot: Selenium2048Bot, seconds: int, reason: str) -> None:
    if seconds <= 0:
        return
    print(f"[bot] Удерживаю браузер {seconds}s после события: {reason}.")
    print("[bot] Не закрывайте Chrome: если сайт показывает кнопку сохранения/получения результата, нажмите ее вручную.")
    deadline = time.perf_counter() + seconds
    next_notice = 0.0
    while time.perf_counter() < deadline:
        now = time.perf_counter()
        if now >= next_notice:
            remaining = int(deadline - now)
            try:
                print(f"[bot] Окно удерживается: осталось {remaining}s, title={bot.driver.title!r}, url={bot.driver.current_url}")
            except Exception as exc:
                print(f"[bot] Не смог проверить браузер во время удержания: {exc!r}")
                return
            next_notice = now + 30.0
        time.sleep(1.0)


def print_launch_guide(args: argparse.Namespace, log_path: Path | None, solver_label: str) -> None:
    delay_min, delay_max = args.delay
    if args.rhythm_profile == "off" and args.rest_every > 0:
        rest_min, rest_max = args.rest_delay
        rest_text = f"каждые {args.rest_every} ходов пауза {rest_min:.1f}-{rest_max:.1f}s"
    elif args.rhythm_profile == "off":
        rest_text = "длинные паузы выключены"
    else:
        rest_text = "нерегулярные micro/medium/long паузы"
    log_text = str(log_path) if log_path else "выключены"

    print()
    print("=" * 72)
    print("[bot] Памятка запуска")
    print(f"[bot]   Браузер: {args.browser}. Решатель: {solver_label}.")
    if args.rhythm_profile == "off":
        rhythm_text = f"legacy/off: ход {delay_min:.2f}-{delay_max:.2f}s, {rest_text}"
    else:
        rhythm_text = f"{args.rhythm_profile}: {rest_text}"
    print(f"[bot]   Ритм ходов: {rhythm_text}.")
    print(f"[bot]   Лог ходов: {log_text}")
    print("[bot]   Сейчас откроется Chrome. Дальше работайте только в этом окне.")
    print("[bot]   Если игра не началась сама - авторизуйтесь, откройте 2048 и нажмите Play/Продолжить.")
    print("[bot]   После конца игры браузер останется открытым, чтобы сайт успел засчитать результат.")
    print("=" * 72)


def run_bot(args: argparse.Namespace) -> int:
    config = SolverConfig(
        depth=args.depth,
        tile_encoding=args.tile_encoding,
        corner=args.corner,
        time_limit_ms=args.time_limit_ms,
        strict_corner=args.strict_corner and not args.relaxed_corner,
        fast_solver=not args.legacy_solver,
        adaptive_depth=not args.fixed_depth,
        cprob_threshold=args.cprob_threshold,
        fast_snake_weight=args.fast_snake_weight,
        million_mode=args.million_mode,
    )
    log_path = Path(args.log_dir) / f"run-{int(time.time())}.jsonl" if args.log_dir else None
    rhythm = RhythmController(args.rhythm_profile, tuple(args.delay), args.rest_every, tuple(args.rest_delay))
    base_dir = Path(__file__).resolve().parent
    tdl_exe = base_dir / "external" / "TDL2048" / "tdl2048.exe"
    tdl_network, tdl_model = resolve_tdl_model(base_dir, args.tdl_network)
    tdl_solver_client: TDLSolverClient | None = None
    solver_label = "Python"

    if args.solver_backend in ("auto", "tdl") and tdl_exe.exists() and tdl_model.exists():
        cache_label = f" cache={args.tdl_cache}{'+peek' if args.tdl_cache_peek else ''}" if args.tdl_cache else " cache=off"
        solver_label = f"TDL2048 {tdl_network}/{args.tdl_search}{cache_label}"
        print(f"[bot] Запускаю решатель {solver_label}...")
        tdl_solver_client = TDLSolverClient(
            tdl_exe,
            tdl_model,
            network=tdl_network,
            search=args.tdl_search,
            cache=args.tdl_cache,
            cache_peek=args.tdl_cache_peek,
        )
        print("[bot] Решатель TDL2048 готов.")
    elif args.solver_backend == "tdl":
        raise RuntimeError("TDL2048 solver/model not found. Build external/TDL2048/tdl2048.exe and download 4x6patt.w.")
    else:
        print("[bot] Грею таблицы Python-решателя...")
        warmup_start = time.perf_counter()
        init_bitboard_tables()
        if config.fast_snake_weight != 0.0:
            init_snake_score_tables()
        print(f"[bot] Python решатель готов за {(time.perf_counter() - warmup_start):.2f}s.")

    print_launch_guide(args, log_path, solver_label)
    bot = Selenium2048Bot(args.url, args.tile_encoding, args.browser)
    move_number = 0
    estimated_score = 0
    last_board: Board | None = None
    last_decision: Decision | None = None
    last_move_sent_at: float | None = None
    force_loss_active = False
    try:
        bot.open_game()

        while args.max_moves <= 0 or move_number < args.max_moves:
            board = bot.read_board()
            if board is None:
                print("[bot] Waiting for board...")
                time.sleep(1.0)
                continue

            startup_pause = rhythm.startup_pause()
            if startup_pause.seconds > 0:
                print(f"[bot] стартовая пауза={startup_pause.seconds:.2f}s rhythm={startup_pause.phase}")
                time.sleep(startup_pause.seconds)

            turn_config = million_mode_config(board, config)
            should_force_loss = (
                (args.force_loss_after_moves > 0 and move_number >= args.force_loss_after_moves)
                or (args.force_loss_after_score > 0 and estimated_score >= args.force_loss_after_score)
            )
            if should_force_loss:
                if not force_loss_active:
                    force_loss_active = True
                    print("[bot] Force-loss mode enabled: finishing before the server move limit.")
                decision = choose_force_loss_move(board, turn_config)
            elif tdl_solver_client:
                decision = tdl_solver_client.choose_best_move(board, turn_config)
            else:
                decision = choose_best_move(board, turn_config)
            last_board = board
            last_decision = decision

            predicted_gain = 0
            if decision.move is not None:
                _, predicted_gain, predicted_moved = simulate_move(board, decision.move)
                if not predicted_moved:
                    predicted_gain = 0
            planned_pause = rhythm.plan_after_move(board, decision, move_number + 1, force_loss_active)
            actual_elapsed_since_prev_move = (
                time.perf_counter() - last_move_sent_at if last_move_sent_at is not None else None
            )
            if args.debug:
                print("\n" + format_board(board))
                print(
                    f"[solver] move={decision.move} value={decision.value:.2f} "
                    f"valid={decision.valid_moves} depth={decision.depth}/{turn_config.depth} "
                    f"time_budget={turn_config.time_limit_ms}ms threshold={turn_config.cprob_threshold:g} "
                    f"elapsed={decision.elapsed_ms:.1f}ms"
                )

            write_jsonl(
                log_path,
                {
                    "move_number": move_number,
                    "board": board,
                    "decision": decision.__dict__,
                    "estimated_score": estimated_score,
                    "predicted_gain": predicted_gain,
                    "rhythm_profile": args.rhythm_profile,
                    "planned_sleep": planned_pause.seconds,
                    "sleep_reason": planned_pause.reason,
                    "rhythm_phase": planned_pause.phase,
                    "actual_elapsed_since_prev_move": actual_elapsed_since_prev_move,
                    "solver_config": {
                        "tdl_search": args.tdl_search if tdl_solver_client else None,
                        "tdl_cache": args.tdl_cache if tdl_solver_client else None,
                        "tdl_cache_peek": args.tdl_cache_peek if tdl_solver_client else None,
                        "depth": turn_config.depth,
                        "time_limit_ms": turn_config.time_limit_ms,
                        "cprob_threshold": turn_config.cprob_threshold,
                        "million_mode": turn_config.million_mode,
                    },
                    "timestamp": time.time(),
                },
            )

            if decision.move is None:
                print("[bot] Нет легальных ходов. Игра, скорее всего, закончилась.")
                print("[bot] Сейчас сохраню диагностику и оставлю браузер открытым для засчета результата.")
                capture_browser_snapshot(
                    bot,
                    args.log_dir,
                    "game-over",
                    move_number,
                    board,
                    decision,
                    estimated_score,
                )
                hold_browser_after_end(bot, args.post_game_hold, "game over")
                return 0

            if args.dry_run:
                print(f"[dry-run] would play {decision.move}")
                return 0

            bot.execute_move(decision.move)
            last_move_sent_at = time.perf_counter()
            estimated_score += predicted_gain
            move_number += 1

            changed_board = bot.wait_for_board_change(board, args.after_move_timeout)
            if changed_board is None:
                print("[bot] Ход отправлен, но поле временно не читается. Продолжаю аккуратно.")
            elif changed_board == board:
                print("[bot] Ход отправлен, но поле не изменилось до таймаута. Продолжаю аккуратно.")

            print(
                f"[bot] ход={move_number} сыграно={decision.move} "
                f"счет~={estimated_score} прирост={predicted_gain} "
                f"depth={decision.depth}/{turn_config.depth} budget={turn_config.time_limit_ms}ms "
                f"пауза={planned_pause.seconds:.2f}s rhythm={planned_pause.phase} value={decision.value:.2f}"
                f"{' force_loss=1' if force_loss_active else ''}"
            )
            time.sleep(planned_pause.seconds)

        print(f"[bot] Достигнут лимит ходов: {args.max_moves}")
        print("[bot] Сейчас сохраню диагностику и оставлю браузер открытым для засчета результата.")
        capture_browser_snapshot(
            bot,
            args.log_dir,
            "max-moves",
            move_number,
            last_board,
            last_decision,
            estimated_score,
        )
        hold_browser_after_end(bot, args.post_game_hold, "max moves")
        return 0
    except Exception as exc:
        print(f"[bot] Критическая ошибка: {exc!r}")
        print("[bot] Постараюсь сохранить снимок страницы и удержать браузер, чтобы можно было разобраться.")
        try:
            capture_browser_snapshot(
                bot,
                args.log_dir,
                "exception",
                move_number,
                last_board,
                last_decision,
                estimated_score,
            )
            hold_browser_after_end(bot, args.error_hold, "fatal error")
        except Exception as snapshot_exc:
            print(f"[bot] Не удалось сохранить диагностику: {snapshot_exc!r}")
        raise
    finally:
        if tdl_solver_client is not None:
            tdl_solver_client.close()
        if args.close_browser:
            bot.close()
        else:
            print("[bot] Браузер оставлен открытым.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Solver bot for the g2048 browser mini-game.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--browser", choices=("firefox", "chrome", "edge"), default="chrome")
    parser.add_argument("--depth", type=int, default=16, help="Expectimax depth in player moves.")
    parser.add_argument("--time-limit-ms", type=int, default=140, help="Soft time budget per decision.")
    parser.add_argument("--legacy-solver", action="store_true", help="Use the old tuple-board solver instead of the fast bitboard solver.")
    parser.add_argument("--solver-backend", choices=("auto", "python", "tdl"), default="auto")
    parser.add_argument("--tdl-network", default="auto", help="TDL2048 network: auto, 4x6patt, 5x6patt, 6x6patt, 7x6patt, 8x6patt.")
    parser.add_argument("--tdl-search", default=DEFAULT_TDL_SEARCH, help="TDL2048 expectimax search setting. The default is a dense-board 7p/6p/5p/4p/3p profile.")
    parser.add_argument("--tdl-cache", default=DEFAULT_TDL_CACHE, help="TDL2048 transposition-table size, such as 256M. Pass an empty value to disable it.")
    parser.add_argument("--tdl-cache-peek", action=argparse.BooleanOptionalAction, default=True, help="Allow TDL to reuse deeper cached search results.")
    parser.add_argument("--fixed-depth", action="store_true", help="Use --depth exactly instead of adaptive distinct-tile depth.")
    parser.add_argument("--cprob-threshold", type=float, default=0.00005, help="Prune expectimax branches below this cumulative probability.")
    parser.add_argument("--million-mode", action="store_true", help="Use staged search budgets for a 32768/65536-tile run.")
    parser.add_argument("--fast-snake-weight", type=float, default=0.0, help="Extra snake-gradient weight for the fast bitboard heuristic.")
    parser.add_argument("--tile-encoding", choices=("auto", "value", "rank"), default="auto")
    parser.add_argument("--corner", choices=("top-left", "top-right", "bottom-left", "bottom-right"), default="bottom-right")
    parser.add_argument("--strict-corner", action="store_true", help="Forbid moving away from the target row unless forced.")
    parser.add_argument("--relaxed-corner", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--rhythm-profile", choices=("off", "balanced", "human"), default="balanced", help="Move timing profile. Use off for legacy --delay/--rest-every behavior.")
    parser.add_argument("--delay", type=float, nargs=2, default=(0.20, 0.42), metavar=("MIN", "MAX"))
    parser.add_argument("--rest-every", type=int, default=300, help="Take a longer pause after every N moves; 0 disables it.")
    parser.add_argument("--rest-delay", type=float, nargs=2, default=(4.0, 10.0), metavar=("MIN", "MAX"))
    parser.add_argument("--after-move-timeout", type=float, default=1.2)
    parser.add_argument("--max-moves", type=int, default=0, help="0 means unlimited.")
    parser.add_argument("--force-loss-after-moves", type=int, default=0, help="Switch to intentionally bad legal moves after N moves; 0 disables it.")
    parser.add_argument("--force-loss-after-score", type=int, default=0, help="Switch to intentionally bad legal moves after estimated score reaches N; 0 disables it.")
    parser.add_argument("--post-game-hold", type=int, default=900, help="Seconds to keep the browser alive after game over/max moves.")
    parser.add_argument("--error-hold", type=int, default=300, help="Seconds to keep the browser alive after a fatal error.")
    parser.add_argument("--log-dir", default="runs")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--close-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.delay[0] < 0 or args.delay[1] < args.delay[0]:
        raise SystemExit("--delay must be two values: MIN MAX, where 0 <= MIN <= MAX")
    if args.rest_delay[0] < 0 or args.rest_delay[1] < args.rest_delay[0]:
        raise SystemExit("--rest-delay must be two values: MIN MAX, where 0 <= MIN <= MAX")
    return run_bot(args)


if __name__ == "__main__":
    raise SystemExit(main())
