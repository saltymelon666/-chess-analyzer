from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

from .models import MoveReview, ProfessionalAnalysis, VariationMove
from .strategic_plans import StrategicPlanPackage
from .threat_analysis import ThreatPackage


NARRATIVE_CLAIM_VERSION = "1.3"
LEGACY_NARRATIVE_MARKERS = (
    "先看全局：",
    "实战把选择摆上棋盘：",
    "再看关键选择：",
    "从计划看，已经得到验证的方向是：",
    "这段变化留给初学者的原则是：",
)
NarrativeClaimKind = Literal[
    "position_fact",
    "position_cause",
    "move_event",
    "move_effect",
    "evaluation_comparison",
    "opponent_resource",
    "verified_plan",
]
NarrativeClaimScope = Literal[
    "before_move", "after_played_move", "candidate_route",
]


class VerifiedNarrativeClaim(BaseModel):
    """One program-verifiable idea that prose may express."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    claim_id: str = Field(alias="claimId", min_length=1)
    kind: NarrativeClaimKind
    scope: NarrativeClaimScope
    statement: str = Field(min_length=1)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)
    confidence: Literal["high", "medium"] = "high"
    source: Literal[
        "python-chess",
        "stockfish",
        "python-chess+stockfish",
        "verified-plan",
        "program-rule",
    ]


class NarrativeClaimPackage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    version: Literal["1.3"] = NARRATIVE_CLAIM_VERSION
    claims: list[VerifiedNarrativeClaim] = Field(default_factory=list)
    recommended_claim_refs: list[str] = Field(alias="recommendedClaimRefs", default_factory=list)
    boundary: str = (
        "核心正文只能重述这些命题。只有程序在同一条合法Stockfish路线中确认牵制持续到"
        "对应棋子被吃，才允许把两者写成战术链；其他同时出现的事件不能自动写成因果关系。"
        "没有独立命题支持时，禁止使用造成、使得、支撑、限制、削弱、打开、迫使等因果表述。"
        "正文直接解释局面，不显示分析步骤、校验过程、固定标题或教学检查清单。"
    )

    @property
    def claim_ids(self) -> set[str]:
        return {item.claim_id for item in self.claims}

    def prompt_payload(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


class NarrativeClaimGroundingMetrics(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    declared_count: int = Field(alias="declaredCount", ge=0)
    valid_ref_count: int = Field(alias="validRefCount", ge=0)
    entailed_count: int = Field(alias="entailedCount", ge=0)
    declared_statement_coverage: float = Field(
        alias="declaredStatementCoverage", ge=0, le=1,
    )
    grounding_precision: float = Field(alias="groundingPrecision", ge=0, le=1)
    exact_render_match: bool = Field(alias="exactRenderMatch")
    invalid_claim_refs: list[str] = Field(alias="invalidClaimRefs", default_factory=list)
    missing_statements: list[str] = Field(alias="missingStatements", default_factory=list)
    unexpected_text: str = Field(alias="unexpectedText", default="")


def build_narrative_claim_package(
    move: MoveReview,
    *,
    priority_evidence_ids: Sequence[str] = (),
    threat_package: ThreatPackage | None = None,
    plan_package: StrategicPlanPackage | None = None,
) -> NarrativeClaimPackage:
    """Build a conservative, deterministic claim menu for the core paragraph."""
    claims: list[VerifiedNarrativeClaim] = []
    recommended: list[str] = []
    played_ref = move.played_move.id or f"move:played:{move.index}"
    side_text = _side_text(move.side)
    piece_text = _piece_text(move.played_move.piece, move.side)

    def add(
        suffix: str,
        kind: NarrativeClaimKind,
        scope: NarrativeClaimScope,
        statement: str,
        evidence_refs: Iterable[str],
        source: str,
        *,
        recommend: bool = True,
        confidence: str = "high",
    ) -> None:
        claim_id = f"claim:{move.index}:{suffix}"
        claims.append(VerifiedNarrativeClaim(
            claimId=claim_id,
            kind=kind,
            scope=scope,
            statement=statement,
            evidenceRefs=list(dict.fromkeys(evidence_refs)),
            source=source,
            confidence=confidence,
        ))
        if recommend:
            recommended.append(claim_id)

    add(
        "position:evaluation",
        "position_fact",
        "before_move",
        _evaluation_posture_statement(move),
        [f"evaluation:before:{move.index}"],
        "stockfish",
    )

    add(
        "played",
        "move_event",
        "after_played_move",
        (
            f"实战中，{side_text}选择{move.played_move.san}：{piece_text}从"
            f"{move.played_move.from_square}来到{move.played_move.to_square}。"
        ),
        [played_ref],
        "python-chess",
    )

    event_parts: list[str] = []
    if move.played_move.capture:
        event_parts.append(f"吃掉{_piece_text(move.played_move.captured_piece or '', _opposite(move.side))}")
    if move.played_move.checkmate:
        event_parts.append("形成将杀")
    elif move.played_move.check:
        event_parts.append("形成将军")
    if move.played_move.castling:
        event_parts.append("完成易位")
    if move.played_move.promotion:
        event_parts.append(f"升变为{_piece_text(move.played_move.promotion, move.side)}")
    if event_parts:
        add(
            "event",
            "move_event",
            "after_played_move",
            f"这一步{'，并'.join(event_parts)}。",
            [played_ref],
            "python-chess",
        )

    before = chess.Board(move.before_fen)
    after = chess.Board(move.after_fen)
    destination = chess.parse_square(move.played_move.to_square)
    origin = chess.parse_square(move.played_move.from_square)
    before_attacks = set(before.attacks(origin))
    after_attacks = set(after.attacks(destination))
    after_piece = after.piece_at(destination)
    effect_piece_name = (
        _piece_name(chess.piece_name(after_piece.piece_type))
        if after_piece is not None
        else _piece_name(move.played_move.piece)
    )
    new_attacks = after_attacks - before_attacks
    allowed_squares = {item.lower() for item in move.allowed_squares}
    controlled_squares = {
        square for square in new_attacks
        if chess.square_name(square).lower() in allowed_squares
    }
    controlled = sorted(chess.square_name(square) for square in controlled_squares)
    if controlled:
        add(
            "new-control",
            "move_effect",
            "after_played_move",
            f"落子后，这枚{effect_piece_name}新增控制{'、'.join(controlled)}。",
            [played_ref],
            "python-chess",
        )

    new_targets = []
    new_defended = []
    mover_color = chess.WHITE if move.side == "white" else chess.BLACK
    for square in controlled_squares:
        target = after.piece_at(square)
        if target is None:
            continue
        label = f"{_side_text('white' if target.color else 'black')}{_piece_name(chess.piece_name(target.piece_type))}（{chess.square_name(square)}）"
        if target.color == mover_color:
            new_defended.append(label)
        else:
            new_targets.append(label)
    if new_targets:
        add(
            "new-target",
            "move_effect",
            "after_played_move",
            f"落子后，这枚{effect_piece_name}新增攻击{'、'.join(sorted(new_targets))}。",
            [played_ref],
            "python-chess",
        )
    if new_defended:
        add(
            "new-defense",
            "move_effect",
            "after_played_move",
            f"落子后，这枚{effect_piece_name}新增保护{'、'.join(sorted(new_defended))}。",
            [played_ref],
            "python-chess",
        )

    before_count = len(before_attacks)
    after_count = len(after_attacks)
    if before_count != after_count:
        direction = "增加" if after_count > before_count else "减少"
        add(
            "control-count",
            "move_effect",
            "after_played_move",
            (
                f"升变或落子后，这枚{effect_piece_name}直接控制的格子由"
                f"{before_count}个{direction}到{after_count}个。"
                if move.played_move.promotion
                else f"这枚{effect_piece_name}直接控制的格子由{before_count}个{direction}到{after_count}个。"
            ),
            [played_ref],
            "python-chess",
            recommend=not controlled,
        )

    if move.best_move_uci:
        if move.played_move.uci == move.best_move_uci:
            comparison = (
                f"引擎的首选与实战一致：{move.played_move.san}并非退而求其次，"
                "它就是当前第一选择。"
            )
        elif move.centipawn_loss is None:
            comparison = ""
        elif move.centipawn_loss < 50:
            comparison = _small_gap_statement(move)
        else:
            comparison = (
                f"分歧从这里出现：与首选{move.best_move_san or move.best_move_uci}相比，"
                f"{move.played_move.san}使评价下降约{move.centipawn_loss / 100:.2f}兵。"
            )
        if comparison:
            add(
                "comparison",
                "evaluation_comparison",
                "after_played_move",
                comparison,
                [played_ref, f"evaluation:before:{move.index}", f"evaluation:after:{move.index}"],
                "stockfish",
                recommend=move.played_move.uci != move.best_move_uci,
            )

    tactical_cause = _pin_then_capture_claim(move)
    if tactical_cause is not None:
        statement, evidence_refs = tactical_cause
        add(
            "position:pin-capture",
            "position_cause",
            "candidate_route",
            statement,
            evidence_refs,
            "python-chess+stockfish",
            recommend=True,
        )

    if move.actual_move_line and move.actual_move_line.moves:
        reply = move.actual_move_line.moves[0]
        reply_detail = _move_event_detail(reply, captured_side=move.side)
        inferior = (
            move.best_move_uci is not None
            and move.played_move.uci != move.best_move_uci
            and move.centipawn_loss is not None
            and move.centipawn_loss >= 50
        )
        if inferior and move.complexity_factors.direct_piece_loss and reply_detail:
            reply_statement = (
                f"{move.played_move.san}之后，{_side_text(_opposite(move.side))}以"
                f"{reply.san}{reply_detail}，实战着立即付出了子力代价。"
            )
        elif inferior:
            detail = f"，这一步{reply_detail}" if reply_detail else ""
            reply_statement = (
                f"{move.played_move.san}之后，{_side_text(_opposite(move.side))}的首选回应是"
                f"{reply.san}{detail}。"
            )
        else:
            reply_statement = ""
        if reply_statement and tactical_cause is None:
            add(
                "reply",
                "opponent_resource",
                "candidate_route",
                reply_statement,
                [move.actual_move_line.id, reply.id],
                "python-chess+stockfish",
                recommend=inferior,
            )

    for plan in (plan_package.plans if plan_package else []):
        if move.played_move.uci not in plan.supporting_moves and move.played_move.san not in plan.supporting_moves:
            continue
        if not plan.evidence_route_ids:
            continue
        add(
            f"plan:{plan.plan_id}",
            "verified_plan",
            "candidate_route",
            plan.goal.rstrip("。") + "。",
            plan.evidence_route_ids,
            "verified-plan",
            recommend=True,
            confidence=plan.confidence,
        )

    return NarrativeClaimPackage(
        claims=claims,
        recommendedClaimRefs=list(dict.fromkeys(recommended))[:4],
    )


def compose_verified_core_paragraph(
    package: NarrativeClaimPackage,
    selected_claim_refs: Sequence[str] = (),
) -> str:
    """Render only verified statements; selection may change emphasis, never facts."""
    selected = resolve_narrative_claims(package, selected_claim_refs)
    groups = {
        "position": [item.statement for item in selected if item.kind == "position_fact"],
        "move": [
            item.statement
            for item in selected
            if item.kind in {"move_event", "move_effect"}
        ],
        "verdict": [
            item.statement
            for item in selected
            if item.kind in {"evaluation_comparison", "opponent_resource"}
        ],
        "cause": [item.statement for item in selected if item.kind == "position_cause"],
        "plan": [item.statement for item in selected if item.kind == "verified_plan"],
    }
    paragraphs: list[str] = []
    if groups["position"]:
        paragraphs.append("".join(groups["position"]))
    if groups["move"]:
        paragraphs.append("".join(groups["move"]))
    if groups["verdict"]:
        paragraphs.append("".join(groups["verdict"]))
    if groups["cause"]:
        paragraphs.append("".join(groups["cause"]))
    if groups["plan"]:
        paragraphs.append("".join(groups["plan"]))
    return "".join(paragraphs)


def resolve_narrative_claims(
    package: NarrativeClaimPackage,
    selected_claim_refs: Sequence[str] = (),
) -> list[VerifiedNarrativeClaim]:
    lookup = {item.claim_id: item for item in package.claims}
    selected = [lookup[item] for item in selected_claim_refs if item in lookup]
    if not selected:
        selected = [lookup[item] for item in package.recommended_claim_refs if item in lookup]
    position_claim = next(
        (item for item in package.claims if item.kind == "position_fact"), None,
    )
    played_claim = next(
        (item for item in package.claims if item.claim_id.endswith(":played")), None,
    )
    event_claim = next(
        (item for item in package.claims if item.claim_id.endswith(":event")), None,
    )
    comparison_claim = next(
        (item for item in package.claims if item.kind == "evaluation_comparison"), None,
    )
    reply_claim = next(
        (item for item in package.claims if item.kind == "opponent_resource"), None,
    )
    cause_claim = next(
        (item for item in package.claims if item.kind == "position_cause"), None,
    )
    has_non_best_comparison = bool(
        comparison_claim
        and "首选与实战一致" not in comparison_claim.statement
    )
    event_text = event_claim.statement if event_claim else ""
    needs_concrete_reply = bool(
        reply_claim
        and (
            has_non_best_comparison
            or "形成将军" in event_text
            or "形成将杀" in event_text
            or "吃掉" in event_text
        )
    )
    required = [
        item
        for item in (
            position_claim,
            played_claim,
            event_claim,
            comparison_claim,
            cause_claim,
            reply_claim if needs_concrete_reply else None,
        )
        if item is not None
    ]
    selected_ids = {item.claim_id for item in selected}
    for item in required:
        if item.claim_id not in selected_ids:
            selected.append(item)
            selected_ids.add(item.claim_id)
    unique = {item.claim_id: item for item in selected}
    source_order = {item.claim_id: index for index, item in enumerate(package.claims)}
    order = {
        "position_fact": 0,
        "move_event": 1,
        "move_effect": 2,
        "evaluation_comparison": 3,
        "position_cause": 4,
        "opponent_resource": 5,
        "verified_plan": 6,
    }
    items = sorted(
        unique.values(),
        key=lambda item: (order[item.kind], source_order.get(item.claim_id, 999)),
    )
    if len(items) <= 6:
        return items
    required = [item for item in required if item in items]
    optional = [item for item in items if item not in required]
    kept = [*required, *optional[:max(0, 6 - len(required))]]
    return sorted(
        kept[:6],
        key=lambda item: (order[item.kind], source_order.get(item.claim_id, 999)),
    )


def evaluate_narrative_claim_grounding(
    analysis: ProfessionalAnalysis,
    package: NarrativeClaimPackage,
) -> NarrativeClaimGroundingMetrics:
    declared = list(dict.fromkeys(analysis.played_move_analysis.claim_refs))
    lookup = {item.claim_id: item for item in package.claims}
    invalid = [item for item in declared if item not in lookup]
    valid = [lookup[item] for item in declared if item in lookup]
    core_text = analysis.played_move_analysis.intention
    missing = [item.claim_id for item in valid if item.statement not in core_text]
    entailed = len(valid) - len(missing)
    expected_text = compose_verified_core_paragraph(package, declared)
    exact_match = not invalid and core_text == expected_text
    unexpected = "" if exact_match else core_text.replace(expected_text, "", 1).strip()
    declared_coverage = entailed / len(declared) if declared else 0.0
    return NarrativeClaimGroundingMetrics(
        declaredCount=len(declared),
        validRefCount=len(valid),
        entailedCount=entailed,
        declaredStatementCoverage=declared_coverage,
        groundingPrecision=(declared_coverage if exact_match else 0.0),
        exactRenderMatch=exact_match,
        invalidClaimRefs=invalid,
        missingStatements=missing,
        unexpectedText=unexpected,
    )


def _pin_then_capture_claim(move: MoveReview) -> tuple[str, list[str]] | None:
    """Describe a pin-to-capture chain only when one legal route proves every step."""
    line = move.actual_move_line
    if line is None or not line.verified or not line.moves:
        return None
    try:
        board = chess.Board(move.after_fen)
    except ValueError:
        return None

    active_pins: dict[int, tuple[chess.Piece, VariationMove | None, str]] = {
        square: (piece, None, chess.square_name(square))
        for square, piece in board.piece_map().items()
        if piece.piece_type != chess.KING and board.is_pinned(piece.color, square)
    }
    for item in line.moves:
        try:
            chess_move = chess.Move.from_uci(item.uci)
        except ValueError:
            return None
        if chess_move not in board.legal_moves:
            return None

        captured = board.piece_at(chess_move.to_square) if board.is_capture(chess_move) else None
        pin_record = active_pins.get(chess_move.to_square)
        if (
            captured is not None
            and pin_record is not None
            and captured == pin_record[0]
            and captured.piece_type in {chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN}
            and board.is_pinned(captured.color, chess_move.to_square)
            and (pin_record[1] is None or item.side == pin_record[1].side)
        ):
            pinned_piece, pin_move, _ = pin_record
            pinned_side = "white" if pinned_piece.color == chess.WHITE else "black"
            piece_name = _piece_name(chess.piece_name(pinned_piece.piece_type))
            piece_label = _piece_text(chess.piece_name(pinned_piece.piece_type), pinned_side)
            if pinned_piece.piece_type == chess.KNIGHT:
                restraint = f"这匹{piece_name}因此一步也不能走"
            else:
                restraint = f"这枚{piece_name}不能离开护王线路"
            intervening = [
                route_move.san
                for route_move in line.moves
                if (pin_move.ply if pin_move is not None else 0) < route_move.ply < item.ply
            ]
            if len(intervening) == 1:
                bridge = f"{_side_text(pinned_side)}走出{intervening[0]}后，"
            elif intervening:
                bridge = f"经过{'、'.join(intervening)}后，"
            else:
                bridge = ""
            advantage_side = _evaluation_advantage_side(move)
            conclusion = (
                f"这正是{_side_text(advantage_side)}优势的具体落点。"
                if advantage_side == item.side
                else ""
            )
            refs = [
                line.id,
                *[
                    route_move.id
                    for route_move in line.moves
                    if (pin_move.ply if pin_move is not None else 1) <= route_move.ply <= item.ply
                    and route_move.id
                ],
            ]
            pin_text = (
                f"{pin_move.san}把{piece_label}钉在王前"
                if pin_move is not None
                else f"{piece_label}已经被钉在王前"
            )
            return (
                f"{pin_text}，{restraint}；"
                f"{bridge}{item.san}随即吃掉这枚{piece_name}。{conclusion}",
                list(dict.fromkeys(refs)),
            )

        if chess_move.from_square in active_pins:
            active_pins.pop(chess_move.from_square, None)

        before_pinned = {
            square
            for square, piece in board.piece_map().items()
            if piece.color != board.turn
            and piece.piece_type != chess.KING
            and board.is_pinned(piece.color, square)
        }
        board.push(chess_move)
        enemy = board.turn
        for square, piece in board.piece_map().items():
            if (
                piece.color == enemy
                and piece.piece_type != chess.KING
                and board.is_pinned(enemy, square)
                and square not in before_pinned
            ):
                active_pins[square] = (piece, item, chess.square_name(square))

        for square, (piece, _, _) in list(active_pins.items()):
            if board.piece_at(square) != piece:
                active_pins.pop(square, None)

    return None


def _evaluation_advantage_side(move: MoveReview) -> str | None:
    if move.before.mate_in is not None:
        return "white" if move.before.mate_in > 0 else "black"
    if move.before.centipawn is None or abs(move.before.centipawn) <= 100:
        return None
    return "white" if move.before.centipawn > 0 else "black"


def _small_gap_statement(move: MoveReview) -> str:
    played = move.played_move.san
    best = move.best_move_san or move.best_move_uci or "首选着"
    prefix = f"{played}与首选{best}的评价接近；"
    centipawn = move.before.centipawn
    if centipawn is None or abs(centipawn) <= 25:
        return prefix + "这步棋没有显著打破原有的平衡。"
    advantage_side = "white" if centipawn > 0 else "black"
    if advantage_side == move.side:
        return prefix + f"{_side_text(move.side)}原有的优势在落子前已经形成，这步棋没有显著改变优势格局。"
    return prefix + f"{_side_text(move.side)}的困难在落子前已经存在，这步棋既没有制造危机，也没有解除危机。"


def _evaluation_posture_statement(move: MoveReview) -> str:
    """Turn the pre-move engine score into a number-free global posture."""
    mate_in = move.before.mate_in
    if mate_in is not None:
        side = "白方" if mate_in > 0 else "黑方"
        return f"轮到{_side_text(move.side)}落子时，引擎已经确认{side}存在强制将杀。"
    centipawn = move.before.centipawn
    if centipawn is None:
        return f"轮到{_side_text(move.side)}落子时，引擎没有提供足以判断优势归属的可靠评价。"
    if abs(centipawn) <= 25:
        return f"轮到{_side_text(move.side)}落子时，局面在引擎眼中大致均衡，双方都没有决定性优势。"
    side = "白方" if centipawn > 0 else "黑方"
    if abs(centipawn) <= 100:
        return f"轮到{_side_text(move.side)}落子时，引擎只给{side}轻微优势，局面远未失去弹性。"
    if abs(centipawn) <= 300:
        return f"轮到{_side_text(move.side)}落子时，引擎认为{side}已经明显占优。"
    return f"轮到{_side_text(move.side)}落子时，引擎认为{side}已经取得决定性优势。"


def _move_event_detail(move: VariationMove, *, captured_side: str) -> str:
    """Describe only rule-level events verified on one route ply."""
    parts: list[str] = []
    if move.capture:
        parts.append(f"吃掉{_piece_text(move.captured_piece or '', captured_side)}")
    if move.checkmate:
        parts.append("形成将杀")
    elif move.check:
        parts.append("形成将军")
    if move.castling:
        parts.append("完成易位")
    if move.promotion:
        parts.append(f"升变为{_piece_text(move.promotion, move.side)}")
    return "、".join(parts)


def _side_text(side: str) -> str:
    return "白方" if side == "white" else "黑方"


def _opposite(side: str) -> str:
    return "black" if side == "white" else "white"


def _piece_name(piece: str) -> str:
    return {
        "pawn": "兵", "knight": "马", "bishop": "象", "rook": "车",
        "queen": "后", "king": "王", "q": "后", "r": "车", "b": "象", "n": "马",
    }.get(str(piece).split("_")[-1].lower(), "棋子")


def _piece_text(piece: str, side: str) -> str:
    return f"{'白' if side == 'white' else '黑'}{_piece_name(piece)}"
