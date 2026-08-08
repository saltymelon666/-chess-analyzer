from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import ValidationError

from .analysis_focus import AnalysisFocus, select_analysis_focus
from .models import (
    MoveReview,
    ProfessionalAnalysis,
    ProfessionalAnalysisDraft,
    ProfessionalCandidateLineAnalysis,
    ProfessionalComparison,
    ProfessionalContinuationPhase,
    ProfessionalEvidenceText,
    ProfessionalLineEvent,
    ProfessionalMainDanger,
    ProfessionalPlan,
    ProfessionalPlans,
    ProfessionalPlayedMoveAnalysis,
    ProfessionalPositionAssessment,
    ProfessionalThreat,
    ProfessionalWeakness,
    ProfessionalWeaknesses,
)
from .professional_validation import ProfessionalValidationContext
from .strategic_plans import StrategicPlanPackage


VAGUE_CLAIMS = ("加强中心", "注意防守", "改善子力", "形成压力", "准备进攻", "局面复杂")

SIDE_NAMES = {"white": "白方", "black": "黑方"}
INCOMPLETE_ENDINGS = (
    "正在", "准备", "为了", "通过", "因为", "因此", "意大", "可以让", "正",
    "需要", "能够", "可以", "以及", "同时", "并", "从", "向", "的",
)

REFERENCE_OUTPUT_CONTRACT = {
    "complexity": "simple|normal|complex",
    "positionAssessment": {
        "summary": "explanation without chess notation",
    },
    "mainDanger": {
        "level": "immediate|short_term|medium_term|long_term|none",
        "dangerRef": "existing ply-id or null",
        "explanation": "causal explanation only",
        "consequence": "consequence only",
        "evidenceRefs": ["existing-id"],
    },
    "plans": {
        "white": [],
        "black": [],
    },
    "planExplanations": [{
        "planId": "existing-plan-id",
        "explanation": "explain this confirmed plan without chess notation",
    }],
    "playedMoveAnalysis": {
        "moveRef": "played-move-id",
        "intention": "idea without notation",
        "positiveEffects": ["explanation"],
        "problems": ["explanation"],
        "strongestReplyRef": "actual-line ply-id",
        "plyRefs": ["all actual-line ply-ids in exact order"],
        "continuationExplanation": "causal explanation",
        "errorType": "tactical|strategic|both|none",
        "evidenceRefs": ["existing-id"],
    },
    "candidateLines": [{
        "lineRef": "line-id",
        "strategyTags": ["allowed-tag"],
        "directPurpose": "purpose without notation",
        "plyRefs": ["all this line ply-ids in exact order"],
        "continuationExplanation": "causal explanation",
        "advantages": ["explanation"],
        "risks": ["explanation"],
        "evidenceRefs": ["existing-id"],
    }],
    "comparison": {
        "mainDifference": "comparison",
        "whyFirstLineIsBest": "reason",
        "evidenceRefs": ["line-id"],
    },
}


@dataclass(frozen=True)
class DraftValidationIssue:
    path: str
    category: str
    message: str

    def render(self) -> str:
        return f"{self.path}\n错误：{self.message}"


def build_reference_payload(move: MoveReview, complexity: str, reasons: list[str]) -> dict[str, Any]:
    focus = select_analysis_focus(move)
    facts = [item.prompt_dict() for item in focus.selected_facts]

    def compact_ply(item: Any) -> dict[str, Any]:
        events = [
            label
            for label, active in (
                ("capture", item.capture),
                ("check", item.check),
                ("mate", item.checkmate),
                ("castle", item.castling),
                ("promotion", bool(item.promotion)),
            )
            if active
        ]
        return _without_empty({
            "id": item.id,
            "side": item.side,
            "san": item.san,
            "piece": item.piece,
            "events": events,
        })

    def compact_line(line: Any) -> dict[str, Any]:
        endpoint = line.resulting_position_facts
        return _without_empty({
            "id": line.id,
            "rank": line.rank,
            "depth": line.depth,
            "scoreCp": line.centipawn,
            "mateIn": line.mate_in,
            "plies": [compact_ply(item) for item in line.moves[:10]],
            "endMaterialDelta": (
                endpoint.material.get("valueDifferenceWhiteMinusBlack", 0) if endpoint else None
            ),
        })

    payload = {
        "v": "professional-v6-focus",
        "cx": {"level": complexity, "reasons": reasons},
        "focus": {
            "priority": "only facts that materially affect the current decision",
            "displaySections": list(focus.display_sections),
            "kingSafetyRelevant": bool(focus.king_safety_relevant_sides),
            "kingSafetySides": sorted(focus.king_safety_relevant_sides),
            "selectedFacts": facts,
        },
        "pos": {
            "fen": move.before_fen,
            "turn": move.side,
            "facts": _deduplicate_by_id(facts),
        },
        "played": _without_empty({
            "ref": move.played_move.id or f"move:played:{move.index}",
            "san": move.played_move.san,
            "side": move.side,
            "evaluationBefore": move.before.evaluation,
            "evaluationAfter": move.after.evaluation,
            "centipawnLoss": move.centipawn_loss,
            "quality": move.quality_label,
            "evidenceRefs": [
                f"evaluation:before:{move.index}",
                f"evaluation:after:{move.index}",
                f"complexity:{move.index}",
            ],
        }),
        "actual": compact_line(move.actual_move_line) if move.actual_move_line else None,
        "lines": [compact_line(line) for line in move.candidate_lines],
    }
    _, aliases_by_source = _reference_maps(move)
    return _replace_known_refs(_without_empty(payload), aliases_by_source)


def parse_professional_draft(content: str) -> tuple[ProfessionalAnalysisDraft | None, list[DraftValidationIssue]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return None, [DraftValidationIssue("$", "JSON结构错误", str(exc))]
    # Strategy tags are labels, not chess facts. Drop unknown model-produced labels
    # before schema validation; never coerce them into a different valid label.
    _drop_unknown_strategy_tags(payload)
    try:
        return ProfessionalAnalysisDraft.model_validate(payload), []
    except ValidationError as exc:
        issues = []
        for error in exc.errors(include_url=False):
            path = _format_path(error.get("loc", ()))
            category = "字段缺失" if error.get("type") == "missing" else "JSON结构错误"
            issues.append(DraftValidationIssue(path, category, error.get("msg", "结构不符合契约")))
        return None, issues


_ALLOWED_STRATEGY_TAGS = {
    "king_attack",
    "improve_king_safety",
    "center_break",
    "center_control",
    "kingside_expansion",
    "queenside_expansion",
    "control_open_file",
    "occupy_weak_square",
    "improve_worst_piece",
    "exchange_and_simplify",
    "create_passed_pawn",
    "defend_immediate_threat",
    "pawn_break",
    "transition_to_endgame",
}


def _drop_unknown_strategy_tags(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    lines = payload.get("candidateLines")
    if not isinstance(lines, list):
        return
    for line in lines:
        if not isinstance(line, dict):
            continue
        tags = line.get("strategyTags")
        if isinstance(tags, list):
            line["strategyTags"] = [
                tag for tag in tags
                if isinstance(tag, str) and tag in _ALLOWED_STRATEGY_TAGS
            ]


def normalize_professional_draft_literals(
    draft: ProfessionalAnalysisDraft,
    move: MoveReview,
    context: ProfessionalValidationContext,
) -> tuple[ProfessionalAnalysisDraft, list[DraftValidationIssue]]:
    allowed_squares = {item.lower() for item in context.allowed_squares}
    allowed_moves = {item.replace("0", "O").rstrip("+#") for item in context.allowed_moves}
    issues: list[DraftValidationIssue] = []
    reference_keys = {
        "evidenceRefs", "dangerRef", "targetRef", "moveRef",
        "strongestReplyRef", "lineRef", "plyRefs", "planId",
    }
    san_pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8](?:=[QRBN])?|"
        r"[a-h]x[a-h][1-8](?:=[QRBN])?)[+#]?(?![A-Za-z0-9])"
    )
    uci_pattern = re.compile(
        r"(?<![A-Za-z0-9])([a-h][1-8][a-h][1-8][qrbn]?)(?![A-Za-z0-9])",
        re.I,
    )
    square_pattern = re.compile(r"(?<![A-Za-z0-9])([a-h][1-8])(?![A-Za-z0-9])", re.I)
    malformed_pattern = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][0-9])(?![A-Za-z0-9])")

    def walk(value: Any, path: str = "", key: str = "") -> Any:
        if key in reference_keys:
            return value
        if isinstance(value, dict):
            return {
                child_key: walk(child, f"{path}.{child_key}" if path else child_key, child_key)
                for child_key, child in value.items()
            }
        if isinstance(value, list):
            return [walk(child, f"{path}[{index}]", key) for index, child in enumerate(value)]
        if not isinstance(value, str):
            return value

        def replace_uci(match: re.Match[str]) -> str:
            issues.append(DraftValidationIssue(path, "不属于Stockfish的走法", f"已移除自由文本UCI：{match.group(1)}"))
            return "该走法"

        def replace_san(match: re.Match[str]) -> str:
            san = match.group(0)
            if san.replace("0", "O").rstrip("+#") in allowed_moves:
                return san
            issues.append(DraftValidationIssue(path, "不属于Stockfish的走法", f"已移除路线外SAN：{san}"))
            return "该路线着法"

        def replace_square(match: re.Match[str]) -> str:
            square = match.group(1)
            if square.lower() in allowed_squares:
                return square
            issues.append(DraftValidationIssue(path, "不存在的格子", f"已移除事实包外格子：{square}"))
            return "该格"

        def replace_malformed(match: re.Match[str]) -> str:
            square = match.group(1)
            if re.fullmatch(r"[a-h][1-8]", square, re.I):
                return square
            issues.append(DraftValidationIssue(path, "不存在的格子", f"已移除棋盘外格子：{square}"))
            return "该格"

        value = uci_pattern.sub(replace_uci, value)
        value = san_pattern.sub(replace_san, value)
        value = square_pattern.sub(replace_square, value)
        value = malformed_pattern.sub(replace_malformed, value)
        return value

    payload = walk(draft.model_dump(by_alias=True))
    source_by_alias, aliases_by_source = _reference_maps(
        move,
        context.allowed_evidence_ids,
    )
    for side in ("white", "black"):
        for index, plan in enumerate(payload["plans"][side]):
            original_refs = list(plan["evidenceRefs"])
            kept = []
            for ref in original_refs:
                source = source_by_alias.get(ref)
                if source is None or context.evidence_sides.get(source) in {side, None}:
                    kept.append(ref)
                else:
                    issues.append(DraftValidationIssue(
                        f"plans.{side}[{index}].evidenceRefs",
                        "黑白说反",
                        f"已移除不支持{side}方计划的引用：{ref}",
                    ))
            if not kept:
                replacement = next(
                    (
                        aliases_by_source[source]
                        for source, evidence_side in context.evidence_sides.items()
                        if evidence_side == side and source in aliases_by_source
                    ),
                    None,
                )
                if replacement:
                    kept.append(replacement)
                    issues.append(DraftValidationIssue(
                        f"plans.{side}[{index}].evidenceRefs",
                        "黑白说反",
                        f"已补入支持{side}方计划的引用：{replacement}",
                    ))
            plan["evidenceRefs"] = kept
    normalized = ProfessionalAnalysisDraft.model_validate(payload)
    return normalized, _deduplicate_issues(issues)


def validate_professional_draft(
    draft: ProfessionalAnalysisDraft,
    move: MoveReview,
    context: ProfessionalValidationContext,
    *,
    strategic_plan_package: StrategicPlanPackage | None = None,
) -> list[DraftValidationIssue]:
    issues: list[DraftValidationIssue] = []
    source_by_alias, aliases_by_source = _reference_maps(
        move,
        context.allowed_evidence_ids,
    )

    def canonical(values: Iterable[str]) -> list[str]:
        return [source_by_alias.get(value, value) for value in values]

    if draft.complexity != context.complexity:
        issues.append(DraftValidationIssue("complexity", "其他原因", f"应为{context.complexity}"))

    payload = draft.model_dump(by_alias=True)
    for path, ref in _walk_refs(payload):
        if ref not in source_by_alias:
            issues.append(DraftValidationIssue(path, "无效evidenceRefs", f"引用的ID不存在：{ref}"))

    allowed_squares = {item.lower() for item in context.allowed_squares}
    allowed_moves = {item.replace("0", "O").rstrip("+#") for item in context.allowed_moves}
    for path, text in _walk_free_text(payload):
        malformed = sorted(set(re.findall(r"(?<![A-Za-z0-9])([A-Za-z][0-9])(?![A-Za-z0-9])", text)))
        for square in malformed:
            if not re.fullmatch(r"[a-h][1-8]", square, re.I):
                issues.append(DraftValidationIssue(path, "不存在的格子", f"格子超出棋盘范围：{square}"))
        for square in sorted(set(re.findall(r"(?<![A-Za-z0-9])([a-h][1-8])(?![A-Za-z0-9])", text, re.I))):
            if square.lower() not in allowed_squares:
                issues.append(DraftValidationIssue(path, "不存在的格子", f"格子不属于事实包：{square}"))
        for uci in sorted(set(re.findall(r"(?<![A-Za-z0-9])([a-h][1-8][a-h][1-8][qrbn]?)(?![A-Za-z0-9])", text, re.I))):
            issues.append(DraftValidationIssue(path, "不属于Stockfish的走法", f"解释文本不得自行输入UCI：{uci}"))
        san_pattern = r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8](?:=[QRBN])?|[a-h]x[a-h][1-8](?:=[QRBN])?)[+#]?(?![A-Za-z0-9])"
        for san in sorted(set(re.findall(san_pattern, text))):
            normalized = san.replace("0", "O").rstrip("+#")
            if normalized not in allowed_moves:
                issues.append(DraftValidationIssue(path, "不属于Stockfish的走法", f"SAN不属于实战或Stockfish路线：{san}"))

    for side, plans in (("white", draft.plans.white), ("black", draft.plans.black)):
        for index, plan in enumerate(plans):
            _validate_side_evidence(
                canonical(plan.evidence_refs),
                side,
                f"plans.{side}[{index}].evidenceRefs",
                context,
                issues,
                allow_neutral=True,
            )
    if strategic_plan_package is not None:
        if draft.plans.white or draft.plans.black:
            issues.append(DraftValidationIssue(
                "plans",
                "其他原因",
                "DeepSeek不得生成战略计划；plans.white和plans.black必须为空数组",
            ))
        allowed_plan_ids = {plan.plan_id for plan in strategic_plan_package.plans}
        returned_plan_ids = [item.plan_id for item in draft.plan_explanations]
        invalid_plan_ids = sorted(set(returned_plan_ids) - allowed_plan_ids)
        if invalid_plan_ids:
            issues.append(DraftValidationIssue(
                "planExplanations",
                "其他原因",
                "引用的planId不存在：" + "、".join(invalid_plan_ids),
            ))
        if len(returned_plan_ids) != len(set(returned_plan_ids)):
            issues.append(DraftValidationIssue(
                "planExplanations",
                "其他原因",
                "planId重复",
            ))
    played_ref = aliases_by_source[move.played_move.id or f"move:played:{move.index}"]
    if draft.played_move_analysis.move_ref != played_ref:
        issues.append(DraftValidationIssue("playedMoveAnalysis.moveRef", "不属于Stockfish的走法", "必须引用实战走法ID"))
    actual = move.actual_move_line
    actual_ids = [aliases_by_source[item.id] for item in actual.moves] if actual else []
    expected_reply = actual_ids[0] if actual_ids else None
    if draft.played_move_analysis.strongest_reply_ref != expected_reply:
        issues.append(DraftValidationIssue("playedMoveAnalysis.strongestReplyRef", "不属于Stockfish的走法", "必须引用实战续算第一ply"))
    if draft.played_move_analysis.ply_refs != actual_ids:
        issues.append(DraftValidationIssue(
            "playedMoveAnalysis.plyRefs",
            "不属于Stockfish的走法",
            "plyRefs必须按顺序完整覆盖实战续算路线",
        ))

    lines_by_id = {aliases_by_source[line.id]: line for line in move.candidate_lines}
    expected_line_ids = [aliases_by_source[line.id] for line in move.candidate_lines]
    returned_line_ids = [item.line_ref for item in draft.candidate_lines]
    if returned_line_ids != expected_line_ids:
        issues.append(DraftValidationIssue("candidateLines", "不属于Stockfish的走法", "lineRef数量或顺序与三条Stockfish路线不一致"))
    for index, item in enumerate(draft.candidate_lines):
        line = lines_by_id.get(item.line_ref)
        if line is None:
            issues.append(DraftValidationIssue(f"candidateLines[{index}].lineRef", "不属于Stockfish的走法", f"路线不存在：{item.line_ref}"))
            continue
        if item.ply_refs != [aliases_by_source[ply.id] for ply in line.moves]:
            issues.append(DraftValidationIssue(
                f"candidateLines[{index}].plyRefs",
                "不属于Stockfish的走法",
                "plyRefs必须按顺序完整覆盖对应Stockfish路线",
            ))

    danger = draft.main_danger
    ply_by_id = {
        aliases_by_source[item.id]: item
        for line in [*move.candidate_lines, *([actual] if actual else [])]
        for item in line.moves
    }
    if danger.danger_ref is not None:
        source = ply_by_id.get(danger.danger_ref or "")
        if source is None:
            issues.append(DraftValidationIssue("mainDanger.dangerRef", "战略结论没有证据", "危险必须引用已有ply ID"))

    return _deduplicate_issues(issues)


def resolve_professional_draft(
    draft: ProfessionalAnalysisDraft,
    move: MoveReview,
    context: ProfessionalValidationContext,
    *,
    strategic_plan_package: StrategicPlanPackage | None = None,
) -> ProfessionalAnalysis:
    focus = select_analysis_focus(move)
    draft = ProfessionalAnalysisDraft.model_validate(
        _sanitize_draft_event_words(draft.model_dump(by_alias=True))
    )
    source_by_alias, aliases_by_source = _reference_maps(
        move,
        context.allowed_evidence_ids,
    )
    lines_by_id = {aliases_by_source[line.id]: line for line in move.candidate_lines}
    actual = move.actual_move_line
    all_lines = [*move.candidate_lines, *([actual] if actual else [])]
    plies_by_id = {aliases_by_source[item.id]: item for line in all_lines for item in line.moves}
    descriptions = {
        key: _sanitize_grounding_description(value, context)
        for key, value in _evidence_descriptions(move).items()
    }

    def refs(values: Iterable[str], *required: str | None) -> list[str]:
        result = []
        for value in [*required, *values]:
            source = source_by_alias.get(value, value)
            if source and source in context.allowed_evidence_ids and source not in result:
                result.append(source)
        return result

    plans = {"white": [], "black": []}
    if strategic_plan_package is not None:
        explanations = {
            item.plan_id: item.explanation
            for item in draft.plan_explanations
        }
        for plan in strategic_plan_package.plans:
            if plan.confidence != "high":
                continue
            evidence = refs(plan.evidence_route_ids)
            if not evidence:
                continue
            explanation = _complete_display_sentence(
                explanations.get(plan.plan_id, "")
            )
            description = explanation or _complete_display_sentence(plan.goal)
            preparation = _complete_display_sentence(
                "；".join(plan.structural_evidence[:2])
            )
            plans[plan.side].append(ProfessionalPlan(
                strategyTag=_strategic_plan_tag(plan.type),
                description=description,
                requiredPreparation=preparation or "按照程序确认的结构条件逐步实施。",
                evidenceRefs=evidence,
            ))
    else:
        for side, items in (("white", draft.plans.white), ("black", draft.plans.black)):
            for item in items:
                evidence = refs(item.evidence_refs)
                plans[side].append(ProfessionalPlan(
                    strategyTag=item.strategy_tag,
                    description=_explanation_with_evidence(item.explanation, evidence, descriptions),
                    requiredPreparation=(
                        item.required_preparation
                        if len(item.required_preparation.strip()) >= 4
                        else "按照对应证据完成必要准备。"
                    ),
                    evidenceRefs=evidence,
                ))

    weaknesses = {"white": [], "black": []}
    for side, items in focus.weaknesses.items():
        for item in items:
            weaknesses[side].append(ProfessionalWeakness(
                description=item.description,
                exploitation=item.decision_impact,
                evidenceRefs=refs(item.evidence_refs),
            ))

    threats = []
    for item in focus.global_threats:
        if item.side not in {"white", "black"}:
            continue
        threats.append(ProfessionalThreat(
            side=item.side,
            level="immediate" if item.importance_score >= 4 else "short_term",
            scope="current_position",
            description=item.description,
            attacker=item.squares[0] if item.squares else "证据中的攻击棋子",
            target=item.squares[-1] if item.squares else "证据中的具体目标",
            preparation="当前局面已经具备执行条件，无需把单条路线事件提升为准备步骤。",
            consequence=item.decision_impact,
            evidenceRefs=refs(item.evidence_refs),
        ))

    danger_ref = draft.main_danger.danger_ref
    danger_move = plies_by_id.get(danger_ref or "")
    danger_side = (
        "black" if danger_move and danger_move.side == "white"
        else "white" if danger_move
        else "none"
    )
    danger_description = draft.main_danger.explanation
    if danger_ref:
        source = source_by_alias.get(danger_ref, danger_ref)
        danger_description = f"{descriptions.get(source, source)}；{danger_description}"
    elif len(danger_description.strip()) < 4:
        danger_description = "当前没有需要优先处理的单一直接危险。"
    main_danger = ProfessionalMainDanger(
        sideInDanger=danger_side,
        level=_normalized_level(draft.main_danger.level),
        description=danger_description,
        consequence=(
            draft.main_danger.consequence
            if len(draft.main_danger.consequence.strip()) >= 4
            else "未确认更具体的直接后果。"
        ),
        evidenceRefs=refs(
            draft.main_danger.evidence_refs,
            danger_ref,
            move.played_move.id or f"move:played:{move.index}",
        ),
    )

    def phase(ply_refs: list[str], explanation: str, label: str) -> list[ProfessionalContinuationPhase]:
        return [ProfessionalContinuationPhase(
            phase=label,
            moves=[plies_by_id[ref].san for ref in ply_refs],
            explanation=explanation,
            evidenceRefs=refs(ply_refs),
        )]

    def result_position(line: Any) -> str:
        if line is None:
            return "没有提供实战续算的结果局面。"
        facts = line.resulting_position_facts
        if facts:
            return f"路线结束时轮到{facts.side_to_move}方行棋；具体子力后果只在路线确实发生重要得失时说明。"
        return "路线结束后没有额外的结果局面事实。"

    played = draft.played_move_analysis
    strongest = plies_by_id[played.strongest_reply_ref]
    played_analysis = ProfessionalPlayedMoveAnalysis(
        move=move.played_move.san,
        intention=played.intention,
        positiveEffects=played.positive_effects,
        problems=played.problems,
        strongestResponse=strongest.san,
        continuationPhases=phase(played.ply_refs, played.continuation_explanation, "实战续算"),
        resultingPosition=result_position(actual),
        evaluationReason=f"实战前评价{move.before.evaluation}，实战后评价{move.after.evaluation}。",
        errorType=played.error_type,
        evidenceRefs=refs(
            played.evidence_refs,
            move.played_move.id or f"move:played:{move.index}",
            f"evaluation:before:{move.index}",
            f"evaluation:after:{move.index}",
        ),
    )

    candidate_lines = []
    for item in draft.candidate_lines:
        line = lines_by_id[item.line_ref]
        reply = line.moves[1].san if len(line.moves) > 1 else "路线未提供对手回应"
        route_events = [
            ProfessionalLineEvent(
                scope=event.scope,
                description=event.description,
                significance=event.decision_impact,
                evidenceRefs=refs(event.evidence_refs),
            )
            for event in focus.line_events.get(line.rank, ())
        ]
        candidate_lines.append(ProfessionalCandidateLineAnalysis(
            rank=line.rank,
            firstMove=line.first_move.san,
            strategyTags=item.strategy_tags,
            directPurpose=_program_direct_purpose(line),
            opponentResponse=reply,
            continuationPhases=phase(item.ply_refs, item.continuation_explanation, "Stockfish续算"),
            resultingPosition=result_position(line),
            advantages=item.advantages,
            risks=item.risks,
            events=route_events,
            whyThisRank=f"该路线由Stockfish列为第{line.rank}候选。",
            evidenceRefs=refs(item.evidence_refs, item.line_ref),
        ))

    def selected_evidence(items: list[Any]) -> ProfessionalEvidenceText | None:
        if not items:
            return None
        evidence = refs([ref for item in items for ref in item.evidence_refs])
        return ProfessionalEvidenceText(
            description="；".join(item.description for item in items),
            evidenceRefs=evidence,
        )

    king_evidence = {
        side: selected_evidence([
            item for item in focus.selected_facts
            if item.display_section == "kingSafety" and item.side == side
        ])
        for side in ("white", "black")
    }
    activity_ids = {item.id for item in move.position_facts.piece_activity}
    pawn_ids = {item.id for item in move.position_facts.pawn_structure}
    activity_evidence = selected_evidence([
        item for item in focus.selected_facts
        if item.display_section == "positionAssessment" and item.id in activity_ids
    ][:1])
    pawn_evidence = selected_evidence([
        item for item in focus.selected_facts
        if item.display_section == "positionAssessment" and item.id in pawn_ids
    ][:1])

    verified_summary = _complete_display_sentence(draft.position_assessment.summary)
    if not verified_summary:
        verified_summary = f"当前由{SIDE_NAMES.get(move.side, move.side)}行棋。"

    analysis = ProfessionalAnalysis(
        complexity=draft.complexity,
        positionAssessment=ProfessionalPositionAssessment(
            summary=verified_summary,
            kingSafety={
                "isRelevant": bool(focus.king_safety_relevant_sides),
                "white": king_evidence["white"],
                "black": king_evidence["black"],
            },
            pieceActivity=activity_evidence,
            pawnStructure=pawn_evidence,
        ),
        mainDanger=main_danger,
        plans=ProfessionalPlans(**plans),
        weaknesses=ProfessionalWeaknesses(**weaknesses),
        threats=threats,
        playedMoveAnalysis=played_analysis,
        candidateLines=candidate_lines,
        comparison=ProfessionalComparison(
            mainDifference=draft.comparison.main_difference,
            whyFirstLineIsBest=draft.comparison.why_first_line_is_best,
            evidenceRefs=refs(draft.comparison.evidence_refs),
        ),
    )
    grounded = _ground_vague_claims(
        analysis.model_dump(by_alias=True),
        descriptions,
        default_ref=move.played_move.id or f"move:played:{move.index}",
    )
    return ProfessionalAnalysis.model_validate(grounded)


def _complete_display_sentence(value: str) -> str:
    """Keep display prose sentence-atomic; never expose a fragment after sanitising."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    plain = text.rstrip("。！？；.!? ")
    if len(plain) < 5 or any(plain.endswith(ending) for ending in INCOMPLETE_ENDINGS):
        return ""
    if re.fullmatch(r"(?:白方|黑方)?(?:[a-h][1-8])?(?:兵|马|象|车|后|王)(?:\([a-h][1-8]\))?", plain):
        return ""
    return text if text.endswith(("。", "！", "？", "；", ".", "!", "?")) else f"{text}。"


def _program_direct_purpose(line: Any) -> str:
    """Describe only the verified first ply; do not infer a strategic story from the PV."""
    first = line.first_move
    side = "白" if first.piece.startswith("white_") else "黑"
    piece_key = first.piece.split("_")[-1]
    piece_name = {
        "pawn": "兵",
        "knight": "马",
        "bishop": "象",
        "rook": "车",
        "queen": "后",
        "king": "王",
    }.get(piece_key, "棋子")
    action = f"第一步{side}{piece_name}从{first.from_square}走到{first.to_square}（{first.san}）"
    effects: list[str] = []
    if first.capture:
        captured_key = (first.captured_piece or "").split("_")[-1]
        captured_name = {
            "pawn": "兵",
            "knight": "马",
            "bishop": "象",
            "rook": "车",
            "queen": "后",
        }.get(captured_key, "对方棋子")
        effects.append(f"吃掉{captured_name}")
    if first.promotion:
        effects.append("完成升变")
    if first.checkmate:
        effects.append("形成将杀")
    elif first.check:
        effects.append("形成将军")
    if effects:
        return f"{action}，并{'、'.join(effects)}。"
    return f"{action}，作为这条Stockfish路线的起点。"


def _evidence_descriptions(move: MoveReview) -> dict[str, str]:
    result = {
        f"evaluation:before:{move.index}": f"实战前评价{move.before.evaluation}",
        f"evaluation:after:{move.index}": f"实战后评价{move.after.evaluation}",
        f"complexity:{move.index}": f"复杂度{move.complexity}",
        move.played_move.id or f"move:played:{move.index}": (
            f"{move.played_move.piece}从{move.played_move.from_square}走到"
            f"{move.played_move.to_square}（{move.played_move.san}）"
        ),
    }
    material_id = str(move.position_facts.material.get("id", ""))
    if material_id:
        result[material_id] = (
            "白方减黑方的结构化子力差为"
            f"{move.position_facts.material.get('valueDifferenceWhiteMinusBlack', 0)}"
        )
    for piece in move.position_facts.pieces:
        result[piece["id"]] = f"{piece['side']}_{piece['piece']}位于{piece['square']}"
    for position in (move.position_facts, move.position_facts_after):
        for group in (
            position.piece_activity,
            position.king_safety,
            position.pawn_structure,
            position.threats,
        ):
            for fact in group:
                result[fact.id] = fact.description
        for fact in (*position.immediate_checks, *position.immediate_captures):
            result[fact.id] = f"{fact.piece}从{fact.from_square}走到{fact.to_square}（{fact.san}）"
    for line in [*move.candidate_lines, *([move.actual_move_line] if move.actual_move_line else [])]:
        first_san = line.moves[0].san if line.moves else "未提供首着"
        result[line.id] = f"Stockfish第{line.rank}路线首着{first_san}"
        for item in line.moves:
            result[item.id] = f"{item.side}_{item.piece}从{item.from_square}走到{item.to_square}（{item.san}）"
    return result


def _explanation_with_evidence(explanation: str, refs: list[str], descriptions: dict[str, str]) -> str:
    if re.search(r"(?<![A-Za-z0-9])[a-h][1-8](?![A-Za-z0-9])", explanation, re.I):
        return explanation
    if re.search(r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8])[+#]?(?![A-Za-z0-9])", explanation):
        return explanation
    available = [descriptions[ref] for ref in refs if ref in descriptions]
    concrete = next((item for item in available if _is_concrete_description(item)), None)
    verified = [concrete or available[0]] if available else []
    return explanation if not verified else f"事实依据：{'、'.join(verified)}，因此{explanation}"


def _ground_vague_claims(
    value: Any,
    descriptions: dict[str, str],
    *,
    default_ref: str,
    inherited_refs: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, dict):
        own_refs = tuple(value.get("evidenceRefs") or inherited_refs)
        return {
            key: child if key == "evidenceRefs" else _ground_vague_claims(
                child,
                descriptions,
                default_ref=default_ref,
                inherited_refs=own_refs,
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _ground_vague_claims(
                child,
                descriptions,
                default_ref=default_ref,
                inherited_refs=inherited_refs,
            )
            for child in value
        ]
    if isinstance(value, str) and any(phrase in value for phrase in VAGUE_CLAIMS):
        candidates = [descriptions[ref] for ref in inherited_refs if ref in descriptions]
        evidence = next(
            (item for item in candidates if _is_concrete_description(item)),
            descriptions.get(default_ref, candidates[0] if candidates else ""),
        )
        if evidence:
            grounded = value
            for phrase in VAGUE_CLAIMS:
                if phrase in grounded:
                    grounded = grounded.replace(phrase, f"依据{evidence[:90]}可判断{phrase}")
            return grounded
    return value


def _is_concrete_description(value: str) -> bool:
    has_square = bool(re.search(r"(?<![A-Za-z0-9])[a-h][1-8](?![A-Za-z0-9])", value, re.I))
    has_piece = bool(re.search(r"(?:白|黑)?(?:兵|马|象|车|后|王)|pawn|knight|bishop|rook|queen|king", value, re.I))
    has_san = bool(re.search(r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8])[+#]?(?![A-Za-z0-9])", value))
    return (has_square and has_piece) or has_san


def _normalized_level(value: str) -> str:
    if value == "medium_term":
        return "short_term"
    if value == "none":
        return "long_term"
    return value


def _sanitize_draft_event_words(value: Any, key: str = "") -> Any:
    reference_keys = {
        "evidenceRefs", "dangerRef", "targetRef", "moveRef",
        "strongestReplyRef", "lineRef", "plyRefs", "planId",
    }
    if key in reference_keys:
        return value
    if isinstance(value, dict):
        return {
            child_key: _sanitize_draft_event_words(child, child_key)
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_draft_event_words(child, key) for child in value]
    if isinstance(value, str):
        replacements = (
            ("将杀", "决定性威胁"),
            ("绝杀", "决定性威胁"),
            ("将军", "直接威胁"),
            ("吃子", "子力收益"),
            ("吃掉", "赢得"),
            ("捕获", "处理"),
            ("拿掉", "处理"),
        )
        for source, replacement in replacements:
            value = value.replace(source, replacement)
    return value


def _sanitize_grounding_description(value: str, context: ProfessionalValidationContext) -> str:
    allowed_moves = {item.replace("0", "O").rstrip("+#") for item in context.allowed_moves}
    allowed_squares = {item.lower() for item in context.allowed_squares}
    san_pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8](?:=[QRBN])?|"
        r"[a-h]x[a-h][1-8](?:=[QRBN])?)[+#]?(?![A-Za-z0-9])"
    )
    value = san_pattern.sub(
        lambda match: match.group(0)
        if match.group(0).replace("0", "O").rstrip("+#") in allowed_moves
        else "该事实着法",
        value,
    )
    value = re.sub(
        r"(?<![A-Za-z0-9])([a-h][1-8])(?![A-Za-z0-9])",
        lambda match: match.group(1) if match.group(1).lower() in allowed_squares else "该格",
        value,
        flags=re.I,
    )
    if not context.allows_checkmate:
        value = value.replace("将杀", "决定性威胁").replace("绝杀", "决定性威胁")
    if not context.allows_check:
        value = value.replace("将军", "直接威胁")
    if not context.allows_capture:
        value = value.replace("吃子", "子力收益").replace("吃掉", "赢得")
    return value


def _strategic_plan_tag(plan_type: str) -> str:
    return {
        "improve_worst_piece": "improve_worst_piece",
        "prepare_center_break": "center_break",
        "occupy_open_file": "control_open_file",
        "activate_rook": "control_open_file",
        "improve_king_safety": "improve_king_safety",
        "attack_weak_pawn": "occupy_weak_square",
        "create_passed_pawn": "create_passed_pawn",
        "simplify_endgame": "exchange_and_simplify",
    }[plan_type]


def _target_text(ref: str, context: ProfessionalValidationContext, descriptions: dict[str, str]) -> str:
    squares = sorted(context.evidence_squares.get(ref, set()))
    if squares:
        return "、".join(squares)
    return descriptions.get(ref, "证据未提供具体目标格")


def _validate_phase_refs(phases: Iterable[Any], expected: list[str], path: str, issues: list[DraftValidationIssue]) -> None:
    actual = [ref for phase in phases for ref in phase.ply_refs]
    if actual != expected:
        issues.append(DraftValidationIssue(path, "不属于Stockfish的走法", "plyRefs必须按顺序完整覆盖对应Stockfish路线"))


def _validate_side_evidence(
    refs: Iterable[str],
    side: str,
    path: str,
    context: ProfessionalValidationContext,
    issues: list[DraftValidationIssue],
    *,
    allow_neutral: bool = False,
) -> None:
    if not _refs_support_side(refs, side, context, allow_neutral=allow_neutral):
        issues.append(DraftValidationIssue(path, "黑白说反", f"证据不支持{side}方结论"))


def _refs_support_side(
    refs: Iterable[str],
    side: str,
    context: ProfessionalValidationContext,
    *,
    allow_neutral: bool = False,
) -> bool:
    sides = [context.evidence_sides.get(ref) for ref in refs if ref in context.evidence_sides]
    if side in sides:
        return True
    return allow_neutral and None in sides


def _walk_refs(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            current = f"{path}.{key}" if path else key
            if key in {
                "evidenceRefs", "dangerRef", "targetRef", "moveRef",
                "strongestReplyRef", "lineRef", "plyRefs",
            }:
                for index, item in enumerate(child if isinstance(child, list) else [child]):
                    if item is not None:
                        yield (f"{current}[{index}]" if isinstance(child, list) else current, str(item))
            else:
                yield from _walk_refs(child, current)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_refs(child, f"{path}[{index}]")


def _walk_free_text(value: Any, path: str = "", key: str = "") -> Iterable[tuple[str, str]]:
    reference_keys = {
        "evidenceRefs", "dangerRef", "targetRef", "moveRef",
        "strongestReplyRef", "lineRef", "plyRefs", "planId",
    }
    if key in reference_keys:
        return
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for child_key, child in value.items():
            child_path = f"{path}.{child_key}" if path else child_key
            yield from _walk_free_text(child, child_path, child_key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_free_text(child, f"{path}[{index}]", key)


def _format_path(location: Iterable[Any]) -> str:
    path = ""
    for item in location:
        if isinstance(item, int):
            path += f"[{item}]"
        else:
            path += f".{item}" if path else str(item)
    return path or "$"


def _reference_maps(
    move: MoveReview,
    extra_sources: Iterable[str] = (),
) -> tuple[dict[str, str], dict[str, str]]:
    source_by_alias: dict[str, str] = {}
    alias_by_source: dict[str, str] = {}

    def register(alias: str, source: str | None) -> None:
        if source and source not in alias_by_source:
            source_by_alias[alias] = source
            alias_by_source[source] = alias

    facts = []
    material_id = str(move.position_facts.material.get("id", ""))
    if material_id:
        facts.append(material_id)
    for group in (
        move.position_facts.piece_activity,
        move.position_facts.king_safety,
        move.position_facts.pawn_structure,
        move.position_facts.threats,
        move.position_facts.immediate_checks,
        move.position_facts.immediate_captures,
    ):
        facts.extend(item.id for item in group)
    for index, source in enumerate(dict.fromkeys(facts), 1):
        register(f"f:{index}", source)

    register("m:played", move.played_move.id or f"move:played:{move.index}")
    register("e:before", f"evaluation:before:{move.index}")
    register("e:after", f"evaluation:after:{move.index}")
    register("e:complexity", f"complexity:{move.index}")

    for line in move.candidate_lines:
        line_alias = f"l:{line.rank}"
        register(line_alias, line.id)
        for ply_index, ply in enumerate(line.moves, 1):
            register(f"{line_alias}:p:{ply_index}", ply.id)
    if move.actual_move_line:
        register("l:a", move.actual_move_line.id)
        for ply_index, ply in enumerate(move.actual_move_line.moves, 1):
            register(f"l:a:p:{ply_index}", ply.id)
    # The compact prompt uses aliases for established move and route facts, while
    # newer program-owned facts (for example prepared threats) appear by their
    # canonical IDs in ChessFactPackage. Accept a canonical ID only when the
    # server-side validation context has already approved it.
    for source in [*alias_by_source, *extra_sources]:
        source_by_alias.setdefault(source, source)
    return source_by_alias, alias_by_source


def _replace_known_refs(value: Any, alias_by_source: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _replace_known_refs(child, alias_by_source) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_known_refs(child, alias_by_source) for child in value]
    if isinstance(value, str):
        return alias_by_source.get(value, value)
    return value


def _without_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_empty(child)
            for key, child in value.items()
            if child is not None and child != [] and child != {}
        }
    if isinstance(value, list):
        return [_without_empty(child) for child in value]
    return value


def _deduplicate_by_id(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        identifier = item.get("id")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        result.append(_without_empty(item))
    return result


def _deduplicate_issues(issues: Iterable[DraftValidationIssue]) -> list[DraftValidationIssue]:
    result = []
    seen = set()
    for issue in issues:
        key = (issue.path, issue.category, issue.message)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result
