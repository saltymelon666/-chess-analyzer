from __future__ import annotations

import hashlib
from typing import Literal, TYPE_CHECKING

import chess
from pydantic import BaseModel, ConfigDict, Field

from .strategic_plans import StrategicPlanPackage
from .threat_analysis import InitiativeAssessment, ThreatPackage, assess_initiative

if TYPE_CHECKING:
    from .chess_facts import ChessFactPackage
    from .models import PositionFacts


POSITION_INTERPRETATION_VERSION = "1.1"
EvaluationDirection = Literal["white", "black", "equal", "unknown"]
EvaluationSource = Literal[
    "material",
    "tactics",
    "king_safety",
    "activity",
    "structure",
    "space",
    "unknown",
]
EvaluationStrength = Literal[
    "equal",
    "slight_edge",
    "clear_edge",
    "winning",
    "unknown",
]
InitiativeSide = Literal["white", "black", "unknown"]
PositionPhase = Literal["opening", "middlegame", "endgame"]
AnalysisObjectiveKind = Literal[
    "forcing_tactics",
    "winning_conversion",
    "attack_conversion",
    "endgame_plan",
    "dynamic_balance",
    "move_quality_explanation",
    "strategic_improvement",
]


class ThemeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str = Field(min_length=1)
    side: Literal["white", "black", "both"]
    scope: Literal["current_position", "candidate_route"] = "current_position"
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: Literal["medium", "high"]


class InterpretationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    side: Literal["white", "black"]
    type: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    evidence_route_ids: list[str] = Field(default_factory=list)
    structural_evidence: list[str] = Field(default_factory=list)
    confidence: Literal["medium", "high"]


class EvaluationBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: EvaluationDirection
    strength: EvaluationStrength
    allowed_wording: str = Field(min_length=1)
    forbidden_wording: list[str] = Field(default_factory=list)


class AnalysisObjective(BaseModel):
    """Program-selected question that the generated explanation must answer first."""

    model_config = ConfigDict(extra="forbid")

    kind: AnalysisObjectiveKind
    focus_side: Literal["white", "black", "both"]
    primary_question: str = Field(min_length=1)
    priority_topics: list[str] = Field(default_factory=list)
    deemphasized_topics: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class PositionInterpretationPackage(BaseModel):
    """Program-owned bridge between verified facts and language generation."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["1.1"] = POSITION_INTERPRETATION_VERSION
    position_id: str = Field(min_length=1)
    position_phase: PositionPhase
    objective: AnalysisObjective
    evaluation_source: EvaluationSource
    evaluation: EvaluationBoundary
    themes: list[ThemeEvidence] = Field(default_factory=list)
    plans: list[InterpretationPlan] = Field(default_factory=list)
    initiative: InitiativeAssessment
    forbidden_claims: list[str] = Field(default_factory=list)

    def prompt_payload(self) -> dict[str, object]:
        """Return only the interpretation contract intended for a language model."""
        return self.model_dump(exclude={"version"})


def build_position_interpretation(
    package: "ChessFactPackage",
    *,
    position_facts: "PositionFacts | None" = None,
    threat_package: ThreatPackage | None = None,
    plan_package: StrategicPlanPackage | None = None,
) -> PositionInterpretationPackage:
    """Combine verified packages without allowing the model to invent themes."""
    threats = threat_package or ThreatPackage(
        position_id=package.position.fen,
        threats=package.threats,
    )
    plans = plan_package or StrategicPlanPackage(
        position_id=package.position.fen,
        plans=package.plans,
    )
    initiative = assess_initiative(package, threats)
    themes = _themes(package, position_facts, threats, plans)
    phase = _position_phase(package.position.fen)
    source = _evaluation_source(package, threats, themes, phase=phase)
    boundary = _evaluation_boundary(package)
    objective = _analysis_objective(
        package,
        position_facts=position_facts,
        threats=threats,
        themes=themes,
        plans=plans,
        phase=phase,
        boundary=boundary,
    )
    forbidden = _forbidden_claims(package, boundary, initiative, themes)
    return PositionInterpretationPackage(
        position_id=_position_id(package.position.fen),
        position_phase=phase,
        objective=objective,
        evaluation_source=source,
        evaluation=boundary,
        themes=themes,
        plans=[
            InterpretationPlan(
                plan_id=plan.plan_id,
                side=plan.side,
                type=plan.type,
                goal=plan.goal,
                evidence_route_ids=plan.evidence_route_ids,
                structural_evidence=plan.structural_evidence,
                confidence=plan.confidence,
            )
            for plan in plans.plans
        ],
        initiative=initiative,
        forbidden_claims=forbidden,
    )


def _themes(
    package: "ChessFactPackage",
    position_facts: "PositionFacts | None",
    threats: ThreatPackage,
    plans: StrategicPlanPackage,
) -> list[ThemeEvidence]:
    result: list[ThemeEvidence] = []
    facts = position_facts
    if facts is not None:
        mappings = {
            "nearby_attackers": "king_safety",
            "king_near_open_file": "king_safety",
            "verified_outpost": "space",
            "central_piece": "space",
            "constrained_piece": "worst_piece",
            "undefended_piece": "coordination",
            "underprotected": "coordination",
            "attacked": "coordination",
            "isolated_pawn": "pawn_structure",
            "backward_pawn": "pawn_structure",
            "passed_pawn": "pawn_structure",
        }
        grouped: dict[tuple[str, str], list[object]] = {}
        for fact in [*facts.king_safety, *facts.piece_activity, *facts.pawn_structure]:
            theme = mappings.get(fact.category)
            if theme is None:
                continue
            grouped.setdefault((theme, fact.side), []).append(fact)
        for (theme, side), items in sorted(grouped.items()):
            result.append(
                ThemeEvidence(
                    theme=theme,
                    side=side,
                    evidence_ids=[item.id for item in items],
                    evidence=[item.description for item in items],
                    confidence="high",
                )
            )
        for side, target_rank in (("white", "7"), ("black", "2")):
            rooks = [
                piece
                for piece in facts.pieces
                if piece.get("side") == side
                and piece.get("piece") == "rook"
                and str(piece.get("square", "")).endswith(target_rank)
            ]
            if len(rooks) >= 2:
                result.append(
                    ThemeEvidence(
                        theme="seventh_rank_activity",
                        side=side,
                        evidence_ids=[piece["id"] for piece in rooks if piece.get("id")],
                        evidence=[
                            f"{'白方' if side == 'white' else '黑方'}两辆车位于第七横线"
                        ],
                        confidence="high",
                    )
                )
        root_moves = {
            route.moves_uci[0]
            for route in package.candidate_routes
            if route.verified and route.moves_uci
        }
        if package.actual_move is not None:
            root_moves.add(package.actual_move.uci)
        tactical_categories = {"double_attack", "pin", "skewer", "tactical_sacrifice"}
        route_tactical_count = 0
        for fact in facts.threats:
            if fact.category not in tactical_categories:
                continue
            verified_move = next(
                (
                    evidence.removeprefix("python-chess验证走法")
                    for evidence in fact.evidence
                    if evidence.startswith("python-chess验证走法")
                ),
                None,
            )
            if verified_move is None:
                continue
            is_root = verified_move in root_moves
            if not is_root and route_tactical_count >= 3:
                continue
            result.append(
                ThemeEvidence(
                    theme=fact.category if is_root else f"route_{fact.category}",
                    side=fact.side if fact.side in {"white", "black"} else "both",
                    scope="current_position" if is_root else "candidate_route",
                    evidence_ids=[fact.id],
                    evidence=[fact.description],
                    confidence="high",
                )
            )
            if not is_root:
                route_tactical_count += 1
    direct_threats = [
        item for item in threats.threats
        if item.scope == "current_direct_threat"
    ]
    if direct_threats:
        result.append(
            ThemeEvidence(
                theme="current_threat",
                side=direct_threats[0].side,
                evidence_ids=[item.threat_id for item in direct_threats],
                evidence=[item.evidence[0] for item in direct_threats],
                confidence="high",
            )
        )
    if threats.prepared_threats:
        result.append(
            ThemeEvidence(
                theme="prepared_threat",
                side=threats.prepared_threats[0].side,
                evidence_ids=[item.threat_id for item in threats.prepared_threats],
                evidence=[item.evidence[0] for item in threats.prepared_threats],
                confidence="high",
            )
        )
    for plan in plans.plans:
        result.append(
            ThemeEvidence(
                theme=plan.type,
                side=plan.side,
                evidence_ids=[*plan.evidence_route_ids],
                evidence=[*plan.structural_evidence],
                confidence=plan.confidence,
            )
        )
    return result


def _position_id(fen: str) -> str:
    """Use an opaque identifier so the FEN never crosses the model boundary."""
    return f"position:{hashlib.sha256(fen.encode('utf-8')).hexdigest()[:16]}"


def _evaluation_source(
    package: "ChessFactPackage",
    threats: ThreatPackage,
    themes: list[ThemeEvidence],
    *,
    phase: PositionPhase,
) -> EvaluationSource:
    if threats.threats:
        return "tactics"
    theme_names = {theme.theme for theme in themes}
    if {"double_attack", "pin", "skewer", "tactical_sacrifice"} & theme_names:
        return "tactics"
    if "king_safety" in theme_names and phase != "endgame":
        return "king_safety"
    if {"pawn_structure", "create_passed_pawn"} & theme_names:
        return "structure"
    if {"worst_piece", "coordination", "activate_rook"} & theme_names:
        return "activity"
    difference = package.evaluation.evaluation_cp
    if difference is not None and difference == 0:
        return "material"
    return "unknown"


def _position_phase(fen: str) -> PositionPhase:
    board = chess.Board(fen)
    non_pawn_material = sum(
        len(board.pieces(piece_type, color)) * value
        for color in (chess.WHITE, chess.BLACK)
        for piece_type, value in (
            (chess.KNIGHT, 3),
            (chess.BISHOP, 3),
            (chess.ROOK, 5),
            (chess.QUEEN, 9),
        )
    )
    queens = len(board.pieces(chess.QUEEN, chess.WHITE)) + len(board.pieces(chess.QUEEN, chess.BLACK))
    if non_pawn_material <= 16 or (queens == 0 and non_pawn_material <= 24):
        return "endgame"
    if board.fullmove_number <= 12:
        return "opening"
    return "middlegame"


def _analysis_objective(
    package: "ChessFactPackage",
    *,
    position_facts: "PositionFacts | None",
    threats: ThreatPackage,
    themes: list[ThemeEvidence],
    plans: StrategicPlanPackage,
    phase: PositionPhase,
    boundary: EvaluationBoundary,
) -> AnalysisObjective:
    current_threats = [item for item in threats.threats if item.scope == "current_direct_threat"]
    if current_threats:
        side = current_threats[0].side
        return AnalysisObjective(
            kind="forcing_tactics",
            focus_side=side,
            primary_question="先解释当前强制手段、对手必须处理的后果以及验证路线。",
            priority_topics=["当前直接威胁", "强制应对", "路线后果"],
            deemphasized_topics=["与强制路线无关的静态小特征"],
            evidence_ids=[item.threat_id for item in current_threats],
        )

    material_id = ""
    material_advantage = "equal"
    if position_facts is not None:
        material_id = str(position_facts.material.get("id", ""))
        material_advantage = str(position_facts.material.get("advantage", "equal"))
    if (
        boundary.strength in {"equal", "slight_edge"}
        and material_advantage in {"white", "black"}
        and (
            boundary.strength == "equal"
            or material_advantage != boundary.direction
        )
    ):
        return AnalysisObjective(
            kind="dynamic_balance",
            focus_side="both",
            primary_question="先解释静态物质与子力活动之间为何形成动态平衡，再比较双方最实际的计划。",
            priority_topics=["活跃子力", "侵入线路", "限制对方反击", "候选着差异"],
            deemphasized_topics=["无强制证据的主动权", "夸大着法错误"],
            evidence_ids=[material_id] if material_id else [],
        )

    king_targets = {theme.side for theme in themes if theme.theme == "king_safety"}
    if boundary.strength == "winning" and boundary.direction in {"white", "black"}:
        disadvantaged = "black" if boundary.direction == "white" else "white"
        if phase != "endgame" and disadvantaged in king_targets:
            king_evidence = [
                evidence_id
                for theme in themes
                if theme.theme == "king_safety" and theme.side == disadvantaged
                for evidence_id in theme.evidence_ids
            ]
            return AnalysisObjective(
                kind="attack_conversion",
                focus_side=boundary.direction,
                primary_question="先解释优势方如何延续对王的进攻、各棋子如何配合以及对手的关键防守资源。",
                priority_topics=["攻击子力配合", "进攻线路", "关键防守资源", "兑现顺序"],
                deemphasized_topics=["与攻势无关的兵形", "单个棋子的泛化改善"],
                evidence_ids=king_evidence,
            )
        return AnalysisObjective(
            kind="winning_conversion",
            focus_side=boundary.direction,
            primary_question="先解释优势方如何兑现胜势，以及防守方为何难以阻止这一计划。",
            priority_topics=(
                ["王和子力活动", "通路兵或固定弱点", "两翼转换", "简化时机"]
                if phase == "endgame"
                else ["优势来源", "限制反击", "有利交换", "兑现路线"]
            ),
            deemphasized_topics=["防守方无关紧要的局部改善", "无证据的主动权"],
            evidence_ids=[],
        )

    if phase == "endgame":
        endgame_evidence = [
            evidence_id
            for theme in themes
            if theme.theme in {"pawn_structure", "coordination", "worst_piece", "space"}
            for evidence_id in theme.evidence_ids
        ][:8]
        return AnalysisObjective(
            kind="endgame_plan",
            focus_side="both",
            primary_question="先解释通路兵、王的活动和子力协调如何决定残局计划。",
            priority_topics=["通路兵", "王的活动", "最差棋子", "攻击固定弱点"],
            deemphasized_topics=["常规护王", "易位历史", "没有强制证据的攻击语言"],
            evidence_ids=endgame_evidence,
        )

    current_tactics = [
        theme for theme in themes
        if theme.scope == "current_position"
        and theme.theme in {"double_attack", "pin", "skewer", "tactical_sacrifice"}
    ]
    if current_tactics:
        return AnalysisObjective(
            kind="forcing_tactics",
            focus_side=current_tactics[0].side,
            primary_question="先解释当前战术构想成立的原因、对手回应和路线后果。",
            priority_topics=["战术目标", "强制回应", "交换或牺牲的回报"],
            deemphasized_topics=["与战术无关的普通发展"],
            evidence_ids=[item for theme in current_tactics for item in theme.evidence_ids],
        )

    if package.actual_move is not None and package.actual_move.loss is not None:
        return AnalysisObjective(
            kind="move_quality_explanation",
            focus_side=package.position.side_to_move,
            primary_question="先比较实战着与首选路线解决问题的方式，并按真实评价差控制批评强度。",
            priority_topics=["实战着意图", "首选路线差异", "丢失的活动或时间"],
            deemphasized_topics=["没有路线支持的战术惩罚", "夸大错误等级"],
            evidence_ids=[],
        )

    if plans.plans:
        plan = plans.plans[0]
        return AnalysisObjective(
            kind="strategic_improvement",
            focus_side=plan.side,
            primary_question="先解释程序确认的首要计划解决了什么问题，以及实施计划需要哪些条件。",
            priority_topics=[plan.type, "计划条件", "对手反制"],
            deemphasized_topics=["未被程序确认的替代计划"],
            evidence_ids=[*plan.evidence_route_ids],
        )

    return AnalysisObjective(
        kind="strategic_improvement",
        focus_side="both",
        primary_question="先比较双方最需要改善的棋子、结构和候选路线，不补写没有证据的计划。",
        priority_topics=["最差棋子", "子力协调", "候选路线差异"],
        deemphasized_topics=["没有证据的攻势或主动权"],
        evidence_ids=[],
    )


def _evaluation_boundary(package: "ChessFactPackage") -> EvaluationBoundary:
    cp = package.evaluation.evaluation_cp
    if cp is None and package.evaluation.mate is None:
        return EvaluationBoundary(
            direction="unknown",
            strength="unknown",
            allowed_wording="评价来源不足，只能描述已验证事实",
            forbidden_wording=["明显优势", "胜势", "掌握主动权"],
        )
    if package.evaluation.mate is not None:
        direction: EvaluationDirection = "white" if package.evaluation.mate > 0 else "black"
        return EvaluationBoundary(
            direction=direction,
            strength="winning",
            allowed_wording="存在已验证的将杀评价",
            forbidden_wording=[],
        )
    assert cp is not None
    direction = "white" if cp > 20 else "black" if cp < -20 else "equal"
    magnitude = abs(cp)
    strength: EvaluationStrength = (
        "winning" if magnitude >= 300 else "clear_edge" if magnitude >= 120 else "slight_edge" if magnitude >= 35 else "equal"
    )
    wording = {
        "equal": "局面大致均衡",
        "slight_edge": "存在轻微优势，不得表述为胜势",
        "clear_edge": "存在较明确优势，但仍需说明优势来源",
        "winning": "评价明显倾向一方，但必须引用具体战术或转换依据",
    }[strength]
    return EvaluationBoundary(
        direction=direction,
        strength=strength,
        allowed_wording=wording,
        forbidden_wording=["必胜", "完全掌握主动权"] if strength != "winning" else [],
    )


def _forbidden_claims(
    package: "ChessFactPackage",
    boundary: EvaluationBoundary,
    initiative: InitiativeAssessment,
    themes: list[ThemeEvidence],
) -> list[str]:
    claims = [
        "不得改写程序提供的物质差、王位置、易位权和评价方向",
        "不得把route_event写成当前威胁",
    ]
    if initiative.side == "unknown":
        claims.append("证据不足时不得声称任何一方掌握主动权")
    claims.extend(boundary.forbidden_wording)
    if not themes:
        claims.append("不得凭空添加战略主题或计划")
    if package.best_move is None:
        claims.append("不得声称实战着与Stockfish首选一致")
    return list(dict.fromkeys(claims))
