import pytest

from app.game_review import analyze_pgn
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
