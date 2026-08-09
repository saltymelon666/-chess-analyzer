from __future__ import annotations

import chess

from scripts.build_phase8c_kling_endgame_dataset import (
    verified_tablebase_route,
    white_outcome,
)


def test_white_outcome_respects_side_to_move() -> None:
    assert white_outcome("win", chess.WHITE) == "white_win"
    assert white_outcome("loss", chess.WHITE) == "black_win"
    assert white_outcome("win", chess.BLACK) == "black_win"
    assert white_outcome("loss", chess.BLACK) == "white_win"
    assert white_outcome("draw", chess.WHITE) == "draw"


def test_tablebase_route_rejects_illegal_move() -> None:
    board = chess.Board("8/8/8/8/8/3k4/7P/3K4 w - - 0 1")

    def probe(_: str) -> dict:
        return {"category": "draw", "moves": [{"uci": "a1a8"}]}

    try:
        verified_tablebase_route(board, probe)
    except ValueError as exc:
        assert "illegal move" in str(exc)
    else:
        raise AssertionError("illegal tablebase route was accepted")


def test_tablebase_route_rebuilds_san_from_legal_board() -> None:
    board = chess.Board("8/8/8/8/8/3k4/7P/3K4 w - - 0 1")
    responses = iter([
        {"category": "draw", "moves": [{"uci": "d1e1", "category": "draw", "dtz": 0}]},
        {"category": "draw", "moves": []},
    ])

    root, route = verified_tablebase_route(board, lambda _: next(responses), max_plies=2)

    assert root["category"] == "draw"
    assert route[0]["uci"] == "d1e1"
    assert route[0]["san"] == "Ke1"
