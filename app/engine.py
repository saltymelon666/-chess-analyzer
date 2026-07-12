from __future__ import annotations

import asyncio
from pathlib import Path

import chess
import chess.engine

from .models import EngineResult, MoveResult


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

    def _analyze_sync(self, board: chess.Board) -> EngineResult:
        engine = chess.engine.SimpleEngine.popen_uci(str(self.executable))
        try:
            options: dict[str, int] = {}
            if "Threads" in engine.options:
                options["Threads"] = self.threads
            if "Hash" in engine.options:
                options["Hash"] = self.hash_mb
            if options:
                engine.configure(options)

            infos = engine.analyse(
                board,
                chess.engine.Limit(depth=self.depth),
                multipv=self.multipv,
            )
            if isinstance(infos, dict):
                infos = [infos]

            top_moves: list[MoveResult] = []
            max_depth = 0
            max_nodes = 0
            max_time_ms = 0

            for info in infos:
                score = info.get("score")
                pv = info.get("pv", [])
                if score is None or not pv:
                    continue

                # Normalize every score to White's point of view.
                white_score = score.pov(chess.WHITE)
                mate_in = white_score.mate()
                centipawn = None if mate_in is not None else white_score.score()
                san_line = self._pv_to_san(board, pv)
                depth = int(info.get("depth", 0))
                max_depth = max(max_depth, depth)
                max_nodes = max(max_nodes, int(info.get("nodes", 0)))
                max_time_ms = max(max_time_ms, round(float(info.get("time", 0)) * 1000))

                top_moves.append(
                    MoveResult(
                        move=pv[0].uci(),
                        san=san_line[0] if san_line else pv[0].uci(),
                        centipawn=centipawn,
                        mate_in=mate_in,
                        pv=san_line[1:],
                        depth=depth,
                    )
                )

            if not top_moves:
                raise RuntimeError("Stockfish 未返回可用分析结果")

            best = top_moves[0]
            evaluation = self._format_evaluation(best.centipawn, best.mate_in)
            return EngineResult(
                evaluation=evaluation,
                centipawn=best.centipawn,
                mate_in=best.mate_in,
                depth=max_depth,
                nodes=max_nodes,
                time_ms=max_time_ms,
                top_moves=top_moves,
            )
        finally:
            engine.quit()

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
    def _format_evaluation(centipawn: int | None, mate_in: int | None) -> str:
        if mate_in is not None:
            return f"白方 M{mate_in}" if mate_in > 0 else f"黑方 M{abs(mate_in)}"
        value = (centipawn or 0) / 100
        return f"{value:+.2f}"

