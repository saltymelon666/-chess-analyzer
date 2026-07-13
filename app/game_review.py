from __future__ import annotations

import io

import chess
import chess.pgn

from .complexity import classify_complexity
from .engine import StockfishService
from .models import EngineResult, EvaluationSnapshot, GameReviewResponse, MoveFacts, MoveReview, MoveResult
from .quality import QUALITY_THRESHOLDS, classify_move, mover_value


PIECE_NAMES_ZH = {
    chess.PAWN: "兵",
    chess.KNIGHT: "马",
    chess.BISHOP: "象",
    chess.ROOK: "车",
    chess.QUEEN: "后",
    chess.KING: "王",
}


async def analyze_pgn(
    *,
    pgn: str,
    stockfish: StockfishService,
    analysis_id: str,
    depth: int,
    timeout_seconds: float,
    max_plies: int,
) -> GameReviewResponse:
    move_facts, fens = parse_pgn_facts(pgn, max_plies=max_plies)
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
        played = facts["played_move"]
        assert isinstance(played, MoveFacts)

        before_board = chess.Board(str(facts["before_fen"]))
        best_result = before_result.top_moves[0] if before_result.top_moves else None
        best_move = _verified_best_move(before_board, best_result)
        pv_facts = _verified_principal_variation(before_board, best_result, best_move)
        principal_variation = [fact.san for fact in pv_facts]
        after_board = chess.Board(str(facts["after_fen"]))
        opponent_result = after_result.top_moves[0] if after_result.top_moves else None
        opponent_reply = _verified_best_move(after_board, opponent_result)
        opponent_pv_facts = _verified_principal_variation(after_board, opponent_result, opponent_reply)
        opponent_variation = [fact.san for fact in opponent_pv_facts]

        quality = classify_move(
            before=before,
            after=after,
            side=str(facts["side"]),
            played_uci=played.uci,
            best_uci=best_move.uci if best_move else None,
            only_legal_move=bool(facts["only_legal_move"]),
            opening_routine=bool(facts["opening_routine"]),
        )
        mate_involved = before.mate_in is not None or after.mate_in is not None
        complexity = classify_complexity(
            before_result=before_result,
            side=str(facts["side"]),
            played=played,
            pv_facts=pv_facts,
            legal_move_count=int(facts["legal_move_count"]),
            evaluation_swing_cp=_evaluation_swing(before, after, str(facts["side"])),
            mate_involved=mate_involved,
            only_legal_move=bool(facts["only_legal_move"]),
            engaged_piece_count=int(facts["engaged_piece_count"]),
        )

        allowed_facts = [played, *pv_facts, *opponent_pv_facts]
        reviews.append(
            MoveReview(
                index=int(facts["index"]),
                move_number=int(facts["move_number"]),
                notation=str(facts["notation"]),
                side=str(facts["side"]),
                san=played.san,
                uci=played.uci,
                from_square=played.from_square,
                to_square=played.to_square,
                before_fen=str(facts["before_fen"]),
                after_fen=str(facts["after_fen"]),
                before=before,
                after=after,
                played_move=played,
                best_move=best_move,
                opponent_reply=opponent_reply,
                centipawn_loss=quality.centipawn_loss,
                best_move_uci=best_move.uci if best_move else None,
                best_move_san=best_move.san if best_move else None,
                best_pv=principal_variation,
                quality_key=quality.key,
                quality_symbol=quality.symbol,
                quality_label=quality.label,
                mate_involved=mate_involved,
                only_legal_move=bool(facts["only_legal_move"]),
                principal_variation=principal_variation,
                principal_variation_facts=pv_facts,
                opponent_variation=opponent_variation,
                opponent_variation_facts=opponent_pv_facts,
                complexity=complexity.level,
                complexity_factors=complexity.factors,
                verified_facts=_verified_fact_lines(
                    side=str(facts["side"]),
                    played=played,
                    best=best_move,
                    before=before,
                    after=after,
                    quality_label=quality.label,
                    principal_variation=principal_variation,
                    opponent_reply=opponent_reply,
                    opponent_variation=opponent_variation,
                ),
                allowed_squares=sorted(
                    {square for fact in allowed_facts for square in (fact.from_square, fact.to_square)}
                ),
                allowed_moves=sorted(
                    {value for fact in allowed_facts for value in (fact.san, fact.uci)}
                ),
                pieces_before=dict(facts["pieces_before"]),
            )
        )

    return GameReviewResponse(
        analysis_id=analysis_id,
        depth=depth,
        move_count=len(reviews),
        moves=reviews,
    )


def parse_pgn_facts(pgn: str, *, max_plies: int) -> tuple[list[dict[str, object]], list[str]]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("无法解析 PGN")
    if game.errors:
        raise ValueError(f"PGN 包含非法走法：{game.errors[0]}")

    board = game.board()
    facts: list[dict[str, object]] = []
    fens = [board.fen()]
    for index, move in enumerate(game.mainline_moves(), start=1):
        if index > max_plies:
            raise ValueError(f"棋谱超过当前上限（{max_plies} 个半回合）")
        if move not in board.legal_moves:
            raise ValueError(f"第 {index} 个半回合不是合法走法")

        before_fen = board.fen()
        side = "white" if board.turn == chess.WHITE else "black"
        fullmove = board.fullmove_number
        legal_count = board.legal_moves.count()
        played = _move_facts(board, move)
        opening_routine = (
            fullmove <= QUALITY_THRESHOLDS["opening_fullmove_max"]
            and not played.capture
            and not played.check
            and played.promotion is None
        )
        pieces_before = _piece_map(board)
        engaged_piece_count = _engaged_piece_count(board)
        board.push(move)
        after_fen = board.fen()
        facts.append(
            {
                "index": index,
                "move_number": fullmove,
                "notation": f"{fullmove}.{played.san}" if side == "white" else f"{fullmove}...{played.san}",
                "side": side,
                "before_fen": before_fen,
                "after_fen": after_fen,
                "played_move": played,
                "legal_move_count": legal_count,
                "only_legal_move": legal_count == 1,
                "opening_routine": opening_routine,
                "engaged_piece_count": engaged_piece_count,
                "pieces_before": pieces_before,
            }
        )
        fens.append(after_fen)

    if not facts:
        raise ValueError("PGN 中没有可分析的走法")
    return facts, fens


def _move_facts(board: chess.Board, move: chess.Move) -> MoveFacts:
    if move not in board.legal_moves:
        raise ValueError(f"非法走法：{move.uci()}")
    piece = board.piece_at(move.from_square)
    if piece is None:
        raise ValueError(f"起点没有棋子：{chess.square_name(move.from_square)}")

    captured_piece = None
    if board.is_capture(move):
        captured_square = move.to_square
        if board.is_en_passant(move):
            captured_square += -8 if board.turn == chess.WHITE else 8
        captured = board.piece_at(captured_square)
        captured_piece = _piece_id(captured) if captured else None

    san = board.san(move)
    check = board.gives_check(move)
    next_board = board.copy(stack=False)
    next_board.push(move)
    return MoveFacts(
        san=san,
        uci=move.uci(),
        from_square=chess.square_name(move.from_square),
        to_square=chess.square_name(move.to_square),
        piece=_piece_id(piece),
        capture=board.is_capture(move),
        captured_piece=captured_piece,
        check=check,
        checkmate=next_board.is_checkmate(),
        castling=board.is_castling(move),
        promotion=chess.piece_name(move.promotion) if move.promotion else None,
    )


def _verified_best_move(board: chess.Board, result: MoveResult | None) -> MoveFacts | None:
    if result is None:
        return None
    try:
        move = chess.Move.from_uci(result.move)
    except ValueError as exc:
        raise RuntimeError(f"Stockfish 返回了无效 UCI：{result.move}") from exc
    if move not in board.legal_moves:
        raise RuntimeError(f"Stockfish 返回了当前局面中的非法走法：{result.move}")
    return _move_facts(board, move)


def _verified_principal_variation(
    board: chess.Board,
    result: MoveResult | None,
    best: MoveFacts | None,
) -> list[MoveFacts]:
    if result is None or best is None:
        return []
    current = board.copy(stack=False)
    first = chess.Move.from_uci(best.uci)
    facts = [_move_facts(current, first)]
    current.push(first)
    for san in result.pv:
        try:
            move = current.parse_san(san)
        except ValueError:
            break
        facts.append(_move_facts(current, move))
        current.push(move)
    return facts


def _verified_fact_lines(
    *,
    side: str,
    played: MoveFacts,
    best: MoveFacts | None,
    before: EvaluationSnapshot,
    after: EvaluationSnapshot,
    quality_label: str,
    principal_variation: list[str],
    opponent_reply: MoveFacts | None,
    opponent_variation: list[str],
) -> list[str]:
    side_zh = "白方" if side == "white" else "黑方"
    lines = [
        f"{side_zh}实战走了 {played.san}：{_piece_zh(played.piece)}从 {played.from_square} 到 {played.to_square}。",
        f"这一步的质量等级是{quality_label}；评价从 {before.evaluation} 变为 {after.evaluation}。",
    ]
    events = []
    if played.capture:
        events.append(f"吃掉了{_piece_zh(played.captured_piece)}")
    if played.castling:
        events.append("完成了王车易位")
    if played.promotion:
        events.append(f"升变为{PIECE_NAMES_ZH[chess.PIECE_NAMES.index(played.promotion)]}")
    if played.checkmate:
        events.append("形成将杀")
    elif played.check:
        events.append("形成将军")
    if events:
        lines.append("棋规确认：" + "、".join(events) + "。")
    else:
        lines.append("棋规确认：这步不是吃子、将军、将杀、易位或升变。")
    if best:
        lines.append(
            f"Stockfish 的合法第一选择是 {best.san}：{_piece_zh(best.piece)}从 {best.from_square} 到 {best.to_square}。"
        )
    if principal_variation:
        lines.append("已验证的主要变化：" + " ".join(principal_variation) + "。")
    if opponent_reply:
        opponent = "黑方" if side == "white" else "白方"
        lines.append(
            f"实战走完后轮到{opponent}，Stockfish 的合法第一选择是 {opponent_reply.san}："
            f"{_piece_zh(opponent_reply.piece)}从 {opponent_reply.from_square} 到 {opponent_reply.to_square}。"
        )
    if opponent_variation:
        lines.append("实战后的已验证变化：" + " ".join(opponent_variation) + "。")
    return lines


def _evaluation_swing(before: EvaluationSnapshot, after: EvaluationSnapshot, side: str) -> int | None:
    before_cp, before_mate = mover_value(before, side)
    after_cp, after_mate = mover_value(after, side)
    if before_mate is not None or after_mate is not None or before_cp is None or after_cp is None:
        return None
    return abs(before_cp - after_cp)


def _snapshot(result: EngineResult) -> EvaluationSnapshot:
    return EvaluationSnapshot(
        evaluation=result.evaluation,
        centipawn=result.centipawn,
        mate_in=result.mate_in,
    )


def _piece_map(board: chess.Board) -> dict[str, str]:
    return {chess.square_name(square): _piece_id(piece) for square, piece in board.piece_map().items()}


def _piece_id(piece: chess.Piece | None) -> str:
    if piece is None:
        return "unknown_piece"
    color = "white" if piece.color == chess.WHITE else "black"
    return f"{color}_{chess.piece_name(piece.piece_type)}"


def _piece_zh(piece_id: str | None) -> str:
    if not piece_id or piece_id == "unknown_piece":
        return "棋子"
    color, name = piece_id.split("_", 1)
    piece_type = chess.PIECE_NAMES.index(name)
    return ("白" if color == "white" else "黑") + PIECE_NAMES_ZH[piece_type]


def _engaged_piece_count(board: chess.Board) -> int:
    engaged: set[int] = set()
    for square, piece in board.piece_map().items():
        for target in board.attacks(square):
            target_piece = board.piece_at(target)
            if target_piece is not None and target_piece.color != piece.color:
                engaged.add(square)
                engaged.add(target)
    return len(engaged)
