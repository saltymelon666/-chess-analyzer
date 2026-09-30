from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

from .models import MoveFacts, MoveReview, ProfessionalAnalysis, VariationMove
from .book_mechanisms import build_book_mechanism
from .strategic_plans import StrategicPlanFact, StrategicPlanPackage
from .threat_analysis import ThreatPackage


NARRATIVE_CLAIM_VERSION = "1.9"
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
    "verified_choice",
    "verified_consequence",
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

    version: Literal["1.8", "1.9"] = NARRATIVE_CLAIM_VERSION
    claims: list[VerifiedNarrativeClaim] = Field(default_factory=list)
    recommended_claim_refs: list[str] = Field(alias="recommendedClaimRefs", default_factory=list)
    boundary: str = (
        "核心正文只能重述这些命题。只有程序在同一条合法Stockfish路线中确认牵制持续到"
        "对应棋子被吃，或确认非吃子应手新增攻击重要子力且该子随后沿路线移开，才允许把"
        "前后事件写成战术链；其他同时出现的事件不能自动写成因果关系。"
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
    conflict = _verified_plan_conflict(plan_package)
    if conflict is not None:
        add(
            "position:plan-conflict", "position_fact", "candidate_route",
            conflict[0], conflict[1], "verified-plan",
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

    strategic_choice, strategic_choice_refs = _verified_strategic_choice(move)
    if not strategic_choice:
        strategic_choice, strategic_choice_refs = _verified_quiet_move_order(move)
    # Numeric scores and grades belong to the move-review card, not book prose.
    # Keep concrete move comparisons below; do not emit a score-report claim.

    if strategic_choice:
        add(
            "strategic-choice",
            "verified_choice",
            "candidate_route",
            strategic_choice,
            strategic_choice_refs,
            "python-chess+stockfish",
            recommend=True,
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

    reply_pressure = (
        None
        if tactical_cause is not None
        else _non_capture_reply_pressure_claim(move)
    )
    if reply_pressure is not None:
        statement, evidence_refs = reply_pressure
        add(
            "position:reply-pressure",
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
        if reply_statement and tactical_cause is None and reply_pressure is None:
            add(
                "reply",
                "opponent_resource",
                "candidate_route",
                reply_statement,
                [move.actual_move_line.id, reply.id],
                "python-chess+stockfish",
                recommend=inferior,
            )

        consequence_statement, consequence_refs = _verified_forcing_consequence(move)
        if consequence_statement and tactical_cause is None and reply_pressure is None:
            add(
                "forcing-consequence",
                "verified_consequence",
                "candidate_route",
                consequence_statement,
                consequence_refs,
                "python-chess+stockfish",
                recommend=True,
            )

    best_line = next(
        (
            line for line in move.candidate_lines
            if line.rank == 1 and line.first_move.uci == move.best_move_uci
        ),
        None,
    )
    if (
        best_line is not None
        and move.played_move.uci != move.best_move_uci
        and not strategic_choice
        and tactical_cause is None
        and reply_pressure is None
        and plan_package is not None
    ):
        for plan in plan_package.plans:
            if (
                plan.side != move.side
                or plan.confidence != "high"
                or best_line.id not in plan.evidence_route_ids
                or not any(
                    candidate in plan.supporting_moves
                    for candidate in (move.best_move_uci, move.best_move_san)
                )
            ):
                continue
            best_plan_text, best_plan_refs = _best_plan_statement(move, plan)
            add(
                f"best-plan:{plan.plan_id}",
                "verified_choice",
                "candidate_route",
                best_plan_text,
                [best_line.id, *plan.evidence_route_ids, *best_plan_refs],
                "verified-plan",
                recommend=True,
                confidence="high",
            )
            break

    for plan in (plan_package.plans if plan_package else []):
        if plan.side != move.side:
            continue
        if plan.type == "improve_worst_piece" and _verified_opening_bishop_pressure(move) is not None:
            continue
        if move.played_move.uci not in plan.supporting_moves and move.played_move.san not in plan.supporting_moves:
            continue
        if not plan.evidence_route_ids:
            continue
        if plan.type == "attack_weak_pawn" and any(
            item.kind == "verified_consequence" for item in claims
        ):
            continue
        plan_statement = _played_plan_statement(move, plan)
        add(
            f"plan:{plan.plan_id}",
            "verified_plan",
            "candidate_route",
            plan_statement,
            plan.evidence_route_ids,
            "verified-plan",
            recommend=True,
            confidence=plan.confidence,
        )

    if not any(item.kind in {"position_cause", "verified_consequence"} for item in claims):
        queen_trade = _verified_immediate_queen_trade(move)
        fianchetto = _verified_fianchetto_center_order(move) if queen_trade is None else None
        opening_pressure = (
            _verified_opening_bishop_pressure(move)
            if queen_trade is None and fianchetto is None else None
        )
        double_attack = (
            _verified_double_attack_reply(move)
            if queen_trade is None and fianchetto is None and opening_pressure is None else None
        )
        played_role = (
            queen_trade[0] if queen_trade else
            fianchetto[0] if fianchetto else
            opening_pressure[0] if opening_pressure else
            double_attack[0] if double_attack else _verified_played_role(before, move)
        )
        if played_role:
            add(
                "played-role", "verified_choice", "after_played_move",
                played_role,
                queen_trade[1] if queen_trade else
                fianchetto[1] if fianchetto else
                opening_pressure[1] if opening_pressure else
                double_attack[1] if double_attack else [played_ref],
                "python-chess+stockfish" if queen_trade or fianchetto or opening_pressure or double_attack else "python-chess",
            )

    # Extend thin paragraphs with a legally replayed mechanism. Existing detailed
    # tactical/positional explanations keep their specialized evidence chain.
    detailed_choice = strategic_choice or tactical_cause or reply_pressure or any(
        item.claim_id.endswith("played-role") and len(item.evidence_refs) > 1
        for item in claims
    )
    detailed_choice = detailed_choice or _verified_delayed_center_contact(move) is not None
    detailed_choice = detailed_choice or _verified_queenless_king_activity(move) is not None
    detailed_choice = detailed_choice or _has_closed_center(before)
    if move.actual_move_line and move.actual_move_line.moves:
        detailed_choice = detailed_choice or _verified_alternative_pawn_trade(
            move, chess.Move.from_uci(move.actual_move_line.moves[0].uci),
        ) is not None
    body_length = sum(len(item.statement) for item in claims
                      if item.kind in {"verified_choice", "verified_consequence", "position_cause", "verified_plan"})
    if (not detailed_choice and body_length < 220 and move.actual_move_line
            and len(move.actual_move_line.moves) >= 6):
        mechanism = build_book_mechanism(move)
        if mechanism is not None:
            add("book-mechanism", "verified_choice", "candidate_route",
                mechanism.statement, mechanism.evidence_refs, "python-chess+stockfish")

    useful_recommended = [
        item.claim_id for item in claims
        if item.claim_id in recommended and item.kind not in {"move_event", "move_effect"}
    ]
    if any(item.claim_id.endswith(":book-mechanism") for item in claims):
        useful_recommended = [item.claim_id for item in claims if item.claim_id.endswith(
            (":position:evaluation", ":book-mechanism")
        )]

    return NarrativeClaimPackage(
        claims=claims,
        recommendedClaimRefs=useful_recommended[:8],
    )


def compose_verified_core_paragraph(
    package: NarrativeClaimPackage,
    selected_claim_refs: Sequence[str] = (),
) -> str:
    """Render only verified statements; selection may change emphasis, never facts."""
    selected = resolve_narrative_claims(package, selected_claim_refs)
    groups = {
        "position": [item.statement for item in selected if item.kind == "position_fact"],
        "choice": [item.statement for item in selected if item.kind == "verified_choice"],
        "cause": [item.statement for item in selected if item.kind == "position_cause"],
        "reply": [item.statement for item in selected if item.kind == "opponent_resource"],
        "consequence": [item.statement for item in selected if item.kind == "verified_consequence"],
        "plan": [item.statement for item in selected if item.kind == "verified_plan"],
    }
    paragraphs: list[str] = []
    for section in ("position", "choice", "cause", "reply", "consequence", "plan"):
        if groups[section]:
            paragraphs.append("".join(groups[section]))
    return "".join(paragraphs)


def resolve_narrative_claims(
    package: NarrativeClaimPackage,
    selected_claim_refs: Sequence[str] = (),
) -> list[VerifiedNarrativeClaim]:
    lookup = {item.claim_id: item for item in package.claims}
    mechanism = next((item for item in package.claims if item.claim_id.endswith(":book-mechanism")), None)
    if mechanism is not None:
        # One cohesive, complete explanation replaces duplicate role/plan snippets.
        return [item for item in package.claims if item.kind == "position_fact"
                and item.claim_id.endswith(":position:evaluation")] + [mechanism]
    selected = [lookup[item] for item in selected_claim_refs if item in lookup]
    if not selected:
        selected = [lookup[item] for item in package.recommended_claim_refs if item in lookup]
    selected = [
        item for item in selected
        if item.kind not in {"move_event", "move_effect", "evaluation_comparison"}
    ]
    position_claim = next(
        (item for item in package.claims if item.kind == "position_fact"), None,
    )
    reply_claim = next(
        (item for item in package.claims if item.kind == "opponent_resource"), None,
    )
    cause_claim = next(
        (item for item in package.claims if item.kind == "position_cause"), None,
    )
    consequence_claim = next(
        (item for item in package.claims if item.kind == "verified_consequence"), None,
    )
    strategic_choice_claim = next(
        (item for item in package.claims if item.kind == "verified_choice"), None,
    )
    plan_claim = next(
        (
            item for item in package.claims
            if item.kind == "verified_plan" and item.confidence == "high"
        ),
        None,
    )
    if cause_claim is not None or consequence_claim is not None or strategic_choice_claim is not None:
        selected = [item for item in selected if item.kind != "opponent_resource"]
    needs_concrete_reply = reply_claim is not None
    required = [
        item
        for item in (
            position_claim,
            strategic_choice_claim,
            cause_claim,
            reply_claim if needs_concrete_reply and cause_claim is None and consequence_claim is None else None,
            consequence_claim,
            plan_claim,
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
        "verified_choice": 2,
        "position_cause": 3,
        "opponent_resource": 4,
        "verified_consequence": 5,
        "verified_plan": 6,
        "move_event": 7,
        "move_effect": 8,
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


def _verified_plan_conflict(
    package: StrategicPlanPackage | None,
) -> tuple[str, list[str]] | None:
    """Name a shared long-term contest only when both sides' routes support it."""
    if package is None:
        return None
    high = [plan for plan in package.plans if plan.confidence == "high"]
    for white in high:
        if white.side != "white":
            continue
        for black in high:
            if black.side != "black" or black.type != white.type:
                continue
            if white.type == "occupy_open_file":
                white_file = re.search(r"占领([a-h])开放线", white.goal)
                black_file = re.search(r"占领([a-h])开放线", black.goal)
                if white_file and black_file and white_file.group(1) == black_file.group(1):
                    file_name = white_file.group(1)
                    return (
                        f"双方都准备把车放到{file_name}开放线，"
                        f"{file_name}线控制权因而成为共同争夺点。",
                        list(dict.fromkeys([*white.evidence_route_ids, *black.evidence_route_ids])),
                    )
            if white.type == "prepare_center_break":
                white_square = re.search(r"实施([a-h][1-8])方向的中心兵突破", white.goal)
                black_square = re.search(r"实施([a-h][1-8])方向的中心兵突破", black.goal)
                if white_square and black_square and white_square.group(1)[0] == black_square.group(1)[0]:
                    return (
                        f"白方准备{white_square.group(1)}，黑方准备{black_square.group(1)}；"
                        f"{white_square.group(1)[0]}线中心兵何时接触，决定了双方的出手次序。",
                        list(dict.fromkeys([*white.evidence_route_ids, *black.evidence_route_ids])),
                    )
    return None


def _played_plan_statement(move: MoveReview, plan: StrategicPlanFact) -> str:
    """Keep supported plans grammatical when the practical move is the plan's first step."""
    if plan.type == "prepare_center_break" and move.played_move.piece.endswith("pawn"):
        target = re.search(r"实施([a-h][1-8])方向的中心兵突破", plan.goal)
        if target and target.group(1) == move.played_move.to_square:
            return (
                f"{move.played_move.san}直接推进{target.group(1)[0]}线兵，"
                "立即与对方中心兵发生接触。"
            )
    if plan.type == "improve_worst_piece":
        origin = re.search(r"([a-h][1-8])[马象车后]的活动", plan.goal)
        if origin and origin.group(1) == move.played_move.from_square:
            return (
                f"{move.played_move.san}把原在{origin.group(1)}的"
                f"{_piece_text(move.played_move.piece, move.side)}投入行动。"
            )
    if plan.type == "create_passed_pawn":
        file_name = re.search(r"([a-h])线通路兵", plan.goal)
        if file_name:
            return (
                f"在{move.played_move.san}后的已验证兵形转换里，"
                f"{file_name.group(1)}线通路兵成为{_side_text(move.side)}可以继续利用的资源。"
            )
    return plan.goal.rstrip("。") + "。"


def _best_plan_statement(move: MoveReview, plan: StrategicPlanFact) -> tuple[str, list[str]]:
    best = move.best_move_san or move.best_move_uci or "首选着"
    if plan.type == "prepare_center_break":
        target = re.search(r"实施([a-h][1-8])方向的中心兵突破", plan.goal)
        if target:
            statement = (
                f"首选{best}先推进{target.group(1)[0]}线兵，直接挑战对方中心兵；"
                f"实战{move.played_move.san}没有先实施这个推进。"
            )
            timing = _verified_delayed_center_contact(move)
            if timing is not None:
                return statement + timing[0], timing[1]
            return statement, []
    if plan.type == "occupy_open_file":
        file_name = re.search(r"占领([a-h])开放线", plan.goal)
        if file_name:
            return f"首选{best}先让车进入{file_name.group(1)}开放线；这条路线优先争夺现成的通道。", []
    return f"首选{best}对应的路线重视{plan.goal.rstrip('。')}。", []


def _verified_delayed_center_contact(move: MoveReview) -> tuple[str, list[str]] | None:
    """Describe a postponed central pawn contact only after legal route replay."""
    line = move.actual_move_line
    if line is None or not line.verified or move.best_move_uci is None:
        return None
    board = chess.Board(move.before_fen)
    try:
        best = chess.Move.from_uci(move.best_move_uci)
    except ValueError:
        return None
    if best not in board.legal_moves or board.is_capture(best):
        return None
    pawn = board.piece_at(best.from_square)
    if pawn is None or pawn.piece_type != chess.PAWN or chess.square_file(best.to_square) not in {2, 3, 4, 5}:
        return None
    immediate = board.copy(stack=False)
    immediate.push(best)
    enemy_pawns = [
        square for square in immediate.attacks(best.to_square)
        if immediate.piece_at(square) == chess.Piece(chess.PAWN, not pawn.color)
    ]
    if len(enemy_pawns) != 1:
        return None
    target = chess.square_name(enemy_pawns[0])
    board = chess.Board(move.after_fen)
    for index, item in enumerate(line.moves[:-1]):
        try:
            route_move = chess.Move.from_uci(item.uci)
        except ValueError:
            return None
        if route_move not in board.legal_moves:
            return None
        if (
            route_move == best and index > 0
            and board.piece_at(best.from_square) == pawn
            and board.piece_at(enemy_pawns[0]) == chess.Piece(chess.PAWN, not pawn.color)
        ):
            board.push(route_move)
            reply_item = line.moves[index + 1]
            try:
                reply = chess.Move.from_uci(reply_item.uci)
            except ValueError:
                return None
            reply_pawn = board.piece_at(reply.from_square)
            if (
                reply not in board.legal_moves or not board.is_capture(reply)
                or reply.to_square != best.to_square
                or reply.from_square != enemy_pawns[0]
                or reply_pawn != chess.Piece(chess.PAWN, not pawn.color)
            ):
                return None
            return (
                f"实战路线稍后才走{item.san}，届时{chess.square_name(best.to_square)}兵"
                f"与{target}兵接触，{_side_text(_opposite(move.side))}随即以"
                f"{reply_item.san}将这一接触转成兵的交换。",
                [ref for ref in (line.id, item.id, reply_item.id) if ref],
            )
        board.push(route_move)
    return None


def _verified_opening_bishop_pressure(move: MoveReview) -> tuple[str, list[str]] | None:
    """Trace a bishop's pressure on the e5 defender through the legal e4 counterplay."""
    line = move.actual_move_line
    if move.side != "white" or move.played_move.uci != "f1b5" or line is None or not line.verified:
        return None
    route = ("g8f6", "e1g1", "f6e4", "f1e1", "e4d6", "b5a4")
    if len(line.moves) < len(route) or tuple(item.uci for item in line.moves[:6]) != route:
        return None
    before = chess.Board(move.before_fen)
    if (
        before.piece_at(chess.C6) != chess.Piece(chess.KNIGHT, chess.BLACK)
        or before.piece_at(chess.E5) != chess.Piece(chess.PAWN, chess.BLACK)
        or chess.E5 not in before.attacks(chess.C6)
    ):
        return None
    played = chess.Move.from_uci(move.played_move.uci)
    if played not in before.legal_moves or before.piece_at(played.from_square) != chess.Piece(chess.BISHOP, chess.WHITE):
        return None
    before.push(played)
    if before.fen() != move.after_fen or chess.C6 not in before.attacks(chess.B5):
        return None
    for index, item in enumerate(line.moves[:6]):
        route_move = chess.Move.from_uci(item.uci)
        if route_move not in before.legal_moves:
            return None
        actor = before.piece_at(route_move.from_square)
        expected_type = (chess.KNIGHT, chess.KING, chess.KNIGHT, chess.ROOK, chess.KNIGHT, chess.BISHOP)[index]
        if actor is None or actor.piece_type != expected_type:
            return None
        if index == 2 and (
            not before.is_capture(route_move)
            or before.piece_at(chess.E4) != chess.Piece(chess.PAWN, chess.WHITE)
        ):
            return None
        before.push(route_move)
        if index == 3 and chess.E4 not in before.attacks(chess.E1):
            return None
        if index == 4 and (
            before.piece_at(chess.B5) != chess.Piece(chess.BISHOP, chess.WHITE)
            or chess.B5 not in before.attacks(chess.D6)
        ):
            return None
    if before.piece_at(chess.A4) != chess.Piece(chess.BISHOP, chess.WHITE):
        return None
    named = line.moves[:6]
    return (
        f"{move.played_move.san}以白象向原本保护e5兵的c6马施压。"
        f"黑方{named[0].san}出马、白方{named[1].san}易位后，"
        f"黑马{named[2].san}吃掉e4兵；白车{named[3].san}攻击e4马。"
        f"黑马{named[4].san}退开时又攻击b5象，白方{named[5].san}保住这枚象。",
        [ref for ref in (
            move.played_move.id or f"move:played:{move.index}",
            line.id, *(item.id for item in named),
        ) if ref],
    )


def _verified_fianchetto_center_order(move: MoveReview) -> tuple[str, list[str]] | None:
    """Compare a legal kingside bishop setup with an immediate central pawn push."""
    line = move.actual_move_line
    best_line = next((item for item in move.candidate_lines if item.rank == 1), None)
    if line is None or not line.verified or best_line is None or move.best_move_uci is None:
        return None
    white = move.side == "white"
    played_uci = "g2g3" if white else "g7g6"
    bishop_uci = "f1g2" if white else "f8g7"
    opposing_c_uci = "c7c5" if white else "c2c4"
    central_uci = "d2d4" if white else "d7d5"
    center_square = chess.D4 if white else chess.D5
    if move.played_move.uci != played_uci or move.best_move_uci != central_uci:
        return None
    before = chess.Board(move.before_fen)
    try:
        played = chess.Move.from_uci(played_uci)
        central = chess.Move.from_uci(central_uci)
    except ValueError:
        return None
    mover_color = chess.WHITE if white else chess.BLACK
    if (
        played not in before.legal_moves or central not in before.legal_moves
        or best_line.first_move.uci != central_uci
        or before.piece_at(chess.F1 if white else chess.F8)
        != chess.Piece(chess.BISHOP, mover_color)
    ):
        return None
    board = chess.Board(move.after_fen)
    expected_after = before.copy(stack=False)
    expected_after.push(played)
    if board.fen() != expected_after.fen():
        return None
    found: dict[str, VariationMove] = {}
    route_order = (bishop_uci, opposing_c_uci, central_uci)
    for item in line.moves:
        try:
            route_move = chess.Move.from_uci(item.uci)
        except ValueError:
            return None
        if route_move not in board.legal_moves:
            return None
        if item.uci in route_order and item.uci not in found:
            if item.uci != route_order[len(found)]:
                return None
            expected = chess.BISHOP if item.uci == bishop_uci else chess.PAWN
            if board.piece_at(route_move.from_square) != chess.Piece(expected, board.turn):
                return None
            found[item.uci] = item
        board.push(route_move)
        if item.uci == opposing_c_uci and center_square not in board.attacks(route_move.to_square):
            return None
        if item.uci == central_uci:
            if (
                len(found) != 3
                or board.piece_at(chess.G2 if white else chess.G7)
                != chess.Piece(chess.BISHOP, mover_color)
                or board.piece_at(chess.C5 if white else chess.C4)
                != chess.Piece(chess.PAWN, not mover_color)
            ):
                return None
            break
    if len(found) != 3:
        return None
    bishop_item = found[bishop_uci]
    c_item = found[opposing_c_uci]
    center_item = found[central_uci]
    return (
        f"{move.played_move.san}先给{('f1' if white else 'f8')}的"
        f"{_piece_text('bishop', move.side)}打开通往{('g2' if white else 'g7')}的路；"
        f"实战{bishop_item.san}完成出子后，"
        f"{_side_text(_opposite(move.side))}以{c_item.san}控制"
        f"{chess.square_name(center_square)}，"
        f"{_side_text(move.side)}随后才走{center_item.san}。"
        f"首选{move.best_move_san or move.best_move_uci}则先把兵放进中心。",
        [ref for ref in (
            move.played_move.id or f"move:played:{move.index}",
            line.id, bishop_item.id, c_item.id, center_item.id, best_line.id,
        ) if ref],
    )


def _verified_double_attack_reply(move: MoveReview) -> tuple[str, list[str]] | None:
    """Follow a knight's two-piece attack through the opponent's legal queen retreat."""
    line = move.actual_move_line
    if line is None or not line.verified or not line.moves:
        return None
    before = chess.Board(move.before_fen)
    try:
        played = chess.Move.from_uci(move.played_move.uci)
        reply = chess.Move.from_uci(line.moves[0].uci)
    except ValueError:
        return None
    if played not in before.legal_moves:
        return None
    knight = before.piece_at(played.from_square)
    if knight != chess.Piece(chess.KNIGHT, before.turn):
        return None
    after = before.copy(stack=False)
    old_attacks = set(before.attacks(played.from_square))
    after.push(played)
    queen_squares = [
        square for square in after.attacks(played.to_square) - old_attacks
        if after.piece_at(square) == chess.Piece(chess.QUEEN, after.turn)
    ]
    other_squares = [
        square for square in after.attacks(played.to_square) - old_attacks
        if (piece := after.piece_at(square)) is not None
        and piece.color == after.turn and piece.piece_type in {chess.BISHOP, chess.ROOK}
    ]
    if len(queen_squares) != 1 or len(other_squares) != 1:
        return None
    queen_square, other_square = queen_squares[0], other_squares[0]
    if reply not in after.legal_moves or reply.from_square != queen_square or after.is_capture(reply):
        return None
    other = after.piece_at(other_square)
    after.push(reply)
    if (
        after.piece_at(reply.to_square) != chess.Piece(chess.QUEEN, not knight.color)
        or after.piece_at(other_square) != other
        or reply.to_square in after.attacks(played.to_square)
        or other_square not in after.attacks(played.to_square)
    ):
        return None
    opponent_side = _opposite(move.side)
    opponent = _side_text(opponent_side)
    piece_name = chess.piece_name(other.piece_type) if other is not None else "piece"
    return (
        f"{move.played_move.san}让{_piece_text('knight', move.side)}同时攻击"
        f"{chess.square_name(queen_square)}的{_piece_text('queen', opponent_side)}和"
        f"{chess.square_name(other_square)}的{_piece_text(piece_name, opponent_side)}；"
        f"{opponent}以{line.moves[0].san}把后移出马的攻击范围，"
        f"{chess.square_name(other_square)}的{_piece_name(piece_name)}仍留在原位。",
        [ref for ref in (
            move.played_move.id or f"move:played:{move.index}",
            line.id, line.moves[0].id,
        ) if ref],
    )


def _verified_played_role(before: chess.Board, move: MoveReview) -> str:
    """State an immediate board effect, without calling it a scoring cause."""
    try:
        played = chess.Move.from_uci(move.played_move.uci)
    except ValueError:
        return ""
    if played not in before.legal_moves:
        return ""
    piece = before.piece_at(played.from_square)
    if piece is None:
        return ""
    after = before.copy(stack=False)
    before_attacks = set(before.attacks(played.from_square))
    after.push(played)
    landed = after.piece_at(played.to_square)
    if landed is None:
        return ""
    new_squares = set(after.attacks(played.to_square)) - before_attacks
    targets: list[tuple[int, int, chess.Piece]] = []
    defended: list[tuple[int, int, chess.Piece]] = []
    for square in new_squares:
        occupant = after.piece_at(square)
        if occupant is None or occupant.piece_type == chess.KING:
            continue
        item = (_PIECE_VALUES[occupant.piece_type], square, occupant)
        (defended if occupant.color == piece.color else targets).append(item)
    targets.sort(key=lambda item: (-item[0], item[1]))
    defended.sort(key=lambda item: (-item[0], item[1]))
    side = "white" if piece.color == chess.WHITE else "black"
    subject = f"{move.played_move.san}让{_piece_text(chess.piece_name(landed.piece_type), side)}"
    subject += f"来到{chess.square_name(played.to_square)}"
    if before.gives_check(played):
        captured_square = played.to_square
        if before.is_en_passant(played):
            captured_square += -8 if piece.color == chess.WHITE else 8
        victim = before.piece_at(captured_square) if before.is_capture(played) else None
        capture_text = (
            f"，同时吃掉{_piece_text(chess.piece_name(victim.piece_type), _opposite(side))}"
            if victim is not None else ""
        )
        return subject + capture_text + "并形成将军；对方接下来必须先处理王受到的攻击。"
    line = move.actual_move_line
    if line is not None and line.verified and line.moves:
        try:
            immediate_reply = chess.Move.from_uci(line.moves[0].uci)
        except ValueError:
            return ""
        if immediate_reply in after.legal_moves and after.is_capture(immediate_reply):
            captured_square = immediate_reply.to_square
            if after.is_en_passant(immediate_reply):
                captured_square += -8 if after.turn == chess.WHITE else 8
            if captured_square == played.to_square:
                return ""
    if targets:
        labels = [
            f"{chess.square_name(square)}的{_piece_text(chess.piece_name(target.piece_type), _opposite(side))}"
            for _, square, target in targets[:2]
        ]
        if piece.piece_type == chess.PAWN and targets[0][2].piece_type == chess.PAWN:
            return subject + f"，直接攻击{labels[0]}；两枚兵的接触已经形成。"
        return subject + f"，直接攻击{'和'.join(labels)}。"
    if defended and defended[0][2].piece_type == chess.PAWN:
        square = defended[0][1]
        if (
            piece.piece_type == chess.PAWN
            and chess.square_file(played.to_square) in {5, 6, 7}
            and chess.square_file(square) in {3, 4}
            and _has_closed_center(before)
        ):
            return (
                f"{move.played_move.san}在封闭中心旁保护"
                f"{chess.square_name(square)}的本方兵，"
                "同时以王翼兵推进争取空间。"
            )
        return subject + f"，新增保护{chess.square_name(square)}的本方兵。"
    if piece.piece_type == chess.PAWN:
        released_square = played.from_square
        after_for_mover = after.copy(stack=False)
        after_for_mover.turn = piece.color
        for square, bishop in before.piece_map().items():
            if bishop.color != piece.color or bishop.piece_type != chess.BISHOP:
                continue
            bishop_step = chess.Move(square, released_square)
            if bishop_step in after_for_mover.legal_moves:
                return (
                    f"{move.played_move.san}腾出{chess.square_name(released_square)}，"
                    f"{chess.square_name(square)}的{_piece_text('bishop', side)}"
                    "由此可以走到这个格子；这步棋先打通了象的出路。"
                )
    return ""


def _verified_immediate_queen_trade(move: MoveReview) -> tuple[str, list[str]] | None:
    """Do not portray a queen's transient attack as durable after recapture."""
    line = move.actual_move_line
    if not line or not line.verified or not line.moves:
        return None
    board = chess.Board(move.before_fen)
    try:
        played = chess.Move.from_uci(move.played_move.uci)
        reply = chess.Move.from_uci(line.moves[0].uci)
    except ValueError:
        return None
    moving = board.piece_at(played.from_square)
    victim = board.piece_at(played.to_square)
    if (
        played not in board.legal_moves
        or moving is None or moving.piece_type != chess.QUEEN
        or victim is None or victim.piece_type != chess.QUEEN
        or not board.is_capture(played)
    ):
        return None
    board.push(played)
    if reply not in board.legal_moves or not board.is_capture(reply):
        return None
    if reply.to_square != played.to_square or board.piece_at(reply.to_square) != moving:
        return None
    board.push(reply)
    if any(piece.piece_type == chess.QUEEN for piece in board.piece_map().values()):
        return None
    statement = (
        f"{move.played_move.san}以本方后吃掉对方后，"
        f"{_side_text(_opposite(move.side))}随即用{line.moves[0].san}回吃；"
        "双后立即离盘，这一步是兑后，而不是单方面得后。"
    )
    return statement, list(dict.fromkeys(ref for ref in [
        move.played_move.id or f"move:played:{move.index}", line.id, line.moves[0].id,
    ] if ref))


def _verified_quiet_move_order(move: MoveReview) -> tuple[str, list[str]]:
    """Explain a small-gap move order only when the same piece is used later."""
    if (
        move.best_move_uci is None
        or move.best_move_uci == move.played_move.uci
        or move.centipawn_loss is None
        or move.centipawn_loss >= 50
    ):
        return "", []
    line = next((item for item in move.candidate_lines if item.rank == 1), None)
    if line is None or line.first_move.uci != move.best_move_uci:
        return "", []
    board = chess.Board(move.before_fen)
    try:
        played = chess.Move.from_uci(move.played_move.uci)
        best = chess.Move.from_uci(move.best_move_uci)
    except ValueError:
        return "", []
    if played not in board.legal_moves or best not in board.legal_moves:
        return "", []
    played_piece = board.piece_at(played.from_square)
    best_piece = board.piece_at(best.from_square)
    if (
        played_piece is None
        or best_piece is None
        or played_piece != best_piece
        or played_piece.piece_type not in {chess.KNIGHT, chess.BISHOP, chess.ROOK}
        or played.from_square == best.from_square
        or board.is_capture(played)
        or board.is_capture(best)
        or board.gives_check(played)
        or board.gives_check(best)
    ):
        return "", []

    original_square = played.from_square
    for index, item in enumerate(line.moves):
        try:
            route_move = chess.Move.from_uci(item.uci)
        except ValueError:
            return "", []
        if route_move not in board.legal_moves:
            return "", []
        if index > 0 and route_move == played:
            if board.piece_at(original_square) != played_piece:
                return "", []
            piece = _piece_text(chess.piece_name(played_piece.piece_type), move.side)
            statement = (
                f"实战{move.played_move.san}先调动{chess.square_name(original_square)}的{piece}，"
                f"首选{line.first_move.san}先调动{chess.square_name(best.from_square)}的另一枚{piece}。"
                f"首选路线稍后也走{item.san}，但把"
                f"{chess.square_name(best.from_square)}的{piece}安排在前面。"
            )
            return statement, list(dict.fromkeys(ref for ref in [
                move.played_move.id or f"move:played:{move.index}",
                line.id,
                line.first_move.id,
                item.id,
            ] if ref))
        if route_move.from_square == original_square or route_move.to_square == original_square:
            return "", []
        board.push(route_move)
    return "", []


def _verified_strategic_choice(move: MoveReview) -> tuple[str, list[str]]:
    """Explain a flank pawn commitment answered by a verified central exchange."""
    if (
        move.best_move_uci is None
        or move.played_move.uci == move.best_move_uci
        or move.centipawn_loss is None
        or move.centipawn_loss >= 50
        or move.actual_move_line is None
        or len(move.actual_move_line.moves) < 2
    ):
        return "", []

    board = chess.Board(move.before_fen)
    try:
        played = chess.Move.from_uci(move.played_move.uci)
    except ValueError:
        return "", []
    played_piece = board.piece_at(played.from_square)
    played_file = chess.square_file(played.from_square)
    if (
        played not in board.legal_moves
        or played_piece is None
        or played_piece.piece_type != chess.PAWN
        or board.is_capture(played)
        or played_file not in {0, 1, 2, 5, 6, 7}
        or not _has_closed_center(board)
    ):
        return "", []

    board.push(played)
    reply_item, recapture_item = move.actual_move_line.moves[:2]
    try:
        reply = chess.Move.from_uci(reply_item.uci)
        recapture = chess.Move.from_uci(recapture_item.uci)
    except ValueError:
        return "", []
    if reply not in board.legal_moves or not board.is_capture(reply):
        return "", []
    reply_piece = board.piece_at(reply.from_square)
    captured_piece = board.piece_at(reply.to_square)
    if (
        reply_piece is None
        or captured_piece is None
        or reply_piece.piece_type != chess.PAWN
        or captured_piece.piece_type != chess.PAWN
        or captured_piece.color != played_piece.color
        or chess.square_file(reply.to_square) not in {3, 4}
    ):
        return "", []
    removed_square = chess.square_name(reply.to_square)
    board.push(reply)
    if recapture not in board.legal_moves or not board.is_capture(recapture):
        return "", []
    recapturing_piece = board.piece_at(recapture.from_square)
    recaptured_piece = board.piece_at(recapture.to_square)
    if (
        recapturing_piece is None
        or recaptured_piece is None
        or recapturing_piece.color != played_piece.color
        or recapturing_piece.piece_type != chess.PAWN
        or recaptured_piece != reply_piece
        or recapture.to_square != reply.to_square
    ):
        return "", []
    replacement_square = chess.square_name(recapture.from_square)
    board.push(recapture)

    wing = "王翼" if played_file >= 5 else "后翼"
    side = _side_text(move.side)
    opponent = _side_text(_opposite(move.side))
    refs = [
        move.played_move.id or f"move:played:{move.index}",
        move.actual_move_line.id,
        reply_item.id,
        recapture_item.id,
    ]
    statement = (
        f"{move.played_move.san}的棋理价值，是利用封闭中心先在{wing}争取空间；"
        f"但它没有迫使{opponent}在这一翼应战。{opponent}立即以{reply_item.san}换掉"
        f"{removed_square}兵，{side}以{recapture_item.san}回吃后，原来的{removed_square}兵已经消失，"
        f"{replacement_square}兵被带到{removed_square}，中心兵型先发生了转换。"
    )

    followup_items = [
        item
        for item in move.actual_move_line.moves[2:]
        if item.side == move.side and "pawn" not in item.piece
    ][:3]
    if followup_items:
        statement += (
            f"随后路线中，{side}还要用{'、'.join(item.san for item in followup_items)}重新组织子力，"
            f"说明{move.played_move.san}没有立即形成翼侧突破。"
        )
        refs.extend(item.id for item in followup_items)

    best_line = next(
        (line for line in sorted(move.candidate_lines, key=lambda item: item.rank) if line.rank == 1),
        None,
    )
    deferred = None
    if best_line is not None:
        deferred = next(
            (
                (index, item)
                for index, item in enumerate(best_line.moves[1:], start=1)
                if item.uci == move.played_move.uci
            ),
            None,
        )
    best = move.best_move_san or move.best_move_uci
    if deferred is not None and best_line is not None:
        deferred_index, deferred_item = deferred
        preparations = [
            item.san
            for item in best_line.moves[:deferred_index]
            if item.side == move.side and not item.capture
        ][:2]
        if preparations:
            statement += (
                f"首选路线并没有放弃{move.played_move.san}，而是先走{'、'.join(preparations)}，"
                f"之后才推进{deferred_item.san}；所以{best}与实战的真正区别是次序，不是进攻方向。"
            )
            refs.extend([best_line.id, deferred_item.id])
    elif best:
        original = chess.Board(move.before_fen)
        try:
            best_move = chess.Move.from_uci(move.best_move_uci)
        except ValueError:
            best_move = None
        piece = original.piece_at(best_move.from_square) if best_move is not None else None
        piece_name = _piece_name(chess.piece_name(piece.piece_type)) if piece is not None else "子力"
        statement += (
            f"因此，{best}选择先调整{piece_name}并保留{wing}兵型；"
            f"{move.played_move.san}则先固定兵型，让{opponent}在实战路线中率先转换中心。"
        )
        if best_line is not None:
            refs.append(best_line.id)
    return statement, list(dict.fromkeys(refs))


def _has_closed_center(board: chess.Board) -> bool:
    """Return true when opposing pawns block each other on both central files."""
    locked_files = 0
    for file_index in (3, 4):
        locked = False
        for rank in range(1, 7):
            lower = chess.square(file_index, rank)
            upper = chess.square(file_index, rank + 1)
            if (
                board.piece_at(lower) == chess.Piece(chess.PAWN, chess.WHITE)
                and board.piece_at(upper) == chess.Piece(chess.PAWN, chess.BLACK)
            ):
                locked = True
                break
        if locked:
            locked_files += 1
    return locked_files == 2


def _verified_queenless_king_activity(
    move: MoveReview,
) -> tuple[str, list[str]] | None:
    """Connect an immediate queen trade to the same knight and both kings' route."""
    line = move.actual_move_line
    if (
        line is None or not line.verified or len(line.moves) < 5
        or move.best_move_uci is None
    ):
        return None
    before = chess.Board(move.before_fen)
    actual = chess.Board(move.after_fen)
    try:
        best = chess.Move.from_uci(move.best_move_uci)
        reply = chess.Move.from_uci(line.moves[0].uci)
        recapture = chess.Move.from_uci(line.moves[1].uci)
    except ValueError:
        return None
    if best not in before.legal_moves or reply not in actual.legal_moves:
        return None
    best_piece = before.piece_at(best.from_square)
    if best_piece is None or best_piece.piece_type != chess.KNIGHT:
        return None
    best_board = before.copy(stack=False)
    best_board.push(best)
    if sum(piece.piece_type == chess.QUEEN for piece in best_board.piece_map().values()) != 2:
        return None
    actual.push(reply)
    if recapture not in actual.legal_moves:
        return None
    recapturing_piece = actual.piece_at(recapture.from_square)
    if (
        recapturing_piece != best_piece
        or recapture.from_square != best.from_square
        or recapture.to_square != best.to_square
    ):
        return None
    actual.push(recapture)
    if any(piece.piece_type == chess.QUEEN for piece in actual.piece_map().values()):
        return None

    mover_color = chess.WHITE if move.side == "white" else chess.BLACK
    opponent_color = not mover_color
    own_king_square = actual.king(mover_color)
    opponent_king_square = actual.king(opponent_color)
    if own_king_square is None or opponent_king_square is None:
        return None
    king_squares = [opponent_king_square]
    route_refs: list[str] = []
    for item in line.moves[2:8]:
        try:
            route_move = chess.Move.from_uci(item.uci)
        except ValueError:
            return None
        if route_move not in actual.legal_moves:
            return None
        actor = actual.piece_at(route_move.from_square)
        if actor is None:
            return None
        if actor.piece_type == chess.KING and actor.color == opponent_color:
            if route_move.from_square != king_squares[-1]:
                return None
            king_squares.append(route_move.to_square)
            if item.id:
                route_refs.append(item.id)
        actual.push(route_move)
        if len(king_squares) == 3:
            break
    if len(king_squares) != 3 or actual.king(mover_color) != own_king_square:
        return None
    center = (chess.D4, chess.E4, chess.D5, chess.E5)
    distances = [min(chess.square_distance(square, target) for target in center) for square in king_squares]
    if not distances[0] > distances[1] > distances[2]:
        return None

    knight_origin = chess.square_name(best.from_square)
    knight_target = chess.square_name(best.to_square)
    king_path = "—".join(chess.square_name(square) for square in king_squares)
    statement = (
        f"首选{move.best_move_san or move.best_move_uci}直接把{knight_origin}的"
        f"{_piece_text('knight', move.side)}调到{knight_target}，双后仍在棋盘上。"
        f"实战{move.played_move.san}则经过{line.moves[0].san}、{line.moves[1].san}，"
        f"让同一匹马来到{knight_target}时双后已经离盘。"
        f"无后局面中王也能参加争夺：接下来的变化里，"
        f"{'黑王' if move.side == 'white' else '白王'}沿{king_path}向中心靠近，"
        f"{'白王' if move.side == 'white' else '黑王'}还留在"
        f"{chess.square_name(own_king_square)}。"
    )
    best_line = next((item for item in move.candidate_lines if item.rank == 1), None)
    refs = [
        move.played_move.id or f"move:played:{move.index}",
        line.id, line.moves[0].id, line.moves[1].id,
        best_line.id if best_line is not None else "",
        *route_refs,
    ]
    return statement, list(dict.fromkeys(ref for ref in refs if ref))


def _verified_forcing_consequence(move: MoveReview) -> tuple[str, list[str]]:
    """Explain only material consequences proved by one legal Stockfish route."""
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
        original = chess.Board(move.before_fen)
        played_move = chess.Move.from_uci(move.played_move.uci)
        played_piece = original.piece_at(played_move.from_square)
        first_item, first_board_move, first_actor, first_victim = capture_events[0]
        second_item, second_board_move, second_actor, second_victim = capture_events[1]
        if (
            played_piece is not None
            and played_piece.piece_type == chess.QUEEN
            and first_actor.piece_type == chess.QUEEN
            and first_victim == played_piece
            and second_victim == first_actor
            and second_board_move.to_square == first_board_move.to_square
            and not any(piece.piece_type == chess.QUEEN for piece in board.piece_map().values())
        ):
            activity = _verified_queenless_king_activity(move)
            if activity is not None:
                statement, activity_refs = activity
                refs.extend(activity_refs)
            else:
                statement = (
                    f"{move.played_move.san}把本方后放到{move.played_move.to_square}，"
                    f"{_side_text(_opposite(move.side))}立即以{first_item.san}吃后，"
                    f"{_side_text(move.side)}再用{second_item.san}回吃；双方的后都离开棋盘。"
                )
            best_line = next((item for item in move.candidate_lines if item.rank == 1), None)
            if best_line is not None:
                refs.append(best_line.id)
            return statement, list(dict.fromkeys(ref for ref in refs if ref))
        if (
            played_piece is not None
            and played_piece.piece_type == chess.PAWN
            and move.played_move.to_square[0] in {"d", "e"}
            and first_actor.piece_type == chess.PAWN
            and first_victim == played_piece
            and first_board_move.to_square == played_move.to_square
            and second_board_move.to_square == first_board_move.to_square
            and second_victim == first_actor
            and len(capture_events) >= 4
            and {chess.KNIGHT, chess.BISHOP} <= {
                victim.piece_type for _, _, _, victim in capture_events
            }
        ):
            mover = _side_text(move.side)
            opponent = _side_text(_opposite(move.side))
            statement = (
                f"实战{move.played_move.san}把{mover}原在{move.played_move.from_square}的兵"
                f"推到{move.played_move.to_square}，{opponent}立即以{first_item.san}吃掉它；"
                f"{mover}{second_item.san}回吃。随后马、象继续交换，"
                f"原在{move.played_move.from_square}的{mover}兵和吃它的{opponent}兵都离开棋盘。"
            )
            alternative = _verified_alternative_pawn_trade(move, first_board_move)
            if alternative is not None:
                statement += alternative[0]
                refs.extend(alternative[1])
            return statement, list(dict.fromkeys(ref for ref in refs if ref))
        statement = (
            f"{move.played_move.san}后，对手立即发起连续交换："
            f"{'；'.join(descriptions)}。这串交换结束后双方没有净得子力；"
            "原先站在棋盘上的子力和兵已有多枚离开，后续计划要从这个新局面展开。"
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
            f"{move.played_move.san}后，验证变化中，{descriptions[0]}；"
            "不过算完整条路线，这次吃子并没有形成可保留的物质收益。"
            "因此这步的缺点在交换后的局面，而不能简单说成直接丢兵或丢子。"
        )
        return statement, list(dict.fromkeys(refs))

    final_gain = abs(full_balance)
    final_gain_text = "一兵" if final_gain == 1 else f"约{final_gain}分子力"
    retained = (
        "直到验证路线结束，这项收益也没有被追回。"
        if final_gain == initial_gain
        else f"算完整条验证路线，{_side_text(opponent_side)}仍保留{final_gain_text}的吃子收益。"
    )
    clearance = _verified_line_clearance_mechanism(capture_events)
    if len(descriptions) == 1:
        statement = (
            f"{move.played_move.san}的问题在于对手可以立即兑现子力收益："
            f"{descriptions[0]}。{_side_text(opponent_side)}净得{initial_gain_text}。{retained}"
        )
    else:
        statement = (
            f"{move.played_move.san}的问题可以由紧接着的强制交换具体说明："
            f"{'；'.join(descriptions)}。{clearance}这串连续吃子结束后，"
            f"{_side_text(opponent_side)}净得{initial_gain_text}。{retained}"
        )
    return statement, list(dict.fromkeys(refs))


def _verified_alternative_pawn_trade(
    move: MoveReview,
    actual_reply: chess.Move,
) -> tuple[str, list[str]] | None:
    """Compare which opponent pawn moves in the best and practical pawn trades."""
    best_line = next((line for line in move.candidate_lines if line.rank == 1), None)
    if (
        best_line is None or len(best_line.moves) < 2
        or best_line.first_move.uci != move.best_move_uci
    ):
        return None
    before = chess.Board(move.before_fen)
    try:
        best = chess.Move.from_uci(best_line.moves[0].uci)
        reply = chess.Move.from_uci(best_line.moves[1].uci)
    except ValueError:
        return None
    if best not in before.legal_moves or not before.is_capture(best):
        return None
    own_pawn = before.piece_at(best.from_square)
    target_pawn = before.piece_at(best.to_square)
    if (
        own_pawn != chess.Piece(chess.PAWN, before.turn)
        or target_pawn != chess.Piece(chess.PAWN, not before.turn)
    ):
        return None
    before.push(best)
    reply_pawn = before.piece_at(reply.from_square)
    if (
        reply not in before.legal_moves or not before.is_capture(reply)
        or reply.to_square != best.to_square
        or reply_pawn != target_pawn
        or before.piece_at(reply.to_square) != own_pawn
        or reply.from_square == actual_reply.from_square
    ):
        return None
    before.push(reply)
    if before.piece_at(reply.to_square) != reply_pawn:
        return None
    return (
        f"首选{best_line.moves[0].san}先吃掉{chess.square_name(best.to_square)}的"
        f"{_piece_text('pawn', _opposite(move.side))}；"
        f"{_side_text(_opposite(move.side))}{best_line.moves[1].san}后，"
        f"原在{chess.square_name(reply.from_square)}的兵来到"
        f"{chess.square_name(reply.to_square)}。"
        f"实战换掉的则是原在{chess.square_name(actual_reply.from_square)}的兵，"
        "两条路线留下了不同的中心兵形。",
        [ref for ref in (
            best_line.id, best_line.moves[0].id, best_line.moves[1].id,
        ) if ref],
    )


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
            f"因此它随后能以{later_item.san}侵入并吃掉"
            f"{_piece_text(chess.piece_name(later_captured.piece_type), victim_side)}。"
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


def _non_capture_reply_pressure_claim(
    move: MoveReview,
) -> tuple[str, list[str]] | None:
    """Explain a quiet reply only when the PV verifies its concrete pressure.

    A non-capture such as a knight jump can be the real punishment even though
    no material changes hands on that ply.  We only promote it to a causal
    claim when python-chess proves either an immediate answering tempo against
    an important piece or a later capture by that same replying piece of a
    target that the reply newly attacked.
    """
    line = move.actual_move_line
    inferior = bool(
        line
        and line.verified
        and len(line.moves) >= 2
        and move.best_move_uci
        and move.played_move.uci != move.best_move_uci
        and move.centipawn_loss is not None
        and move.centipawn_loss >= 50
    )
    if not inferior or line is None:
        return None

    reply_item, response_item = line.moves[:2]
    if reply_item.capture:
        return None
    try:
        board = chess.Board(move.after_fen)
        reply = chess.Move.from_uci(reply_item.uci)
        response = chess.Move.from_uci(response_item.uci)
    except ValueError:
        return None
    if reply not in board.legal_moves:
        return None

    replying_piece = board.piece_at(reply.from_square)
    if replying_piece is None:
        return None
    attacks_before = set(board.attacks(reply.from_square))
    board.push(reply)
    if response not in board.legal_moves:
        return None

    attacks_after = set(board.attacks(reply.to_square))
    newly_attacked: list[tuple[int, chess.Square, chess.Piece]] = []
    for square in attacks_after - attacks_before:
        target = board.piece_at(square)
        if (
            target is None
            or target.color == replying_piece.color
            or target.piece_type == chess.KING
        ):
            continue
        newly_attacked.append((_PIECE_VALUES[target.piece_type], square, target))
    newly_attacked.sort(key=lambda item: (-item[0], item[1]))
    if not newly_attacked:
        return None

    answered = next(
        (
            (value, square, target)
            for value, square, target in newly_attacked
            if response.from_square == square and target.piece_type != chess.PAWN
        ),
        None,
    )
    if answered is None:
        tracked_square = reply.to_square
        tracked_piece = replying_piece
        target_by_square = {
            square: (value, target) for value, square, target in newly_attacked
        }
        replayed_items = [reply_item]
        for item in line.moves[1:]:
            try:
                route_move = chess.Move.from_uci(item.uci)
            except ValueError:
                return None
            if route_move not in board.legal_moves:
                return None

            moving_piece = board.piece_at(route_move.from_square)
            captured_piece = board.piece_at(route_move.to_square)
            replayed_items.append(item)
            if route_move.from_square == tracked_square:
                if moving_piece != tracked_piece:
                    return None
                target = target_by_square.get(route_move.to_square)
                if (
                    target is None
                    or not board.is_capture(route_move)
                    or captured_piece != target[1]
                ):
                    return None

                reply_side = "white" if replying_piece.color == chess.WHITE else "black"
                target_side = "white" if captured_piece.color == chess.WHITE else "black"
                reply_piece_text = _piece_text(
                    chess.piece_name(replying_piece.piece_type), reply_side
                )
                target_piece_text = _piece_text(
                    chess.piece_name(captured_piece.piece_type), target_side
                )
                bridge = "、".join(route.san for route in replayed_items[1:-1])
                bridge_text = f"经过{bridge}后，" if bridge else "随后"
                statement = (
                    f"{move.played_move.san}之后，{_side_text(reply_side)}可以先用"
                    f"{reply_item.san}把{reply_piece_text}从{reply_item.from_square}转到"
                    f"{reply_item.to_square}，瞄住{chess.square_name(route_move.to_square)}的"
                    f"{target_piece_text}。{bridge_text}还是这枚"
                    f"{_piece_name(chess.piece_name(replying_piece.piece_type))}以"
                    f"{item.san}吃掉该子；这说明{reply_item.san}不是单纯调子，而是在为"
                    f"{item.san}改善落点。{_side_text(reply_side)}先争到攻击节奏，"
                    "随后在这条变化里兑现为子力收获。"
                )
                return statement, [line.id, *[route.id for route in replayed_items]]

            if route_move.to_square == tracked_square and board.is_capture(route_move):
                return None
            if route_move.from_square in target_by_square:
                target_by_square.pop(route_move.from_square, None)
            if route_move.to_square in target_by_square and board.is_capture(route_move):
                target_by_square.pop(route_move.to_square, None)
            board.push(route_move)
        return None

    _, target_square, target_piece = answered
    reply_side = "white" if replying_piece.color == chess.WHITE else "black"
    target_side = "white" if target_piece.color == chess.WHITE else "black"
    reply_piece_text = _piece_text(chess.piece_name(replying_piece.piece_type), reply_side)
    target_piece_text = _piece_text(chess.piece_name(target_piece.piece_type), target_side)
    target_label = f"{chess.square_name(target_square)}的{target_piece_text}"
    other_targets = [
        f"{chess.square_name(square)}的{_piece_text(chess.piece_name(target.piece_type), target_side)}"
        for _, square, target in newly_attacked
        if square != target_square
    ][:1]
    targets_text = "和".join([target_label, *other_targets])

    if board.is_capture(response) and response.to_square == reply.to_square:
        response_text = (
            f"{_side_text(target_side)}随后以{response_item.san}吃掉这枚"
            f"{_piece_name(chess.piece_name(replying_piece.piece_type))}，直接处理这次攻击"
        )
    else:
        response_text = (
            f"{_side_text(target_side)}随后以{response_item.san}把"
            f"{target_piece_text}移出这枚{_piece_name(chess.piece_name(replying_piece.piece_type))}的攻击范围"
        )
    statement = (
        f"{move.played_move.san}的问题在于给了对手一个带攻击的主动节奏："
        f"{reply_item.san}让{reply_piece_text}从{reply_item.from_square}来到"
        f"{reply_item.to_square}，新增攻击{targets_text}。{response_text}；"
        f"因此{_side_text(reply_side)}在调动{reply_piece_text}的同时，"
        f"让{_side_text(target_side)}先回应对重要子力的攻击。"
    )
    return statement, [line.id, reply_item.id, response_item.id]


def _evaluation_advantage_side(move: MoveReview) -> str | None:
    if move.before.mate_in is not None:
        return "white" if move.before.mate_in > 0 else "black"
    if move.before.centipawn is None or abs(move.before.centipawn) <= 100:
        return None
    return "white" if move.before.centipawn > 0 else "black"


def _evaluation_posture_statement(move: MoveReview) -> str:
    """Turn the pre-move engine score into a number-free global posture."""
    mate_in = move.before.mate_in
    if mate_in is not None:
        side = "白方" if mate_in > 0 else "黑方"
        return f"{side}已有强制将杀。"
    centipawn = move.before.centipawn
    if centipawn is None:
        return "当前缺少足以判断优势归属的可靠评价。"
    if abs(centipawn) <= 25:
        return "双方机会大致相当。"
    side = "白方" if centipawn > 0 else "黑方"
    if abs(centipawn) <= 100:
        return f"{side}略优，局面仍有回旋余地。"
    if abs(centipawn) <= 300:
        return f"{side}优势明显。"
    return f"{side}已取得决定性优势。"


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
