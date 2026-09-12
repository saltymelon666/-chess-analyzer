from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

from .models import MoveReview, ProfessionalAnalysis, VariationMove
from .strategic_plans import StrategicPlanPackage
from .threat_analysis import ThreatPackage


NARRATIVE_CLAIM_VERSION = "1.1"
LEGACY_NARRATIVE_MARKERS = (
    "先看全局：",
    "实战把选择摆上棋盘：",
    "再看关键选择：",
    "从计划看，已经得到验证的方向是：",
    "这段变化留给初学者的原则是：",
)
NarrativeClaimKind = Literal[
    "position_fact",
    "move_event",
    "move_effect",
    "evaluation_comparison",
    "opponent_resource",
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
        "正文按棋理自然推进，不显示固定步骤标题或栏目口号。"
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
            f"更具体地说，{fact.description}",
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
            comparison = (
                f"引擎首选{move.best_move_san or move.best_move_uci}，而实战走了"
                f"{move.played_move.san}；现有数据没有提供可靠分差，因此不能"
                "从分数倒推两种选择的机制差异。"
            )
        elif move.centipawn_loss < 50:
            comparison = (
                f"引擎把{move.played_move.san}与{move.best_move_san or move.best_move_uci}"
                "放得很近，评价差距不足以支持“这步必须受罚”的说法。"
            )
        else:
            comparison = (
                f"分歧从这里出现：与首选{move.best_move_san or move.best_move_uci}相比，"
                f"{move.played_move.san}使评价下降约{move.centipawn_loss / 100:.2f}兵。"
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
            "当前没有可验证的首选路线，实战着与候选着也就不能作可靠比较。",
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
                f"惩罚首先以具体着法出现：{move.played_move.san}之后，"
                f"{_side_text(_opposite(move.side))}最强回应是{reply.san}，这一步"
                f"{reply_detail}。它和评价下降同时得到验证，但现有数据不足以证明"
                "这次吃子解释了全部分差。"
            )
        elif inferior:
            detail = f"，这一步{reply_detail}" if reply_detail else ""
            reply_statement = (
                f"分岔口出现在对手的回答上：{move.played_move.san}之后，"
                f"{_side_text(_opposite(move.side))}最强回应是{reply.san}{detail}。"
                "当前能够确认的是评价差和整条验证路线；如果关键事件出现在后续，"
                "就不能倒推成第一回应的直接效果。"
            )
        else:
            detail = f"，这一步{reply_detail}" if reply_detail else ""
            reply_statement = (
                f"这一步并没有结束争论：在{move.played_move.san}后的验证路线中，"
                f"{_side_text(_opposite(move.side))}首先以{reply.san}回应{detail}。"
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
        "plan": [item.statement for item in selected if item.kind == "verified_plan"],
        "teaching": [item.statement for item in selected if item.kind == "teaching_rule"],
    }
    paragraphs: list[str] = []
    if groups["position"]:
        paragraphs.append("".join(groups["position"]))
    if groups["move"]:
        paragraphs.append("".join(groups["move"]))
    if groups["verdict"]:
        paragraphs.append("".join(groups["verdict"]))
    if groups["plan"]:
        paragraphs.append(
            "后续计划只有在变化支持时才站得住；这里得到验证的方向是："
            + "".join(groups["plan"])
        )
    if groups["teaching"]:
        teaching_text = "".join(groups["teaching"])
        if "所有将军" in teaching_text:
            lead = "这段变化提醒我们，强制着的检查次序不能颠倒："
        elif "连续交换" in teaching_text:
            lead = "这里真正值得记住的不是一次吃子，而是计算次序："
        elif "完成易位" in teaching_text:
            lead = "易位只是动作，随后的安全与协调才是检验："
        elif "升变" in teaching_text:
            lead = "兵走到终点并不代表计算结束："
        else:
            quiet_leads = (
                "这里值得带走的不是需要背诵的答案，而是一种检查习惯：",
                "把这一步真正学会，关键不在记住着法，而在记住检查顺序：",
                "下一次遇到相似局面，可以先从同一个问题入手：",
            )
            move_surface = "".join(groups["move"])
            lead = quiet_leads[sum(ord(character) for character in move_surface) % len(quiet_leads)]
        paragraphs.append(lead + teaching_text)
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
    teaching_claim = next(
        (item for item in package.claims if item.kind == "teaching_rule"), None,
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
            reply_claim if needs_concrete_reply else None,
            teaching_claim,
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
        "opponent_resource": 4,
        "verified_plan": 5,
        "teaching_rule": 6,
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


def _teaching_statement(
    move: MoveReview,
    has_event: bool,
    has_target: bool,
    has_defense: bool,
    has_control: bool,
) -> str:
    if move.played_move.check or move.played_move.checkmate:
        return "先检查所有将军和直接威胁，再研究较慢的计划。"
    if move.played_move.capture:
        return "先把连续交换逐步算清，再判断眼前收益能否保住。"
    if move.played_move.castling:
        return "完成易位后先核对王的安全，再看车是否顺利参加战斗。"
    if move.played_move.promotion:
        return "升变后先检查新棋子的将军、攻击和对手最强回应。"
    if has_target:
        return "先检查落子后新增攻击哪些具体目标，再计算对手最强回应。"
    if has_defense:
        return "先比较落子前后的保护关系，再判断防守是否真正改善。"
    if has_control:
        return "先比较落子前后新增和失去的控制格，再判断这步是否配合当前计划。"
    return "先核对这步实际改变的格子和对手最强回应，再评价它的战略意义。"


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
