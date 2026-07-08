import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.util.Locale;

public final class Solver2048 {
    static final int SIZE = 4;
    static final int ROW_MASK = 0xFFFF;
    static final long COL_MASK = 0x000F000F000F000FL;
    static final long MASK64 = 0xFFFFFFFFFFFFFFFFL;

    static final int[] ROW_LEFT = new int[65536];
    static final int[] ROW_RIGHT = new int[65536];
    static final long[] COL_UP = new long[65536];
    static final long[] COL_DOWN = new long[65536];
    static final double[] HEUR = new double[65536];
    static final byte[] EMPTY = new byte[65536];
    static final int[] DISTINCT = new int[65536];

    static final double SCORE_LOST_PENALTY = 200000.0;
    static final double SCORE_MONOTONICITY_POWER = 4.0;
    static final double SCORE_MONOTONICITY_WEIGHT = 47.0;
    static final double SCORE_SUM_POWER = 3.5;
    static final double SCORE_SUM_WEIGHT = 11.0;
    static final double SCORE_MERGES_WEIGHT = 700.0;
    static final double SCORE_EMPTY_WEIGHT = 270.0;

    static final String[] MOVE_NAMES = {"UP", "DOWN", "LEFT", "RIGHT"};

    static final int CACHE_BITS = 22;
    static final int CACHE_SIZE = 1 << CACHE_BITS;
    static final int CACHE_MASK = CACHE_SIZE - 1;
    static final long[] CACHE_BOARD = new long[CACHE_SIZE];
    static final byte[] CACHE_DEPTH = new byte[CACHE_SIZE];
    static final double[] CACHE_VALUE = new double[CACHE_SIZE];
    static final int[] CACHE_STAMP = new int[CACHE_SIZE];
    static int currentStamp = 1;

    long deadlineNs;
    double cprobThreshold;
    long nodes;

    static {
        initTables();
    }

    static int reverseRow(int row) {
        return ((row >>> 12) | ((row >>> 4) & 0x00F0) | ((row << 4) & 0x0F00) | ((row << 12) & 0xF000)) & ROW_MASK;
    }

    static long unpackCol(int row) {
        long value = row & 0xFFFFL;
        return (value | (value << 12) | (value << 24) | (value << 36)) & COL_MASK;
    }

    static long transpose(long board) {
        long a1 = board & 0xF0F00F0FF0F00F0FL;
        long a2 = board & 0x0000F0F00000F0F0L;
        long a3 = board & 0x0F0F00000F0F0000L;
        long a = a1 | (a2 << 12) | (a3 >>> 12);
        long b1 = a & 0xFF00FF0000FF00FFL;
        long b2 = a & 0x00FF00FF00000000L;
        long b3 = a & 0x00000000FF00FF00L;
        return b1 | (b2 >>> 24) | (b3 << 24);
    }

    static int[] rowToRanks(int row) {
        return new int[] {
            row & 0xF,
            (row >>> 4) & 0xF,
            (row >>> 8) & 0xF,
            (row >>> 12) & 0xF,
        };
    }

    static int ranksToRow(int[] ranks) {
        return (ranks[0] & 0xF) | ((ranks[1] & 0xF) << 4) | ((ranks[2] & 0xF) << 8) | ((ranks[3] & 0xF) << 12);
    }

    static int moveRowLeft(int row) {
        int[] ranks = rowToRanks(row);
        int[] nonZero = new int[4];
        int count = 0;
        for (int rank : ranks) {
            if (rank != 0) {
                nonZero[count++] = rank;
            }
        }

        int[] result = new int[4];
        int out = 0;
        int index = 0;
        while (index < count) {
            int current = nonZero[index];
            if (index + 1 < count && current == nonZero[index + 1]) {
                result[out++] = Math.min(current + 1, 0xF);
                index += 2;
            } else {
                result[out++] = current;
                index++;
            }
        }
        return ranksToRow(result);
    }

    static void initTables() {
        for (int row = 0; row < 65536; row++) {
            int[] ranks = rowToRanks(row);
            int empty = 0;
            int merges = 0;
            int previous = 0;
            int counter = 0;
            double rankSum = 0.0;
            int distinct = 0;

            for (int rank : ranks) {
                rankSum += Math.pow(rank, SCORE_SUM_POWER);
                if (rank == 0) {
                    empty++;
                } else {
                    distinct |= 1 << rank;
                    if (previous == rank) {
                        counter++;
                    } else if (counter > 0) {
                        merges += 1 + counter;
                        counter = 0;
                    }
                    previous = rank;
                }
            }
            if (counter > 0) {
                merges += 1 + counter;
            }

            double monoLeft = 0.0;
            double monoRight = 0.0;
            for (int index = 1; index < SIZE; index++) {
                int prev = ranks[index - 1];
                int current = ranks[index];
                if (prev > current) {
                    monoLeft += Math.pow(prev, SCORE_MONOTONICITY_POWER) - Math.pow(current, SCORE_MONOTONICITY_POWER);
                } else {
                    monoRight += Math.pow(current, SCORE_MONOTONICITY_POWER) - Math.pow(prev, SCORE_MONOTONICITY_POWER);
                }
            }

            HEUR[row] = SCORE_LOST_PENALTY
                + SCORE_EMPTY_WEIGHT * empty
                + SCORE_MERGES_WEIGHT * merges
                - SCORE_MONOTONICITY_WEIGHT * Math.min(monoLeft, monoRight)
                - SCORE_SUM_WEIGHT * rankSum;
            EMPTY[row] = (byte) empty;
            DISTINCT[row] = distinct;

            int result = moveRowLeft(row);
            int revOriginal = reverseRow(row);
            int revResult = reverseRow(result);
            ROW_LEFT[row] = row ^ result;
            ROW_RIGHT[revOriginal] = revOriginal ^ revResult;
            COL_UP[row] = unpackCol(row) ^ unpackCol(result);
            COL_DOWN[revOriginal] = unpackCol(revOriginal) ^ unpackCol(revResult);
        }
    }

    static long[] allMoves(long board) {
        long transposed = transpose(board);

        long up = board;
        up ^= COL_UP[(int) ((transposed >>> 0) & ROW_MASK)] << 0;
        up ^= COL_UP[(int) ((transposed >>> 16) & ROW_MASK)] << 4;
        up ^= COL_UP[(int) ((transposed >>> 32) & ROW_MASK)] << 8;
        up ^= COL_UP[(int) ((transposed >>> 48) & ROW_MASK)] << 12;

        long down = board;
        down ^= COL_DOWN[(int) ((transposed >>> 0) & ROW_MASK)] << 0;
        down ^= COL_DOWN[(int) ((transposed >>> 16) & ROW_MASK)] << 4;
        down ^= COL_DOWN[(int) ((transposed >>> 32) & ROW_MASK)] << 8;
        down ^= COL_DOWN[(int) ((transposed >>> 48) & ROW_MASK)] << 12;

        long left = board;
        left ^= ((long) ROW_LEFT[(int) ((board >>> 0) & ROW_MASK)]) << 0;
        left ^= ((long) ROW_LEFT[(int) ((board >>> 16) & ROW_MASK)]) << 16;
        left ^= ((long) ROW_LEFT[(int) ((board >>> 32) & ROW_MASK)]) << 32;
        left ^= ((long) ROW_LEFT[(int) ((board >>> 48) & ROW_MASK)]) << 48;

        long right = board;
        right ^= ((long) ROW_RIGHT[(int) ((board >>> 0) & ROW_MASK)]) << 0;
        right ^= ((long) ROW_RIGHT[(int) ((board >>> 16) & ROW_MASK)]) << 16;
        right ^= ((long) ROW_RIGHT[(int) ((board >>> 32) & ROW_MASK)]) << 32;
        right ^= ((long) ROW_RIGHT[(int) ((board >>> 48) & ROW_MASK)]) << 48;

        return new long[] {up, down, left, right};
    }

    static int countEmpty(long board) {
        return EMPTY[(int) ((board >>> 0) & ROW_MASK)]
            + EMPTY[(int) ((board >>> 16) & ROW_MASK)]
            + EMPTY[(int) ((board >>> 32) & ROW_MASK)]
            + EMPTY[(int) ((board >>> 48) & ROW_MASK)];
    }

    static int countDistinct(long board) {
        int mask = DISTINCT[(int) ((board >>> 0) & ROW_MASK)]
            | DISTINCT[(int) ((board >>> 16) & ROW_MASK)]
            | DISTINCT[(int) ((board >>> 32) & ROW_MASK)]
            | DISTINCT[(int) ((board >>> 48) & ROW_MASK)];
        return Integer.bitCount(mask);
    }

    static double heuristic(long board) {
        long transposed = transpose(board);
        return HEUR[(int) ((board >>> 0) & ROW_MASK)]
            + HEUR[(int) ((board >>> 16) & ROW_MASK)]
            + HEUR[(int) ((board >>> 32) & ROW_MASK)]
            + HEUR[(int) ((board >>> 48) & ROW_MASK)]
            + HEUR[(int) ((transposed >>> 0) & ROW_MASK)]
            + HEUR[(int) ((transposed >>> 16) & ROW_MASK)]
            + HEUR[(int) ((transposed >>> 32) & ROW_MASK)]
            + HEUR[(int) ((transposed >>> 48) & ROW_MASK)];
    }

    void nextStamp() {
        currentStamp++;
        if (currentStamp == Integer.MAX_VALUE) {
            for (int i = 0; i < CACHE_STAMP.length; i++) {
                CACHE_STAMP[i] = 0;
            }
            currentStamp = 1;
        }
    }

    static int hash(long board, int depth) {
        long x = board ^ (0x9E3779B97F4A7C15L * (depth + 1));
        x ^= x >>> 33;
        x *= 0xff51afd7ed558ccdL;
        x ^= x >>> 33;
        x *= 0xc4ceb9fe1a85ec53L;
        x ^= x >>> 33;
        return (int) x;
    }

    Double cacheGet(long board, int movesLeft) {
        int slot = hash(board, movesLeft) & CACHE_MASK;
        while (CACHE_STAMP[slot] == currentStamp) {
            if (CACHE_BOARD[slot] == board && CACHE_DEPTH[slot] == (byte) movesLeft) {
                return CACHE_VALUE[slot];
            }
            slot = (slot + 1) & CACHE_MASK;
        }
        return null;
    }

    void cachePut(long board, int movesLeft, double value) {
        int slot = hash(board, movesLeft) & CACHE_MASK;
        while (CACHE_STAMP[slot] == currentStamp) {
            if (CACHE_BOARD[slot] == board && CACHE_DEPTH[slot] == (byte) movesLeft) {
                CACHE_VALUE[slot] = value;
                return;
            }
            slot = (slot + 1) & CACHE_MASK;
        }
        CACHE_STAMP[slot] = currentStamp;
        CACHE_BOARD[slot] = board;
        CACHE_DEPTH[slot] = (byte) movesLeft;
        CACHE_VALUE[slot] = value;
    }

    void checkDeadline() {
        nodes++;
        if ((nodes & 8191L) == 0 && System.nanoTime() > deadlineNs) {
            throw new SearchTimeout();
        }
    }

    double tileNode(long board, int movesLeft, double cprob) {
        checkDeadline();
        if (movesLeft <= 0 || cprob < cprobThreshold) {
            return heuristic(board);
        }

        Double cached = cacheGet(board, movesLeft);
        if (cached != null) {
            return cached;
        }

        int numOpen = countEmpty(board);
        if (numOpen == 0) {
            double value = moveNode(board, movesLeft, cprob);
            cachePut(board, movesLeft, value);
            return value;
        }

        double cellProbability = cprob / numOpen;
        double total = 0.0;
        long tmp = board;
        long tile2 = 1L;
        for (int index = 0; index < 16; index++, tmp >>>= 4, tile2 <<= 4) {
            if ((tmp & 0xFL) == 0) {
                total += 0.9 * moveNode(board | tile2, movesLeft - 1, cellProbability * 0.9);
                total += 0.1 * moveNode(board | (tile2 << 1), movesLeft - 1, cellProbability * 0.1);
            }
        }

        double value = total / numOpen;
        cachePut(board, movesLeft, value);
        return value;
    }

    double moveNode(long board, int movesLeft, double cprob) {
        checkDeadline();
        double best = Double.NEGATIVE_INFINITY;
        long[] moves = allMoves(board);
        for (long next : moves) {
            if (next != board) {
                double value = tileNode(next, movesLeft, cprob);
                if (value > best) {
                    best = value;
                }
            }
        }
        return best == Double.NEGATIVE_INFINITY ? -1_000_000_000.0 : best;
    }

    Result solve(long board, int requestedDepth, int timeLimitMs, double threshold) {
        nextStamp();
        nodes = 0;
        cprobThreshold = threshold;
        deadlineNs = System.nanoTime() + Math.max(1, timeLimitMs) * 1_000_000L;

        long[] rootMoves = allMoves(board);
        boolean any = false;
        for (long next : rootMoves) {
            if (next != board) {
                any = true;
                break;
            }
        }
        if (!any) {
            return new Result(-1, -1_000_000_000.0, requestedDepth, 0.0, nodes);
        }

        int maxDepth = Math.max(requestedDepth, 1);
        int bestMove = -1;
        double bestValue = Double.NEGATIVE_INFINITY;
        int completedDepth = 0;
        long started = System.nanoTime();

        for (int depth = 1; depth <= maxDepth; depth++) {
            int depthBestMove = -1;
            double depthBestValue = Double.NEGATIVE_INFINITY;
            int movesLeft = Math.max(depth - 1, 0);
            try {
                for (int move = 0; move < 4; move++) {
                    long next = rootMoves[move];
                    if (next == board) {
                        continue;
                    }
                    double value = tileNode(next, movesLeft, 1.0);
                    if (value > depthBestValue) {
                        depthBestValue = value;
                        depthBestMove = move;
                    }
                }
            } catch (SearchTimeout timeout) {
                break;
            }

            if (depthBestMove >= 0) {
                bestMove = depthBestMove;
                bestValue = depthBestValue;
                completedDepth = depth;
            }
        }

        if (bestMove < 0) {
            for (int move = 0; move < 4; move++) {
                long next = rootMoves[move];
                if (next == board) {
                    continue;
                }
                double value = heuristic(next);
                if (value > bestValue) {
                    bestValue = value;
                    bestMove = move;
                }
            }
        }

        double elapsedMs = (System.nanoTime() - started) / 1_000_000.0;
        return new Result(bestMove, bestValue, completedDepth, elapsedMs, nodes);
    }

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.US);
        Solver2048 solver = new Solver2048();
        BufferedReader reader = new BufferedReader(new InputStreamReader(System.in));
        PrintWriter writer = new PrintWriter(System.out, true);
        writer.println("READY");

        String line;
        while ((line = reader.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) {
                continue;
            }
            if (line.equals("QUIT")) {
                break;
            }
            String[] parts = line.split("\\s+");
            if (parts.length < 4 || !parts[0].equals("SOLVE")) {
                writer.println("ERR bad_request");
                continue;
            }
            try {
                long board = Long.parseUnsignedLong(parts[1], 16);
                int depth = Integer.parseInt(parts[2]);
                int timeLimitMs = Integer.parseInt(parts[3]);
                double threshold = parts.length >= 5 ? Double.parseDouble(parts[4]) : 0.003;
                Result result = solver.solve(board, depth, timeLimitMs, threshold);
                String moveName = result.move >= 0 ? MOVE_NAMES[result.move] : "NONE";
                writer.printf(
                    "OK %s %d %.10f %.3f %d%n",
                    moveName,
                    result.depth,
                    result.value,
                    result.elapsedMs,
                    result.nodes
                );
            } catch (Exception exc) {
                writer.println("ERR " + exc.getClass().getSimpleName() + ":" + exc.getMessage());
            }
        }
    }

    static final class Result {
        final int move;
        final double value;
        final int depth;
        final double elapsedMs;
        final long nodes;

        Result(int move, double value, int depth, double elapsedMs, long nodes) {
            this.move = move;
            this.value = value;
            this.depth = depth;
            this.elapsedMs = elapsedMs;
            this.nodes = nodes;
        }
    }

    static final class SearchTimeout extends RuntimeException {
    }
}
