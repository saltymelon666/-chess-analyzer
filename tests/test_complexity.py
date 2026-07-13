from app.complexity import classify_complexity
from app.models import EngineResult, MoveFacts, MoveResult


def move_fact(**updates) -> MoveFacts:
    values = {
        "san": "e4",
        "uci": "e2e4",
        "from_square": "e2",
        "to_square": "e4",
        "piece": "white_pawn",
        "capture": False,
        "check": False,
        "checkmate": False,
        "castling": False,
    }
    values.update(updates)
    return MoveFacts(**values)


def engine_result(gap: int = 10) -> EngineResult:
    return EngineResult(
        evaluation="+0.20",
        centipawn=20,
        depth=10,
        nodes=100,
        time_ms=5,
        top_moves=[
            MoveResult(move="e2e4", san="e4", centipawn=20, pv=[], depth=10),
            MoveResult(move="d2d4", san="d4", centipawn=20 - gap, pv=[], depth=10),
        ],
    )


def test_quiet_position_is_simple() -> None:
    result = classify_complexity(
        before_result=engine_result(10),
        side="white",
        played=move_fact(),
        pv_facts=[move_fact()],
        legal_move_count=20,
        evaluation_swing_cp=5,
        mate_involved=False,
        only_legal_move=False,
        engaged_piece_count=0,
    )
    assert result.level == "simple"
    assert result.factors.candidate_gap_cp == 10


def test_forcing_mating_position_is_complex() -> None:
    forcing = [
        move_fact(san="Qxf7+", uci="h5f7", from_square="h5", to_square="f7", piece="white_queen", capture=True, check=True),
        move_fact(san="Kxf7", uci="e8f7", from_square="e8", to_square="f7", piece="black_king", capture=True),
        move_fact(san="Ng5+", uci="f3g5", from_square="f3", to_square="g5", piece="white_knight", check=True),
    ]
    result = classify_complexity(
        before_result=engine_result(180),
        side="white",
        played=forcing[0],
        pv_facts=forcing * 3,
        legal_move_count=34,
        evaluation_swing_cp=240,
        mate_involved=True,
        only_legal_move=False,
        engaged_piece_count=10,
    )
    assert result.level == "complex"
    assert result.factors.only_reasonable_move is True
    assert result.factors.forcing_line_plies >= 3
