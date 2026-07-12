from pathlib import Path

import chess
import pytest

from app.engine import StockfishService


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_stockfish_returns_white_pov_for_both_turns() -> None:
    service = StockfishService(
        ROOT / "stockfish.exe",
        depth=5,
        threads=1,
        hash_mb=16,
        multipv=2,
        timeout_seconds=20,
    )

    white_result = await service.analyze(chess.STARTING_FEN)
    black_to_move = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    black_result = await service.analyze(black_to_move)

    assert white_result.top_moves
    assert black_result.top_moves
    assert white_result.depth >= 5
    assert black_result.depth >= 5
    assert len(white_result.top_moves) == 2
    assert len(black_result.top_moves) == 2


@pytest.mark.asyncio
async def test_invalid_fen_is_rejected() -> None:
    service = StockfishService(
        ROOT / "stockfish.exe",
        depth=1,
        threads=1,
        hash_mb=16,
        multipv=1,
        timeout_seconds=5,
    )
    with pytest.raises(ValueError, match="无效的 FEN"):
        await service.analyze("not-a-fen")

