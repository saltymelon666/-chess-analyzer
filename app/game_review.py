from __future__ import annotations

import io

import chess
import chess.pgn

from .complexity import classify_complexity
from .engine import StockfishService
from .models import (
    CandidateLine,
    EngineResult,
    EvaluationSnapshot,
    GameReviewResponse,
    MoveFacts,
    MoveReview,
    MoveResult,
    VerifiedTactic,
    VariationMove,
)
from .position_facts import extract_position_facts
from .quality import QUALITY_THRESHOLDS, classify_move, mover_value


PIECE_NAMES_ZH = {
    chess.PAWN: "兵",
    chess.KNIGHT: "马",
    chess.BISHOP: "象",
    chess.ROOK: "车",
    chess.QUEEN: "后",
    chess.KING: "王",
}
PIECE_VALUES = {"pawn": 1, "knight": 3, "bishop": 3, "rook": 5, "queen": 9, "king": 100}


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
        best_result, best_move, pv_facts = _first_verified_engine_route(before_board, before_result)
        principal_variation = [fact.san for fact in pv_facts]
        after_board = chess.Board(str(facts["after_fen"]))
        opponent_result, opponent_reply, opponent_pv_facts = _first_verified_engine_route(
            after_board,
            after_result,
        )
        opponent_variation = [fact.san for fact in opponent_pv_facts]
        verified_tactics = _detect_tactical_motifs(before_board, played)
        if opponent_reply:
            verified_tactics.extend(_detect_tactical_motifs(after_board, opponent_reply))
            sacrifice = _detect_tactical_sacrifice(before_board, played, opponent_reply)
            if sacrifice is not None:
                verified_tactics.append(sacrifice)
        direct_piece_loss = bool(
            opponent_reply
            and opponent_reply.capture
            and _piece_value(opponent_reply.captured_piece) >= 3
        )
        multi_step_tactic = max(
            _forcing_prefix_length(pv_facts),
            _forcing_prefix_length(opponent_pv_facts),
        ) >= 3
        opponent_forcing_options = _forcing_candidate_count(after_board, after_result)
        candidate_lines = _candidate_lines(before_board, before_result)
        actual_move_line = _actual_move_line(after_board, after_result)
        for line in candidate_lines:
            verified_tactics.extend(_detect_line_tactics(before_board, line))
        if actual_move_line:
            verified_tactics.extend(_detect_line_tactics(after_board, actual_move_line))
        verified_tactics = _unique_tactics(verified_tactics)

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
            direct_piece_loss=direct_piece_loss,
            tactical_motif_count=len(verified_tactics),
            multi_step_tactic=multi_step_tactic,
            opponent_forcing_options=opponent_forcing_options,
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
                    verified_tactics=verified_tactics,
                    direct_piece_loss=direct_piece_loss,
                ),
                allowed_squares=sorted(
                    {
                        *dict(facts["pieces_before"]).keys(),
                        *[square for fact in allowed_facts for square in (fact.from_square, fact.to_square)],
                        *[square for tactic in verified_tactics for square in tactic.squares],
                        *[square for line in candidate_lines for item in line.moves for square in (item.from_square, item.to_square)],
                        *[square for item in (actual_move_line.moves if actual_move_line else []) for square in (item.from_square, item.to_square)],
                    }
                ),
                allowed_moves=sorted(
                    {
                        *[value for fact in allowed_facts for value in (fact.san, fact.uci)],
                        *[value for line in candidate_lines for item in line.moves for value in (item.san, item.uci)],
                        *[value for item in (actual_move_line.moves if actual_move_line else []) for value in (item.san, item.uci)],
                    }
                ),
                pieces_before=dict(facts["pieces_before"]),
                verified_tactics=verified_tactics,
                candidate_lines=candidate_lines,
                actual_move_line=actual_move_line,
                position_facts=extract_position_facts(
                    str(facts["before_fen"]),
                    candidate_lines=candidate_lines,
                    actual_move_line=actual_move_line,
                    tactics=verified_tactics,
                    namespace=f"move-{int(facts['index'])}-before",
                ),
                position_facts_after=extract_position_facts(
                    str(facts["after_fen"]),
                    candidate_lines=[],
                    actual_move_line=actual_move_line,
                    tactics=verified_tactics,
                    namespace=f"move-{int(facts['index'])}-after",
                ),
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
        played.id = f"move:played:{index}"
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
    if not result.verified:
        raise RuntimeError("Stockfish 路线已标记为未通过验证")
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
    if not result.verified:
        raise RuntimeError("Stockfish 路线已标记为未通过验证")
    current = board.copy(stack=False)
    facts: list[MoveFacts] = []
    uci_line = [item.uci for item in result.line] if result.line else []
    if not uci_line:
        uci_line = [best.uci]
        replay = current.copy(stack=False)
        replay.push(chess.Move.from_uci(best.uci))
        for san in result.pv:
            try:
                parsed = replay.parse_san(san)
            except ValueError as exc:
                raise RuntimeError(f"Stockfish PV 包含非法 SAN：{san}") from exc
            uci_line.append(parsed.uci())
            replay.push(parsed)
    for uci in uci_line[:10]:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError as exc:
            raise RuntimeError(f"Stockfish PV 包含无效 UCI：{uci}") from exc
        if move not in current.legal_moves:
            raise RuntimeError(f"Stockfish PV 包含非法走法：{uci}")
        facts.append(_move_facts(current, move))
        current.push(move)
    if len(facts) != len(uci_line[:10]):
        raise RuntimeError("Stockfish PV 未通过整条路线验证")
    if result.resulting_fen and current.fen() != chess.Board(result.resulting_fen).fen():
        raise RuntimeError("Stockfish PV 最终局面与验证重放结果不一致")
    return facts


def _first_verified_engine_route(
    board: chess.Board,
    result: EngineResult,
) -> tuple[MoveResult | None, MoveFacts | None, list[MoveFacts]]:
    for candidate in result.top_moves:
        try:
            best = _verified_best_move(board, candidate)
            facts = _verified_principal_variation(board, candidate, best)
        except RuntimeError:
            continue
        return candidate, best, facts
    return None, None, []


def _candidate_lines(board: chess.Board, result: EngineResult) -> list[CandidateLine]:
    lines: list[CandidateLine] = []
    for candidate in result.top_moves[:3]:
        try:
            best = _verified_best_move(board, candidate)
            if best is None:
                continue
            facts = _verified_principal_variation(board, candidate, best)
        except RuntimeError:
            continue
        rank = candidate.rank or len(lines) + 1
        moves, resulting_fen = _variation_moves(board, facts)
        line = CandidateLine(
                id=f"line:{rank}",
                rank=rank,
                depth=candidate.depth,
                centipawn=candidate.centipawn,
                mate_in=candidate.mate_in,
                first_move=best,
                moves=moves,
                resulting_fen=candidate.resulting_fen or resulting_fen,
                verified=True,
            )
        for item in line.moves:
            item.id = f"line:{rank}:ply:{item.ply}"
        line.resulting_position_facts = extract_position_facts(
            line.resulting_fen,
            candidate_lines=[],
            actual_move_line=None,
            tactics=[],
            namespace=f"line-{rank}-result",
        )
        lines.append(line)
    return lines


def _actual_move_line(board: chess.Board, after_result: EngineResult) -> CandidateLine | None:
    candidate, reply, facts = _first_verified_engine_route(board, after_result)
    if candidate is None or reply is None:
        return None
    facts = facts[:10]
    moves, resulting_fen = _variation_moves(board, facts)
    line = CandidateLine(
        id="line:played",
        rank=1,
        depth=candidate.depth,
        centipawn=candidate.centipawn,
        mate_in=candidate.mate_in,
        first_move=reply,
        moves=moves,
        resulting_fen=resulting_fen,
        verified=True,
    )
    for item in line.moves:
        item.id = f"line:played:ply:{item.ply}"
    line.resulting_position_facts = extract_position_facts(
        resulting_fen,
        candidate_lines=[],
        actual_move_line=None,
        tactics=[],
        namespace="line-played-result",
    )
    return line


def _variation_moves(board: chess.Board, facts: list[MoveFacts]) -> tuple[list[VariationMove], str]:
    current = board.copy(stack=False)
    moves: list[VariationMove] = []
    for ply, fact in enumerate(facts[:10], start=1):
        move = chess.Move.from_uci(fact.uci)
        if move not in current.legal_moves:
            break
        moves.append(
            VariationMove(
                ply=ply,
                move_number=current.fullmove_number,
                side="white" if current.turn == chess.WHITE else "black",
                san=fact.san,
                uci=fact.uci,
                from_square=fact.from_square,
                to_square=fact.to_square,
                piece=fact.piece,
                capture=fact.capture,
                captured_piece=fact.captured_piece,
                check=fact.check,
                checkmate=fact.checkmate,
                castling=fact.castling,
                promotion=fact.promotion,
            )
        )
        current.push(move)
    return moves, current.fen()


def _detect_line_tactics(board: chess.Board, line: CandidateLine) -> list[VerifiedTactic]:
    current = board.copy(stack=False)
    tactics: list[VerifiedTactic] = []
    for item in line.moves:
        move = chess.Move.from_uci(item.uci)
        if move not in current.legal_moves:
            break
        fact = _move_facts(current, move)
        tactics.extend(_detect_tactical_motifs(current, fact))
        current.push(move)
    return tactics


def _unique_tactics(tactics: list[VerifiedTactic]) -> list[VerifiedTactic]:
    unique: list[VerifiedTactic] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for tactic in tactics:
        key = (tactic.name, tactic.move_uci, tuple(tactic.squares))
        if key not in seen:
            seen.add(key)
            unique.append(tactic)
    return unique


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
    verified_tactics: list[VerifiedTactic],
    direct_piece_loss: bool,
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
    if direct_piece_loss and opponent_reply and opponent_reply.captured_piece:
        lines.append(
            f"实战后的引擎第一选择会直接吃掉{_piece_zh(opponent_reply.captured_piece)}，这是已验证的直接丢子风险。"
        )
    lines.extend("已验证战术：" + tactic.description for tactic in verified_tactics)
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


def _piece_value(piece_id: str | None) -> int:
    if not piece_id or "_" not in piece_id:
        return 0
    return PIECE_VALUES.get(piece_id.split("_", 1)[1], 0)


def _forcing_prefix_length(facts: list[MoveFacts]) -> int:
    count = 0
    for fact in facts:
        if not (fact.capture or fact.check or fact.checkmate or fact.promotion is not None):
            break
        count += 1
    return count


def _forcing_candidate_count(board: chess.Board, result: EngineResult) -> int:
    count = 0
    for candidate in result.top_moves:
        try:
            fact = _verified_best_move(board, candidate)
        except RuntimeError:
            continue
        if fact and (fact.capture or fact.check or fact.checkmate or fact.promotion is not None):
            count += 1
    return count


def _detect_tactical_motifs(board: chess.Board, fact: MoveFacts) -> list[VerifiedTactic]:
    try:
        move = chess.Move.from_uci(fact.uci)
    except ValueError:
        return []
    if move not in board.legal_moves:
        return []

    actor = board.turn
    enemy = not actor
    before_attacks = set(board.attacks(move.from_square))
    next_board = board.copy(stack=False)
    next_board.push(move)
    moved_piece = next_board.piece_at(move.to_square)
    if moved_piece is None:
        return []

    motifs: list[VerifiedTactic] = []
    attacked_targets = [
        square
        for square in next_board.attacks(move.to_square)
        if (target := next_board.piece_at(square)) is not None
        and target.color == enemy
    ]
    valuable_targets = [
        square
        for square in attacked_targets
        if (target := next_board.piece_at(square)) is not None
        and target.piece_type in {chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING}
    ]
    newly_attacked = [square for square in attacked_targets if square not in before_attacks]
    if len(attacked_targets) >= 2 and valuable_targets and newly_attacked:
        target_names = "、".join(
            f"{_piece_zh(_piece_id(next_board.piece_at(square)))}（{chess.square_name(square)}）"
            for square in attacked_targets[:3]
        )
        motifs.append(
            VerifiedTactic(
                name="double_attack",
                side="white" if actor == chess.WHITE else "black",
                move_uci=fact.uci,
                description=f"{fact.san} 后，{_piece_zh(fact.piece)}同时攻击 {target_names}。",
                squares=[fact.to_square, *[chess.square_name(square) for square in attacked_targets[:3]]],
            )
        )

    newly_pinned = [
        square
        for square, piece in next_board.piece_map().items()
        if piece.color == enemy
        and piece.piece_type != chess.KING
        and next_board.is_pinned(enemy, square)
        and not board.is_pinned(enemy, square)
    ]
    for square in newly_pinned[:2]:
        square_name = chess.square_name(square)
        motifs.append(
            VerifiedTactic(
                name="pin",
                side="white" if actor == chess.WHITE else "black",
                move_uci=fact.uci,
                description=f"{fact.san} 后，{_piece_zh(_piece_id(next_board.piece_at(square)))}在 {square_name} 被钉在王前。",
                squares=[fact.to_square, square_name],
            )
        )

    skewer = _king_skewer_target(next_board, move.to_square, enemy)
    if skewer is not None:
        king_square, target_square = skewer
        motifs.append(
            VerifiedTactic(
                name="skewer",
                side="white" if actor == chess.WHITE else "black",
                move_uci=fact.uci,
                description=(
                    f"{fact.san} 形成王在前、{_piece_zh(_piece_id(next_board.piece_at(target_square)))}在后的串击。"
                ),
                squares=[fact.to_square, chess.square_name(king_square), chess.square_name(target_square)],
            )
        )
    return motifs


def _detect_tactical_sacrifice(
    board: chess.Board,
    played: MoveFacts,
    opponent_reply: MoveFacts,
) -> VerifiedTactic | None:
    """Confirm a sacrifice only when the engine reply takes the moved piece."""
    if not played.capture or not opponent_reply.capture:
        return None
    if opponent_reply.to_square != played.to_square:
        return None
    if opponent_reply.captured_piece != played.piece:
        return None
    if _piece_value(played.piece) <= _piece_value(played.captured_piece):
        return None
    side = "white" if board.turn == chess.WHITE else "black"
    return VerifiedTactic(
        name="tactical_sacrifice",
        side=side,
        move_uci=played.uci,
        description=(
            f"{played.san}是战术性牺牲，以{_piece_zh(played.piece)}换取"
            f"{_piece_zh(played.captured_piece)}，对手主线以{opponent_reply.san}吃回；"
            "是否成立必须结合后续强制路线判断。"
        ),
        squares=[played.from_square, played.to_square],
    )


def _king_skewer_target(board: chess.Board, attacker_square: int, enemy: chess.Color) -> tuple[int, int] | None:
    attacker = board.piece_at(attacker_square)
    if attacker is None or attacker.piece_type not in {chess.BISHOP, chess.ROOK, chess.QUEEN}:
        return None
    king_square = board.king(enemy)
    if king_square is None or king_square not in board.attacks(attacker_square):
        return None
    attacker_file, attacker_rank = chess.square_file(attacker_square), chess.square_rank(attacker_square)
    king_file, king_rank = chess.square_file(king_square), chess.square_rank(king_square)
    file_step = (king_file > attacker_file) - (king_file < attacker_file)
    rank_step = (king_rank > attacker_rank) - (king_rank < attacker_rank)
    file_index, rank_index = king_file + file_step, king_rank + rank_step
    while 0 <= file_index < 8 and 0 <= rank_index < 8:
        square = chess.square(file_index, rank_index)
        piece = board.piece_at(square)
        if piece is not None:
            if piece.color == enemy and piece.piece_type in {chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN}:
                return king_square, square
            return None
        file_index += file_step
        rank_index += rank_step
    return None
