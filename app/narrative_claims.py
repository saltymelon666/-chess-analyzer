from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

from .models import MoveFacts, MoveReview, ProfessionalAnalysis, VariationMove
from .strategic_plans import StrategicPlanPackage
from .threat_analysis import ThreatPackage


NARRATIVE_CLAIM_VERSION = "1.1"
NarrativeClaimKind = Literal[
    "position_fact",
    "move_event",
    "move_effect",
    "evaluation_comparison",
    "opponent_resource",
    "verified_consequence",
    "verified_plan",
    "teaching_rule",
]
NarrativeClaimScope = Literal[
    "before_move", "after_played_move", "candidate_route", "teaching",
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

    version: Literal["1.1"] = NARRATIVE_CLAIM_VERSION
    claims: list[VerifiedNarrativeClaim] = Field(default_factory=list)
    recommended_claim_refs: list[str] = Field(alias="recommendedClaimRefs", default_factory=list)
    boundary: str = (
        "核心正文只能重述这些命题。路线中同时出现的事件不能自动写成因果关系；"
        "没有独立命题支持时，禁止使用造成、使得、支撑、限制、削弱、打开、迫使等因果表述。"
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

    priority_lookup = _position_fact_lookup(move)
    for fact_id in priority_evidence_ids:
        fact = priority_lookup.get(fact_id)
        if fact is None:
            continue
        add(
            f"position:{len([item for item in claims if item.kind == 'position_fact']) + 1}",
            "position_fact",
            "before_move",
            f"走棋前，{fact.description}",
            [fact.id],
            "python-chess",
            recommend=True,
        )
        break

    add(
        "played",
        "move_event",
        "after_played_move",
        (
            f"{side_text}走{move.played_move.san}，{piece_text}从"
            f"{move.played_move.from_square}走到{move.played_move.to_square}。"
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
            comparison = f"{move.played_move.san}是这个局面中最准确的选择。"
        elif move.centipawn_loss is None:
            comparison = (
                f"Stockfish当前首选是{move.best_move_san or move.best_move_uci}，"
                f"而不是实战的{move.played_move.san}；现有数据没有提供可靠分差。"
            )
        elif move.centipawn_loss < 50:
            comparison = (
                f"{move.played_move.san}与{move.best_move_san or move.best_move_uci}差距很小，"
                "两者都可以成立。"
            )
        else:
            verdict = (
                "不够准确"
                if move.centipawn_loss < 100
                else "明显失误"
                if move.centipawn_loss < 300
                else "严重失误"
            )
            comparison = (
                f"相较于{move.best_move_san or move.best_move_uci}，"
                f"{move.played_move.san}是{verdict}。"
            )
        add(
            "comparison",
            "evaluation_comparison",
            "after_played_move",
            comparison,
            [played_ref, f"evaluation:before:{move.index}", f"evaluation:after:{move.index}"],
            "stockfish",
            recommend=move.played_move.uci != move.best_move_uci,
        )
    else:
        add(
            "comparison",
            "evaluation_comparison",
            "after_played_move",
            "Stockfish没有提供可验证的首选路线，因此这里不能比较实战着与候选着。",
            [played_ref, f"evaluation:before:{move.index}", f"evaluation:after:{move.index}"],
            "stockfish",
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
                f"关键转折的具体线索是，{move.played_move.san}后的最强回应是"
                f"{reply.san}，这一步{reply_detail}。它与前述评价差同时成立，"
                "但现有数据没有证明这次吃子单独造成全部分差。"
            )
        elif inferior:
            detail = f"，这一步{reply_detail}" if reply_detail else ""
            reply_statement = (
                f"关键转折在于，{move.played_move.san}后的最强回应是{reply.san}{detail}。"
                "当前数据确认的是评价差与整条验证路线，不能把后段事件提前说成"
                "这第一回应的直接效果。"
            )
        else:
            detail = f"，这一步{reply_detail}" if reply_detail else ""
            reply_statement = (
                f"在{move.played_move.san}后的验证路线中，对手首先以{reply.san}回应"
                f"{detail}。"
            )
        add(
            "reply",
            "opponent_resource",
            "candidate_route",
            reply_statement,
            [move.actual_move_line.id, reply.id],
            "python-chess+stockfish",
            recommend=False,
        )

        consequence_statement, consequence_refs = _verified_forcing_consequence(move)
        if consequence_statement:
            add(
                "forcing-consequence",
                "verified_consequence",
                "candidate_route",
                consequence_statement,
                consequence_refs,
                "python-chess+stockfish",
                recommend=True,
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

    teaching = _teaching_statement(move, bool(event_parts), bool(new_targets), bool(new_defended), bool(controlled))
    add(
        "teaching",
        "teaching_rule",
        "teaching",
        teaching,
        [played_ref],
        "program-rule",
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
        "verdict": [
            item.statement
            for item in selected
            if item.kind in {
                "evaluation_comparison",
                "opponent_resource",
                "verified_consequence",
            }
        ],
        "plan": [item.statement for item in selected if item.kind == "verified_plan"],
        "teaching": [item.statement for item in selected if item.kind == "teaching_rule"],
    }
    paragraphs: list[str] = []
    if groups["position"]:
        paragraphs.append("".join(groups["position"]))
    if groups["verdict"]:
        paragraphs.append("".join(groups["verdict"]))
    if groups["plan"]:
        paragraphs.append("".join(groups["plan"]))
    if groups["teaching"]:
        paragraphs.append("".join(groups["teaching"]))
    return "".join(paragraphs)


def resolve_narrative_claims(
    package: NarrativeClaimPackage,
    selected_claim_refs: Sequence[str] = (),
) -> list[VerifiedNarrativeClaim]:
    lookup = {item.claim_id: item for item in package.claims}
    selected = [lookup[item] for item in selected_claim_refs if item in lookup]
    if not selected:
        selected = [lookup[item] for item in package.recommended_claim_refs if item in lookup]
    selected = [
        item for item in selected
        if item.kind not in {"move_event", "move_effect", "teaching_rule"}
    ]
    position_claims = [
        item for item in package.claims if item.kind == "position_fact"
    ]
    position_claim = position_claims[0] if position_claims else None
    key_position_claim = position_claims[1] if len(position_claims) > 1 else None
    comparison_claim = next(
        (item for item in package.claims if item.kind == "evaluation_comparison"), None,
    )
    reply_claim = next(
        (item for item in package.claims if item.kind == "opponent_resource"), None,
    )
    consequence_claim = next(
        (item for item in package.claims if item.kind == "verified_consequence"), None,
    )
    inferior_comparison = bool(
        comparison_claim
        and any(
            marker in comparison_claim.statement
            for marker in ("不够准确", "明显失误", "严重失误")
        )
    )
    if consequence_claim is not None or not inferior_comparison:
        selected = [item for item in selected if item.kind != "opponent_resource"]
    needs_concrete_reply = bool(
        reply_claim
        and inferior_comparison
    )
    # Coordinate narration and control counts remain available as evidence but
    # are not forced into the reader-facing chess-book evaluation.  When a
    # verified consequence already contains the punishment line, the generic
    # first-reply disclaimer would only interrupt that causal explanation.
    required = [
        item
        for item in (
            position_claim,
            key_position_claim,
            comparison_claim,
            reply_claim if needs_concrete_reply and consequence_claim is None else None,
            consequence_claim,
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
        "evaluation_comparison": 1,
        "opponent_resource": 2,
        "verified_consequence": 3,
        "verified_plan": 4,
        "teaching_rule": 5,
        "move_event": 6,
        "move_effect": 7,
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


_PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}


def _verified_forcing_consequence(move: MoveReview) -> tuple[str, list[str]]:
    """Explain a legal forcing capture and the material outcome retained in the PV."""
    line = move.actual_move_line
    inferior = bool(
        line
        and line.moves
        and move.best_move_uci
        and move.played_move.uci != move.best_move_uci
        and move.centipawn_loss is not None
        and move.centipawn_loss >= 50
    )
    if line is None:
        return "", []
    if not inferior:
        return _verified_favorable_route_consequence(move)

    board = chess.Board(move.after_fen)
    mover_color = chess.WHITE if move.side == "white" else chess.BLACK
    balance_for_mover = 0
    descriptions: list[str] = []
    capture_events: list[tuple[VariationMove, chess.Move, chess.Piece, chess.Piece]] = []
    refs = [move.played_move.id or f"move:played:{move.index}", line.id]
    previous_target: chess.Square | None = None

    for index, item in enumerate(line.moves):
        try:
            board_move = chess.Move.from_uci(item.uci)
        except ValueError:
            return "", []
        if board_move not in board.legal_moves:
            return "", []
        if not board.is_capture(board_move):
            break

        moving_piece = board.piece_at(board_move.from_square)
        captured_square = board_move.to_square
        if board.is_en_passant(board_move):
            captured_square += -8 if board.turn == chess.WHITE else 8
        captured_piece = board.piece_at(captured_square)
        if moving_piece is None or captured_piece is None:
            return "", []
        value = _PIECE_VALUES.get(captured_piece.piece_type)
        if value is None:
            return "", []
        balance_for_mover += value if moving_piece.color == mover_color else -value

        actor_side = "white" if moving_piece.color == chess.WHITE else "black"
        victim_side = "white" if captured_piece.color == chess.WHITE else "black"
        actor = _piece_text(chess.piece_name(moving_piece.piece_type), actor_side)
        victim = _piece_text(chess.piece_name(captured_piece.piece_type), victim_side)
        if index and previous_target == board_move.to_square:
            descriptions.append(f"{actor}随即以{item.san}回吃这枚{victim}")
        else:
            connector = "先" if index == 0 else "再"
            descriptions.append(f"{actor}{connector}以{item.san}吃掉{victim}")
        refs.append(item.id)
        capture_events.append((item, board_move, moving_piece, captured_piece))
        previous_target = board_move.to_square
        board.push(board_move)

    if not descriptions:
        return "", []

    if balance_for_mover == 0 and len(descriptions) >= 2:
        statement = (
            f"{move.played_move.san}的问题不是直接丢子，而是允许对手用强制交换改变局面："
            f"{'；'.join(descriptions)}。这串交换结束后双方没有净得子力；"
            "评价下降来自交换后的局面，不能只看第一步吃子就下结论。"
        )
        return statement, list(dict.fromkeys(refs))
    if balance_for_mover > 0:
        return "", []

    opponent_side = _opposite(move.side)
    initial_gain = abs(balance_for_mover)
    initial_gain_text = "一兵" if initial_gain == 1 else f"约{initial_gain}分子力"
    full_balance, full_refs = _route_capture_balance(move)
    refs.extend(full_refs)
    if full_balance is None:
        return "", []
    if full_balance >= 0:
        statement = (
            f"{move.played_move.san}后，验证变化以{descriptions[0]}开始；"
            "不过算完整条路线，这次吃子并没有形成可保留的物质收益。"
            "因此这步的缺点在交换后的局面，而不能简单说成直接丢兵或丢子。"
        )
        return statement, list(dict.fromkeys(refs))
    final_gain = abs(full_balance)
    final_gain_text = "一兵" if final_gain == 1 else f"约{final_gain}分子力"
    same_gain = final_gain == initial_gain
    retained = (
        "直到验证路线结束，这项收益也没有被追回。"
        if same_gain
        else f"算完整条验证路线，{_side_text(opponent_side)}最终保留{final_gain_text}的吃子收益。"
    )
    clearance = _verified_line_clearance_mechanism(capture_events)

    if len(descriptions) == 1:
        statement = (
            f"{move.played_move.san}的问题在于对手可以立即兑现子力收益："
            f"{descriptions[0]}，{_side_text(opponent_side)}净得{initial_gain_text}。{retained}"
        )
    else:
        statement = (
            f"{move.played_move.san}的问题可以由紧接着的强制交换具体说明："
            f"{'；'.join(descriptions)}。{clearance}这串连续吃子结束后，"
            f"{_side_text(opponent_side)}净得{initial_gain_text}。{retained}"
        )
    return statement, list(dict.fromkeys(refs))


def _verified_favorable_route_consequence(move: MoveReview) -> tuple[str, list[str]]:
    """State only a favorable forcing exchange that starts with the played move."""
    line = move.actual_move_line
    if line is None:
        return "", []
    board = chess.Board(move.before_fen)
    mover_color = chess.WHITE if move.side == "white" else chess.BLACK
    route: list[MoveFacts | VariationMove] = [move.played_move, *line.moves]
    balance = 0
    descriptions: list[str] = []
    refs = [line.id]

    for index, item in enumerate(route):
        try:
            board_move = chess.Move.from_uci(item.uci)
        except ValueError:
            return "", []
        if board_move not in board.legal_moves:
            return "", []
        if not board.is_capture(board_move):
            if index == 0:
                return "", []
            break
        if board.is_capture(board_move):
            captured_square = board_move.to_square
            if board.is_en_passant(board_move):
                captured_square += -8 if board.turn == chess.WHITE else 8
            moving_piece = board.piece_at(board_move.from_square)
            captured_piece = board.piece_at(captured_square)
            if moving_piece is None or captured_piece is None:
                return "", []
            value = _PIECE_VALUES.get(captured_piece.piece_type)
            if value is None:
                return "", []
            balance += value if moving_piece.color == mover_color else -value
            actor_side = "white" if moving_piece.color == chess.WHITE else "black"
            victim_side = "white" if captured_piece.color == chess.WHITE else "black"
            actor = _piece_text(chess.piece_name(moving_piece.piece_type), actor_side)
            victim = _piece_text(chess.piece_name(captured_piece.piece_type), victim_side)
            connector = "先" if not descriptions else "随后"
            descriptions.append(f"{actor}{connector}以{item.san}吃掉{victim}")
            if item.id:
                refs.append(item.id)
        board.push(board_move)

    if board.is_checkmate():
        return (
            f"{move.played_move.san}的关键价值在于攻势能够强制延续；"
            "对手按验证路线应对后，局面最终形成将杀。",
            list(dict.fromkeys(refs)),
        )
    if balance <= 0 or not descriptions:
        return "", []
    gain_text = "一兵" if balance == 1 else f"约{balance}分子力"
    return (
        f"{move.played_move.san}的价值体现在紧接着的强制交换中："
        f"{'；'.join(descriptions)}。这串交换结束后，{_side_text(move.side)}净得{gain_text}。",
        list(dict.fromkeys(refs)),
    )


def _verified_line_clearance_mechanism(
    events: Sequence[tuple[VariationMove, chess.Move, chess.Piece, chess.Piece]],
) -> str:
    """Explain a pawn vacating a file for a later same-side rook capture."""
    if len(events) < 3:
        return ""
    first_item, first_move, first_piece, _ = events[0]
    if first_piece.piece_type != chess.PAWN:
        return ""
    vacated_file = chess.square_file(first_move.from_square)
    if chess.square_file(first_move.to_square) == vacated_file:
        return ""

    for later_item, later_move, later_piece, later_captured in events[2:]:
        if later_piece.color != first_piece.color or later_piece.piece_type != chess.ROOK:
            continue
        if not (
            chess.square_file(later_move.from_square)
            == chess.square_file(later_move.to_square)
            == vacated_file
        ):
            continue
        ranks = {
            chess.square_rank(later_move.from_square),
            chess.square_rank(later_move.to_square),
        }
        vacated_rank = chess.square_rank(first_move.from_square)
        if not min(ranks) < vacated_rank < max(ranks):
            continue
        side = "white" if later_piece.color == chess.WHITE else "black"
        victim_side = "white" if later_captured.color == chess.WHITE else "black"
        file_name = chess.FILE_NAMES[vacated_file]
        return (
            f"这里的机制是，{first_item.san}让{_piece_text('pawn', side)}离开{file_name}线，"
            f"清出了{_piece_text('rook', side)}从{chess.square_name(later_move.from_square)}"
            f"通往{chess.square_name(later_move.to_square)}的线路；"
            f"因此它随后能以{later_item.san}侵入并吃掉{_piece_text(chess.piece_name(later_captured.piece_type), victim_side)}。"
        )
    return ""


def _route_capture_balance(move: MoveReview) -> tuple[int | None, list[str]]:
    """Return capture-only material change for the played side over the full PV."""
    line = move.actual_move_line
    if line is None:
        return None, []
    board = chess.Board(move.after_fen)
    mover_color = chess.WHITE if move.side == "white" else chess.BLACK
    balance = 0
    refs: list[str] = []
    for item in line.moves:
        try:
            board_move = chess.Move.from_uci(item.uci)
        except ValueError:
            return None, []
        if board_move not in board.legal_moves:
            return None, []
        if board.is_capture(board_move):
            captured_square = board_move.to_square
            if board.is_en_passant(board_move):
                captured_square += -8 if board.turn == chess.WHITE else 8
            captured_piece = board.piece_at(captured_square)
            moving_piece = board.piece_at(board_move.from_square)
            if captured_piece is None or moving_piece is None:
                return None, []
            value = _PIECE_VALUES.get(captured_piece.piece_type)
            if value is None:
                return None, []
            balance += value if moving_piece.color == mover_color else -value
            refs.append(item.id)
        board.push(board_move)
    return balance, refs


def _position_fact_lookup(move: MoveReview) -> dict[str, object]:
    facts = [
        *move.position_facts.piece_activity,
        *move.position_facts.king_safety,
        *move.position_facts.pawn_structure,
    ]
    return {item.id: item for item in facts if item.id}


def _evaluation_posture_statement(move: MoveReview) -> str:
    """Turn the pre-move engine score into a number-free global posture."""
    mate_in = move.before.mate_in
    if mate_in is not None:
        side = "白方" if mate_in > 0 else "黑方"
        return f"走棋前，{side}存在已经确认的强制将杀。"
    centipawn = move.before.centipawn
    if centipawn is None:
        return "走棋前，现有分析还不足以可靠判断优势归属。"
    if abs(centipawn) <= 25:
        return "走棋前，局面大致均衡，双方都没有形成决定性优势。"
    side = "白方" if centipawn > 0 else "黑方"
    level = (
        "轻微"
        if abs(centipawn) <= 100
        else "明显"
        if abs(centipawn) <= 300
        else "决定性"
    )
    return f"走棋前，{side}{level}占优。"


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


def _teaching_statement(
    move: MoveReview,
    has_event: bool,
    has_target: bool,
    has_defense: bool,
    has_control: bool,
) -> str:
    if move.played_move.check or move.played_move.checkmate:
        return "类似局面中，先检查所有将军和直接威胁，再研究较慢的计划。"
    if move.played_move.capture:
        return "类似局面中，先把连续交换逐步算清，再判断眼前收益能否保住。"
    if move.played_move.castling:
        return "类似局面中，完成易位后先核对王的安全，再看车是否顺利参加战斗。"
    if move.played_move.promotion:
        return "类似局面中，升变后先检查新棋子的将军、攻击和对手最强回应。"
    if has_target:
        return "类似局面中，先检查落子后新增攻击哪些具体目标，再计算对手最强回应。"
    if has_defense:
        return "类似局面中，先比较落子前后的保护关系，再判断防守是否真正改善。"
    if has_control:
        return "类似局面中，先比较落子前后新增和失去的控制格，再判断这步是否配合当前计划。"
    return "类似局面中，先核对这步实际改变的格子和对手最强回应，再评价它的战略意义。"


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
