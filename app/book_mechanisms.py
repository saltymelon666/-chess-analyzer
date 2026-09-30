"""Small chess explanations derived from replayed routes, never book quotations.

Each explanation states a local mechanism or a possible continuation. It does
not attribute the complete evaluation difference to that mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

import chess

from .models import CandidateLine, MoveFacts, MoveReview, VariationMove


NAMES = {chess.PAWN: "兵", chess.KNIGHT: "马", chess.BISHOP: "象",
         chess.ROOK: "车", chess.QUEEN: "后", chess.KING: "王"}
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
          chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 20}


@dataclass(frozen=True)
class Step:
    before: chess.Board
    after: chess.Board
    move: chess.Move
    fact: MoveFacts | VariationMove
    actor: chess.Piece
    identity: int


@dataclass(frozen=True)
class BookMechanism:
    statement: str
    evidence_refs: list[str]


def _piece(piece: chess.Piece) -> str:
    return ("白" if piece.color else "黑") + NAMES[piece.piece_type]


def _passed(board: chess.Board, square: int, color: chess.Color) -> bool:
    if (board.ep_square == square + (-8 if color else 8)
            and board.has_legal_en_passant()):
        return False
    return not any(
        abs(chess.square_file(enemy) - chess.square_file(square)) <= 1
        and (chess.square_rank(enemy) - chess.square_rank(square)) * (1 if color else -1) > 0
        for enemy in board.pieces(chess.PAWN, not color)
    )


def _replay(board: chess.Board, facts: list[MoveFacts | VariationMove]) -> list[Step] | None:
    identities = {square: square for square in board.piece_map()}
    result: list[Step] = []
    board = board.copy(stack=False)
    for fact in facts:
        try:
            move = chess.Move.from_uci(fact.uci)
        except ValueError:
            return None
        if move not in board.legal_moves:
            return None
        actor = board.piece_at(move.from_square)
        if actor is None or (
            board.san(move) != fact.san
            or chess.square_name(move.from_square) != fact.from_square
            or chess.square_name(move.to_square) != fact.to_square
            or fact.piece.split("_")[-1] != chess.piece_name(actor.piece_type)
            or ("_" in fact.piece and fact.piece.split("_", 1)[0] != ("white" if actor.color else "black"))
            or board.is_capture(move) != fact.capture
            or board.gives_check(move) != fact.check
            or (isinstance(fact, VariationMove) and fact.side != ("white" if actor.color else "black"))
        ):
            return None
        identity = identities.pop(move.from_square)
        captured = move.to_square
        if board.is_en_passant(move):
            captured += -8 if actor.color else 8
        identities.pop(captured, None)
        if board.is_castling(move):
            rank = chess.square_rank(move.from_square)
            rook_from = chess.square(7 if move.to_square > move.from_square else 0, rank)
            rook_to = chess.square(5 if move.to_square > move.from_square else 3, rank)
            if rook_from in identities:
                identities[rook_to] = identities.pop(rook_from)
        identities[move.to_square] = identity
        after = board.copy(stack=False)
        after.push(move)
        if (fact.checkmate != after.is_checkmate() or fact.castling != board.is_castling(move)
                or fact.promotion != (chess.piece_name(move.promotion) if move.promotion else None)):
            return None
        result.append(Step(board, after, move, fact, actor, identity))
        board = after
    return result


def _structure(step: Step) -> tuple[int, str] | None:
    """Explain pawn changes after an actual move, with board-local consequences."""
    before, after = step.before, step.after
    for color in (step.actor.color, not step.actor.color):
        for square in sorted(after.pieces(chess.PAWN, color)):
            if _passed(after, square, color) and not (
                before.piece_at(square) == chess.Piece(chess.PAWN, color)
                and _passed(before, square, color)
            ):
                # A previously passed pawn advancing is not a newly created passer.
                if (step.actor == chess.Piece(chess.PAWN, color)
                        and square == step.move.to_square
                        and _passed(before, step.move.from_square, color)):
                    continue
                label = chess.square_name(square)
                return 9, (f"{label}的{_piece(chess.Piece(chess.PAWN, color))}成为通路兵，"
                           "前方本线和相邻两线已没有敌兵能够阻挡它；对手需要用其他子力看住它的推进")
        for file in range(8):
            pawns = sorted(sq for sq in after.pieces(chess.PAWN, color) if chess.square_file(sq) == file)
            old = [sq for sq in before.pieces(chess.PAWN, color) if chess.square_file(sq) == file]
            if len(pawns) >= 2 and len(pawns) > len(old):
                labels = "、".join(chess.square_name(sq) for sq in pawns)
                return 8, (f"{'白方' if color else '黑方'}的{labels}兵叠在同一条线上，"
                           "彼此不能像相邻线的兵那样互相保护，兵形也随这次交换改变")
    return None


def _effect(step: Step) -> tuple[int, str] | None:
    board, after, move, actor = step.before, step.after, step.move, step.actor
    origin, target = chess.square_name(move.from_square), chess.square_name(move.to_square)
    name = _piece(actor)
    if after.is_checkmate():
        return 20, f"{name}落到{target}形成将杀"
    structure = _structure(step)
    if structure:
        return structure
    attacks = set(after.attacks(move.to_square) & after.pin(actor.color, move.to_square))
    old_attacks = set(board.attacks(move.from_square))
    targets = sorted(
        ((VALUES[p.piece_type], sq, p) for sq in attacks
         if (p := after.piece_at(sq)) is not None and p.color != actor.color), reverse=True,
    )
    defended = [(sq, after.piece_at(sq)) for sq in sorted(attacks)
                if after.piece_at(sq) is not None and after.piece_at(sq).color == actor.color]
    # Supporting an advanced passer is more informative than counting controlled squares.
    for sq, pawn in defended:
        if pawn.piece_type == chess.PAWN and _passed(after, sq, pawn.color):
            progress = chess.square_rank(sq) if pawn.color else 7 - chess.square_rank(sq)
            if progress >= 4:
                return 10, f"{name}在{target}保护{chess.square_name(sq)}通路兵"
    if len(targets) >= 2 and targets[1][0] >= 3:
        labels = "和".join(f"{chess.square_name(sq)}的{_piece(p)}" for _, sq, p in targets[:2])
        return 12, f"{name}在{target}同时攻击{labels}，对手必须处理两个受攻击的目标"
    if board.gives_check(move):
        return 9, f"{name}在{target}将军，{'黑方' if actor.color else '白方'}必须先应将"
    if actor.piece_type == chess.KING:
        pawn_targets = [sq for sq, piece in defended
                        if piece.piece_type == chess.PAWN and sq not in old_attacks]
        if pawn_targets:
            labels = "、".join(chess.square_name(sq) for sq in pawn_targets)
            return 8, f"{name}从{origin}走到{target}后亲自保护{labels}兵"
        for sq, piece in defended:
            if piece.piece_type in {chess.QUEEN, chess.ROOK} and sq not in old_attacks:
                return 9, (f"{name}从{origin}走到{target}后直接保护{chess.square_name(sq)}的本方{NAMES[piece.piece_type]}，"
                           "让王也参加对子力的支援")
    if board.is_capture(move):
        square = move.to_square + ((-8 if actor.color else 8) if board.is_en_passant(move) else 0)
        victim = board.piece_at(square)
        if victim:
            return 5, (
                f"{name}以{step.fact.san}吃掉{chess.square_name(square)}的{_piece(victim)}"
            )
    if targets:
        _, sq, victim = targets[0]
        if sq not in old_attacks:
            return 7, f"{name}来到{target}后直接攻击{chess.square_name(sq)}的{_piece(victim)}"
    if actor.piece_type == chess.ROOK:
        file = chess.square_file(move.to_square)
        own = [sq for sq in after.pieces(chess.PAWN, actor.color) if chess.square_file(sq) == file]
        enemy = [sq for sq in after.pieces(chess.PAWN, not actor.color) if chess.square_file(sq) == file]
        if not own:
            term = "半开放线" if enemy else "开放线"
            return 7, (f"{name}进入{target[0]}{term}，这条线上没有本方兵挡路，"
                       "车可以沿纵线寻找侵入点或攻击目标")
    if actor.piece_type == chess.PAWN:
        for sq in sorted(attacks - old_attacks):
            for enemy_sq in after.pieces(chess.PAWN, not actor.color):
                push = chess.Move(enemy_sq, sq)
                if push not in after.legal_moves or after.piece_at(sq) is not None:
                    continue
                hypothetical = after.copy(stack=False)
                hypothetical.push(push)
                if chess.Move(move.to_square, sq) not in hypothetical.legal_moves:
                    continue
                chased = [target_sq for target_sq in hypothetical.attacks(sq)
                          if hypothetical.piece_at(target_sq) == chess.Piece(chess.KNIGHT, actor.color)]
                explanation = (f"{name}从{origin}推进到{target}后控制{chess.square_name(sq)}，"
                               f"{chess.square_name(enemy_sq)}的敌兵若推进到这里")
                if chased:
                    explanation += f"赶走{chess.square_name(chased[0])}马"
                return 8, explanation + f"，{target}兵便可以吃掉它；这步兵着限制了对方的推兵选择"
            for enemy_sq in after.pieces(chess.KNIGHT, not actor.color):
                knight_move = chess.Move(enemy_sq, sq)
                if knight_move in after.legal_moves and after.piece_at(sq) is None:
                    hypothetical = after.copy(stack=False)
                    hypothetical.push(knight_move)
                    if chess.Move(move.to_square, sq) not in hypothetical.legal_moves:
                        continue
                    return 8, (f"{name}控制{chess.square_name(sq)}，"
                               f"{chess.square_name(enemy_sq)}的敌马若跳入这里，就会受到这枚兵的攻击")
        for sq, piece in board.piece_map().items():
            if piece == chess.Piece(chess.BISHOP, actor.color):
                opened = set(after.attacks(sq)) - set(board.attacks(sq))
                if move.from_square in board.attacks(sq) and opened:
                    return 6, (f"{name}离开{origin}，给{chess.square_name(sq)}的本方象打开对角线，"
                               "为象从原位出动腾出了空间")
    new_defended = [(sq, piece) for sq, piece in defended
                    if sq not in old_attacks and piece.piece_type != chess.KING]
    if new_defended:
        sq, piece = max(new_defended, key=lambda item: VALUES[item[1].piece_type])
        return 4, f"{name}在{target}新增保护{chess.square_name(sq)}的本方{NAMES[piece.piece_type]}"
    return None


def _notation(steps: list[Step]) -> str:
    return " ".join(("…" if step.actor.color == chess.BLACK else "") + step.fact.san for step in steps)


def _supported(text: str, allowed: set[str]) -> bool:
    return set(re.findall(r"[a-h][1-8]", text)) <= allowed


def _lasting_effect(steps: list[Step], index: int) -> tuple[int, str] | None:
    step = steps[index]
    if index + 1 < len(steps):
        reply = steps[index + 1]
        if reply.fact.capture and reply.move.to_square == step.move.to_square:
            # A piece immediately recaptured cannot be presented as a durable
            # attacker, supporter or passed pawn. Describe the exchange instead.
            if step.fact.capture:
                victim = step.before.piece_at(step.move.to_square)
                if victim:
                    return 6, (
                        f"{_piece(step.actor)}以{step.fact.san}吃掉{chess.square_name(step.move.to_square)}的{_piece(victim)}，"
                        f"{reply.fact.san}随即回吃；双方交换了这两枚{NAMES[victim.piece_type]}"
                        if step.actor.piece_type == victim.piece_type else
                        f"{_piece(step.actor)}以{step.fact.san}吃掉{chess.square_name(step.move.to_square)}的{_piece(victim)}，"
                        f"{reply.fact.san}随即回吃，双方在这里完成子力交换"
                    )
            return None
    return _effect(step)


def _journey(steps: list[Step], color: chess.Color, allowed: set[str]) -> tuple[int, str, int] | None:
    best: tuple[int, str, int] | None = None
    for identity in dict.fromkeys(step.identity for step in steps if step.actor.color == color):
        visits = [(i, step) for i, step in enumerate(steps) if step.identity == identity]
        if len(visits) < 2 or visits[0][1].actor.piece_type == chess.PAWN:
            continue
        for offset in range(1, len(visits)):
            index, last = visits[offset]
            first = visits[0][1]
            effect = _lasting_effect(steps, index)
            path = "—".join([chess.square_name(first.move.from_square)] + [chess.square_name(s.move.to_square) for _, s in visits[:offset + 1]])
            if (first.actor.piece_type == chess.KING
                    and not any(p.piece_type in {chess.QUEEN, chess.ROOK} for p in first.before.piece_map().values())):
                enemies = list(last.after.pieces(chess.PAWN, not color))
                closer = [sq for sq in enemies if chess.square_distance(last.move.to_square, sq)
                          < chess.square_distance(first.move.from_square, sq)]
                if closer:
                    target = min(closer, key=lambda sq: chess.square_distance(last.move.to_square, sq))
                    effect = 14 - chess.square_distance(last.move.to_square, target), (f"{_piece(first.actor)}沿{path}逐步靠近{chess.square_name(target)}兵；"
                                  "双方已无后和车，王的任务转为亲自参加兵的攻防")
                    text = effect[1]
                else:
                    continue
            elif effect:
                text = f"同一枚{_piece(first.actor)}沿{path}调动；{effect[1]}"
            else:
                continue
            score = effect[0] + 2
            if not _supported(text, allowed):
                continue
            if best is None or score > best[0]:
                best = score, text, index
    return best


def _pawn_clearance(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    if len(steps) < 3:
        return None
    first, reply = steps[:2]
    if (first.actor != chess.Piece(chess.PAWN, color)
            or reply.actor != chess.Piece(chess.PAWN, not color)
            or not reply.fact.capture or reply.move.to_square != first.move.to_square):
        return None
    blocker = reply.move.from_square
    pawn_square = blocker - (8 if color else -8)
    pawn = first.before.piece_at(pawn_square) if 0 <= pawn_square < 64 else None
    if pawn != chess.Piece(chess.PAWN, color):
        return None
    for index, step in enumerate(steps[2:], 2):
        if step.identity == pawn_square and step.move.to_square == blocker:
            return 18, (
                f"{first.fact.san}引来{reply.fact.san}，原在{chess.square_name(blocker)}的敌兵转到"
                f"{chess.square_name(reply.move.to_square)}，让开了{chess.square_name(pawn_square)}兵前方的格子；"
                f"{step.fact.san}便利用了这次让路，推进本方的{chess.square_name(pawn_square)[0]}兵"
            ), index
    return None


def _promotion_trade(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    for index, step in enumerate(steps[:-2]):
        if step.actor.color != color or step.move.promotion != chess.QUEEN:
            continue
        reply, recapture = steps[index + 1:index + 3]
        square = step.move.to_square
        if (reply.fact.capture and reply.move.to_square == square
                and reply.actor == chess.Piece(chess.QUEEN, not color)
                and recapture.fact.capture and recapture.move.to_square == square
                and recapture.actor.color == color):
            surviving_queens = list(recapture.after.pieces(chess.QUEEN, color))
            if (len(surviving_queens) == 1 and not recapture.after.pieces(chess.QUEEN, not color)
                    and step.before.piece_at(surviving_queens[0]) == chess.Piece(chess.QUEEN, color)):
                return 20, (
                    f"兵在{chess.square_name(square)}升变后引来{reply.fact.san}，"
                    f"{_piece(recapture.actor)}再以{recapture.fact.san}回吃黑后"
                    if color else
                    f"兵在{chess.square_name(square)}升变后引来{reply.fact.san}，"
                    f"{_piece(recapture.actor)}再以{recapture.fact.san}回吃白后"
                ) + (f"；本方原有的{chess.square_name(surviving_queens[0])}后仍在棋盘上，"
                     "升变兵由此换掉了对手的后"), index + 2
    return None


def _rook_support(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    for index, step in enumerate(steps):
        if step.actor != chess.Piece(chess.ROOK, color):
            continue
        for sq in step.after.attacks(step.move.to_square):
            if (step.after.piece_at(sq) != chess.Piece(chess.PAWN, color)
                    or chess.square_file(sq) not in {0, 1, 5, 6, 7}
                    or sq in step.before.attacks(step.move.from_square)):
                continue
            pawn_identity = next((prior.identity for prior in reversed(steps[:index])
                                  if prior.move.to_square == sq and prior.actor == chess.Piece(chess.PAWN, color)), sq)
            for later_index in range(index + 1, len(steps)):
                later = steps[later_index]
                if later.identity == pawn_identity and later.actor == chess.Piece(chess.PAWN, color):
                    rook_square = step.move.to_square
                    if (any(intermediate.move.from_square == rook_square or intermediate.move.to_square == rook_square
                            for intermediate in steps[index + 1:later_index + 1])
                            or later.after.piece_at(rook_square) != step.actor
                            or later.move.to_square not in later.after.attacks(rook_square)):
                        break
                    return 13, (
                        f"{step.fact.san}让{_piece(step.actor)}在{chess.square_name(step.move.to_square)}支援"
                        f"{chess.square_name(sq)}兵，随后{later.fact.san}再推进这枚兵；"
                        "先调车支援、再推兵，是这条变化中子力与兵的配合次序"
                    ), later_index
    return None


def _chase_exchange(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    if len(steps) < 3:
        return None
    first, reply, recapture = steps[:3]
    chased = first.after.piece_at(reply.move.from_square)
    victim = reply.before.piece_at(reply.move.to_square)
    if (chased is None or victim is None or chased.color == color
            or chased.piece_type != victim.piece_type
            or reply.move.from_square not in first.after.attacks(first.move.to_square)
            or reply.move.from_square in first.before.attacks(first.move.from_square)
            or not reply.fact.capture or not recapture.fact.capture
            or recapture.move.to_square != reply.move.to_square):
        return None
    return 18, (f"受到攻击的{_piece(chased)}先以{reply.fact.san}换掉"
                f"{chess.square_name(reply.move.to_square)}的{_piece(victim)}，"
                f"{recapture.fact.san}回吃后双方各少一枚{NAMES[chased.piece_type]}；"
                "受到攻击的一方通过等价交换处理了这次攻击"), 2


def _checking_reply(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    checks = [(i, step) for i, step in enumerate(steps[1:], 1)
              if step.actor.color != color and step.fact.check]
    if len(checks) < 2:
        return None
    first_index, first = checks[0]
    last_index, last = checks[1]
    if last_index != first_index + 2 or last_index + 1 >= len(steps):
        return None
    responses = [steps[first_index + 1], steps[last_index + 1]]
    if not all(item.actor == chess.Piece(chess.KING, color) for item in responses):
        return None
    return 13, (f"{'黑方' if color else '白方'}以{first.fact.san}、{last.fact.san}连续将军，"
                f"{_piece(responses[0].actor)}先后用{responses[0].fact.san}、{responses[1].fact.san}避将；"
                "对王的追击让防守方连续花步数应将"), last_index + 1


def _pawn_chases(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    if len(steps) < 4:
        return None
    first, response, followup, escape = steps[:4]
    if first.actor != chess.Piece(chess.PAWN, color) or followup.actor != first.actor:
        return None
    for attack, answer in ((first, response), (followup, escape)):
        if (answer.actor.color == color or answer.actor.piece_type in {chess.PAWN, chess.KING}
                or answer.move.from_square not in attack.after.attacks(attack.move.to_square)
                or answer.move.to_square in attack.after.attacks(attack.move.to_square)):
            return None
    retained = [chess.square_name(sq) for sq in (chess.D4, chess.E4, chess.D5, chess.E5)
                if first.before.piece_at(sq) == chess.Piece(chess.PAWN, not color)
                and escape.after.piece_at(sq) == chess.Piece(chess.PAWN, not color)]
    text = (f"{first.fact.san}攻击{chess.square_name(response.move.from_square)}的{_piece(response.actor)}，"
            f"{response.fact.san}退开后，{followup.fact.san}又攻击{chess.square_name(escape.move.from_square)}的{_piece(escape.actor)}，"
            f"后者以{escape.fact.san}移开；两次推兵都伴随着对方子力的退让")
    if len(retained) >= 2:
        text += f"，但对方{'、'.join(retained)}的中心兵仍然保留"
    return 18, text, 3


def _continuation(steps: list[Step], color: chess.Color, allowed: set[str]) -> tuple[str, int] | None:
    journey = _journey(steps, color, allowed)
    candidates: list[tuple[int, str, int]] = []
    if journey:
        candidates.append(journey)
    for special in (_pawn_clearance(steps, color), _promotion_trade(steps, color),
                    _rook_support(steps, color), _chase_exchange(steps, color),
                    _checking_reply(steps, color), _pawn_chases(steps, color),
                    _bishop_development(steps, color), _central_pawn_pair(steps, color),
                    _passer_decoy(steps, color), _slider_clearance(steps, color)):
        if special and _supported(special[1], allowed):
            candidates.append(special)
    for index, step in enumerate(steps[1:], 1):
        effect = _lasting_effect(steps, index)
        if effect and _supported(effect[1], allowed):
            score, text = effect
            # Prefer structural consequences and the mover's organization over
            # an unrelated late capture. All quoted intervening moves remain visible.
            if step.actor.color != color:
                score -= 2
            candidates.append((score, text, index))
    if not candidates:
        return None
    _, explanation, index = max(candidates, key=lambda item: (item[0] - item[2] * 0.3, -item[2]))
    # Include the answering recapture if the explanation mentions it.
    if "随即回吃" in explanation and index + 1 < len(steps):
        index += 1
    # Preserve a forcing fork's continuation through the same piece's capture.
    selected = steps[index]
    if "同时攻击" in explanation and index + 2 < len(steps):
        reply, capture = steps[index + 1:index + 3]
        if capture.identity == selected.identity and capture.fact.capture:
            explanation += f"；{reply.fact.san}后，这枚{NAMES[capture.actor.piece_type]}以{capture.fact.san}兑现吃子"
            index += 2
    losses = []
    original = steps[0].before
    for prior in steps[1:index + 1]:
        if prior.actor.color == color or not prior.fact.capture:
            continue
        sq = prior.move.to_square
        if (original.piece_at(sq) == chess.Piece(chess.PAWN, color)
                and prior.before.piece_at(sq) == chess.Piece(chess.PAWN, color)
                and _passed(original, sq, color)
                and (chess.square_rank(sq) if color else 7 - chess.square_rank(sq)) >= 4):
            losses.append(f"其中{prior.fact.san}吃掉了原在{chess.square_name(sq)}的通路兵")
    if losses:
        explanation = "；".join(losses) + "；" + explanation
    return f"例如{_notation(steps[1:index + 1])}，{explanation}。", index


def _bishop_development(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    """A pawn vacates an occupied diagonal; the same home bishop then uses it."""
    for index, step in enumerate(steps):
        if step.actor != chess.Piece(chess.PAWN, color) or step.fact.capture:
            continue
        for bishop_square in step.before.pieces(chess.BISHOP, color):
            if (chess.square_rank(bishop_square) != (0 if color else 7)
                    or step.move.from_square not in step.before.attacks(bishop_square)):
                continue
            for later_index in range(index + 1, len(steps)):
                later = steps[later_index]
                if later.move.from_square != bishop_square:
                    continue
                if (later.actor != chess.Piece(chess.BISHOP, color)
                        or later.move.to_square != step.move.from_square
                        or later.fact.capture
                        or any(intermediate.move.from_square == bishop_square
                               or intermediate.move.to_square == bishop_square
                               for intermediate in steps[index + 1:later_index])):
                    break
                opened = later.after.attacks(later.move.to_square) & later.after.pin(color, later.move.to_square)
                if len(opened) < 3:
                    break
                return 13, (
                    f"{step.fact.san}让开{chess.square_name(step.move.from_square)}，"
                    f"{later.fact.san}随后让{chess.square_name(bishop_square)}的{_piece(later.actor)}从底线出动；"
                    "推兵与出象相配合，使原先被本方兵挡住的象有了活动通道"
                ), later_index
    return None


def _central_pawn_pair(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    pushes = [(i, step) for i, step in enumerate(steps)
              if step.actor == chess.Piece(chess.PAWN, color) and not step.fact.capture
              and chess.square_file(step.move.to_square) in {3, 4}]
    for offset, (index, later) in enumerate(pushes[1:], 1):
        for first_index, first in pushes[:offset]:
            support = first.move.to_square
            target = later.move.to_square
            if (first.identity == later.identity
                    or any(step.move.from_square == support or step.move.to_square == support
                           for step in steps[first_index + 1:index + 1])
                    or later.after.piece_at(support) != first.actor
                    or target not in (later.after.attacks(support) & later.after.pin(color, support))):
                continue
            attacked = [sq for sq in later.after.attacks(target) & later.after.pin(color, target)
                        if later.after.piece_at(sq) is not None and later.after.color_at(sq) != color]
            if not attacked:
                continue
            labels = "、".join(chess.square_name(sq) for sq in attacked)
            return 16, (f"{first.fact.san}与{later.fact.san}相继推进中心兵，"
                        f"{chess.square_name(support)}兵支援{chess.square_name(target)}兵，"
                        f"后者接触对方{labels}的子力；中心推进有另一枚兵作后盾，"
                        "对手需要决定维持兵的接触还是通过交换改变中心"), index
    return None


def _passer_decoy(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    first = steps[0]
    if first.actor != chess.Piece(chess.PAWN, color):
        return None
    offered = first.move.to_square
    for index in range(1, len(steps) - 1):
        capture, advance = steps[index:index + 2]
        if (capture.actor != chess.Piece(chess.KING, not color)
                or not capture.fact.capture or capture.move.to_square != offered
                or any(step.move.from_square == offered or step.move.to_square == offered
                       for step in steps[1:index])
                or advance.actor != chess.Piece(chess.PAWN, color) or advance.fact.capture
                or not _passed(advance.before, advance.move.from_square, color)):
            continue
        pawn = advance.move.from_square
        if (first.after.piece_at(pawn) != advance.actor
                or not _passed(first.after, pawn, color)
                or any(step.move.from_square == pawn or step.move.to_square == pawn
                       for step in steps[1:index + 1])
                or pawn not in first.after.attacks(offered)
                or chess.square_distance(capture.move.to_square, advance.move.to_square)
                <= chess.square_distance(capture.move.from_square, pawn)):
            continue
        return 18, (f"{first.fact.san}先用兵支援{chess.square_name(pawn)}通路兵；"
                    f"{capture.fact.san}吃掉这枚支援兵后，{advance.fact.san}继续推进通路兵，"
                    f"对方王落在{chess.square_name(offered)}，与推进后的{chess.square_name(advance.move.to_square)}兵拉开了距离"), index + 1
    return None


def _slider_clearance(steps: list[Step], color: chess.Color) -> tuple[int, str, int] | None:
    """Explain an actual line-opening move followed by a heavy-piece capture."""
    for index, opening in enumerate(steps[:-1]):
        if opening.actor.color != color or opening.actor.piece_type == chess.KING:
            continue
        vacated = opening.move.from_square
        for later_index in range(index + 1, min(index + 5, len(steps))):
            capture = steps[later_index]
            if (capture.actor.color != color or capture.actor.piece_type not in {chess.ROOK, chess.BISHOP, chess.QUEEN}
                    or not capture.fact.capture):
                continue
            source, target = capture.move.from_square, capture.move.to_square
            victim = opening.before.piece_at(target)
            if (opening.before.piece_at(source) != capture.actor or victim is None
                    or victim.color == color or victim.piece_type not in {chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN}
                    or capture.before.piece_at(target) != victim
                    or vacated not in chess.SquareSet(chess.between(source, target))
                    or target in opening.before.attacks(source)
                    or target not in (opening.after.attacks(source) & opening.after.pin(color, source))
                    or any(step.move.from_square in {source, target} or step.move.to_square in {source, target}
                           for step in steps[index + 1:later_index])):
                continue
            return 20, (f"{opening.fact.san}让开{chess.square_name(vacated)}，打开"
                        f"{chess.square_name(source)}的{_piece(capture.actor)}通向{chess.square_name(target)}"
                        f"{NAMES[victim.piece_type]}的线路；随后{capture.fact.san}沿这条线路吃掉该子，"
                        "前一枚棋子的移开与后一枚棋子的进攻形成配合"), later_index
    return None


def _same_capture_choice(actual: Step, best: Step, allowed: set[str]) -> str | None:
    if (not actual.fact.capture or not best.fact.capture
            or actual.move.to_square != best.move.to_square
            or actual.actor.piece_type == best.actor.piece_type):
        return None
    target = actual.move.to_square
    victim = actual.before.piece_at(target)
    if victim is None:
        return None
    extra = actual.after.attacks(target) - best.after.attacks(target)
    targets = [(sq, actual.after.piece_at(sq)) for sq in sorted(extra)
               if actual.after.piece_at(sq) is not None
               and actual.after.piece_at(sq).color != actual.actor.color
               and actual.after.piece_at(sq).piece_type != chess.KING]
    if not targets:
        return None
    sq, attacked = targets[0]
    text = (f"{actual.fact.san}与{best.fact.san}都能吃掉{chess.square_name(target)}的{_piece(victim)}。"
            f"实战由{_piece(actual.actor)}占据{chess.square_name(target)}，直接攻击{chess.square_name(sq)}的{_piece(attacked)}；"
            f"首选{best.fact.san}则让{_piece(best.actor)}占据这个格子")
    original = actual.move.from_square
    if (best.after.piece_at(original) == actual.actor
            and target in best.after.attacks(original)):
        text += f"，{_piece(actual.actor)}留在{chess.square_name(original)}支援它"
    return text + "。" if _supported(text, allowed) else None


def _shared_pawn_chase_choice(actual: list[Step], best: list[Step], allowed: set[str]) -> str | None:
    """Compare placements of the same knight against the same pawn/bishop reply."""
    if len(actual) < 3 or len(best) < 3:
        return None
    played, push, retreat = actual[:3]
    alternative, other_push, other_retreat = best[:3]
    if (played.actor.piece_type != chess.KNIGHT or played.actor != alternative.actor
            or played.move.from_square != alternative.move.from_square
            or push.move != other_push.move or retreat.move != other_retreat.move
            or push.actor.piece_type != chess.PAWN or push.fact.capture
            or retreat.actor != chess.Piece(chess.BISHOP, played.actor.color)
            or retreat.move.from_square not in push.after.attacks(push.move.to_square)
            or retreat.move.to_square in push.after.attacks(push.move.to_square)):
        return None
    actual_control = played.after.attacks(played.move.to_square) & played.after.pin(played.actor.color, played.move.to_square)
    best_control = alternative.after.attacks(alternative.move.to_square) & alternative.after.pin(alternative.actor.color, alternative.move.to_square)
    if not {retreat.move.from_square, push.move.to_square} <= set(best_control - actual_control):
        return None
    effect = _effect(played)
    if not effect:
        return None
    enemy = "黑方" if played.actor.color else "白方"
    text = (f"{played.fact.san}后，{effect[1]}；首选{alternative.fact.san}则让这匹马保护"
            f"{chess.square_name(retreat.move.from_square)}象，同时控制{chess.square_name(push.move.to_square)}。"
            f"两种走法后都可出现{_notation([push, retreat])}：{enemy}用兵赶象，"
            f"{_piece(retreat.actor)}退到{chess.square_name(retreat.move.to_square)}。")
    return text if _supported(text, allowed) else None


def build_book_mechanism(move: MoveReview) -> BookMechanism | None:
    """Return a concrete continuation plus a contrasting alternative if available."""
    line = move.actual_move_line
    if line is None or not line.verified or not line.moves:
        return None
    before = chess.Board(move.before_fen)
    actual = _replay(before, [move.played_move, *line.moves])
    if (actual is None or actual[0].after.fen() != move.after_fen
            or actual[-1].after.fen() != line.resulting_fen
            or line.first_move.uci != line.moves[0].uci):
        return None
    color = actual[0].actor.color
    if move.side != ("white" if color else "black"):
        return None
    allowed = set(move.allowed_squares)
    # Admit empty control squares only with an explicit, recomputed fact for
    # the played pawn. A fabricated/stale fact must not expand the vocabulary.
    control_refs: list[tuple[str, set[str]]] = []
    if actual[0].actor.piece_type == chess.PAWN and move.position_facts_after:
        square = actual[0].move.to_square
        controlled = {chess.square_name(sq) for sq in
                      actual[0].after.attacks(square) & actual[0].after.pin(color, square)}
        expected = {chess.square_name(square), *controlled}
        for fact in move.position_facts_after.pawn_structure:
            if (fact.category == "pawn_control" and fact.id and fact.side == move.side
                    and set(fact.squares) == expected):
                allowed.update(controlled)
                control_refs.append((fact.id, controlled))
    continuation = _continuation(actual, color, allowed)
    if continuation is None:
        return None
    immediate = _lasting_effect(actual, 0)
    if immediate and not _supported(immediate[1], allowed):
        immediate = None
    introduction = (
        (f"{immediate[1]}。" if move.played_move.san in immediate[1] else f"{move.played_move.san}后，{immediate[1]}。")
        if immediate else
        f"实战{move.played_move.san}把{_piece(actual[0].actor)}从{move.played_move.from_square}转到{move.played_move.to_square}。"
    )
    text = introduction + continuation[0]
    refs = [move.played_move.id or f"move:played:{move.index}", line.id,
            *(step.fact.id for step in actual[1:continuation[1] + 1])]
    refs.extend(ref for ref, squares in control_refs
                if squares & set(re.findall(r"[a-h][1-8]", text)))
    best_line = next((candidate for candidate in move.candidate_lines
                      if candidate.rank == 1 and candidate.first_move.uci == move.best_move_uci), None)
    if best_line and best_line.verified and move.best_move_uci != move.played_move.uci:
        best = _replay(before, list(best_line.moves))
        if (best and best[-1].after.fen() == best_line.resulting_fen
                and best[0].move.uci() == move.best_move_uci):
            shared_chase = _shared_pawn_chase_choice(actual, best, allowed)
            if shared_chase:
                return BookMechanism(shared_chase, list(dict.fromkeys([
                    move.played_move.id or f"move:played:{move.index}", line.id, best_line.id,
                    *(step.fact.id for step in actual[1:3]), *(step.fact.id for step in best[:3]),
                ])))
            alternative = _continuation(best, color, allowed)
            capture_comparison = _same_capture_choice(actual[0], best[0], allowed)
            if capture_comparison:
                text = capture_comparison + continuation[0]
                refs.extend([best_line.id, best[0].fact.id])
                if alternative:
                    text += f"若选{best[0].fact.san}，" + alternative[0]
                    refs.extend(step.fact.id for step in best[:alternative[1] + 1])
                return BookMechanism(text, list(dict.fromkeys(ref for ref in refs if ref)))
            best_effect = _lasting_effect(best, 0)
            if best_effect and not _supported(best_effect[1], allowed):
                best_effect = None
            if best_effect and best_effect[0] >= 6 and best[0].actor.piece_type == chess.PAWN:
                if alternative and _passer_decoy(best, color):
                    text += f"首选{best[0].fact.san}的后续可以这样展开：" + alternative[0]
                    refs.extend([best_line.id, *(step.fact.id for step in best[:alternative[1] + 1])])
                else:
                    text += f"首选{best[0].fact.san}后，{best_effect[1]}。"
                    refs.extend([best_line.id, *(step.fact.id for step in best[:2])])
            elif alternative:
                text += (f"首选{best[0].fact.san}后，{best_effect[1]}。" if best_effect
                         else f"首选{best[0].fact.san}的后续可以这样展开：") + alternative[0]
                refs.extend([best_line.id, *(step.fact.id for step in best[:max(2, alternative[1] + 1)])])
            elif best_effect:
                text += f"首选{best[0].fact.san}则先让{best_effect[1]}。"
                refs.extend([best_line.id, *(step.fact.id for step in best[:2])])
    return BookMechanism(text, list(dict.fromkeys(ref for ref in refs if ref)))
