import chess
import pytest

from app.game_review import _detect_tactical_motifs, analyze_pgn, parse_pgn_facts
from app.models import EngineResult, MoveResult


def engine_result(centipawn: int, best_move: str, best_san: str) -> EngineResult:
    return EngineResult(
        evaluation=f"{centipawn / 100:+.2f}",
        centipawn=centipawn,
        mate_in=None,
        depth=10,
        nodes=100,
        time_ms=5,
        top_moves=[
            MoveResult(
                move=best_move,
                san=best_san,
                centipawn=centipawn,
                mate_in=None,
                pv=[best_san],
                depth=10,
            )
        ],
    )


class ScriptedStockfish:
    async def analyze_many(self, fens, *, depth, timeout_seconds):
        assert len(fens) == 3
        assert depth == 10
        return [
            engine_result(20, "e2e4", "e4"),
            engine_result(10, "c7c5", "c5"),
            engine_result(80, "g1f3", "Nf3"),
        ]


@pytest.mark.asyncio
async def test_game_review_uses_black_mover_perspective() -> None:
    result = await analyze_pgn(
        pgn="1. e4 e5",
        stockfish=ScriptedStockfish(),
        analysis_id="test-analysis",
        depth=10,
        timeout_seconds=30,
        max_plies=20,
    )

    assert result.move_count == 2
    assert result.moves[0].quality_key == "routine"
    assert result.moves[1].side == "black"
    assert result.moves[1].centipawn_loss == 70
    assert result.moves[1].quality_key == "inaccuracy"
    assert result.moves[1].before_fen.endswith(" b KQkq - 0 1")
    assert result.moves[1].played_move.piece == "black_pawn"
    assert result.moves[1].played_move.from_square == "e7"
    assert result.moves[1].best_move is not None
    assert result.moves[1].best_move.san == "c5"
    assert result.moves[1].complexity in {"simple", "normal", "complex"}


@pytest.mark.asyncio
async def test_damaged_pgn_is_rejected_before_engine_call() -> None:
    with pytest.raises(ValueError, match="PGN 包含非法走法"):
        await analyze_pgn(
            pgn="1. e4 e5 2. Ke3",
            stockfish=ScriptedStockfish(),
            analysis_id="broken-analysis",
            depth=10,
            timeout_seconds=30,
            max_plies=20,
        )


def test_rule_facts_for_castling_capture_and_last_white_move() -> None:
    facts, _ = parse_pgn_facts(
        "1. e4 d5 2. exd5 Qxd5 3. Nf3 Nc6 4. Bc4 Qd8 5. O-O",
        max_plies=30,
    )
    capture = facts[2]["played_move"]
    castling = facts[-1]["played_move"]
    assert capture.capture is True
    assert capture.captured_piece == "black_pawn"
    assert castling.castling is True
    assert castling.san == "O-O"
    assert facts[-1]["side"] == "white"
    assert facts[-1]["move_number"] == 5
    assert len(facts) == 9


def test_rule_facts_for_promotion() -> None:
    pgn = """[SetUp "1"]
[FEN "7k/P7/8/8/8/8/8/7K w - - 0 1"]

1. a8=Q+"""
    facts, _ = parse_pgn_facts(pgn, max_plies=10)
    promoted = facts[0]["played_move"]
    assert promoted.promotion == "queen"
    assert promoted.check is True
    assert promoted.from_square == "a7"
    assert promoted.to_square == "a8"


def test_san_disambiguation_is_generated_by_chess_rules() -> None:
    pgn = """[SetUp "1"]
[FEN "7k/8/8/8/8/8/8/1N3N1K w - - 0 1"]

1. Nbd2"""
    facts, _ = parse_pgn_facts(pgn, max_plies=10)
    move = facts[0]["played_move"]
    assert move.san == "Nbd2"
    assert move.uci == "b1d2"
    assert move.piece == "white_knight"


def test_custom_fen_black_to_move_preserves_full_move_number() -> None:
    pgn = """[SetUp "1"]
[FEN "8/8/8/8/8/8/4k3/6RK b - - 0 12"]

12... Kf3"""
    facts, fens = parse_pgn_facts(pgn, max_plies=10)
    assert len(facts) == 1
    assert facts[0]["side"] == "black"
    assert facts[0]["move_number"] == 12
    assert facts[0]["notation"] == "12...Kf3"
    assert fens[0].endswith(" b - - 0 12")


def test_verified_fork_and_pin_are_detected_conservatively() -> None:
    fork_pgn = """[SetUp "1"]
[FEN "1r1q3k/8/8/4N3/8/8/8/7K w - - 0 1"]

1. Nc6"""
    fork_facts, _ = parse_pgn_facts(fork_pgn, max_plies=10)
    fork_board = chess.Board(fork_facts[0]["before_fen"])
    fork = _detect_tactical_motifs(fork_board, fork_facts[0]["played_move"])
    assert any(tactic.name == "double_attack" for tactic in fork)

    pin_pgn = """[SetUp "1"]
[FEN "4k3/8/2n5/8/8/8/8/5B1K w - - 0 1"]

1. Bb5"""
    pin_facts, _ = parse_pgn_facts(pin_pgn, max_plies=10)
    pin_board = chess.Board(pin_facts[-1]["before_fen"])
    pin = _detect_tactical_motifs(pin_board, pin_facts[-1]["played_move"])
    assert any(tactic.name == "pin" for tactic in pin)
