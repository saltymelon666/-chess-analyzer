from pathlib import Path

import chess
import pytest

from app.engine import StockfishService
from app.models import CandidateLine, MoveFacts, VariationMove
from app.position_facts import extract_position_facts


REPRESENTATIVE_FENS = [
    chess.STARTING_FEN,
    "r1bq1rk1/ppp2ppp/2np1n2/4p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 4 7",
    "4k3/8/8/8/8/2P5/2P1P3/4K3 w - - 0 1",
    "4k3/p7/8/8/8/8/7P/4K3 w - - 0 1",
    "6k1/5ppp/8/8/2B5/5Q2/8/6K1 w - - 0 1",
    "8/2p2pk1/1p1p2p1/p2Pp3/P1P1P3/1P3KP1/5P2/8 w - - 0 30",
    "8/8/2k5/3p4/3P4/2K5/8/8 b - - 0 40",
    "4k3/8/8/8/8/8/4q3/4K3 w - - 0 1",
    "7k/P7/8/8/8/8/8/7K w - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1",
]


def facts(fen: str):
    return extract_position_facts(fen, candidate_lines=[], actual_move_line=None, tactics=[])


@pytest.mark.parametrize("fen", REPRESENTATIVE_FENS)
def test_representative_positions_only_return_real_squares_and_evidence(fen: str) -> None:
    board = chess.Board(fen)
    result = facts(fen)
    assert len(result.pieces) == len(board.piece_map())
    assert result.side_to_move == ("white" if board.turn else "black")
    for group in (
        result.piece_activity,
        result.king_safety,
        result.pawn_structure,
        result.threats,
        result.key_pieces,
    ):
        for item in group:
            assert item.evidence
            assert all(chess.parse_square(square) >= 0 for square in item.squares)


def test_material_pawns_and_file_structure_are_computed_from_board() -> None:
    result = facts("4k3/p7/8/8/8/2P5/2P1P2P/4K3 w - - 0 1")
    assert result.material["white"]["value"] == 4
    assert result.material["black"]["value"] == 1
    assert result.material["advantage"] == "white"
    assert any(item.category == "doubled_pawns" and set(item.squares) == {"c2", "c3"} for item in result.pawn_structure)
    assert "d" in result.open_files
    assert "a" in result.semi_open_files["white"]
    assert "h" in result.semi_open_files["black"]


def test_immediate_checks_and_captures_are_legal_moves() -> None:
    fen = "4k3/8/8/8/8/8/4q3/4K3 w - - 0 1"
    board = chess.Board(fen)
    result = facts(fen)
    for fact in [*result.immediate_checks, *result.immediate_captures]:
        move = chess.Move.from_uci(fact.uci)
        assert move in board.legal_moves
        assert board.san(move) == fact.san


def test_pv_details_stop_at_ten_plies_and_restore_resulting_fen() -> None:
    board = chess.Board()
    uci_moves = [
        "e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6",
        "d2d3", "f8c5", "c2c3", "d7d6", "b1d2", "e8g8",
    ]
    pv = [chess.Move.from_uci(uci) for uci in uci_moves]
    line, resulting_fen = StockfishService._pv_details(board, pv)
    assert len(line) == 10
    assert [item.uci for item in line] == uci_moves[:10]
    replay = board.copy(stack=False)
    for item in line:
        assert chess.Move.from_uci(item.uci) in replay.legal_moves
        replay.push_uci(item.uci)
    assert chess.Board(resulting_fen).fen() == replay.fen()


def test_candidate_line_serializes_complete_route_metadata() -> None:
    first = MoveFacts(
        san="e4", uci="e2e4", from_square="e2", to_square="e4",
        piece="white_pawn", capture=False, check=False, checkmate=False,
        castling=False,
    )
    route = CandidateLine(
        rank=1,
        depth=18,
        evaluation=34,
        mate=None,
        firstMove=first,
        pv=[
            VariationMove(
                plyIndex=1, fullMoveNumber=1, side="white", san="e4", uci="e2e4",
                **{"from": "e2", "to": "e4"}, piece="white_pawn", capture=False,
                check=False, checkmate=False, castling=False,
            )
        ],
        resultingFen="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    )
    payload = route.model_dump(by_alias=True)
    assert payload["rank"] == 1
    assert payload["evaluation"] == 34
    assert payload["pv"][0]["fullMoveNumber"] == 1
    assert payload["pv"][0]["from"] == "e2"
    assert chess.Board(payload["resultingFen"]).is_valid()
