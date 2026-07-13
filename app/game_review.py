from __future__ import annotations

import io

import chess
import chess.pgn

from .engine import StockfishService
from .models import EvaluationSnapshot, GameReviewResponse, MoveReview
from .quality import QUALITY_THRESHOLDS, classify_move


async def analyze_pgn(
    *,
    pgn: str,
    stockfish: StockfishService,
    analysis_id: str,
    depth: int,
    timeout_seconds: float,
    max_plies: int,
) -> GameReviewResponse:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("无法解析 PGN")
    if game.errors:
        raise ValueError(f"PGN 包含非法走法：{game.errors[0]}")

    board = game.board()
    move_facts: list[dict[str, object]] = []
    fens = [board.fen()]
    for index, move in enumerate(game.mainline_moves(), start=1):
        if index > max_plies:
            raise ValueError(f"棋谱超过当前上限（{max_plies} 个半回合）")
        if move not in board.legal_moves:
            raise ValueError(f"第 {index} 步不是合法走法")

        side = "white" if board.turn == chess.WHITE else "black"
        san = board.san(move)
        fullmove = board.fullmove_number
        legal_count = board.legal_moves.count()
        opening_routine = (
            fullmove <= QUALITY_THRESHOLDS["opening_fullmove_max"]
            and not board.is_capture(move)
            and not board.gives_check(move)
            and move.promotion is None
        )
        before_fen = board.fen()
        board.push(move)
        after_fen = board.fen()
        move_facts.append(
            {
                "index": index,
                "move_number": fullmove,
                "notation": f"{fullmove}.{san}" if side == "white" else f"{fullmove}...{san}",
                "side": side,
                "san": san,
                "uci": move.uci(),
                "from_square": chess.square_name(move.from_square),
                "to_square": chess.square_name(move.to_square),
                "before_fen": before_fen,
                "after_fen": after_fen,
                "only_legal_move": legal_count == 1,
                "opening_routine": opening_routine,
            }
        )
        fens.append(after_fen)

    if not move_facts:
        raise ValueError("PGN 中没有可分析的走法")

    evaluations = await stockfish.analyze_many(
        fens,
        depth=depth,
        timeout_seconds=timeout_seconds,
    )
    reviews: list[MoveReview] = []
    for offset, facts in enumerate(move_facts):
        before_result = evaluations[offset]
        after_result = evaluations[offset + 1]
        before = _snapshot(before_result)
        after = _snapshot(after_result)
        best = before_result.top_moves[0] if before_result.top_moves else None
        quality = classify_move(
            before=before,
            after=after,
            side=str(facts["side"]),
            played_uci=str(facts["uci"]),
            best_uci=best.move if best else None,
            only_legal_move=bool(facts["only_legal_move"]),
            opening_routine=bool(facts["opening_routine"]),
        )
        reviews.append(
            MoveReview(
                index=int(facts["index"]),
                move_number=int(facts["move_number"]),
                notation=str(facts["notation"]),
                side=str(facts["side"]),
                san=str(facts["san"]),
                uci=str(facts["uci"]),
                from_square=str(facts["from_square"]),
                to_square=str(facts["to_square"]),
                before_fen=str(facts["before_fen"]),
                after_fen=str(facts["after_fen"]),
                before=before,
                after=after,
                centipawn_loss=quality.centipawn_loss,
                best_move_uci=best.move if best else None,
                best_move_san=best.san if best else None,
                best_pv=([best.san] + best.pv) if best else [],
                quality_key=quality.key,
                quality_symbol=quality.symbol,
                quality_label=quality.label,
                mate_involved=before.mate_in is not None or after.mate_in is not None,
                only_legal_move=bool(facts["only_legal_move"]),
            )
        )

    return GameReviewResponse(
        analysis_id=analysis_id,
        depth=depth,
        move_count=len(reviews),
        moves=reviews,
    )


def _snapshot(result: object) -> EvaluationSnapshot:
    return EvaluationSnapshot(
        evaluation=getattr(result, "evaluation"),
        centipawn=getattr(result, "centipawn"),
        mate_in=getattr(result, "mate_in"),
    )
