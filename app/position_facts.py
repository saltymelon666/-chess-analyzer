from __future__ import annotations

from collections import Counter

import chess

from .models import CandidateLine, EvidenceFact, MoveFacts, PositionFacts, VerifiedTactic


PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
PIECE_NAMES = {
    chess.PAWN: "兵",
    chess.KNIGHT: "马",
    chess.BISHOP: "象",
    chess.ROOK: "车",
    chess.QUEEN: "后",
    chess.KING: "王",
}


def extract_position_facts(
    fen: str,
    *,
    candidate_lines: list[CandidateLine],
    actual_move_line: CandidateLine | None,
    tactics: list[VerifiedTactic],
    namespace: str = "position",
) -> PositionFacts:
    """Extract only board-verifiable facts; every narrative fact carries evidence."""
    board = chess.Board(fen)
    pieces = [
        {
            "id": f"piece:{namespace}:{'white' if piece.color else 'black'}-{chess.piece_name(piece.piece_type)}-{chess.square_name(square)}",
            "side": "white" if piece.color else "black",
            "piece": chess.piece_name(piece.piece_type),
            "square": chess.square_name(square),
        }
        for square, piece in sorted(board.piece_map().items())
    ]
    material = _material(board)
    material["id"] = f"fact:{namespace}:material"
    activity = _piece_activity(board)
    king_safety = _king_safety(board)
    pawn_structure = _pawn_structure(board)
    threats = _route_threats(candidate_lines, actual_move_line, tactics)
    open_files, semi_open_files = _file_status(board)
    immediate_checks = []
    immediate_captures = []
    for move in board.legal_moves:
        fact = _legal_move_fact(board, move)
        fact.id = f"move:{namespace}:legal:{fact.uci}"
        if fact.check:
            immediate_checks.append(fact)
            threats.append(
                EvidenceFact(
                    category="immediate_checkmate" if fact.checkmate else "immediate_check",
                    side="white" if board.turn else "black",
                    description=f"当前有合法将军走法{fact.san}",
                    evidence=[f"python-chess验证: {fact.uci} / {fact.san}"],
                    squares=[fact.from_square, fact.to_square],
                )
            )
        if fact.capture:
            immediate_captures.append(fact)
            threats.append(
                EvidenceFact(
                    category="immediate_capture",
                    side="white" if board.turn else "black",
                    description=f"当前有合法吃子走法{fact.san}",
                    evidence=[f"python-chess验证: {fact.uci} / {fact.san}", f"被吃棋子: {fact.captured_piece}"],
                    squares=[fact.from_square, fact.to_square],
                )
            )
        if fact.promotion:
            threats.append(
                EvidenceFact(
                    category="immediate_promotion",
                    side="white" if board.turn else "black",
                    description=f"当前有合法升变走法{fact.san}",
                    evidence=[f"python-chess验证: {fact.uci}升变为{fact.promotion}"],
                    squares=[fact.from_square, fact.to_square],
                )
            )
    result = PositionFacts(
        side_to_move="white" if board.turn else "black",
        pieces=pieces,
        material=material,
        piece_activity=activity,
        king_safety=king_safety,
        pawn_structure=pawn_structure,
        threats=threats,
        open_files=open_files,
        semi_open_files=semi_open_files,
        immediate_checks=immediate_checks,
        immediate_captures=immediate_captures,
    )
    _assign_fact_ids(result, namespace)
    return result


def _material(board: chess.Board) -> dict[str, object]:
    sides: dict[str, dict[str, object]] = {}
    totals: dict[str, int] = {}
    for color, side in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        inventory: dict[str, list[str]] = {}
        total = 0
        for piece_type, name in PIECE_NAMES.items():
            squares = sorted(chess.square_name(sq) for sq in board.pieces(piece_type, color))
            inventory[chess.piece_name(piece_type)] = squares
            total += PIECE_VALUES[piece_type] * len(squares)
        sides[side] = {"value": total, "pieces": inventory}
        totals[side] = total
    difference = totals["white"] - totals["black"]
    return {
        **sides,
        "valueDifferenceWhiteMinusBlack": difference,
        "advantage": "white" if difference > 0 else "black" if difference < 0 else "equal",
        "evidence": [f"FEN: {board.fen()}", "子力价值按兵1、马3、象3、车5、后9计算"],
    }


def _piece_activity(board: chess.Board) -> list[EvidenceFact]:
    facts: list[EvidenceFact] = []
    for square, piece in board.piece_map().items():
        if piece.piece_type == chess.KING:
            continue
        side = "white" if piece.color == chess.WHITE else "black"
        square_name = chess.square_name(square)
        enemy_attackers = len(board.attackers(not piece.color, square))
        defenders = len(board.attackers(piece.color, square))
        if defenders == 0:
            facts.append(
                EvidenceFact(
                    category="undefended_piece",
                    side=side,
                    description=f"{_piece_label(piece, square_name)}当前没有本方棋子保护",
                    evidence=[f"python-chess计算{square_name}的本方保护者数量为0"],
                    squares=[square_name],
                )
            )
        if enemy_attackers:
            category = "underprotected" if enemy_attackers > defenders else "attacked"
            description = (
                f"{_piece_label(piece, square_name)}受攻，攻击者{enemy_attackers}个，保护者{defenders}个"
            )
            facts.append(
                EvidenceFact(
                    category=category,
                    side=side,
                    description=description,
                    evidence=[f"{square_name}被对方攻击{enemy_attackers}次", f"{square_name}被本方保护{defenders}次"],
                    squares=[square_name],
                )
            )

        reachable = [target for target in board.attacks(square) if board.color_at(target) != piece.color]
        if len(reachable) >= 7:
            facts.append(
                EvidenceFact(
                    category="active_piece",
                    side=side,
                    description=f"{_piece_label(piece, square_name)}当前可作用到{len(reachable)}个非己方占据格",
                    evidence=[f"从{square_name}按棋规可到达或攻击{len(reachable)}个格子"],
                    squares=[square_name],
                )
            )
        elif len(reachable) <= 2:
            facts.append(
                EvidenceFact(
                    category="constrained_piece",
                    side=side,
                    description=f"{_piece_label(piece, square_name)}当前活动范围较小",
                    evidence=[f"从{square_name}按棋规仅可作用到{len(reachable)}个非己方占据格"],
                    squares=[square_name],
                )
            )

        if piece.piece_type in {chess.KNIGHT, chess.BISHOP} and _is_outpost(board, square, piece.color):
            facts.append(
                EvidenceFact(
                    category="verified_outpost",
                    side=side,
                    description=f"{_piece_label(piece, square_name)}位于有兵保护且不受敌兵攻击的前哨格",
                    evidence=[f"{square_name}由本方兵保护", f"{square_name}不受对方兵攻击"],
                    squares=[square_name],
                )
            )
        if square in {chess.D4, chess.E4, chess.D5, chess.E5}:
            facts.append(
                EvidenceFact(
                    category="central_piece",
                    side=side,
                    description=f"{_piece_label(piece, square_name)}占据四个中心格之一",
                    evidence=[f"{square_name}属于d4、e4、d5、e5中心格"],
                    squares=[square_name],
                )
            )

        defended_targets = []
        for target in board.attacks(square):
            target_piece = board.piece_at(target)
            if target_piece and target_piece.color == piece.color and target_piece.piece_type in {chess.KING, chess.QUEEN}:
                defended_targets.append(chess.square_name(target))
        if defended_targets:
            facts.append(
                EvidenceFact(
                    category="important_defender",
                    side=side,
                    description=f"{_piece_label(piece, square_name)}正在保护本方王或后",
                    evidence=[f"从{square_name}按棋规保护: {'、'.join(defended_targets)}"],
                    squares=[square_name, *defended_targets],
                )
            )
    return facts


def _king_safety(board: chess.Board) -> list[EvidenceFact]:
    facts: list[EvidenceFact] = []
    for color, side in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        king = board.king(color)
        if king is None:
            continue
        king_name = chess.square_name(king)
        evidence = [f"{side} king在{king_name}"]
        if board.turn == color and board.is_check():
            evidence.append("当前行棋方正被将军")
        facts.append(
            EvidenceFact(
                category="king_square",
                side=side,
                description=f"{'白' if color else '黑'}王位于{king_name}" + ("，当前被将军" if board.turn == color and board.is_check() else ""),
                evidence=evidence,
                squares=[king_name],
            )
        )
        facts.append(
            EvidenceFact(
                category="castling_history_unavailable",
                side=side,
                description="仅凭当前FEN不能可靠判断是否已经完成过易位",
                evidence=["FEN只保存当前易位权，不保存王和车的完整移动历史"],
                squares=[king_name],
            )
        )
        rights = []
        if board.has_kingside_castling_rights(color):
            rights.append("王翼")
        if board.has_queenside_castling_rights(color):
            rights.append("后翼")
        facts.append(
            EvidenceFact(
                category="castling_rights",
                side=side,
                description=f"{'白' if color else '黑'}方" + (f"仍有{'、'.join(rights)}易位权" if rights else "没有易位权"),
                evidence=[f"FEN易位字段: {board.castling_xfen() or '-'}"],
                squares=[king_name],
            )
        )

        shield = []
        direction = 1 if color == chess.WHITE else -1
        king_file = chess.square_file(king)
        king_rank = chess.square_rank(king)
        shield_rank = king_rank + direction
        if 0 <= shield_rank < 8:
            for file_index in range(max(0, king_file - 1), min(7, king_file + 1) + 1):
                sq = chess.square(file_index, shield_rank)
                if board.piece_at(sq) == chess.Piece(chess.PAWN, color):
                    shield.append(chess.square_name(sq))
        facts.append(
            EvidenceFact(
                category="pawn_shield",
                side=side,
                description=f"{king_name}王前相邻一排有{len(shield)}枚本方兵",
                evidence=["王前兵位置: " + ("、".join(shield) if shield else "无")],
                squares=[king_name, *shield],
            )
        )

        nearby = []
        for sq, piece in board.piece_map().items():
            if piece.color == color:
                continue
            if chess.square_distance(king, sq) <= 2:
                nearby.append(chess.square_name(sq))
        if nearby:
            facts.append(
                EvidenceFact(
                    category="nearby_attackers",
                    side=side,
                    description=f"{king_name}附近两格范围内有{len(nearby)}枚对方棋子",
                    evidence=["附近对方棋子: " + "、".join(sorted(nearby))],
                    squares=[king_name, *sorted(nearby)],
                )
            )
        open_files, semi_open = _file_status(board)
        neighboring_files = {
            chess.FILE_NAMES[index]
            for index in range(max(0, king_file - 1), min(7, king_file + 1) + 1)
        }
        nearby_open = sorted(neighboring_files.intersection(open_files))
        nearby_semi = sorted(neighboring_files.intersection(semi_open[side]))
        if nearby_open or nearby_semi:
            facts.append(
                EvidenceFact(
                    category="king_near_open_file",
                    side=side,
                    description=f"{king_name}附近有开放或对本方半开放的线路",
                    evidence=[
                        "附近开放线: " + ("、".join(nearby_open) if nearby_open else "无"),
                        "附近半开放线: " + ("、".join(nearby_semi) if nearby_semi else "无"),
                    ],
                    squares=[king_name],
                )
            )
    return facts


def _pawn_structure(board: chess.Board) -> list[EvidenceFact]:
    facts: list[EvidenceFact] = []
    all_pawn_files = {
        color: Counter(chess.square_file(sq) for sq in board.pieces(chess.PAWN, color))
        for color in (chess.WHITE, chess.BLACK)
    }
    for color, side in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        pawns = sorted(board.pieces(chess.PAWN, color))
        by_file = all_pawn_files[color]
        for file_index, count in by_file.items():
            if count >= 2:
                squares = sorted(chess.square_name(sq) for sq in pawns if chess.square_file(sq) == file_index)
                facts.append(_fact("doubled_pawns", side, f"{chess.FILE_NAMES[file_index]}线有{count}枚叠兵", squares))
        for sq in pawns:
            file_index = chess.square_file(sq)
            square_name = chess.square_name(sq)
            adjacent_has_pawn = any(by_file.get(adj, 0) for adj in (file_index - 1, file_index + 1) if 0 <= adj < 8)
            if not adjacent_has_pawn:
                facts.append(_fact("isolated_pawn", side, f"{square_name}兵是孤兵，相邻线路没有本方兵", [square_name]))
            if _is_passed_pawn(board, sq, color):
                facts.append(_fact("passed_pawn", side, f"{square_name}兵是通路兵，前方及相邻线路没有敌兵", [square_name]))
            if file_index in (3, 4):
                facts.append(_fact("central_pawn", side, f"{square_name}兵位于中心d/e线", [square_name]))
            attackers = len(board.attackers(not color, sq))
            defenders = len(board.attackers(color, sq))
            if attackers > defenders:
                facts.append(
                    EvidenceFact(
                        category="vulnerable_pawn",
                        side=side,
                        description=f"{square_name}兵受到{attackers}次攻击、只有{defenders}次保护",
                        evidence=[f"攻击者{attackers}个", f"保护者{defenders}个"],
                        squares=[square_name],
                    )
                )

    locked_center_pairs: list[str] = []
    locked_center_squares: list[str] = []
    for file_index in (3, 4):
        for white_square in board.pieces(chess.PAWN, chess.WHITE):
            if chess.square_file(white_square) != file_index:
                continue
            black_square = white_square + 8
            if black_square < 64 and board.piece_at(black_square) == chess.Piece(chess.PAWN, chess.BLACK):
                white_name = chess.square_name(white_square)
                black_name = chess.square_name(black_square)
                locked_center_pairs.append(f"{white_name}-{black_name}")
                locked_center_squares.extend((white_name, black_name))
    if len(locked_center_pairs) >= 2:
        facts.append(
            EvidenceFact(
                category="closed_center",
                side=None,
                description=f"中心由相互阻挡的兵链封闭：{'、'.join(locked_center_pairs)}",
                evidence=["d/e线至少有两对白兵与正前方黑兵相互阻挡"],
                squares=sorted(set(locked_center_squares)),
            )
        )

    for file_index, file_name in enumerate(chess.FILE_NAMES):
        white_count = all_pawn_files[chess.WHITE].get(file_index, 0)
        black_count = all_pawn_files[chess.BLACK].get(file_index, 0)
        if not white_count and not black_count:
            facts.append(_fact("open_file", None, f"{file_name}线是开放线，双方都没有兵", []))
        elif not white_count or not black_count:
            side = "white" if not white_count else "black"
            facts.append(_fact("half_open_file", side, f"{file_name}线对{'白' if side == 'white' else '黑'}方是半开放线", []))
    return facts


def _route_threats(
    candidate_lines: list[CandidateLine],
    actual_move_line: CandidateLine | None,
    tactics: list[VerifiedTactic],
) -> list[EvidenceFact]:
    facts: list[EvidenceFact] = []
    for tactic in tactics:
        facts.append(
            EvidenceFact(
                category=tactic.name,
                side=tactic.side,
                description=tactic.description,
                evidence=[f"python-chess验证走法{tactic.move_uci}", "涉及格子: " + "、".join(tactic.squares)],
                squares=tactic.squares,
            )
        )
    for label, line in [(f"候选路线{item.rank}", item) for item in candidate_lines] + ([ ("实战后路线", actual_move_line) ] if actual_move_line else []):
        for move in line.moves:
            san = move.san
            events = []
            if "x" in san:
                events.append("吃子")
            if "#" in san:
                events.append("将杀")
            elif "+" in san:
                events.append("将军")
            if "=" in san:
                events.append("升变")
            if events:
                facts.append(
                    EvidenceFact(
                        category="route_event",
                        side=move.side,
                        description=f"{label}第{move.ply}个半回合{san}包含{'、'.join(events)}",
                        evidence=[f"Stockfish {label}: {move.uci} / {san}"],
                        squares=[move.from_square, move.to_square],
                    )
                )
            if move.capture and _piece_value_id(move.captured_piece) >= 3:
                facts.append(
                    EvidenceFact(
                        category="direct_piece_loss",
                        side="black" if move.side == "white" else "white",
                        description=f"{label}中的{move.san}直接吃掉价值至少3分的棋子",
                        evidence=[f"Stockfish路线: {move.uci} / {move.san}", f"被吃棋子: {move.captured_piece}"],
                        squares=[move.from_square, move.to_square],
                    )
                )
    return facts


def _fact(category: str, side: str | None, description: str, squares: list[str]) -> EvidenceFact:
    return EvidenceFact(
        category=category,
        side=side,
        description=description,
        evidence=[description],
        squares=squares,
    )


def _assign_fact_ids(facts: PositionFacts, namespace: str) -> None:
    groups = {
        "activity": facts.piece_activity,
        "king": facts.king_safety,
        "pawn": facts.pawn_structure,
        "threat": facts.threats,
    }
    for group, items in groups.items():
        seen: Counter[str] = Counter()
        for item in items:
            square_key = "-".join(item.squares) or "board"
            base = f"fact:{namespace}:{group}:{item.category}:{item.side or 'both'}:{square_key}"
            seen[base] += 1
            item.id = base if seen[base] == 1 else f"{base}:{seen[base]}"


def _file_status(board: chess.Board) -> tuple[list[str], dict[str, list[str]]]:
    open_files: list[str] = []
    semi_open = {"white": [], "black": []}
    for file_index, file_name in enumerate(chess.FILE_NAMES):
        white = any(chess.square_file(sq) == file_index for sq in board.pieces(chess.PAWN, chess.WHITE))
        black = any(chess.square_file(sq) == file_index for sq in board.pieces(chess.PAWN, chess.BLACK))
        if not white and not black:
            open_files.append(file_name)
        elif not white:
            semi_open["white"].append(file_name)
        elif not black:
            semi_open["black"].append(file_name)
    return open_files, semi_open


def _legal_move_fact(board: chess.Board, move: chess.Move) -> MoveFacts:
    piece = board.piece_at(move.from_square)
    captured_piece = None
    if board.is_capture(move):
        captured_square = move.to_square
        if board.is_en_passant(move):
            captured_square += -8 if board.turn == chess.WHITE else 8
        captured = board.piece_at(captured_square)
        if captured is not None:
            captured_piece = f"{'white' if captured.color else 'black'}_{chess.piece_name(captured.piece_type)}"
    after = board.copy(stack=False)
    after.push(move)
    return MoveFacts(
        san=board.san(move),
        uci=move.uci(),
        from_square=chess.square_name(move.from_square),
        to_square=chess.square_name(move.to_square),
        piece=(
            f"{'white' if piece and piece.color else 'black'}_{chess.piece_name(piece.piece_type)}"
            if piece else "unknown_piece"
        ),
        capture=board.is_capture(move),
        captured_piece=captured_piece,
        check=board.gives_check(move),
        checkmate=after.is_checkmate(),
        castling=board.is_castling(move),
        promotion=chess.piece_name(move.promotion) if move.promotion else None,
    )


def _piece_label(piece: chess.Piece, square: str) -> str:
    return f"{'白' if piece.color else '黑'}{PIECE_NAMES[piece.piece_type]}({square})"


def _piece_value_id(piece_id: str | None) -> int:
    if not piece_id or "_" not in piece_id:
        return 0
    values = {"pawn": 1, "knight": 3, "bishop": 3, "rook": 5, "queen": 9, "king": 100}
    return values.get(piece_id.split("_", 1)[1], 0)


def _is_passed_pawn(board: chess.Board, square: int, color: chess.Color) -> bool:
    file_index = chess.square_file(square)
    rank_index = chess.square_rank(square)
    for enemy in board.pieces(chess.PAWN, not color):
        enemy_file = chess.square_file(enemy)
        enemy_rank = chess.square_rank(enemy)
        if abs(enemy_file - file_index) <= 1:
            if color == chess.WHITE and enemy_rank > rank_index:
                return False
            if color == chess.BLACK and enemy_rank < rank_index:
                return False
    return True


def _is_outpost(board: chess.Board, square: int, color: chess.Color) -> bool:
    rank_index = chess.square_rank(square)
    if color == chess.WHITE and rank_index < 4:
        return False
    if color == chess.BLACK and rank_index > 3:
        return False
    own_pawns = board.pieces(chess.PAWN, color)
    enemy_pawns = board.pieces(chess.PAWN, not color)
    return any(square in board.attacks(pawn) for pawn in own_pawns) and not any(
        square in board.attacks(pawn) for pawn in enemy_pawns
    )
