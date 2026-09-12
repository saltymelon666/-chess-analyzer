import asyncio
import threading
from pathlib import Path

import chess
import chess.engine
import pytest

from app.engine import StockfishBusyError, StockfishService
from app.models import EngineResult, MoveResult


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


@pytest.mark.asyncio
async def test_busy_stockfish_request_has_bounded_queue_wait() -> None:
    service = StockfishService(
        ROOT / "stockfish.exe",
        depth=1,
        threads=1,
        hash_mb=16,
        multipv=1,
        timeout_seconds=5,
        queue_timeout_seconds=0.01,
    )
    service.available = lambda: True
    await service._lock.acquire()
    try:
        with pytest.raises(StockfishBusyError, match="正在处理其他整盘分析"):
            await service.analyze_many(
                [chess.STARTING_FEN],
                depth=1,
                timeout_seconds=5,
            )
    finally:
        service._lock.release()


@pytest.mark.asyncio
async def test_timed_out_worker_keeps_lock_until_background_thread_finishes() -> None:
    service = StockfishService(
        ROOT / "stockfish.exe",
        depth=1,
        threads=1,
        hash_mb=16,
        multipv=1,
        timeout_seconds=5,
    )
    started = threading.Event()
    release = threading.Event()

    def slow_worker() -> None:
        started.set()
        release.wait(timeout=2)

    with pytest.raises(asyncio.TimeoutError):
        await service._run_exclusive(slow_worker, timeout_seconds=0.01)

    assert started.is_set()
    assert service._lock.locked()
    release.set()
    for _ in range(100):
        if not service._lock.locked():
            break
        await asyncio.sleep(0.01)
    assert not service._lock.locked()


@pytest.mark.asyncio
async def test_timeout_signals_batch_worker_to_stop() -> None:
    service = StockfishService(
        ROOT / "stockfish.exe",
        depth=1,
        threads=1,
        hash_mb=16,
        multipv=1,
        timeout_seconds=5,
    )
    started = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()

    def slow_worker() -> None:
        started.set()
        release.wait(timeout=2)

    with pytest.raises(asyncio.TimeoutError):
        await service._run_exclusive(
            slow_worker,
            timeout_seconds=0.01,
            on_cancel=cancelled.set,
        )

    assert started.is_set()
    assert cancelled.is_set()
    release.set()
    for _ in range(100):
        if not service._lock.locked():
            break
        await asyncio.sleep(0.01)
    assert not service._lock.locked()


def test_illegal_move_invalidates_complete_engine_pv() -> None:
    board = chess.Board()
    legal = chess.Move.from_uci("e2e4")
    illegal_after_legal = chess.Move.from_uci("e1e3")

    with pytest.raises(ValueError, match="illegal move at ply 2"):
        StockfishService._pv_details(board, [legal, illegal_after_legal])


def _engine_result(
    evaluation_cp: int,
    best_move: str,
    *,
    second_cp: int | None = None,
    depth: int = 10,
) -> EngineResult:
    moves = [
        MoveResult(
            move=best_move,
            san=best_move,
            centipawn=evaluation_cp,
            pv=[],
            depth=depth,
            rank=1,
        )
    ]
    if second_cp is not None:
        moves.append(
            MoveResult(
                move="second",
                san="second",
                centipawn=second_cp,
                pv=[],
                depth=depth,
                rank=2,
            )
        )
    return EngineResult(
        evaluation=f"{evaluation_cp / 100:+.2f}",
        centipawn=evaluation_cp,
        depth=depth,
        nodes=1,
        time_ms=1,
        top_moves=moves,
    )


def test_batch_analysis_rechecks_unstable_low_depth_choice(monkeypatch) -> None:
    service = StockfishService(
        ROOT / "stockfish.exe",
        depth=10,
        threads=1,
        hash_mb=16,
        multipv=2,
        timeout_seconds=20,
    )
    before = chess.Board()
    after = chess.Board()
    after.push_uci("g1f3")
    responses = {
        (id(before), 10): _engine_result(-15, "stable", second_cp=-15),
        (id(before), 16): _engine_result(-10, "stable", second_cp=-12, depth=16),
        (id(after), 10): _engine_result(-91, "Nxd4", second_cp=-19),
        (id(after), 16): _engine_result(-17, "Bb7", second_cp=-6, depth=16),
        (id(after), 20): _engine_result(-5, "Bb7", second_cp=0, depth=20),
    }
    calls: list[tuple[int, int]] = []

    class DummyEngine:
        def quit(self) -> None:
            pass

    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", lambda _: DummyEngine())
    monkeypatch.setattr(service, "_configure_engine", lambda _: None)

    def fake_analyze(_engine, board: chess.Board, depth: int) -> EngineResult:
        calls.append((id(board), depth))
        return responses[(id(board), depth)]

    monkeypatch.setattr(service, "_analyze_board", fake_analyze)

    results = service._analyze_many_sync([before, after], 10)

    assert results[1].top_moves[0].move == "Bb7"
    assert results[1].centipawn == -5
    assert (id(after), 16) in calls
    assert (id(after), 20) in calls


def test_batch_analysis_reuses_cached_positions(monkeypatch) -> None:
    service = StockfishService(
        ROOT / "stockfish.exe",
        depth=10,
        threads=1,
        hash_mb=16,
        multipv=2,
        timeout_seconds=20,
    )
    board = chess.Board()
    calls = 0
    engine_starts = 0

    class DummyEngine:
        def quit(self) -> None:
            pass

    def fake_start_engine(_path):
        nonlocal engine_starts
        engine_starts += 1
        return DummyEngine()

    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", fake_start_engine)
    monkeypatch.setattr(service, "_configure_engine", lambda _: None)

    def fake_analyze(_engine, board: chess.Board, depth: int) -> EngineResult:
        nonlocal calls
        calls += 1
        return _engine_result(0, "e2e4", depth=depth)

    monkeypatch.setattr(service, "_analyze_board", fake_analyze)

    first = service._analyze_many_sync([board], 10)
    second = service._analyze_many_sync([board], 10)

    assert first[0] == second[0]
    assert calls == 1
    assert engine_starts == 1


def test_batch_analysis_caps_deep_rechecks_to_most_unstable_positions(monkeypatch) -> None:
    service = StockfishService(
        ROOT / "stockfish.exe",
        depth=10,
        threads=1,
        hash_mb=16,
        multipv=2,
        timeout_seconds=20,
    )
    boards = []
    for halfmove_clock in range(15):
        board = chess.Board()
        board.halfmove_clock = halfmove_clock
        boards.append(board)
    rechecked: list[int] = []

    class DummyEngine:
        def quit(self) -> None:
            pass

    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", lambda _: DummyEngine())
    monkeypatch.setattr(service, "_configure_engine", lambda _: None)

    def fake_analyze(_engine, board: chess.Board, depth: int) -> EngineResult:
        if depth == 10:
            return _engine_result(0, "best", second_cp=-(50 + board.halfmove_clock), depth=10)
        rechecked.append(board.halfmove_clock)
        return _engine_result(0, "best", second_cp=0, depth=depth)

    monkeypatch.setattr(service, "_analyze_board", fake_analyze)

    service._analyze_many_sync(boards, 10)

    assert len(rechecked) == 12
    assert set(rechecked) == set(range(3, 15))


def test_engine_refuses_to_promote_second_line_when_rank_one_is_invalid() -> None:
    service = StockfishService(
        ROOT / "stockfish.exe",
        depth=10,
        threads=1,
        hash_mb=16,
        multipv=2,
        timeout_seconds=20,
    )

    class MissingBestEngine:
        def analyse(self, board, limit, multipv):
            return [
                {
                    "multipv": 1,
                    "score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE),
                    "pv": [chess.Move.from_uci("e7e5")],
                    "depth": 10,
                },
                {
                    "multipv": 2,
                    "score": chess.engine.PovScore(chess.engine.Cp(-91), chess.WHITE),
                    "pv": [chess.Move.from_uci("e2e4")],
                    "depth": 10,
                },
            ]

    with pytest.raises(RuntimeError, match="拒绝用次选路线替代"):
        service._analyze_board(MissingBestEngine(), chess.Board(), 10)
