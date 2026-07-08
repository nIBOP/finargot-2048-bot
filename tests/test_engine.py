import unittest

import main


class EngineTests(unittest.TestCase):
    def test_merge_line_simple_pair(self):
        merged, gain = main.merge_line([2, 2, 0, 0])
        self.assertEqual(merged, (4, 0, 0, 0))
        self.assertEqual(gain, 4)

    def test_merge_line_two_pairs(self):
        merged, gain = main.merge_line([2, 2, 2, 2])
        self.assertEqual(merged, (4, 4, 0, 0))
        self.assertEqual(gain, 8)

    def test_merge_line_does_not_chain_in_one_move(self):
        merged, gain = main.merge_line([4, 4, 4, 0])
        self.assertEqual(merged, (8, 4, 0, 0))
        self.assertEqual(gain, 8)

    def test_simulate_move_left(self):
        board = main.as_board(
            (
                (2, 0, 2, 0),
                (4, 4, 8, 8),
                (0, 0, 0, 0),
                (2, 4, 8, 16),
            )
        )
        next_board, gain, moved = main.simulate_move(board, "LEFT")
        self.assertTrue(moved)
        self.assertEqual(gain, 28)
        self.assertEqual(
            next_board,
            main.as_board(
                (
                    (4, 0, 0, 0),
                    (8, 16, 0, 0),
                    (0, 0, 0, 0),
                    (2, 4, 8, 16),
                )
            ),
        )

    def test_invalid_move_is_not_returned_by_solver(self):
        board = main.as_board(
            (
                (2, 0, 0, 0),
                (0, 0, 0, 0),
                (0, 0, 0, 0),
                (0, 0, 0, 0),
            )
        )
        decision = main.choose_best_move(board, main.SolverConfig(depth=1, time_limit_ms=100))
        self.assertIn(decision.move, ("RIGHT", "DOWN"))

    def test_no_move_on_lost_board(self):
        board = main.as_board(
            (
                (2, 4, 2, 4),
                (4, 2, 4, 2),
                (2, 4, 2, 4),
                (4, 2, 4, 2),
            )
        )
        decision = main.choose_best_move(board, main.SolverConfig(depth=2, time_limit_ms=100))
        self.assertIsNone(decision.move)
        self.assertEqual(decision.valid_moves, ())

    def test_solver_keeps_locked_bottom_right_corner(self):
        board = main.as_board(
            (
                (0, 0, 0, 0),
                (0, 0, 0, 0),
                (2, 0, 0, 0),
                (2, 0, 0, 128),
            )
        )
        decision = main.choose_best_move(
            board,
            main.SolverConfig(depth=2, corner="bottom-right", strict_corner=True, time_limit_ms=100),
        )
        self.assertIn(decision.move, ("DOWN", "RIGHT"))

    def test_policy_filters_up_for_bottom_corner_when_possible(self):
        moves = ("UP", "DOWN", "LEFT")
        config = main.SolverConfig(corner="bottom-right", strict_corner=True)
        self.assertEqual(main.policy_moves(moves, config), ("DOWN", "LEFT"))

    def test_bitboard_moves_match_reference_engine(self):
        board = main.as_board(
            (
                (2, 0, 2, 4),
                (4, 4, 8, 0),
                (0, 16, 16, 2),
                (32, 0, 32, 64),
            )
        )
        packed = main.board_to_bitboard(board)
        for move in main.DIRECTIONS:
            expected, _, _ = main.simulate_move(board, move)
            actual = main.bitboard_to_board(main.bitboard_execute_move(packed, move))
            self.assertEqual(actual, expected, move)

    def test_million_mode_escalates_late_game_budget(self):
        board = main.as_board(
            (
                (0, 0, 0, 0),
                (0, 0, 0, 0),
                (0, 0, 0, 0),
                (16384, 8192, 4096, 2048),
            )
        )
        config = main.SolverConfig(
            depth=3,
            time_limit_ms=10,
            cprob_threshold=0.1,
            strict_corner=True,
            million_mode=True,
        )
        tuned = main.million_mode_config(board, config)

        self.assertGreaterEqual(tuned.depth, 16)
        self.assertGreaterEqual(tuned.time_limit_ms, 160)
        self.assertLessEqual(tuned.cprob_threshold, 0.00015)
        self.assertFalse(tuned.adaptive_depth)
        self.assertFalse(tuned.strict_corner)


class ParserTests(unittest.TestCase):
    def test_parse_alt_value(self):
        payload = {"attrs": {}, "images": [{"alt": "4"}], "className": "", "text": ""}
        self.assertEqual(main.parse_cell_payload(payload, "value"), 4)

    def test_parse_rank_encoding(self):
        payload = {"attrs": {"data-rank": "3"}, "images": [], "className": "", "text": ""}
        self.assertEqual(main.parse_cell_payload(payload, "rank"), 8)

    def test_parse_class_value(self):
        payload = {"attrs": {}, "images": [], "className": "tile tile-16 tile-position-1-2", "text": ""}
        self.assertEqual(main.parse_cell_payload(payload, "value"), 16)

    def test_empty_payload_is_zero(self):
        payload = {"attrs": {}, "images": [], "className": "g2048__cell", "text": ""}
        self.assertEqual(main.parse_cell_payload(payload, "auto"), 0)


if __name__ == "__main__":
    unittest.main()
