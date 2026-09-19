from __future__ import annotations

from dataclasses import dataclass

import chess
from pydantic import BaseModel, ConfigDict, Field

from .models import MoveReview


PIECE_NAMES = {
    chess.PAWN: "兵",
    chess.KNIGHT: "马",
    chess.BISHOP: "象",
    chess.ROOK: "车",
    chess.QUEEN: "后",
    chess.KING: "王",
}
PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,
}


class MoveEffect(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    move: str
    verified_effects: list[str] = Field(alias="verifiedEffects", default_factory=list)
    resolves_direct_threat: bool = Field(alias="resolvesDirectThreat", default=False)
    captures_piece: bool = Field(alias="capturesPiece", default=False)
    gives_check: bool = Field(alias="givesCheck", default=False)
    loses_piece: bool = Field(alias="losesPiece", default=False)
    protects_key_piece: bool = Field(alias="protectsKeyPiece", default=False)
    creates_direct_threat: bool = Field(alias="createsDirectThreat", default=False)
    changes_attack_defense: bool = Field(alias="changesAttackDefense", default=False)


class PlayedBestComparison(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    played: MoveEffect
    best: MoveEffect
    main_difference: str = Field(alias="mainDifference")
    evidence_source: str = Field(alias="evidenceSource", default="python-chess + Stockfish PV")


@dataclass(frozen=True)
class _BoardSignals:
    endangered: frozenset[int]
    attacked_enemy: frozenset[int]


def _piece_label(board: chess.Board, square: int) -> str:
    piece = board.piece_at(square)
    return f"{chess.square_name(square)}的{PIECE_NAMES.get(piece.piece_type, '棋子')}" if piece else chess.square_name(square)


def _signals(board: chess.Board, side: chess.Color) -> _BoardSignals:
    endangered: set[int] = set()
    attacked_enemy: set[int] = set()
    for square, piece in board.piece_map().items():
        if piece.color == side:
            if (
                PIECE_VALUES[piece.piece_type] < 100
                and board.is_attacked_by(not side, square)
                and not board.is_attacked_by(side, square)
            ):
                endangered.add(square)
        elif PIECE_VALUES[piece.piece_type] < 100 and board.is_attacked_by(side, square):
            attacked_enemy.add(square)
    return _BoardSignals(frozenset(endangered), frozenset(attacked_enemy))


def _effect(before: chess.Board, move: chess.Move, san: str, reply_uci: str | None) -> MoveEffect:
    side = before.turn
    before_signals = _signals(before, side)
    capture = before.is_capture(move)
    captured_piece = before.piece_at(move.to_square)
    if before.is_en_passant(move):
        captured_piece = chess.Piece(chess.PAWN, not side)
    gives_check = before.gives_check(move)
    after = before.copy(stack=False)
    after.push(move)
    after_signals = _signals(after, side)
    resolved = before_signals.endangered - after_signals.endangered
    protected = []
    for square in resolved:
        piece = before.piece_at(square)
        after_piece = after.piece_at(square)
        if (
            piece
            and after_piece == piece
            and PIECE_VALUES[piece.piece_type] >= 3
            and after.is_attacked_by(not side, square)
            and after.is_attacked_by(side, square)
        ):
            protected.append(square)
    new_targets = after_signals.attacked_enemy - before_signals.attacked_enemy
    valuable_targets = sorted(
        new_targets,
        key=lambda square: PIECE_VALUES[after.piece_at(square).piece_type] if after.piece_at(square) else 0,
        reverse=True,
    )
    reply_capture = False
    reply_target = None
    if reply_uci:
        try:
            reply = chess.Move.from_uci(reply_uci)
            if reply in after.legal_moves and after.is_capture(reply):
                reply_capture = True
                reply_target = after.piece_at(reply.to_square)
                if after.is_en_passant(reply):
                    reply_target = chess.Piece(chess.PAWN, side)
        except ValueError:
            pass
    effects: list[str] = []
    if capture and captured_piece:
        effects.append(f"吃掉{PIECE_NAMES[captured_piece.piece_type]}")
    if gives_check:
        effects.append("形成将军")
    if resolved:
        effects.append(f"使{_piece_label(before, sorted(resolved)[0])}脱离未受保护的直接受攻状态")
    if protected:
        effects.append(f"为{_piece_label(before, protected[0])}补上保护")
    if valuable_targets:
        effects.append(f"新攻击{_piece_label(after, valuable_targets[0])}")
    if reply_capture and reply_target:
        effects.append(f"Stockfish验证的直接回应可以吃掉{PIECE_NAMES[reply_target.piece_type]}")
    if not effects:
        effects.append("没有检测到可程序确认的立即吃子、将军或直接受攻变化")
    return MoveEffect(
        move=san,
        verifiedEffects=effects,
        resolvesDirectThreat=bool(resolved),
        capturesPiece=capture,
        givesCheck=gives_check,
        losesPiece=bool(reply_capture and reply_target and PIECE_VALUES[reply_target.piece_type] >= 3),
        protectsKeyPiece=bool(protected),
        createsDirectThreat=bool(valuable_targets or gives_check),
        changesAttackDefense=bool(resolved or new_targets),
    )


def build_played_best_comparison(review: MoveReview) -> PlayedBestComparison | None:
    if not review.best_move_uci or not review.best_move_san:
        return None
    board = chess.Board(review.before_fen)
    try:
        played_move = chess.Move.from_uci(review.played_move.uci)
        best_move = chess.Move.from_uci(review.best_move_uci)
    except ValueError:
        return None
    if played_move not in board.legal_moves or best_move not in board.legal_moves:
        return None
    played_reply = review.actual_move_line.moves[0].uci if review.actual_move_line and review.actual_move_line.moves else None
    best_reply = review.candidate_lines[0].moves[1].uci if review.candidate_lines and len(review.candidate_lines[0].moves) > 1 else None
    played = _effect(board, played_move, review.played_move.san, played_reply)
    best = _effect(board, best_move, review.best_move_san, best_reply)
    if review.played_move.uci == review.best_move_uci:
        difference = "实战着就是Stockfish首选，两者不存在需要虚构的功能差异。"
    elif best.resolves_direct_threat and not played.resolves_direct_threat:
        difference = "首选着先处理了程序确认的直接受攻关系，实战着没有做到这一点。"
    elif played.loses_piece and not best.loses_piece:
        difference = "实战着允许Stockfish验证的直接吃子，首选着避免了这项立即损失。"
    elif best.captures_piece and not played.captures_piece:
        difference = "首选着立即兑现了可确认的吃子机会，实战着错过了这项机会。"
    elif best.gives_check and not played.gives_check:
        difference = "首选着形成程序确认的将军，实战着没有同等强制性。"
    elif best.creates_direct_threat and not played.creates_direct_threat:
        difference = "首选着制造了可程序确认的直接威胁，实战着没有形成同类立即作用。"
    elif played.verified_effects == best.verified_effects:
        difference = "程序未确认两着在立即战术功能上存在差异；评价差来自更深路线，不能凭空补写原因。"
    else:
        difference = "两着改变的立即攻防关系不同，具体差异以上述程序验证结果为准。"
    return PlayedBestComparison(played=played, best=best, mainDifference=difference)
