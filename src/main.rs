use std::io::{self, BufRead, Write};
use std::time::{Duration, Instant};

const SIZE: usize = 4;
const ROW_MASK: u64 = 0xFFFF;
const COL_MASK: u64 = 0x000F_000F_000F_000F;
const CACHE_BITS: usize = 22;
const CACHE_SIZE: usize = 1 << CACHE_BITS;
const CACHE_MASK: usize = CACHE_SIZE - 1;

const SCORE_LOST_PENALTY: f64 = 200_000.0;
const SCORE_MONOTONICITY_POWER: f64 = 4.0;
const SCORE_MONOTONICITY_WEIGHT: f64 = 47.0;
const SCORE_SUM_POWER: f64 = 3.5;
const SCORE_SUM_WEIGHT: f64 = 11.0;
const SCORE_MERGES_WEIGHT: f64 = 700.0;
const SCORE_EMPTY_WEIGHT: f64 = 270.0;
const ORIENT_MONOTONICITY_WEIGHT: f64 = 8.0;
const ORIENT_POSITION_WEIGHT: f64 = 10.0;

const MOVE_NAMES: [&str; 4] = ["UP", "DOWN", "LEFT", "RIGHT"];

struct Tables {
    row_left: Vec<u16>,
    row_right: Vec<u16>,
    col_up: Vec<u64>,
    col_down: Vec<u64>,
    heur: Vec<f64>,
    orient_asc: Vec<f64>,
    empty: Vec<u8>,
    max_rank: Vec<u8>,
}

struct Cache {
    board: Vec<u64>,
    depth: Vec<u8>,
    value: Vec<f64>,
    stamp: Vec<u32>,
    current_stamp: u32,
}

struct Solver {
    tables: Tables,
    cache: Cache,
    deadline: Instant,
    cprob_threshold: f64,
    nodes: u64,
}

#[derive(Debug, Clone, Copy)]
struct ResultMove {
    mv: i32,
    value: f64,
    depth: i32,
    elapsed_ms: f64,
    nodes: u64,
}

#[derive(Debug)]
struct SearchTimeout;

fn reverse_row(row: u16) -> u16 {
    ((row >> 12) | ((row >> 4) & 0x00F0) | ((row << 4) & 0x0F00) | (row << 12)) & 0xFFFF
}

fn unpack_col(row: u16) -> u64 {
    let value = row as u64;
    (value | (value << 12) | (value << 24) | (value << 36)) & COL_MASK
}

fn transpose(board: u64) -> u64 {
    let a1 = board & 0xF0F0_0F0F_F0F0_0F0F;
    let a2 = board & 0x0000_F0F0_0000_F0F0;
    let a3 = board & 0x0F0F_0000_0F0F_0000;
    let a = a1 | (a2 << 12) | (a3 >> 12);
    let b1 = a & 0xFF00_FF00_00FF_00FF;
    let b2 = a & 0x00FF_00FF_0000_0000;
    let b3 = a & 0x0000_0000_FF00_FF00;
    b1 | (b2 >> 24) | (b3 << 24)
}

fn row_to_ranks(row: u16) -> [u8; 4] {
    [
        (row & 0xF) as u8,
        ((row >> 4) & 0xF) as u8,
        ((row >> 8) & 0xF) as u8,
        ((row >> 12) & 0xF) as u8,
    ]
}

fn ranks_to_row(ranks: [u8; 4]) -> u16 {
    (ranks[0] as u16)
        | ((ranks[1] as u16) << 4)
        | ((ranks[2] as u16) << 8)
        | ((ranks[3] as u16) << 12)
}

fn move_row_left(row: u16) -> u16 {
    let ranks = row_to_ranks(row);
    let mut non_zero = [0u8; 4];
    let mut count = 0usize;
    for rank in ranks {
        if rank != 0 {
            non_zero[count] = rank;
            count += 1;
        }
    }

    let mut result = [0u8; 4];
    let mut out = 0usize;
    let mut index = 0usize;
    while index < count {
        let current = non_zero[index];
        if index + 1 < count && current == non_zero[index + 1] {
            result[out] = (current + 1).min(0xF);
            out += 1;
            index += 2;
        } else {
            result[out] = current;
            out += 1;
            index += 1;
        }
    }
    ranks_to_row(result)
}

fn init_tables() -> Tables {
    let mut row_left = vec![0u16; 65_536];
    let mut row_right = vec![0u16; 65_536];
    let mut col_up = vec![0u64; 65_536];
    let mut col_down = vec![0u64; 65_536];
    let mut heur = vec![0.0f64; 65_536];
    let mut orient_asc = vec![0.0f64; 65_536];
    let mut empty = vec![0u8; 65_536];
    let mut max_rank = vec![0u8; 65_536];

    for row in 0u32..65_536 {
        let row_u16 = row as u16;
        let ranks = row_to_ranks(row_u16);
        let mut empty_count = 0u8;
        let mut merges = 0i32;
        let mut previous = 0u8;
        let mut counter = 0i32;
        let mut rank_sum = 0.0f64;
        let mut position_score = 0.0f64;
        let mut row_max_rank = 0u8;

        for (index, rank) in ranks.iter().enumerate() {
            let rank = *rank;
            row_max_rank = row_max_rank.max(rank);
            rank_sum += (rank as f64).powf(SCORE_SUM_POWER);
            position_score += (rank as f64).powf(3.0) * (index as f64 + 1.0);
            if rank == 0 {
                empty_count += 1;
            } else {
                if previous == rank {
                    counter += 1;
                } else if counter > 0 {
                    merges += 1 + counter;
                    counter = 0;
                }
                previous = rank;
            }
        }
        if counter > 0 {
            merges += 1 + counter;
        }

        let mut mono_left = 0.0f64;
        let mut mono_right = 0.0f64;
        let mut asc_penalty = 0.0f64;
        for index in 1..SIZE {
            let prev = ranks[index - 1] as f64;
            let current = ranks[index] as f64;
            if prev > current {
                let penalty = prev.powf(SCORE_MONOTONICITY_POWER) - current.powf(SCORE_MONOTONICITY_POWER);
                mono_left += penalty;
                asc_penalty += penalty;
            } else {
                mono_right += current.powf(SCORE_MONOTONICITY_POWER) - prev.powf(SCORE_MONOTONICITY_POWER);
            }
        }

        heur[row as usize] = SCORE_LOST_PENALTY
            + SCORE_EMPTY_WEIGHT * empty_count as f64
            + SCORE_MERGES_WEIGHT * merges as f64
            - SCORE_MONOTONICITY_WEIGHT * mono_left.min(mono_right)
            - SCORE_SUM_WEIGHT * rank_sum;
        orient_asc[row as usize] =
            ORIENT_POSITION_WEIGHT * position_score - ORIENT_MONOTONICITY_WEIGHT * asc_penalty;
        empty[row as usize] = empty_count;
        max_rank[row as usize] = row_max_rank;

        let result = move_row_left(row_u16);
        let rev_original = reverse_row(row_u16);
        let rev_result = reverse_row(result);

        row_left[row as usize] = row_u16 ^ result;
        row_right[rev_original as usize] = rev_original ^ rev_result;
        col_up[row as usize] = unpack_col(row_u16) ^ unpack_col(result);
        col_down[rev_original as usize] = unpack_col(rev_original) ^ unpack_col(rev_result);
    }

    Tables {
        row_left,
        row_right,
        col_up,
        col_down,
        heur,
        orient_asc,
        empty,
        max_rank,
    }
}

impl Cache {
    fn new() -> Self {
        Self {
            board: vec![0u64; CACHE_SIZE],
            depth: vec![0u8; CACHE_SIZE],
            value: vec![0.0f64; CACHE_SIZE],
            stamp: vec![0u32; CACHE_SIZE],
            current_stamp: 1,
        }
    }

    fn next_stamp(&mut self) {
        self.current_stamp = self.current_stamp.wrapping_add(1);
        if self.current_stamp == 0 {
            self.stamp.fill(0);
            self.current_stamp = 1;
        }
    }

    fn hash(board: u64, depth: i32) -> usize {
        let mut x = board ^ (0x9E37_79B9_7F4A_7C15u64).wrapping_mul(depth as u64 + 1);
        x ^= x >> 33;
        x = x.wrapping_mul(0xff51_afd7_ed55_8ccd);
        x ^= x >> 33;
        x = x.wrapping_mul(0xc4ce_b9fe_1a85_ec53);
        x ^= x >> 33;
        x as usize
    }

    fn get(&self, board: u64, depth: i32) -> Option<f64> {
        let mut slot = Self::hash(board, depth) & CACHE_MASK;
        loop {
            if self.stamp[slot] != self.current_stamp {
                return None;
            }
            if self.board[slot] == board && self.depth[slot] == depth as u8 {
                return Some(self.value[slot]);
            }
            slot = (slot + 1) & CACHE_MASK;
        }
    }

    fn put(&mut self, board: u64, depth: i32, value: f64) {
        let mut slot = Self::hash(board, depth) & CACHE_MASK;
        loop {
            if self.stamp[slot] != self.current_stamp {
                self.stamp[slot] = self.current_stamp;
                self.board[slot] = board;
                self.depth[slot] = depth as u8;
                self.value[slot] = value;
                return;
            }
            if self.board[slot] == board && self.depth[slot] == depth as u8 {
                self.value[slot] = value;
                return;
            }
            slot = (slot + 1) & CACHE_MASK;
        }
    }
}

impl Solver {
    fn new() -> Self {
        Self {
            tables: init_tables(),
            cache: Cache::new(),
            deadline: Instant::now(),
            cprob_threshold: 0.003,
            nodes: 0,
        }
    }

    #[inline]
    fn all_moves(&self, board: u64) -> [u64; 4] {
        let transposed = transpose(board);

        let mut up = board;
        up ^= self.tables.col_up[((transposed >> 0) & ROW_MASK) as usize] << 0;
        up ^= self.tables.col_up[((transposed >> 16) & ROW_MASK) as usize] << 4;
        up ^= self.tables.col_up[((transposed >> 32) & ROW_MASK) as usize] << 8;
        up ^= self.tables.col_up[((transposed >> 48) & ROW_MASK) as usize] << 12;

        let mut down = board;
        down ^= self.tables.col_down[((transposed >> 0) & ROW_MASK) as usize] << 0;
        down ^= self.tables.col_down[((transposed >> 16) & ROW_MASK) as usize] << 4;
        down ^= self.tables.col_down[((transposed >> 32) & ROW_MASK) as usize] << 8;
        down ^= self.tables.col_down[((transposed >> 48) & ROW_MASK) as usize] << 12;

        let mut left = board;
        left ^= (self.tables.row_left[((board >> 0) & ROW_MASK) as usize] as u64) << 0;
        left ^= (self.tables.row_left[((board >> 16) & ROW_MASK) as usize] as u64) << 16;
        left ^= (self.tables.row_left[((board >> 32) & ROW_MASK) as usize] as u64) << 32;
        left ^= (self.tables.row_left[((board >> 48) & ROW_MASK) as usize] as u64) << 48;

        let mut right = board;
        right ^= (self.tables.row_right[((board >> 0) & ROW_MASK) as usize] as u64) << 0;
        right ^= (self.tables.row_right[((board >> 16) & ROW_MASK) as usize] as u64) << 16;
        right ^= (self.tables.row_right[((board >> 32) & ROW_MASK) as usize] as u64) << 32;
        right ^= (self.tables.row_right[((board >> 48) & ROW_MASK) as usize] as u64) << 48;

        [up, down, left, right]
    }

    #[inline]
    fn count_empty(&self, board: u64) -> i32 {
        self.tables.empty[((board >> 0) & ROW_MASK) as usize] as i32
            + self.tables.empty[((board >> 16) & ROW_MASK) as usize] as i32
            + self.tables.empty[((board >> 32) & ROW_MASK) as usize] as i32
            + self.tables.empty[((board >> 48) & ROW_MASK) as usize] as i32
    }

    #[inline]
    fn max_rank(&self, board: u64) -> u8 {
        self.tables.max_rank[((board >> 0) & ROW_MASK) as usize]
            .max(self.tables.max_rank[((board >> 16) & ROW_MASK) as usize])
            .max(self.tables.max_rank[((board >> 32) & ROW_MASK) as usize])
            .max(self.tables.max_rank[((board >> 48) & ROW_MASK) as usize])
    }

    #[inline]
    fn heuristic(&self, board: u64) -> f64 {
        let transposed = transpose(board);
        let base = self.tables.heur[((board >> 0) & ROW_MASK) as usize]
            + self.tables.heur[((board >> 16) & ROW_MASK) as usize]
            + self.tables.heur[((board >> 32) & ROW_MASK) as usize]
            + self.tables.heur[((board >> 48) & ROW_MASK) as usize]
            + self.tables.heur[((transposed >> 0) & ROW_MASK) as usize]
            + self.tables.heur[((transposed >> 16) & ROW_MASK) as usize]
            + self.tables.heur[((transposed >> 32) & ROW_MASK) as usize]
            + self.tables.heur[((transposed >> 48) & ROW_MASK) as usize];

        let max_rank = self.max_rank(board);
        if max_rank < 12 {
            return base;
        }

        let orient = self.tables.orient_asc[((board >> 0) & ROW_MASK) as usize]
            + self.tables.orient_asc[((board >> 16) & ROW_MASK) as usize]
            + self.tables.orient_asc[((board >> 32) & ROW_MASK) as usize]
            + self.tables.orient_asc[((board >> 48) & ROW_MASK) as usize]
            + self.tables.orient_asc[((transposed >> 0) & ROW_MASK) as usize]
            + self.tables.orient_asc[((transposed >> 16) & ROW_MASK) as usize]
            + self.tables.orient_asc[((transposed >> 32) & ROW_MASK) as usize]
            + self.tables.orient_asc[((transposed >> 48) & ROW_MASK) as usize];

        let scale = if max_rank >= 14 { 1.0 } else if max_rank == 13 { 0.7 } else { 0.4 };
        base + orient * scale
    }

    #[inline]
    fn check_deadline(&mut self) -> Result<(), SearchTimeout> {
        self.nodes += 1;
        if (self.nodes & 8191) == 0 && Instant::now() > self.deadline {
            return Err(SearchTimeout);
        }
        Ok(())
    }

    fn tile_node(&mut self, board: u64, moves_left: i32, cprob: f64) -> Result<f64, SearchTimeout> {
        self.check_deadline()?;
        if moves_left <= 0 || cprob < self.cprob_threshold {
            return Ok(self.heuristic(board));
        }

        if let Some(value) = self.cache.get(board, moves_left) {
            return Ok(value);
        }

        let num_open = self.count_empty(board);
        if num_open == 0 {
            let value = self.move_node(board, moves_left, cprob)?;
            self.cache.put(board, moves_left, value);
            return Ok(value);
        }

        let cell_probability = cprob / num_open as f64;
        let mut total = 0.0f64;
        let mut tmp = board;
        let mut tile_2 = 1u64;
        for _index in 0..16 {
            if (tmp & 0xF) == 0 {
                total += 0.9 * self.move_node(board | tile_2, moves_left - 1, cell_probability * 0.9)?;
                total += 0.1 * self.move_node(board | (tile_2 << 1), moves_left - 1, cell_probability * 0.1)?;
            }
            tmp >>= 4;
            tile_2 <<= 4;
        }

        let value = total / num_open as f64;
        self.cache.put(board, moves_left, value);
        Ok(value)
    }

    fn move_node(&mut self, board: u64, moves_left: i32, cprob: f64) -> Result<f64, SearchTimeout> {
        self.check_deadline()?;
        let mut best = f64::NEG_INFINITY;
        for next in self.all_moves(board) {
            if next != board {
                let value = self.tile_node(next, moves_left, cprob)?;
                if value > best {
                    best = value;
                }
            }
        }
        Ok(if best == f64::NEG_INFINITY { -1_000_000_000.0 } else { best })
    }

    fn solve(&mut self, board: u64, requested_depth: i32, time_limit_ms: u64, threshold: f64) -> ResultMove {
        self.cache.next_stamp();
        self.nodes = 0;
        self.cprob_threshold = threshold;
        self.deadline = Instant::now() + Duration::from_millis(time_limit_ms.max(1));

        let root_moves = self.all_moves(board);
        if root_moves.iter().all(|&next| next == board) {
            return ResultMove {
                mv: -1,
                value: -1_000_000_000.0,
                depth: requested_depth,
                elapsed_ms: 0.0,
                nodes: self.nodes,
            };
        }

        let max_depth = requested_depth.max(1);
        let started = Instant::now();
        let mut best_move = -1;
        let mut best_value = f64::NEG_INFINITY;
        let mut completed_depth = 0;

        for depth in 1..=max_depth {
            let mut depth_best_move = -1;
            let mut depth_best_value = f64::NEG_INFINITY;
            let moves_left = (depth - 1).max(0);

            let mut timed_out = false;
            for (mv, &next) in root_moves.iter().enumerate() {
                if next == board {
                    continue;
                }
                match self.tile_node(next, moves_left, 1.0) {
                    Ok(value) => {
                        if value > depth_best_value {
                            depth_best_value = value;
                            depth_best_move = mv as i32;
                        }
                    }
                    Err(_) => {
                        timed_out = true;
                        break;
                    }
                }
            }
            if timed_out {
                break;
            }
            if depth_best_move >= 0 {
                best_move = depth_best_move;
                best_value = depth_best_value;
                completed_depth = depth;
            }
        }

        if best_move < 0 {
            for (mv, &next) in root_moves.iter().enumerate() {
                if next == board {
                    continue;
                }
                let value = self.heuristic(next);
                if value > best_value {
                    best_value = value;
                    best_move = mv as i32;
                }
            }
        }

        ResultMove {
            mv: best_move,
            value: best_value,
            depth: completed_depth,
            elapsed_ms: started.elapsed().as_secs_f64() * 1000.0,
            nodes: self.nodes,
        }
    }
}

fn main() {
    let mut solver = Solver::new();
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout());
    writeln!(stdout, "READY").unwrap();
    stdout.flush().unwrap();

    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if line == "QUIT" {
            break;
        }
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 4 || parts[0] != "SOLVE" {
            writeln!(stdout, "ERR bad_request").unwrap();
            stdout.flush().unwrap();
            continue;
        }
        let parsed = || -> Option<ResultMove> {
            let board = u64::from_str_radix(parts[1], 16).ok()?;
            let depth = parts[2].parse::<i32>().ok()?;
            let time_limit_ms = parts[3].parse::<u64>().ok()?;
            let threshold = if parts.len() >= 5 {
                parts[4].parse::<f64>().ok()?
            } else {
                0.003
            };
            Some(solver.solve(board, depth, time_limit_ms, threshold))
        }();

        match parsed {
            Some(result) => {
                let move_name = if result.mv >= 0 {
                    MOVE_NAMES[result.mv as usize]
                } else {
                    "NONE"
                };
                writeln!(
                    stdout,
                    "OK {} {} {:.10} {:.3} {}",
                    move_name, result.depth, result.value, result.elapsed_ms, result.nodes
                )
                .unwrap();
            }
            None => {
                writeln!(stdout, "ERR parse").unwrap();
            }
        }
        stdout.flush().unwrap();
    }
}
