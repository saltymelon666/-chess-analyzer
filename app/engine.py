from __future__ import annotations

import asyncio
from pathlib import Path

import chess
import chess.engine

from .models import EngineResult, MoveResult, VariationMove


MAX_PV_PLIES = 10


class StockfishService:
    def __init__(
        self,
        executable: Path,
        *,
        depth: int,
        threads: int,
        hash_mb: int,
        multipv: int,
        timeout_seconds: float,
    ) -> None:
        self.executable = executable
        self.depth = depth
        self.threads = threads
        self.hash_mb = hash_mb
        self.multipv = multipv
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    def available(self) -> bool:
        return self.executable.is_file()

    async def analyze(self, fen: str) -> EngineResult:
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise ValueError(f"无效的 FEN：{exc}") from exc

        if not self.available():
            raise RuntimeError(f"找不到 Stockfish：{self.executable}")

        async with self._lock:
            return await asyncio.wait_for(
                asyncio.to_thread(self._analyze_sync, board),
                timeout=self.timeout_seconds,
            )

    async def analyze_many(
        self,
        fens: list[str],
        *,
        depth: int,
        timeout_seconds: float,
    ) -> list[EngineResult]:
        boards: list[chess.Board] = []
        for fen in fens:
            try:
                boards.append(chess.Board(fen))
            except ValueError as exc:
                raise ValueError(f"无效的 FEN：{exc}") from exc
        if not self.available():
            raise RuntimeError(f"找不到 Stockfish：{self.executable}")
        async with self._lock:
            return await asyncio.wait_for(
                asyncio.to_thread(self._analyze_many_sync, boards, depth),
                timeout=timeout_seconds,
            )

    def _analyze_sync(self, board: chess.Board) -> EngineResult:
        engine = chess.engine.SimpleEngine.popen_uci(str(self.executable))
        try:
            self._configure_engine(engine)
            return self._analyze_board(engine, board, self.depth)
        finally:
            engine.quit()

    def _analyze_many_sync(self, boards: list[chess.Board], depth: int) -> list[EngineResult]:
        engine = chess.engine.SimpleEngine.popen_uci(str(self.executable))
        try:
            self._configure_engine(engine)
            return [self._analyze_board(engine, board, depth) for board in boards]
        finally:
            engine.quit()

    def _configure_engine(self, engine: chess.engine.SimpleEngine) -> None:
        options: dict[str, int] = {}
        if "Threads" in engine.options:
            options["Threads"] = self.threads
        if "Hash" in engine.options:
            options["Hash"] = self.hash_mb
        if options:
            engine.configure(options)

    def _analyze_board(
        self,
        engine: chess.engine.SimpleEngine,
        board: chess.Board,
        depth: int,
    ) -> EngineResult:
        if board.is_game_over(claim_draw=True):
            return self._terminal_result(board, depth)

        multipv = min(self.multipv, board.legal_moves.count())
        # python-chess treats MultiPV as a managed UCI option and sends the
        # equivalent setoption command for this analysis call.
        infos = engine.analyse(
            board,
            chess.engine.Limit(depth=depth),
            multipv=multipv,
        )
        if isinstance(infos, dict):
            infos = [infos]

        top_moves: list[MoveResult] = []
        max_depth = 0
        max_nodes = 0
        max_time_ms = 0
        for rank, info in enumerate(infos, start=1):
            score = info.get("score")
            pv = info.get("pv", [])
            if score is None or not pv:
                continue
            white_score = score.pov(chess.WHITE)
            mate_in = white_score.mate()
            centipawn = None if mate_in is not None else white_score.score()
            try:
                line, resulting_fen = self._pv_details(board, pv[:MAX_PV_PLIES])
            except ValueError:
                # A partially valid PV is not a trustworthy route. Discard the
                # complete line instead of exposing a verified-looking prefix.
                continue
            san_line = [item.san for item in line]
            info_depth = int(info.get("depth", 0))
            max_depth = max(max_depth, info_depth)
            max_nodes = max(max_nodes, int(info.get("nodes", 0)))
            max_time_ms = max(max_time_ms, round(float(info.get("time", 0)) * 1000))
            top_moves.append(
                MoveResult(
                    move=pv[0].uci(),
                    san=san_line[0] if san_line else pv[0].uci(),
                    centipawn=centipawn,
                    mate_in=mate_in,
                    pv=san_line[1:],
                    depth=info_depth,
                    rank=rank,
                    line=line,
                    resulting_fen=resulting_fen,
                    verified=True,
                )
            )

        if not top_moves:
            raise RuntimeError("Stockfish 未返回可用分析结果")
        best = top_moves[0]
        return EngineResult(
            evaluation=self._format_evaluation(best.centipawn, best.mate_in),
            centipawn=best.centipawn,
            mate_in=best.mate_in,
            depth=max_depth,
            nodes=max_nodes,
            time_ms=max_time_ms,
            top_moves=top_moves,
        )

    def _terminal_result(self, board: chess.Board, depth: int) -> EngineResult:
        if board.is_checkmate():
            white_won = board.turn == chess.BLACK
            mate_in = 1 if white_won else -1
            evaluation = "白方已将杀" if white_won else "黑方已将杀"
            return EngineResult(
                evaluation=evaluation,
                centipawn=None,
                mate_in=mate_in,
                depth=depth,
                nodes=0,
                time_ms=0,
                top_moves=[],
            )
        return EngineResult(
            evaluation="+0.00",
            centipawn=0,
            mate_in=None,
            depth=depth,
            nodes=0,
            time_ms=0,
            top_moves=[],
        )

    @staticmethod
    def _pv_to_san(board: chess.Board, pv: list[chess.Move]) -> list[str]:
        current = board.copy(stack=False)
        san_line: list[str] = []
        for move in pv:
            if move not in current.legal_moves:
                break
            san_line.append(current.san(move))
            current.push(move)
        return san_line

    @staticmethod
    def _pv_details(board: chess.Board, pv: list[chess.Move]) -> tuple[list[VariationMove], str]:
        current = board.copy(stack=False)
        line: list[VariationMove] = []
        for ply, move in enumerate(pv[:MAX_PV_PLIES], start=1):
            if move not in current.legal_moves:
                raise ValueError(f"Stockfish PV contains illegal move at ply {ply}: {move.uci()}")
            piece = current.piece_at(move.from_square)
            captured_piece = None
            if current.is_capture(move):
                captured_square = move.to_square
                if current.is_en_passant(move):
                    captured_square += -8 if current.turn == chess.WHITE else 8
                captured = current.piece_at(captured_square)
                if captured is not None:
                    captured_piece = f"{'white' if captured.color else 'black'}_{chess.piece_name(captured.piece_type)}"
            next_board = current.copy(stack=False)
            next_board.push(move)
            line.append(
                VariationMove(
                    ply=ply,
                    move_number=current.fullmove_number,
                    side="white" if current.turn == chess.WHITE else "black",
                    san=current.san(move),
                    uci=move.uci(),
                    from_square=chess.square_name(move.from_square),
                    to_square=chess.square_name(move.to_square),
                    piece=(
                        f"{'white' if piece and piece.color else 'black'}_{chess.piece_name(piece.piece_type)}"
                        if piece else "unknown_piece"
                    ),
                    capture=current.is_capture(move),
                    captured_piece=captured_piece,
                    check=current.gives_check(move),
                    checkmate=next_board.is_checkmate(),
                    castling=current.is_castling(move),
                    promotion=chess.piece_name(move.promotion) if move.promotion else None,
                )
            )
            current = next_board
        if len(line) != len(pv[:MAX_PV_PLIES]):
            raise ValueError("Stockfish PV validation did not cover the complete route")
        return line, current.fen()

    @staticmethod
    def _format_evaluation(centipawn: int | None, mate_in: int | None) -> str:
        if mate_in is not None:
            return f"白方 M{mate_in}" if mate_in > 0 else f"黑方 M{abs(mate_in)}"
        value = (centipawn or 0) / 100
        return f"{value:+.2f}"
