from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import chess
import httpx

from .chess_facts import (
    CHESS_FACT_PACKAGE_VERSION,
    ChessFactPackage,
    build_move_fact_package,
)
from .models import (
    GeneratedProfessionalAnalysis,
    MoveReview,
    ProfessionalAnalysis,
    ProfessionalAnalysisUsage,
    ProfessionalComplexity,
    ProfessionalEvidenceText,
    ProfessionalThreat,
)
from .chess_reasoning_rules import ChessReasoningRuleEngine
from .book_evaluation_style import build_book_evaluation_style
from .unified_book_knowledge import (
    UNIFIED_BOOK_CONTEXT_VERSION,
    UnifiedBookKnowledgeRepository,
)
from .decision_context import (
    DECISION_CONTEXT_VERSION,
    build_decision_context,
    decision_history_signature,
)
from .narrative_claims import (
    NARRATIVE_CLAIM_VERSION,
    NarrativeClaimPackage,
    build_narrative_claim_package,
    compose_verified_core_paragraph,
    resolve_narrative_claims,
)
from .opening_knowledge import OpeningPresentation
from .professional_validation import (
    LENGTH_RANGES,
    VERIFIED_NARRATIVE_LENGTH_RANGES,
    VAGUE_PHRASES,
    _narrative_length,
    build_validation_context,
    normalize_program_owned_claims,
    validate_professional_analysis,
)
from .strategic_plans import (
    STRATEGIC_PLAN_PACKAGE_VERSION,
    StrategicPlanAnalyzer,
    StrategicPlanPackage,
)
from .threat_analysis import (
    THREAT_PACKAGE_VERSION,
    ThreatAnalyzer,
    ThreatPackage,
    assess_initiative,
    position_id,
)
from .position_interpretation import (
    POSITION_INTERPRETATION_VERSION,
    build_position_interpretation,
)
from .position_factor_ranker import PositionFactorRanker
from .position_importance_ranker import PositionImportanceRanker
from .analysis_focus import select_analysis_focus
from .professional_refs import (
    REFERENCE_OUTPUT_CONTRACT,
    DraftValidationIssue,
    build_reference_payload,
    parse_professional_draft,
    normalize_professional_draft_literals,
    resolve_professional_draft,
    validate_professional_draft,
)


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .book_case_transfer import BookCaseTransferPackage
PROFESSIONAL_PROMPT_VERSION = "professional-v46-reply-pressure-causality"
PROFESSIONAL_TOKEN_LIMITS = {"simple": 1500, "normal": 2600, "complex": 3400}
STRATEGY_TAGS = [
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
]
PROFESSIONAL_OUTPUT_CONTRACT = REFERENCE_OUTPUT_CONTRACT


@dataclass(frozen=True)
class ChatResult:
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    elapsed_ms: int


@dataclass(frozen=True)
class ProfessionalAttemptDiagnostic:
    attempt: int
    accepted: bool
    issues: list[DraftValidationIssue]
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    network_ms: int
    validation_ms: int
    postprocess_ms: int
    normalizations: list[DraftValidationIssue] = field(default_factory=list)


class ProfessionalAnalysisService:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        book_knowledge: UnifiedBookKnowledgeRepository | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = max(timeout_seconds, 120.0)
        self.book_knowledge = book_knowledge

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def analyze(
        self,
        move: MoveReview,
        diagnostics: list[ProfessionalAttemptDiagnostic] | None = None,
        *,
        threat_package: ThreatPackage | None = None,
        book_context: "BookCaseTransferPackage | None" = None,
        opening_context: OpeningPresentation | None = None,
        recent_moves: list[MoveReview] | None = None,
    ) -> GeneratedProfessionalAnalysis:
        complexity = compute_professional_complexity(move)
        fact_package = build_move_fact_package(move)
        if threat_package is None:
            threat_package = ThreatAnalyzer().classify(fact_package)
        elif threat_package.position_id != position_id(fact_package.position.fen):
            raise ValueError("ThreatPackage position does not match MoveReview")
        fact_package.threats = threat_package.threats
        initiative = assess_initiative(fact_package, threat_package)
        context = build_validation_context(
            move,
            complexity.level,
            initiative_side=initiative.side,
            threat_package=threat_package,
        )
        if not self.configured:
            narrative_claims = build_narrative_claim_package(
                move,
                threat_package=threat_package,
            )
            safe = _fit_resolved_analysis_length(
                _humanize_user_visible_prose(apply_hard_fact_guard(
                    build_safe_professional_analysis(
                        move,
                        complexity,
                        threat_package=threat_package,
                    ),
                    move,
                    threat_package=threat_package,
                    opening_context=opening_context,
                    narrative_claims=narrative_claims,
                )),
                move,
                complexity.level,
            )
            _apply_verified_narrative_surface_guard(safe, move, narrative_claims)
            safe = _fit_resolved_analysis_length(safe, move, complexity.level)
            errors = validate_professional_analysis(safe, context)
            if errors:
                raise RuntimeError("安全专业分析未通过事实校验")
            return GeneratedProfessionalAnalysis(
                analysis=safe,
                complexity_reasons=complexity.reasons,
                validation_warnings=["服务端尚未配置DeepSeek，已使用统一事实包生成安全结果。"],
                usage=_usage([]),
            )
        strategic_plan_package = StrategicPlanAnalyzer().analyze(
            fact_package,
            position_facts=move.position_facts,
            threat_package=threat_package,
        )
        fact_package.plans = strategic_plan_package.plans
        payload = build_professional_payload(
            move,
            complexity,
            context.allowed_evidence_ids,
            fact_package=fact_package,
            threat_package=threat_package,
            plan_package=strategic_plan_package,
        )
        narrative_claims = NarrativeClaimPackage.model_validate(payload["narrativeClaims"])
        objective = payload["positionInterpretation"]["objective"]
        decision_context = build_decision_context(
            move,
            recent_moves or [],
            objective_kind=objective["kind"],
            objective_question=objective["primary_question"],
        )
        payload["decisionContext"] = decision_context.prompt_payload()
        if book_context is None and self.book_knowledge is not None:
            try:
                priority = payload.get("decisionPriority", {})
                theme_hints = [
                    priority.get("primary_theme"),
                    *priority.get("supporting_themes", []),
                ]
                knowledge_context = self.book_knowledge.analysis_context(
                    move.before_fen,
                    theme_hints=[item for item in theme_hints if isinstance(item, str)],
                    played_move_uci=move.played_move.uci,
                    best_move_uci=move.best_move_uci,
                )
                if knowledge_context.excerpts:
                    payload["bookKnowledgeContext"] = knowledge_context.prompt_payload()
            except Exception as exc:
                logger.warning("Optional unified book context unavailable: %s", exc)
        if opening_context is not None:
            payload["confirmedOpening"] = opening_context.prompt_payload()
        if book_context is not None and book_context.cases:
            payload["analogousBookContext"] = book_context.prompt_payload()
        system = professional_system_prompt()
        prompt = professional_user_prompt(payload, complexity.level)
        usage_results: list[ChatResult] = []
        last_issues: list[DraftValidationIssue] = []
        all_issues: list[DraftValidationIssue] = []
        parsed: ProfessionalAnalysis | None = None
        validation_ms = 0
        postprocess_ms = 0
        transport_error: str | None = None

        for attempt in range(2):
            current_prompt = prompt
            if attempt:
                current_prompt += (
                    "\n\n上一次返回未通过程序校验。错误如下：\n- "
                    + "\n- ".join(_compact_validation_errors([issue.render() for issue in last_issues]))
                    + "\n请只修正这些引用或字段，不要重新输入棋子、格子、SAN或UCI。"
                )
            try:
                result = await self._chat(
                    system=system,
                    prompt=current_prompt,
                    max_tokens=PROFESSIONAL_TOKEN_LIMITS[complexity.level],
                    temperature=0.0,
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                transport_error = str(exc)
                logger.warning("Professional DeepSeek unavailable; using safe fallback: %s", exc)
                break
            usage_results.append(result)
            validation_started = time.perf_counter()
            draft, last_issues = parse_professional_draft(result.content)
            normalizations: list[DraftValidationIssue] = []
            if draft is not None:
                draft, normalizations = normalize_professional_draft_literals(draft, move, context)
                last_issues.extend(validate_professional_draft(
                    draft,
                    move,
                    context,
                    strategic_plan_package=strategic_plan_package,
                ))
                invalid_claim_refs = sorted(
                    set(draft.played_move_analysis.claim_refs) - narrative_claims.claim_ids
                )
                if invalid_claim_refs:
                    last_issues.append(DraftValidationIssue(
                        path="playedMoveAnalysis.claimRefs",
                        category="棋理命题引用",
                        message="引用了不存在的棋理命题：" + "、".join(invalid_claim_refs),
                    ))
                if not draft.played_move_analysis.claim_refs:
                    draft.played_move_analysis.claim_refs = narrative_claims.recommended_claim_refs
                    normalizations.append(DraftValidationIssue(
                        path="playedMoveAnalysis.claimRefs",
                        category="棋理命题引用",
                        message="模型未选择命题，后端使用程序推荐的已验证命题",
                    ))
            attempt_validation_ms = round((time.perf_counter() - validation_started) * 1000)
            validation_ms += attempt_validation_ms

            attempt_postprocess_ms = 0
            if draft is not None and not last_issues:
                postprocess_started = time.perf_counter()
                parsed = resolve_professional_draft(
                    draft,
                    move,
                    context,
                    strategic_plan_package=strategic_plan_package,
                )
                parsed = apply_hard_fact_guard(
                    parsed,
                    move,
                    threat_package=threat_package,
                    opening_context=opening_context,
                    narrative_claims=narrative_claims,
                )
                parsed = _humanize_user_visible_prose(parsed)
                parsed, claim_normalizations = normalize_program_owned_claims(
                    parsed,
                    context,
                )
                normalizations.extend(
                    DraftValidationIssue(
                        path=path,
                        category="硬事实保护",
                        message="已整句重建越界的程序专属结论",
                    )
                    for path in claim_normalizations
                )
                parsed = _fit_resolved_analysis_length(parsed, move, complexity.level)
                _apply_verified_narrative_surface_guard(parsed, move, narrative_claims)
                parsed = _fit_resolved_analysis_length(parsed, move, complexity.level)
                attempt_postprocess_ms = round((time.perf_counter() - postprocess_started) * 1000)
                postprocess_ms += attempt_postprocess_ms
                resolved_started = time.perf_counter()
                resolved_errors = validate_professional_analysis(
                    parsed,
                    context,
                    enforce_core_explanation=True,
                )
                resolved_validation_ms = round((time.perf_counter() - resolved_started) * 1000)
                validation_ms += resolved_validation_ms
                attempt_validation_ms += resolved_validation_ms
                last_issues.extend(_resolved_validation_issue(error) for error in resolved_errors)
            accepted = parsed is not None and not last_issues
            all_issues.extend(last_issues)
            if diagnostics is not None:
                diagnostics.append(ProfessionalAttemptDiagnostic(
                    attempt=attempt + 1,
                    accepted=accepted,
                    issues=list(last_issues),
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.total_tokens,
                    network_ms=result.elapsed_ms,
                    validation_ms=attempt_validation_ms,
                    postprocess_ms=attempt_postprocess_ms,
                    normalizations=normalizations,
                ))
            if accepted:
                return GeneratedProfessionalAnalysis(
                    analysis=parsed,
                    complexity_reasons=complexity.reasons,
                    usage=_usage(
                        usage_results,
                        validation_ms=validation_ms,
                        postprocess_ms=postprocess_ms,
                    ),
                )
            logger.warning(
                "Professional DeepSeek validation failed on attempt %s: %s",
                attempt + 1,
                [issue.render() for issue in last_issues],
            )

        postprocess_started = time.perf_counter()
        safe = _fit_resolved_analysis_length(
            _humanize_user_visible_prose(apply_hard_fact_guard(
                build_safe_professional_analysis(
                    move,
                    complexity,
                    threat_package=threat_package,
                ),
                move,
                threat_package=threat_package,
                opening_context=opening_context,
                narrative_claims=narrative_claims,
            )),
            move,
            complexity.level,
        )
        _apply_verified_narrative_surface_guard(safe, move, narrative_claims)
        safe = _fit_resolved_analysis_length(safe, move, complexity.level)
        postprocess_ms += round((time.perf_counter() - postprocess_started) * 1000)
        validation_started = time.perf_counter()
        safe_errors = validate_professional_analysis(safe, context)
        validation_ms += round((time.perf_counter() - validation_started) * 1000)
        if safe_errors:
            logger.error("Safe professional analysis failed validation: %s", safe_errors)
            raise RuntimeError("安全专业分析未通过事实校验")
        warnings = (
            ["DeepSeek暂不可用，已直接使用统一事实包生成安全结果。"]
            if transport_error is not None
            else ["DeepSeek两次返回均未通过校验，已删除不可信内容并使用结构化事实生成安全结果。"]
        )
        warnings.extend(issue.render() for issue in all_issues)
        return GeneratedProfessionalAnalysis(
            analysis=safe,
            complexity_reasons=complexity.reasons,
            validation_warnings=warnings,
            usage=_usage(
                usage_results,
                validation_ms=validation_ms,
                postprocess_ms=postprocess_ms,
            ),
        )

    async def _chat(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> ChatResult:
        started = time.perf_counter()
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
        data: dict[str, Any] | None = None
        for transport_attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    )
                    response.raise_for_status()
                    data = response.json()
                break
            except httpx.TransportError:
                if transport_attempt:
                    raise
                logger.warning("DeepSeek transport interrupted; retrying once without logging request headers")
        if data is None:
            raise RuntimeError("DeepSeek专业分析未返回响应")
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("DeepSeek专业分析返回了空内容")
        usage = data.get("usage") or {}
        return ChatResult(
            content=content,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            total_tokens=_optional_int(usage.get("total_tokens")),
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )


def compute_professional_complexity(move: MoveReview) -> ProfessionalComplexity:
    reasons: list[str] = []
    score = 0
    checks = len(move.position_facts.immediate_checks)
    captures = len(move.position_facts.immediate_captures)
    if checks >= 2:
        score += 2
        reasons.append(f"当前有{checks}个合法将军选择")
    elif checks:
        score += 1
        reasons.append("当前存在合法将军选择")
    if captures >= 4:
        score += 2
        reasons.append(f"当前有{captures}个合法吃子选择")
    elif captures:
        score += 1
        reasons.append(f"当前有{captures}个合法吃子选择")

    all_lines = [*move.candidate_lines, *([move.actual_move_line] if move.actual_move_line else [])]
    all_moves = [item for line in all_lines for item in line.moves]
    if any(item.checkmate for item in all_moves) or any(line.mate_in is not None for line in all_lines):
        score += 3
        reasons.append("Stockfish路线包含将杀信息")
    if any(fact.category == "direct_piece_loss" for fact in move.position_facts.threats):
        score += 2
        reasons.append("参考路线中存在可以验证的直接丢子")
    swing = move.complexity_factors.evaluation_swing_cp
    if swing is not None and swing >= 200:
        score += 2
        reasons.append(f"实战走法前后评价变化达到{swing}厘兵")
    elif swing is not None and swing >= 90:
        score += 1
        reasons.append(f"实战走法前后评价变化达到{swing}厘兵")

    scored = [_mover_score(line.centipawn, line.mate_in, move.side) for line in move.candidate_lines]
    scored = [value for value in scored if value is not None]
    if len(scored) >= 2 and scored[0] - scored[1] >= 100:
        score += 2
        reasons.append("第一候选比第二候选高至少100厘兵，合理选择较集中")
    forcing = max((_forcing_prefix(line.moves) for line in all_lines), default=0)
    if forcing >= 3:
        score += 2
        reasons.append(f"Stockfish路线包含连续{forcing}个强制半回合")
    nearby = sum(1 for fact in move.position_facts.king_safety if fact.category == "nearby_attackers")
    if nearby:
        score += 1
        reasons.append("至少一方王区附近存在对方棋子")
    exposed = sum(
        1 for fact in move.position_facts.piece_activity
        if fact.category in {"undefended_piece", "underprotected"}
    )
    if exposed >= 3:
        score += 1
        reasons.append(f"当前有{exposed}条未保护或保护不足的棋子事实")
    structural = sum(
        1 for fact in move.position_facts.pawn_structure
        if fact.category in {"isolated_pawn", "doubled_pawns", "vulnerable_pawn", "open_file", "half_open_file"}
    )
    if structural >= 4:
        score += 1
        reasons.append(f"兵结构与开放线相关事实有{structural}条")
    if move.complexity_factors.only_reasonable_move:
        score += 2
        reasons.append("引擎评价显示只有一个合理走法")
    signatures = {_line_signature(line) for line in move.candidate_lines}
    if len(signatures) >= 2:
        score += 1
        reasons.append("候选首着使用不同棋子或作用于不同棋盘区域")

    level = "complex" if score >= 6 else "normal" if score >= 2 else "simple"
    if not reasons:
        reasons.append("没有检测到多重强制变化、明显评价波动或集中战术事件")
    return ProfessionalComplexity(level=level, reasons=reasons)


def build_professional_payload(
    move: MoveReview,
    complexity: ProfessionalComplexity,
    allowed_evidence_ids: set[str],
    *,
    fact_package: ChessFactPackage | None = None,
    threat_package: ThreatPackage | None = None,
    plan_package: StrategicPlanPackage | None = None,
) -> dict[str, Any]:
    # The context still owns the complete allow-list for server-side validation.
    # DeepSeek receives each current-position fact once and refers to it by ID.
    del allowed_evidence_ids
    package = fact_package or build_move_fact_package(move)
    classified_threats = threat_package or ThreatAnalyzer().classify(package)
    package.threats = classified_threats.threats
    if plan_package is not None:
        strategic_plan_package = plan_package
        package.plans = strategic_plan_package.plans
    elif not package.plans:
        strategic_plan_package = StrategicPlanAnalyzer().analyze(
            package,
            position_facts=move.position_facts,
            threat_package=classified_threats,
        )
        package.plans = strategic_plan_package.plans
    else:
        strategic_plan_package = StrategicPlanPackage(
            position_id=package.position.fen,
            plans=package.plans,
        )
    interpretation = build_position_interpretation(
        package,
        position_facts=move.position_facts,
        threat_package=classified_threats,
        plan_package=strategic_plan_package,
    )
    reasoning_rules = ChessReasoningRuleEngine().evaluate(
        package.position.fen,
        fact_package=package,
        threat_package=classified_threats,
        plan_package=strategic_plan_package,
    )
    factor_ranking = PositionFactorRanker().rank(reasoning_rules)
    importance = PositionImportanceRanker().rank(
        factor_ranking,
        fact_package=package,
        threat_package=classified_threats,
        plan_package=strategic_plan_package,
        interpretation=interpretation,
    )
    evaluation_style = build_book_evaluation_style(interpretation, importance)
    payload = build_reference_payload(move, complexity.level, complexity.reasons)
    payload.get("pos", {}).pop("fen", None)
    payload["chessFacts"] = package.protocol_manifest()
    payload["positionInterpretation"] = interpretation.prompt_payload()
    payload["decisionPriority"] = importance.model_dump(exclude={"forbidden_claims"})
    payload["bookEvaluationMethod"] = evaluation_style.prompt_payload()
    payload["narrativeClaims"] = build_narrative_claim_package(
        move,
        priority_evidence_ids=interpretation.objective.evidence_ids,
        threat_package=classified_threats,
        plan_package=strategic_plan_package,
    ).prompt_payload()
    payload["interpretationPolicy"] = {
        "initiative": assess_initiative(
            package,
            classified_threats,
        ).model_dump(),
        "hardFacts": "program_controlled",
    }
    return payload


def _compact_prompt_value(value: Any) -> Any:
    """Drop duplicated human-readable derivation notes while retaining facts, IDs and squares."""
    if isinstance(value, dict):
        return {
            key: _compact_prompt_value(child)
            for key, child in value.items()
            if key != "evidence"
        }
    if isinstance(value, list):
        return [_compact_prompt_value(child) for child in value]
    return value


def professional_system_prompt() -> str:
    return (
        "你只负责解释后端提供的国际象棋事实引用，不负责重新抄写或计算棋盘。"
        "你的首要任务是围绕当前局面中最影响决策的一个重点，写出初学者能读懂的棋书式讲解。"
        "不要把所有棋盘事实都写进分析，只有focus.selectedFacts允许进入最终结论。"
        "候选路线必须用lineRef，PV必须用plyRefs，事实必须用evidenceRefs。"
        "解释必须以中文为主，并优先直接写事实目录中已有的具体棋子、格子和SAN走法；"
        "只能使用输入中已经出现的格子和SAN，禁止自行编造UCI、吃子、将军、将杀或绝杀。"
        "不能引用输入目录之外的ID，不能把白方与黑方说反。证据不足时返回空数组、null或isRelevant为false。"
        "没有保护不等于弱点，王前兵较少不等于存在攻王，没有易位权不等于王不安全。"
        "单条Stockfish路线中的普通吃子不等于全局潜在威胁，物质数量不作为固定栏目。"
        "物质差、双方王位置、易位边界、实战着是否与首选一致、评价方向和走法质量由后端模板生成，"
        "自由文本不得重新表述这些硬事实。"
        "Stockfish分数不能直接推出主动权；interpretationPolicy.initiative.side为unknown时，"
        "禁止使用主动权、掌握主动、攻势完全在某方手中等结论。"
        "positionInterpretation.themes中scope为candidate_route的战术只能解释对应候选路线，"
        "不得升级为当前局面已经存在的直接威胁。"
        "positionInterpretation.objective是程序选定的首要分析任务，必须先回答该问题；"
        "deemphasizedTopics中的内容不得作为分析主线。"
        "decisionContext.corePainPoint是程序根据当前事实与最近决策信号选出的解释核心，最终解释必须"
        "先回答它；recentSignals不能用来猜测棋手心理，也不能证明不同失误属于同一种棋理错误。"
        "围绕corePainPoint形成一条连续的棋书式讲解主线：第一句直接给出核心判断，并把正文完整写入"
        "playedMoveAnalysis.intention。按bookEvaluationMethod.narrative_path组织局面结论、具体原因、选择与代价，"
        "按prose_rules控制文风；这些是写作顺序，不输出内部思考过程或三个固定标题。"
        "先核对事实包的走前行棋方与实战落子方，区分走后轮到谁；从实战落子方的选择解释客观作用，不猜主观动机。"
        "正文只保留解释机制必需的短变化，最多出现实战着和一手直接回应；需要更多着法才能成立的后果，"
        "在对应路线区完整证明，核心正文只解释已验证机制，不能省略中间条件后写成即时结果。"
        "不得把后续验证路线中的零散事件搬进正文。变化只用于证明文字，不能用着法列表代替解释。"
        "只保留理解核心问题所需的信息，不罗列全部评价维度。每个结论都必须由事实包或短变化支持。"
        "bookEvaluationMethod只规定棋书式评价顺序：先判断，再解释机制和后果，随后按需比较路线、"
        "指出对手资源并落到计划；它不能增加任何当前局面事实。"
        "bookKnowledgeContext若存在，只用于学习棋书作者选择重点、解释因果和组织语言的方法；"
        "analogous_position与principle_only摘录都不是当前局面事实，禁止复制其中的棋子、格子、"
        "着法、评价或结论。"
        "narrativeClaims是核心正文唯一允许表达的棋理命题目录。模型只能通过claimRefs选择命题，"
        "不得扩展命题中的因果、目标、计划或时序；后端会按claimRefs将核心正文重建为"
        "没有固定栏目口号的连贯棋书叙述。"
        "confirmedOpening若存在，其名称、ECO和变例由程序确认；不得重新判断、改名或补写其他变例。"
        "开局背景只是常见思路，只有当前事实包另有支持时才能把它表述成当前局面的事实或计划。"
        "PV只是参考变化，不是必然发生。"
        "所有解释必须是完整、自然、可直接展示给用户的中文棋理句子。"
        "禁止在解释中出现事实依据、判断依据、根据某事实可以判断、证据数量、内部变量名或引用ID。"
        "不要输出white、black、pawn、knight、bishop、rook、queen、king等程序化名称。"
        "输入中的战略计划由程序确认。禁止创建计划、修改计划类型或扩展计划；"
        "只能通过planId解释已有计划，plans.white和plans.black必须保持空数组。"
        "不要使用只有几个字的模板短语，例如‘巩固中心，准备’或‘暂时减缓发展’，必须说明具体作用、后续准备和局面影响。"
        "说话顺序要像教练带读者看棋：先讲局面究竟好在哪里，再讲这步改变了什么及已验证效果。"
        "不要把‘评价差距不足以’‘验证路线显示’‘下一次先检查’等分析过程或自检步骤写给用户。"
        "不要先报分数、栏目或校验过程，也不要使用‘当前应继续比较路线’‘该项不作额外评价’‘作为路线起点’"
        "‘程序记录’‘评价方向由’等报告腔。允许使用中心张力、支点、弱格、开放线、交换次序、"
        "子力协调、强制变化等专业术语，但术语后必须紧跟具体棋子、格子、路线或直接后果；"
        "禁止只用‘逐步施压’‘导致局面恶化’等抽象结论代替分析，也不要使用‘去问它’‘撞中心’"
        "等过度口语化比喻。PV中的后续收益若不是紧接着发生，必须交代中间着法，不能写成即时结果。"
    )


def professional_user_prompt(payload: dict[str, Any], complexity: str) -> str:
    length = {"simple": "180—300", "normal": "320—500", "complex": "550—800"}[complexity]
    compact_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    compact_contract = json.dumps(PROFESSIONAL_OUTPUT_CONTRACT, ensure_ascii=False, separators=(",", ":"))
    line_skeleton = json.dumps(
        [
            {
                "lineRef": line["id"],
                "plyRefs": [item["id"] for item in line.get("plies", [])],
            }
            for line in payload.get("lines", [])
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    strategy_tags = ",".join(STRATEGY_TAGS)
    analogous_rule = ""
    if payload.get("analogousBookContext"):
        analogous_rule = (
            "\n15. analogousBookContext只提供相似棋书案例的观察角度和思考顺序，不属于当前局面事实。"
            "不得复制其中的棋子、格子、走法、评价、胜负或威胁；只有当前引用目录已有证据时才能借鉴解释角度。"
            "棋书案例ID不得进入任何Ref字段，变化内部事件不得升级为当前事件。"
        )
    opening_rule = ""
    if payload.get("confirmedOpening"):
        opening_rule = (
            "\n16. confirmedOpening的名称、ECO和变例已经由程序锁定，不得重新识别或输出其他名称。"
            "background只提供该开局的常见思路，不能覆盖当前局面的Stockfish评价、事实、计划或威胁；"
            "只有chessFacts或positionInterpretation同时支持时，才能借鉴其解释角度。"
            "若当前仍在开局阶段，先交代已确认的开局名称和常见战略方向。开局通常存在多种合理选择；"
            "除非程序质量和评价损失明确证明是失误，不得把Stockfish首选写成唯一正确的开局原则，"
            "只说明它在当前局面优先解决了什么问题。"
        )
    knowledge_rule = ""
    if payload.get("bookKnowledgeContext"):
        knowledge_rule = (
            "\n17. bookKnowledgeContext中的exact_current_position表示完整合法状态相同，但其中的着法、"
            "评价和战术事件仍必须由当前引用目录支持；analogous_position与principle_only只用于"
            "借鉴判断顺序、因果解释和自然棋书语言。禁止把来源摘录中的棋子、格子、着法、胜负、"
            "主动权或计划写入当前结论。"
        )
    return f"""请根据以下引用目录生成分析草稿：
{compact_payload}

严格规则：
1. candidateLines必须恰好返回{len(payload.get('lines', []))}项，lineRef按lines顺序逐条引用；每条路线只返回一个plyRefs数组，必须按顺序完整覆盖该路线plies[].id，不能串线。本次不可改动的引用骨架为：{line_skeleton}
2. playedMoveAnalysis.moveRef必须等于played.ref；strongestReplyRef及唯一的plyRefs数组只能来自并完整覆盖actual.plies。
3. evidenceRefs、dangerRef只能引用输入中出现的ID。每组evidenceRefs只选1—4个最相关ID，不要枚举整份事实目录。每个危险、计划和因果结论必须有证据。
4. 自由文本以中文为主；为说清棋理，可以直接使用输入目录已经出现的具体棋子、格子和SAN走法，但不得写任何UCI，也不得写目录之外的格子或SAN。只有对应ply明确包含时才能写“吃子、将军、将杀、绝杀”。正确示例是明确写出“白马从f3跳到g5”，不要用“该棋子来到该格”回避具体对象。
5. mainDanger有具体危险时用dangerRef引用一个已有ply；无可靠直接危险时dangerRef写null且level写none。危险一方由后端从ply推导，不要输出sideInDanger。
6. positionAssessment只允许输出summary，不得输出material、kingSafety、pieceActivity或pawnStructure；这些动态栏目全部由后端重点选择器按selectedFacts回填。
7. positionAssessment.summary必须是围绕corePainPoint的完整段落，只说明理解核心问题必需的局面条件。不得为了显得全面而同时罗列子力、王安全、中心和两翼；不能只写“当前局面某方子”之类残句。
8. plans.white和plans.black必须返回空数组。战略计划只能通过planExplanations按chessFacts.plans中的plan_id解释；没有程序计划时planExplanations返回空数组。禁止创建planId、修改计划类型或增加棋步。
9. playedMoveAnalysis.claimRefs必须从narrativeClaims.claims中选择1—6项，覆盖走前全局态势和实战选择；存在引擎比较时纳入比较，存在position_cause时必须优先选择它。intention只说明所选命题的组织意图，后端将按这些claimRefs重建页面核心正文。不得在intention增加命题目录之外的因果、计划、目标或时序。最终正文按“局面结论 → 实战选择与代价 → 已验证的具体原因”自然推进，不显示分析流程、校验过程或教学检查清单；从实战落子方角度解释，不能把走完这步后轮到的一方说反。多步后果必须保留命题中已经验证的中间着法。positiveEffects和problems只记录必要补充，不重复评价。
10. 每条candidateLines的directPurpose、continuationExplanation、advantages和risks必须使用完整具体中文；优点和风险要说明对子力、空间、兵形或线路的实际影响，不能只写标签。用棋手复盘时会说的短句直接讲清“为什么”和“接下来怎样”，避免“阶段性、当前交换段、实际结果、符合当前局面需求、继续比较路线、作为路线起点、该项不作评价”等报告腔套话。
11. 弱点、王安全、子力活动、兵形、全局威胁与路线内部事件由后端重点选择器生成，不要输出这些字段；不要自行拆分PV阶段。strategyTags只能使用：{strategy_tags}。
12. 草稿解释文字目标为{length}个中文字符；后端会追加结构化事实并回填真实走法。complexity必须是{complexity}。
13. 物质差、王位置、易位、评价方向、走法质量以及实战着是否与首选一致全部由程序填写。自由文本不得重写。interpretationPolicy.initiative.side为unknown时，禁止声称任何一方拥有主动权；不得把Stockfish分数直接解释成主动权。
14. 必须先回答positionInterpretation.objective.primaryQuestion，并围绕priorityTopics组织局面概览、实战着解释和路线比较。deemphasizedTopics不得成为主线。winning_conversion应解释优势方如何兑现；attack_conversion应解释攻势配合和防守资源；endgame_plan不得在没有直接危险时泛谈护王；dynamic_balance应比较活动性与静态因素；move_quality_explanation必须按真实评价差控制批评强度。
15. bookEvaluationMethod.narrative_path规定正文叙述顺序，bookEvaluationMethod.prose_rules规定语言边界，steps规定证据支持时需要解释的内容：局面评价与本步分差是两个问题；小分差只说明危机不是由本步造成，不能代替对全局优劣原因的解释。对手资源与计划只在已有证据时解释。required不能要求补造事实。短变化只证明已经说清的因果关系，不得代替中文解释。用鲜明判断、具体因果和克制修辞形成棋书文风，不复刻特定作者，不猜测棋手心理，不为戏剧性虚构惩罚或陷阱。
16. decisionContext.corePainPoint和mustAnswer是本次讲解的最高优先级。先回答痛点，只保留理解痛点所需的信息，再用必要的首选路线、对手直接回应和后果证明；不要先罗列物质、王位置、三条路线或全部评价维度。若实战着与首选着评价损失小于半兵，只说明局面问题早已存在，不得拿小分差冒充全局原因。完成草稿前按bookEvaluationMethod.reader_checks自检；自检过程不得写入正文。trend只表示程序确认的近期决策现象，禁止推断棋手心理、习惯或水平。
{analogous_rule}
{opening_rule}
{knowledge_rule}

只返回与以下契约完全一致的JSON，不要Markdown或额外字段。数组对象表示元素结构：
{compact_contract}"""


def professional_cache_key(
    move: MoveReview,
    *,
    stockfish_version: str,
    stockfish_depth: int,
    opening_id: str | None = None,
    recent_moves: list[MoveReview] | None = None,
) -> str:
    route_summary = [
        {
            "rank": line.rank,
            "depth": line.depth,
            "evaluation": line.centipawn,
            "mate": line.mate_in,
            "moves": [item.uci for item in line.moves],
        }
        for line in move.candidate_lines
    ]
    raw = json.dumps(
        {
            "fen": move.before_fen,
            "playedMove": move.played_move.uci,
            "stockfishVersion": stockfish_version,
            "stockfishDepth": stockfish_depth,
            "multiPv": len(move.candidate_lines),
            "routes": route_summary,
            "promptVersion": PROFESSIONAL_PROMPT_VERSION,
            "factPackageVersion": CHESS_FACT_PACKAGE_VERSION,
            "threatPackageVersion": THREAT_PACKAGE_VERSION,
            "strategicPlanPackageVersion": STRATEGIC_PLAN_PACKAGE_VERSION,
            "positionInterpretationVersion": POSITION_INTERPRETATION_VERSION,
            "bookKnowledgeContextVersion": UNIFIED_BOOK_CONTEXT_VERSION,
            "decisionContextVersion": DECISION_CONTEXT_VERSION,
            "narrativeClaimVersion": NARRATIVE_CLAIM_VERSION,
            "decisionHistory": decision_history_signature(recent_moves or []),
            "openingId": opening_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_safe_professional_analysis(
    move: MoveReview,
    complexity: ProfessionalComplexity,
    *,
    threat_package: ThreatPackage | None = None,
) -> ProfessionalAnalysis:
    focus = select_analysis_focus(move)
    fact_package = build_move_fact_package(move)
    classified_threats = threat_package or ThreatAnalyzer().classify(fact_package)
    fact_package.threats = classified_threats.threats
    confirmed_threat_moves = {
        san
        for threat in classified_threats.threats
        for san in threat.supporting_moves
    }
    played_ref = move.played_move.id or f"move:played:{move.index}"
    raw_by_id = {
        fact.id: fact
        for group in (
            move.position_facts.piece_activity,
            move.position_facts.king_safety,
            move.position_facts.pawn_structure,
            move.position_facts.threats,
        )
        for fact in group
    }

    danger_side = "none"
    danger_level = "long_term"
    danger_description = "结构化事实没有确认需要立即处理的单一危险，证据不足，无法可靠判断更具体的威胁。"
    danger_consequence = "继续比较棋规库列出的强制走法和Stockfish第一路线，不补写未验证后果。"
    danger_refs = [played_ref]
    threat_pairs = [
        (fact, source)
        for fact in focus.global_threats
        for source in (*move.position_facts.immediate_checks, *move.position_facts.immediate_captures)
        if source.id in fact.evidence_refs and source.san in confirmed_threat_moves
    ]
    top_threat, threat_source = threat_pairs[0] if threat_pairs else (None, None)
    if top_threat is not None and threat_source is not None:
        danger_side = "black" if top_threat.side == "white" else "white"
        danger_level = "immediate"
        danger_description = (
            f"{_human_side_text(danger_side)}的直接危险来自"
            f"{_human_piece_text(threat_source.piece, top_threat.side)}从"
            f"{threat_source.from_square}走到{threat_source.to_square}的参考着{threat_source.san}。"
        )
        danger_consequence = top_threat.decision_impact
        danger_refs = list(top_threat.evidence_refs)

    plans = {"white": [], "black": []}
    strategic_package = StrategicPlanAnalyzer().analyze(
        fact_package,
        position_facts=move.position_facts,
        threat_package=classified_threats,
    )
    for plan in strategic_package.plans:
        if plan.confidence != "high":
            continue
        plans[plan.side].append(
            {
                "strategyTag": _safe_strategic_plan_tag(plan.type),
                "description": plan.goal,
                "requiredPreparation": "；".join(plan.structural_evidence),
                "evidenceRefs": list(plan.evidence_route_ids),
            }
        )

    weaknesses = {"white": [], "black": []}
    for side, items in focus.weaknesses.items():
        for fact in items:
            weaknesses[side].append(
                {
                    "description": fact.description,
                    "exploitation": fact.decision_impact,
                    "evidenceRefs": list(fact.evidence_refs),
                }
            )
    threats = []
    for fact in focus.global_threats:
        source = next(
            (
                item
                for item in (*move.position_facts.immediate_checks, *move.position_facts.immediate_captures)
                if item.id in fact.evidence_refs and item.san in confirmed_threat_moves
            ),
            None,
        )
        if source is None:
            continue
        if fact.side in {"white", "black"}:
            threats.append(
                {
                    "side": fact.side,
                    "level": "immediate" if fact.importance_score >= 4 else "short_term",
                    "scope": "current_position",
                    "description": fact.description,
                    "target": "、".join(fact.squares) or "证据不足，无法可靠判断具体目标",
                    "attacker": fact.squares[0] if fact.squares else "证据中的攻击棋子",
                    "preparation": "当前局面已经具备执行条件。",
                    "consequence": fact.decision_impact,
                    "evidenceRefs": list(fact.evidence_refs),
                }
            )
    for threat in classified_threats.prepared_threats:
        threats.append({
            "side": threat.side,
            "level": "short_term",
            "scope": "current_position",
            "description": (
                f"这是准备型威胁：{'白方' if threat.side == 'white' else '黑方'}计划以"
                f"{'、'.join(threat.preparation_moves)}形成{threat.type}。"
            ),
            "target": threat.target or "程序未指定单一目标",
            "attacker": "、".join(threat.preparation_moves),
            "preparation": (
                f"程序验证了对手选择{threat.ignore_test.ignored_move or '中性应手'}后的结果，"
                f"忽略该构想至少会损失{threat.ignore_test.evaluation_loss:.2f}兵的评价。"
                if threat.ignore_test.evaluation_loss is not None
                else "准备步骤已经确认，但评价损失待确认。"
            ),
            "consequence": "对手不能把这一构想当作普通PV事件安全忽略。",
            "evidenceRefs": list(threat.evidence_route_ids),
        })

    actual = move.actual_move_line
    actual_phases = _safe_phases(actual.moves if actual else [], 3)
    strongest = actual.first_move.san if actual else "证据不足，无法可靠判断"
    candidate_analyses = []
    for line in move.candidate_lines:
        first = line.moves[0] if line.moves else None
        events = [
            {
                "scope": event.scope,
                "description": event.description,
                "significance": event.decision_impact,
                "evidenceRefs": list(event.evidence_refs),
            }
            for event in focus.line_events.get(line.rank, ())
        ]
        candidate_analyses.append(
            {
                "rank": line.rank,
                "firstMove": line.first_move.san,
                "strategyTags": [_safe_strategy_tag(first)] if first else [],
                "directPurpose": (
                    f"第一步走{line.first_move.san}，"
                    f"{_human_piece_text(first.piece, first.side)}从{first.from_square}来到{first.to_square}。"
                    if first else "这条变化没有给出第一步。"
                ),
                "opponentResponse": line.moves[1].san if len(line.moves) > 1 else "路线未提供对手回应",
                "continuationPhases": _safe_phases(line.moves, 3),
                "resultingPosition": _result_position_text(line),
                "advantages": [],
                "risks": [],
                "events": events,
                "whyThisRank": "",
                "evidenceRefs": [line.id, *([first.id] if first else [])],
            }
        )

    first_line = move.candidate_lines[0] if move.candidate_lines else None
    comparison_refs = [line.id for line in move.candidate_lines] or [played_ref]
    activity_ids = {item.id for item in move.position_facts.piece_activity}
    pawn_ids = {item.id for item in move.position_facts.pawn_structure}
    activity_focus = next(
        (item for item in focus.selected_facts if item.id in activity_ids and item.display_section == "positionAssessment"),
        None,
    )
    pawn_focus = next(
        (item for item in focus.selected_facts if item.id in pawn_ids and item.display_section == "positionAssessment"),
        None,
    )
    activity = raw_by_id.get(activity_focus.id) if activity_focus else None
    pawn = raw_by_id.get(pawn_focus.id) if pawn_focus else None
    king_payload: dict[str, Any] = {"isRelevant": bool(focus.king_safety_relevant_sides)}
    for side in ("white", "black"):
        selected = [
            item for item in focus.selected_facts
            if item.display_section == "kingSafety" and item.side == side
        ]
        king_payload[side] = (
            {
                "description": "；".join(item.description for item in selected),
                "evidenceRefs": [item.id for item in selected],
            }
            if selected else None
        )
    safe_payload = {
        "complexity": complexity.level,
        "positionAssessment": {
            "summary": f"当前由{move.side}行棋；只展示会影响本回合决策的事实和Stockfish参考路线。",
            "kingSafety": king_payload,
            "pieceActivity": ({"description": activity.description, "evidenceRefs": [activity.id]} if activity else None),
            "pawnStructure": ({"description": pawn.description, "evidenceRefs": [pawn.id]} if pawn else None),
        },
        "mainDanger": {
            "sideInDanger": danger_side,
            "level": danger_level,
            "description": danger_description,
            "consequence": danger_consequence,
            "evidenceRefs": danger_refs,
        },
        "plans": plans,
        "weaknesses": weaknesses,
        "threats": threats,
        "playedMoveAnalysis": {
            "move": move.played_move.san,
            "intention": (
                f"实战着把{_human_piece_text(move.played_move.piece, move.side)}从"
                f"{move.played_move.from_square}走到{move.played_move.to_square}；"
                "主观意图证据不足，无法可靠判断。"
            ),
            "positiveEffects": [_played_event_text(move)],
            "problems": [f"评价从{move.before.evaluation}变为{move.after.evaluation}；根本战略原因证据不足时不补写。"],
            "strongestResponse": strongest,
            "continuationPhases": actual_phases,
            "resultingPosition": _result_position_text(actual) if actual else "棋局已经结束或没有续算路线。",
            "evaluationReason": "只确认结构化评价变化和参考路线，不猜测未验证原因。",
            "errorType": "tactical" if move.complexity_factors.direct_piece_loss else "none",
            "evidenceRefs": [move.played_move.id or f"move:played:{move.index}", f"evaluation:before:{move.index}", f"evaluation:after:{move.index}"],
        },
        "candidateLines": candidate_analyses,
        "comparison": {
            "mainDifference": "各路线的排名、评价和PV不同；未由事实确认的战略差异不作补写。",
            "whyFirstLineIsBest": (
                f"第一路线由Stockfish排在首位，首着为{first_line.first_move.san}。"
                if first_line else "当前没有可用候选路线。"
            ),
            "evidenceRefs": comparison_refs,
        },
    }
    analysis = ProfessionalAnalysis.model_validate(safe_payload)
    return _apply_safe_length_profile(analysis, move, complexity.level)


def _apply_safe_length_profile(
    analysis: ProfessionalAnalysis,
    move: MoveReview,
    level: str,
) -> ProfessionalAnalysis:
    """Keep the deterministic fallback inside the same length bands required from DeepSeek."""
    if level == "complex":
        return _fit_complex_safe_length(analysis, move)

    result = analysis.model_copy(deep=True)
    fact_limit = 1
    weakness_limit = 1
    threat_limit = 1 if level == "simple" else 2

    for side, target in (
        ("white", result.position_assessment.king_safety.white),
        ("black", result.position_assessment.king_safety.black),
    ):
        if target is None:
            continue
        facts = [fact for fact in move.position_facts.king_safety if fact.side == side][:fact_limit]
        if facts:
            target.description = "；".join(fact.description for fact in facts)
            target.evidence_refs = [fact.id for fact in facts]

    activity = move.position_facts.piece_activity[:fact_limit]
    if activity and result.position_assessment.piece_activity is not None:
        result.position_assessment.piece_activity.description = "；".join(fact.description for fact in activity)
        result.position_assessment.piece_activity.evidence_refs = [fact.id for fact in activity]
    pawns = move.position_facts.pawn_structure[:fact_limit]
    if pawns and result.position_assessment.pawn_structure is not None:
        result.position_assessment.pawn_structure.description = "；".join(fact.description for fact in pawns)
        result.position_assessment.pawn_structure.evidence_refs = [fact.id for fact in pawns]

    result.weaknesses.white = result.weaknesses.white[:weakness_limit]
    result.weaknesses.black = result.weaknesses.black[:weakness_limit]
    result.threats = result.threats[:threat_limit]
    for weakness in [*result.weaknesses.white, *result.weaknesses.black]:
        weakness.exploitation = "利用方式须以对应参考路线为准。"

    result.played_move_analysis.resulting_position = _short_result_position(move.actual_move_line)
    for phase in result.played_move_analysis.continuation_phases:
        phase.explanation = "按Stockfish顺序参考，不代表必然发生。"
    for line, source in zip(result.candidate_lines, move.candidate_lines):
        line.resulting_position = _short_result_position(source)
        line.advantages = ["这是Stockfish给出的合法候选。"]
        line.risks = ["路线之外证据不足。"]
        line.why_this_rank = f"Stockfish排名{line.rank}。"
        for phase in line.continuation_phases:
            phase.explanation = "按该PV顺序参考，不代表必然发生。"

    result.position_assessment.summary = f"{move.side}行棋；判断只引用事实包与Stockfish参考线。"
    for plans in (result.plans.white, result.plans.black):
        for plan in plans:
            plan.required_preparation = "路线外准备证据不足。"

    if level == "normal":
        return _trim_profile_max(result, level)

    if result.main_danger.side_in_danger == "none":
        result.main_danger.description = "未确认单一直接危险，证据不足。"
        result.main_danger.consequence = "继续比较合法强制着与第一参考线。"
    else:
        result.main_danger.consequence = "若进入该参考线，将出现已验证的吃子或将军。"
    for plans in (result.plans.white, result.plans.black):
        for plan in plans:
            plan.description = plan.description.replace("参考路线只确认", "PV确认").replace("走到", "到")
    result.played_move_analysis.intention = (
        f"{_human_piece_text(move.played_move.piece, move.side)}从"
        f"{move.played_move.from_square}到{move.played_move.to_square}；主观意图证据不足。"
    )
    result.played_move_analysis.problems = [f"评价{move.before.evaluation}变为{move.after.evaluation}。"]
    result.played_move_analysis.evaluation_reason = "只确认评价变化与参考线。"
    if move.actual_move_line:
        result.played_move_analysis.continuation_phases = _model_phases(move.actual_move_line.moves, 1)
        for phase in result.played_move_analysis.continuation_phases:
            phase.phase = "PV"
            phase.explanation = "按PV顺序参考。"
    result.played_move_analysis.resulting_position = _very_short_result_position(move.actual_move_line)
    result.weaknesses.white = []
    result.weaknesses.black = []
    for line, source in zip(result.candidate_lines, move.candidate_lines):
        first = source.moves[0] if source.moves else None
        if first:
            line.direct_purpose = (
                f"{_human_piece_text(first.piece, first.side)}从"
                f"{first.from_square}到{first.to_square}。"
            )
        line.continuation_phases = _model_phases(source.moves, 1)
        for phase in line.continuation_phases:
            phase.phase = "PV"
            phase.explanation = "按PV顺序参考。"
        line.resulting_position = _very_short_result_position(source)
    result.comparison.main_difference = "三线首着、顺序与评价不同。"
    result.comparison.why_first_line_is_best = "第一线由Stockfish排首位。"
    return _fit_resolved_analysis_length(result, move, level)


def apply_hard_fact_guard(
    analysis: ProfessionalAnalysis,
    move: MoveReview,
    *,
    threat_package: ThreatPackage | None = None,
    opening_context: OpeningPresentation | None = None,
    narrative_claims: NarrativeClaimPackage | None = None,
) -> ProfessionalAnalysis:
    """Replace protected conclusions with deterministic program-owned text."""
    result = analysis.model_copy(deep=True)
    result.position_assessment.summary = _controlled_position_summary(move)
    if opening_context is not None:
        result.position_assessment.summary = (
            f"这是{opening_context.display_name}。{opening_context.description}"
            f"{result.position_assessment.summary}"
        )
    result.played_move_analysis.evaluation_reason = _controlled_move_summary(move)
    if (
        move.best_move_uci
        and move.best_move_uci == move.played_move.uci
        and move.candidate_lines
    ):
        result.comparison.main_difference = (
            f"{move.played_move.san}本来就是这里的首选，关键是看懂它的作用。"
        )
        result.comparison.evidence_refs = list(dict.fromkeys([
            move.played_move.id,
            move.candidate_lines[0].id,
            *result.comparison.evidence_refs,
        ]))
    # These fields are displayed next to program-owned evaluation facts. Keep
    # them deterministic so model prose cannot reclassify a static score or
    # invent a danger when the program found none.
    if result.main_danger.side_in_danger == "none":
        result.main_danger.description = "眼前没有必须马上处理的单一威胁。"
        result.main_danger.consequence = "可以按自己的计划走，不必先做防守。"
    direct_threats = (
        [
            item for item in threat_package.threats
            if item.scope == "current_direct_threat"
        ]
        if threat_package is not None else []
    )
    if direct_threats:
        direct = direct_threats[0]
        piece_text, from_square, to_square = _threat_move_details(move, direct)
        supporting = "、".join(direct.supporting_moves)
        result.main_danger.side_in_danger = _opposite_side(direct.side)
        result.main_danger.level = "immediate"
        result.main_danger.description = (
            f"{piece_text}当前可以从{from_square}走到{to_square}（{supporting}），"
            f"程序确认这是{_professional_threat_name(direct.type)}。"
        )
        result.main_danger.consequence = (
            "条件化深度升级已经确认强制将杀，必须立即处理。"
            if direct.type == "mate_threat"
            else "这是根节点可立即执行的程序确认威胁，必须纳入本回合决策。"
        )
        result.main_danger.evidence_refs = [
            direct.threat_id,
            *direct.evidence_route_ids,
        ]
        direct_text = ProfessionalThreat(
            side=direct.side,
            level="immediate",
            scope="current_position",
            description=(
                f"当前直接威胁：{piece_text}可从{from_square}走到{to_square}"
                f"（{supporting}），形成{_professional_threat_name(direct.type)}。"
            ),
            target=direct.target or to_square,
            attacker=from_square,
            preparation="根节点当前合法走法可以直接执行，不需要准备步骤。",
            consequence=result.main_danger.consequence,
            evidenceRefs=[direct.threat_id, *direct.evidence_route_ids],
        )
        result.threats = [
            direct_text,
            *[
                item for item in result.threats
                if "当前直接威胁" not in item.description
            ],
        ]
    elif threat_package is not None and threat_package.prepared_threats:
        prepared = threat_package.prepared_threats[0]
        prepared_uci = _prepared_threat_uci(move, prepared)
        prepared_piece = _prepared_piece_text(move, prepared_uci, prepared.side)
        from_square = prepared_uci[:2] if prepared_uci else "来源格"
        to_square = prepared_uci[2:4] if prepared_uci else "目标格"
        result.main_danger.side_in_danger = _opposite_side(prepared.side)
        result.main_danger.level = "short_term"
        result.main_danger.description = (
            f"当前没有程序确认的可立即执行战术；{prepared_piece}准备从"
            f"{from_square}走到{to_square}（{'、'.join(prepared.preparation_moves)}），"
            "形成准备型威胁。"
        )
        result.main_danger.consequence = (
            "程序验证表明，忽略该构想至少会损失"
            f"{prepared.ignore_test.evaluation_loss:.2f}兵的评价，对手必须认真应对。"
            if prepared.ignore_test.evaluation_loss is not None
            else "程序已确认准备关系，但尚无足够评价损失证据。"
        )
        result.main_danger.evidence_refs = [
            prepared.threat_id,
            *prepared.evidence_route_ids,
        ]
        prepared_text = ProfessionalThreat(
            side=prepared.side,
            level="short_term",
            scope="current_position",
            description=(
                f"这是准备型威胁：{'白方' if prepared.side == 'white' else '黑方'}计划以"
                f"{'、'.join(prepared.preparation_moves)}形成战术压力，尚未在当前局面执行。"
            ),
            target=prepared.target or "程序未指定单一目标",
            attacker="、".join(prepared.preparation_moves),
            preparation=(
                f"程序检查了对手选择{prepared.ignore_test.ignored_move or '中性应手'}后的结果；"
                f"忽略该构想至少会损失{prepared.ignore_test.evaluation_loss:.2f}兵的评价。"
                if prepared.ignore_test.evaluation_loss is not None
                else "程序已确认准备步骤。"
            ),
            consequence="对手不能把这一构想当作普通PV事件安全忽略。",
            evidenceRefs=[prepared.threat_id, *prepared.evidence_route_ids],
        )
        result.threats = [
            prepared_text,
            *[
                item for item in result.threats
                if "准备型威胁" not in item.description
            ],
        ]
    elif threat_package is not None:
        result.main_danger.side_in_danger = "none"
        result.main_danger.level = "long_term"
        result.main_danger.description = "眼前没有必须马上处理的单一威胁。"
        result.main_danger.consequence = "可以按自己的计划走，不必先做防守。"
        result.main_danger.evidence_refs = [
            move.played_move.id or f"move:played:{move.index}"
        ]
    same_as_best = bool(
        move.best_move_uci
        and move.best_move_uci == move.played_move.uci
    )
    if same_as_best:
        result.played_move_analysis.problems = []
    elif move.best_move_san and move.centipawn_loss is not None and move.centipawn_loss < 50:
        result.played_move_analysis.problems = []
    elif move.best_move_san and move.centipawn_loss is not None and move.centipawn_loss <= 100:
        result.played_move_analysis.problems = [
            f"{move.played_move.san}不是大错，但{move.best_move_san}更精确。"
        ]
    elif move.best_move_san:
        result.played_move_analysis.problems = [
            f"{move.played_move.san}之后局面明显变差，先比较{move.best_move_san}这条变化。"
        ]
    else:
        result.played_move_analysis.problems = []
    result.played_move_analysis.intention = _sanitize_core_explanation(
        result.played_move_analysis.intention,
        move,
    )
    if narrative_claims is not None:
        selected_claims = resolve_narrative_claims(
            narrative_claims,
            result.played_move_analysis.claim_refs,
        )
        result.played_move_analysis.intention = compose_verified_core_paragraph(
            narrative_claims,
            [item.claim_id for item in selected_claims],
        )
        result.played_move_analysis.claim_refs = [item.claim_id for item in selected_claims]
    current_tactics = [
        tactic for tactic in move.verified_tactics
        if tactic.move_uci == move.played_move.uci
    ]
    if current_tactics:
        tactic_text = _guarded_tactic_text(current_tactics[0].description)
        result.played_move_analysis.intention = (
            f"{tactic_text}这一手的重点是让同一枚棋子同时盯住多个目标。"
            "类似局面先检查有没有能一次攻击两个以上目标的落点，再计算对手最强回应。"
        )
        result.played_move_analysis.positive_effects = list(dict.fromkeys([
            tactic_text,
            *result.played_move_analysis.positive_effects,
        ]))
        tactic_fact_ids = [
            fact.id
            for fact in move.position_facts.threats
            if fact.category == current_tactics[0].name
            and fact.side == current_tactics[0].side
            and set(fact.squares) == set(current_tactics[0].squares)
        ]
        result.played_move_analysis.evidence_refs = list(dict.fromkeys([
            *result.played_move_analysis.evidence_refs,
            *tactic_fact_ids,
        ]))
    if move.played_move.check:
        result.played_move_analysis.intention = (
            f"{move.played_move.san}让{_human_piece_text(move.played_move.piece, move.side)}"
            f"从{move.played_move.from_square}到{move.played_move.to_square}并直接将军，"
            "这一手的先手来自迫使对方先处理王的安全。"
            "类似局面先检查所有强制手段，再确认对手回应后攻势能否继续。"
        )
    if (
        move.played_move.capture
        and (move.played_move.captured_piece or "").endswith("queen")
        and move.actual_move_line
        and move.actual_move_line.moves
        and move.actual_move_line.moves[0].capture
        and (move.actual_move_line.moves[0].captured_piece or "").endswith("queen")
    ):
        side_text = "白方" if move.side == "white" else "黑方"
        result.played_move_analysis.intention = (
            f"{move.played_move.san}只是正常兑后：{side_text}后在"
            f"{move.played_move.to_square}吃掉对方后，把局面直接简化，没有额外战术需要展开。"
        )
        result.played_move_analysis.evidence_refs = list(dict.fromkeys([
            *result.played_move_analysis.evidence_refs,
            move.played_move.id or f"move:played:{move.index}",
            move.actual_move_line.moves[0].id,
        ]))
    elif not re.search(
        r"类似局面|先检查|先看|首先确认|第一眼|优先检查",
        result.played_move_analysis.intention,
    ):
        piece = (move.played_move.piece or "").split("_")[-1]
        if piece in {"knight", "bishop"}:
            transfer = "类似局面先检查这步出子是否同时争夺中心、制造具体威胁或改善最差棋子。"
        elif piece == "pawn" and move.played_move.to_square[:1] in {"d", "e"}:
            transfer = "类似局面先检查中心兵推进会打开哪些线路，以及对手能否立即反击中心。"
        elif piece == "pawn":
            transfer = "类似局面先检查兵推进后留下的格子，以及它是否真的形成有效突破。"
        elif piece == "rook":
            transfer = "类似局面先检查车能否占据开放线，并确认进入后有没有具体目标。"
        elif piece == "queen":
            transfer = "类似局面先检查后的落点是否会被对手赶走，以及这步是否取得了具体收益。"
        elif piece == "king":
            transfer = "类似局面先检查王的安全，以及这步是否会妨碍其他棋子协调。"
        else:
            transfer = "类似局面先检查这步棋解决了什么具体问题，以及对手最强回应是什么。"
        result.played_move_analysis.intention += transfer
    for side, target_rank in (("white", "7"), ("black", "2")):
        rooks = [
            piece
            for piece in move.position_facts.pieces
            if piece.get("side") == side
            and piece.get("piece") == "rook"
            and str(piece.get("square", "")).endswith(target_rank)
        ]
        if len(rooks) < 2:
            continue
        description = f"{'白方' if side == 'white' else '黑方'}两辆车已经位于第七横线，重子的深入活动是当前重要局面因素。"
        refs = [piece["id"] for piece in rooks if piece.get("id")]
        if result.position_assessment.piece_activity is None:
            result.position_assessment.piece_activity = ProfessionalEvidenceText(
                description=description,
                evidenceRefs=refs,
            )
        elif description not in result.position_assessment.piece_activity.description:
            result.position_assessment.piece_activity.description += description
            result.position_assessment.piece_activity.evidence_refs = list(dict.fromkeys([
                *result.position_assessment.piece_activity.evidence_refs,
                *refs,
            ]))
    for rendered_line, source_line in zip(result.candidate_lines, move.candidate_lines):
        exchange_summary, exchange_refs = _initial_exchange_material_summary(source_line)
        advanced_pawn_summary, advanced_pawn_refs = _advanced_pawn_capture_summary(
            source_line
        )
        verified_summaries = [
            summary for summary in (exchange_summary, advanced_pawn_summary) if summary
        ]
        verified_refs = [*exchange_refs, *advanced_pawn_refs]
        if verified_summaries:
            rendered_line.direct_purpose = "".join(verified_summaries) + rendered_line.direct_purpose
            rendered_line.advantages = list(dict.fromkeys([
                *verified_summaries,
                *rendered_line.advantages,
            ]))
            rendered_line.evidence_refs = list(dict.fromkeys([
                *rendered_line.evidence_refs,
                source_line.id,
                *verified_refs,
            ]))
            if source_line.rank == 1:
                result.comparison.why_first_line_is_best = (
                    "".join(verified_summaries)
                    + result.comparison.why_first_line_is_best
                )
                result.comparison.evidence_refs = list(dict.fromkeys([
                    *result.comparison.evidence_refs,
                    source_line.id,
                    *verified_refs,
                ]))
    if narrative_claims is not None:
        _apply_verified_narrative_surface_guard(result, move, narrative_claims)
    return result


def _apply_verified_narrative_surface_guard(
    result: ProfessionalAnalysis,
    move: MoveReview,
    package: NarrativeClaimPackage,
) -> None:
    """Rebuild every move-route explanation that is visible beside engine facts.

    This runs last because older tactic and exchange guards may also touch the
    core paragraph.  The model may choose emphasis through claimRefs, but it
    cannot add a causal bridge that the program did not verify.
    """
    selected = _restore_verified_core(result, package)
    result.played_move_analysis.evidence_refs = list(dict.fromkeys([
        move.played_move.id or f"move:played:{move.index}",
        f"evaluation:before:{move.index}",
        f"evaluation:after:{move.index}",
        *(ref for item in selected for ref in item.evidence_refs),
    ]))
    result.played_move_analysis.positive_effects = [
        item.statement
        for item in selected
        if item.kind in {"move_event", "move_effect"}
    ][:3]
    result.played_move_analysis.error_type = (
        "tactical"
        if not (
            move.best_move_uci == move.played_move.uci
            or (move.centipawn_loss is not None and move.centipawn_loss < 50)
        )
        and (move.complexity_factors.direct_piece_loss or move.mate_involved)
        else "none"
    )
    for phase in result.played_move_analysis.continuation_phases:
        phase.explanation = "按Stockfish验证顺序列出，只说明实战着后的应对。"

    for rendered_line, source_line in zip(result.candidate_lines, move.candidate_lines):
        rendered_line.strategy_tags = []
        rendered_line.direct_purpose = _verified_candidate_first_move_text(
            source_line,
            move.side,
        )
        if len(source_line.moves) > 1:
            reply = source_line.moves[1]
            rendered_line.opponent_response = (
                f"在这条Stockfish验证路线中，对手首先走{reply.san}。"
            )
        else:
            rendered_line.opponent_response = "这条验证路线没有提供对手的下一手。"
        for phase in rendered_line.continuation_phases:
            phase.explanation = "按Stockfish验证顺序列出，不推断额外因果。"
        rendered_line.advantages = []
        rendered_line.risks = []
        rendered_line.why_this_rank = (
            "它是本次Stockfish MultiPV分析的第一路线。"
            if source_line.rank == 1
            else f"它是本次Stockfish MultiPV分析的第{source_line.rank}路线。"
        )
        rendered_line.evidence_refs = [source_line.id]

    if move.best_move_uci and move.best_move_uci == move.played_move.uci:
        result.comparison.main_difference = (
            f"实战着{move.played_move.san}与Stockfish第一路线首着相同。"
        )
    elif move.best_move_san and move.centipawn_loss is not None:
        result.comparison.main_difference = (
            f"实战走{move.played_move.san}；Stockfish第一路线从{move.best_move_san}开始，"
            f"两者相差约{move.centipawn_loss / 100:.2f}兵的评价。"
        )
    elif move.best_move_san:
        result.comparison.main_difference = (
            f"实战走{move.played_move.san}；Stockfish第一路线从{move.best_move_san}开始。"
        )
    else:
        result.comparison.main_difference = "当前没有足够的首选路线数据可供比较。"
    if move.candidate_lines:
        first = move.candidate_lines[0]
        result.comparison.why_first_line_is_best = (
            f"Stockfish在本次MultiPV分析中把{first.first_move.san}排在第一位。"
        )
        result.comparison.evidence_refs = list(dict.fromkeys([
            first.id,
            move.played_move.id or f"move:played:{move.index}",
            f"evaluation:before:{move.index}",
            f"evaluation:after:{move.index}",
        ]))
    else:
        result.comparison.why_first_line_is_best = "当前没有可用的Stockfish候选路线。"


def _restore_verified_core(
    result: ProfessionalAnalysis,
    package: NarrativeClaimPackage,
) -> list[Any]:
    """Restore the exact claim rendering after generic prose normalization."""
    selected = resolve_narrative_claims(
        package,
        result.played_move_analysis.claim_refs,
    )
    selected_ids = [item.claim_id for item in selected]
    result.played_move_analysis.intention = compose_verified_core_paragraph(
        package,
        selected_ids,
    )
    result.played_move_analysis.claim_refs = selected_ids
    return selected


def _verified_candidate_first_move_text(line: Any, side: str) -> str:
    """Describe candidate purpose as verified move mechanics, without motive inference."""
    first = line.first_move
    text = (
        f"先走{first.san}，让{_human_piece_text(first.piece, side)}从"
        f"{first.from_square}到{first.to_square}"
    )
    events: list[str] = []
    if first.capture:
        events.append(
            f"吃掉{_human_piece_text(first.captured_piece or 'piece', _opposite_side(side))}"
        )
    if first.promotion:
        events.append(f"升变为{_human_piece_text(first.promotion, side)}")
    if first.checkmate:
        events.append("形成将杀")
    elif first.check:
        events.append("形成将军")
    if events:
        text += "，并" + "、".join(events)
    return text + "。"


def _sanitize_core_explanation(text: str, move: MoveReview) -> str:
    """Drop whole off-task sentences while keeping a coherent mover-focused core."""
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[。！？])", str(text or ""))
        if item.strip()
    ]
    small_gap = bool(
        move.best_move_uci
        and move.best_move_uci != move.played_move.uci
        and move.centipawn_loss is not None
        and move.centipawn_loss < 50
    )
    comparative = re.compile(
        r"更精确|更好|不如|优于|更关键|才是|错失|逊色|没有|并未|未能|"
        r"并非最|不够|较为被动|失去|错过"
    )
    route_phrase = re.compile(r"实战后验证路线|验证路线包含|后续验证路线")
    san_candidates = {
        item.san
        for line in [
            *move.candidate_lines,
            *([move.actual_move_line] if move.actual_move_line else []),
        ]
        for item in line.moves
    } | {move.played_move.san}
    seen_moves: set[str] = set()
    kept: list[str] = []
    for sentence in sentences:
        if route_phrase.search(sentence):
            continue
        if small_gap and comparative.search(sentence):
            continue
        sentence_moves = {
            san for san in san_candidates
            if re.search(
                rf"(?<![A-Za-z0-9]){re.escape(san)}(?![A-Za-z0-9])",
                sentence,
            )
        }
        if len(seen_moves | sentence_moves) > 2:
            continue
        seen_moves.update(sentence_moves)
        kept.append(sentence)
    if kept:
        return "".join(kept)
    return (
        f"{move.played_move.san}让{_human_piece_text(move.played_move.piece, move.side)}"
        f"从{move.played_move.from_square}走到{move.played_move.to_square}，"
        "这是当前局面中的一个合理选择。"
    )


def _guarded_tactic_text(description: str) -> str:
    """Keep the tactical relation without restating a protected king square."""
    guarded = re.sub(
        r"((?:白|黑)(?:方的)?王)[（(]\s*[a-h][1-8]\s*[）)]",
        r"\1",
        description,
        flags=re.IGNORECASE,
    )
    if re.search(r"(?:白|黑)(?:方的)?王", guarded):
        guarded = re.sub(
            r"[（(]\s*[a-h][1-8]\s*[）)]",
            "",
            guarded,
            flags=re.IGNORECASE,
        )
    return guarded


def _opposite_side(side: str) -> str:
    return "black" if side == "white" else "white"


def _prepared_threat_uci(
    move: MoveReview,
    threat: ThreatFact,
) -> str | None:
    for evidence in threat.evidence:
        match = re.search(
            r"准备走法([a-h][1-8][a-h][1-8][qrbn]?)",
            evidence,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).lower()
    preparations = {_normalize_san(item) for item in threat.preparation_moves}
    if preparations:
        for route in move.candidate_lines:
            if route.id not in threat.evidence_route_ids:
                continue
            for ply in route.moves:
                if _normalize_san(ply.san) in preparations:
                    return ply.uci.lower()
    return None


def _prepared_piece_text(
    move: MoveReview,
    uci: str | None,
    side: str,
) -> str:
    piece_name = "棋子"
    if uci:
        piece = chess.Board(move.before_fen).piece_at(chess.parse_square(uci[:2]))
        if piece is not None:
            piece_name = {
                chess.PAWN: "兵",
                chess.KNIGHT: "马",
                chess.BISHOP: "象",
                chess.ROOK: "车",
                chess.QUEEN: "后",
                chess.KING: "王",
            }[piece.piece_type]
    return f"{'白' if side == 'white' else '黑'}{piece_name}"


def _human_side_text(side: str) -> str:
    return "白方" if side == "white" else "黑方" if side == "black" else "一方"


def _human_piece_text(piece: str, side: str | None = None) -> str:
    parts = piece.split("_")
    inferred_side = parts[0] if parts and parts[0] in {"white", "black"} else side
    piece_name = {
        "pawn": "兵",
        "knight": "马",
        "bishop": "象",
        "rook": "车",
        "queen": "后",
        "king": "王",
    }.get(parts[-1], "棋子")
    prefix = "白" if inferred_side == "white" else "黑" if inferred_side == "black" else ""
    return f"{prefix}{piece_name}"


def _threat_move_details(
    move: MoveReview,
    threat: ThreatFact,
) -> tuple[str, str, str]:
    supporting = set(threat.supporting_moves)
    for fact in (*move.position_facts.immediate_checks, *move.position_facts.immediate_captures):
        if fact.san not in supporting:
            continue
        side = "白" if threat.side == "white" else "黑"
        piece = {
            "pawn": "兵",
            "knight": "马",
            "bishop": "象",
            "rook": "车",
            "queen": "后",
            "king": "王",
        }.get(fact.piece.split("_")[-1], "棋子")
        return f"{side}{piece}", fact.from_square, fact.to_square
    for evidence in threat.evidence:
        match = re.search(
            r"(?:深度升级走法|准备走法)([a-h][1-8][a-h][1-8][qrbn]?)",
            evidence,
            re.IGNORECASE,
        )
        if match:
            uci = match.group(1).lower()
            return (
                _prepared_piece_text(move, uci, threat.side),
                uci[:2],
                uci[2:4],
            )
    route_sans = {
        _normalize_san(item)
        for item in [*threat.preparation_moves, *threat.supporting_moves]
    }
    for route in move.candidate_lines:
        if route.id not in threat.evidence_route_ids:
            continue
        for ply in route.moves:
            if _normalize_san(ply.san) not in route_sans:
                continue
            return (
                _prepared_piece_text(move, ply.uci, threat.side),
                ply.from_square,
                ply.to_square,
            )
    return (
        f"{'白' if threat.side == 'white' else '黑'}方棋子",
        "来源格",
        threat.target or "目标格",
    )


def _normalize_san(value: str) -> str:
    return value.replace("0", "O").rstrip("+#")


def _professional_threat_name(threat_type: str) -> str:
    return {
        "mate_threat": "将杀威胁",
        "tactical_capture": "战术吃子",
        "material_win": "赢子威胁",
        "promotion_threat": "升变威胁",
        "center_break": "中心突破",
        "prepared_tactic": "准备型战术",
    }.get(threat_type, "直接威胁")


def _controlled_position_summary(move: MoveReview) -> str:
    board = chess.Board(move.before_fen)
    parts = [
        _controlled_evaluation_text(move.before.centipawn, move.before.mate_in),
        _controlled_material_text(move),
    ]
    for color, side_name in ((chess.WHITE, "白方"), (chess.BLACK, "黑方")):
        square = board.king(color)
        if square is None:
            continue
        rights = []
        if board.has_kingside_castling_rights(color):
            rights.append("王翼")
        if board.has_queenside_castling_rights(color):
            rights.append("后翼")
        rights_text = f"还可以向{'或'.join(rights)}易位" if rights else "已经没有易位权"
        parts.append(f"{side_name}王在{chess.square_name(square)}，{rights_text}。")
    return "".join(parts)


def _controlled_evaluation_text(
    centipawn: int | None,
    mate_in: int | None,
) -> str:
    if mate_in is not None:
        side = "白方" if mate_in > 0 else "黑方"
        return f"{side}有强制将杀。"
    if centipawn is None:
        return "这盘暂时没有可靠分数。"
    if abs(centipawn) <= 25:
        return "局面大致均衡。"
    side = "白方" if centipawn > 0 else "黑方"
    level = "轻微" if abs(centipawn) <= 100 else "明显" if abs(centipawn) <= 300 else "决定性"
    return f"{side}{level}占优。"


def _controlled_material_text(move: MoveReview) -> str:
    material = move.position_facts.material
    white = material.get("white")
    black = material.get("black")
    difference = material.get("valueDifferenceWhiteMinusBlack")
    if not isinstance(white, dict) or not isinstance(black, dict) or not isinstance(difference, int):
        return "子力账目暂时算不完整。"
    white_value = white.get("value")
    black_value = black.get("value")
    white_pieces = white.get("pieces")
    black_pieces = black.get("pieces")
    if difference == 0:
        return f"双方子力相等，都是{white_value}分。"
    side = "白方" if difference > 0 else "黑方"
    value = abs(difference)
    if isinstance(white_pieces, dict) and isinstance(black_pieces, dict) and value == 1:
        white_pawns = white_pieces.get("pawn")
        black_pawns = black_pieces.get("pawn")
        non_pawn_counts_equal = all(
            isinstance(white_pieces.get(piece), list)
            and isinstance(black_pieces.get(piece), list)
            and len(white_pieces[piece]) == len(black_pieces[piece])
            for piece in ("knight", "bishop", "rook", "queen")
        )
        if (
            isinstance(white_pawns, list)
            and isinstance(black_pawns, list)
            and non_pawn_counts_equal
        ):
            pawn_difference = len(white_pawns) - len(black_pawns)
            if pawn_difference == (1 if difference > 0 else -1):
                return f"{side}多一兵。"
    return (
        f"白方子力为{white_value}分，黑方为{black_value}分，"
        f"{side}多{value}分子力价值。"
    )


def _controlled_move_summary(move: MoveReview) -> str:
    same_as_best = bool(
        move.best_move_uci
        and move.best_move_uci == move.played_move.uci
    )
    played = move.played_move.san
    if same_as_best:
        return f"{played}就是引擎首选，这一步没有问题。"
    elif move.best_move_san:
        best = move.best_move_san
        if move.centipawn_loss is not None and move.centipawn_loss < 50:
            return f"{played}与{best}的引擎评价接近；真正需要比较的是两步造成的计划和行棋次序。"
        elif move.centipawn_loss is not None and move.centipawn_loss <= 100:
            return (
                f"{played}不算大错，但比{best}差约"
                f"{move.centipawn_loss / 100:.2f}兵。"
            )
        return f"{played}让局面明显变差，{best}才是这里更关键的选择。"
    else:
        return f"{played}的好坏暂时没有可靠首选可作比较。"


def _controlled_score_direction(centipawn: int | None) -> str:
    if centipawn is None:
        return "未知"
    if centipawn > 25:
        return "白方较好"
    if centipawn < -25:
        return "黑方较好"
    return "接近均势"


_USER_VISIBLE_PROSE_KEYS = {
    "summary", "description", "explanation", "consequence", "requiredPreparation",
    "exploitation", "intention", "positiveEffects", "problems", "resultingPosition",
    "evaluationReason", "directPurpose", "advantages", "risks", "whyThisRank",
    "mainDifference", "whyFirstLineIsBest", "preparation", "significance",
}


def _humanize_user_visible_prose(analysis: ProfessionalAnalysis) -> ProfessionalAnalysis:
    payload = analysis.model_dump(by_alias=True)
    played = analysis.played_move_analysis.move
    best = analysis.candidate_lines[0].first_move if analysis.candidate_lines else "首选着"

    def convert(value: Any, *, prose: bool = False) -> Any:
        if isinstance(value, dict):
            return {
                key: convert(child, prose=prose or key in _USER_VISIBLE_PROSE_KEYS)
                for key, child in value.items()
            }
        if isinstance(value, list):
            converted = [convert(child, prose=prose) for child in value]
            return [
                child for child in converted
                if not (isinstance(child, str) and not child.strip())
            ]
        if not prose or not isinstance(value, str):
            return value

        def piece_replacement(match: re.Match[str]) -> str:
            side = match.group(1)
            piece = match.group(2)
            return _human_piece_text(piece, side)

        text = re.sub(
            r"(?<![A-Za-z0-9_])(white|black)_(?:(?:white|black)_)?"
            r"(pawn|knight|bishop|rook|queen|king)(?![A-Za-z0-9_])",
            piece_replacement,
            value,
            flags=re.IGNORECASE,
        )
        text = (
            text.replace("white方", "白方")
            .replace("black方", "黑方")
            .replace("Ignore Test", "应对验证")
            .replace("这条变化还要看对手接下来怎么应对。", "")
        )
        replacements = (
            (r"实战着法旨在|实战着意在|实战着选择", f"{played}是要"),
            (r"实战着与首选路线的主要差异在于[，,]?", ""),
            (r"首选路线首着", best),
            (r"首选路线通过", f"{best}先"),
            (r"首选路线", best),
            (r"当前的首要问题", "眼前最要紧的事"),
            (r"更符合当前首要问题", "次序更合适"),
            (r"该着旨在", f"{best}是要"),
            (r"符合开局发展原则", "出子也更顺"),
            (r"存在本质差异", "次序完全不同"),
            (r"为残局奠定(?:了)?(?:更)?坚实的基础", "让残局更好下"),
            (r"为残局奠定", "为后续残局创造"),
            (r"解决根本问题", "照顾到真正的麻烦"),
        )
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text)
        # Expand labels before deduplicating: "实战着e4" must not become "e4e4".
        text = text.replace("实战着", played)
        for san in {played, best}:
            if san:
                text = re.sub(
                    rf"(?<![A-Za-z0-9])(?:{re.escape(san)}){{2}}(?![A-Za-z0-9])",
                    san,
                    text,
                )
        text = re.sub(
            r"(?<![A-Za-z0-9])([a-h][1-8])\1(?![A-Za-z0-9])",
            r"\1",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"(?<![A-Za-z0-9])((?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8])[+#]?)法",
            r"\1",
            text,
        )
        text = re.sub(
            rf"{re.escape(best)}先(兵|马|象|车|后|王)的调动",
            rf"{best}先调动\1",
            text,
        )
        return (
            text.replace("先先", "先")
            .replace("，意图", "，想")
            .replace("为后续发展子力做好准备", "接着再出子")
            .replace("为后续子力调动创造条件", "给其他棋子留出后续空间")
            .replace("同时保持兵形的灵活性", "兵形也不会定死")
            .replace("导致局面恶化", "局面会更难下")
        )

    return ProfessionalAnalysis.model_validate(convert(payload))


def _fit_resolved_analysis_length(
    analysis: ProfessionalAnalysis,
    move: MoveReview,
    level: str,
) -> ProfessionalAnalysis:
    """Fit generated prose to the existing band without changing any referenced chess fact."""
    result = _trim_profile_max(analysis, level)
    verified_core = bool(result.played_move_analysis.claim_refs)
    minimum = (
        VERIFIED_NARRATIVE_LENGTH_RANGES[level][0]
        if verified_core
        else LENGTH_RANGES[level][0]
    )
    first_line = move.candidate_lines[0] if move.candidate_lines else None
    first = first_line.first_move if first_line else None
    verified = (
        f"再看实战，{_human_piece_text(move.played_move.piece, move.side)}从"
        f"{move.played_move.from_square}到"
        f"{move.played_move.to_square}（{move.played_move.san}）"
    )
    if first:
        verified += (
            f"；引擎先看{first.san}，从{first.from_square}到{first.to_square}"
        )
    verified += "。"
    if verified_core and _narrative_length(result.model_dump(by_alias=True)) < minimum:
        if len(result.position_assessment.summary) + len(verified) <= 500:
            result.position_assessment.summary += verified
        else:
            result.comparison.main_difference += verified
        return _trim_profile_max(result, level)
    while _narrative_length(result.model_dump(by_alias=True)) < minimum:
        if len(result.position_assessment.summary) + len(verified) <= 500:
            result.position_assessment.summary += verified
        else:
            result.comparison.main_difference += verified
    return _trim_profile_max(result, level)


def _trim_profile_max(analysis: ProfessionalAnalysis, level: str) -> ProfessionalAnalysis:
    """Trim only redundant prose when a deterministic profile is slightly over its band."""
    result = analysis.model_copy(deep=True)
    ranges = (
        VERIFIED_NARRATIVE_LENGTH_RANGES
        if result.played_move_analysis.claim_refs
        else LENGTH_RANGES
    )
    maximum = ranges[level][1]

    def length() -> int:
        return _narrative_length(result.model_dump(by_alias=True))

    fields = [
        (result.comparison, "main_difference"),
        (result.comparison, "why_first_line_is_best"),
        (result.position_assessment, "summary"),
        (result.played_move_analysis, "resulting_position"),
    ]
    if not result.played_move_analysis.claim_refs:
        fields.append((result.played_move_analysis, "intention"))
    for optional in (
        result.position_assessment.king_safety.white,
        result.position_assessment.king_safety.black,
        result.position_assessment.piece_activity,
        result.position_assessment.pawn_structure,
    ):
        if optional is not None:
            fields.append((optional, "description"))
    for plan in [*result.plans.white, *result.plans.black]:
        fields.extend([(plan, "description"), (plan, "required_preparation")])
    for weakness in [*result.weaknesses.white, *result.weaknesses.black]:
        fields.extend([(weakness, "description"), (weakness, "exploitation")])
    for threat in result.threats:
        fields.append((threat, "description"))
    for phase in result.played_move_analysis.continuation_phases:
        fields.append((phase, "explanation"))
    for line in result.candidate_lines:
        fields.extend(
            [
                (line, "direct_purpose"),
                (line, "resulting_position"),
                (line, "why_this_rank"),
            ]
        )
        for phase in line.continuation_phases:
            fields.append((phase, "explanation"))
    for target, attribute in fields:
        while length() > maximum:
            value = getattr(target, attribute)
            if any(phrase in value for phrase in VAGUE_PHRASES):
                break
            if len(value) <= 8:
                break
            excess = length() - maximum
            keep = max(8, len(value) - min(excess + 1, len(value) - 8))
            shortened = _trim_to_complete_sentence(value, keep)
            if len(shortened) >= len(value):
                shortened = _trim_to_complete_sentence(value, max(8, keep - 2))
            if not shortened or len(shortened) >= len(value):
                break
            setattr(target, attribute, shortened)
        if length() <= maximum:
            break
    list_fields = [
        result.played_move_analysis.positive_effects,
        result.played_move_analysis.problems,
        *[line.advantages for line in result.candidate_lines],
        *[line.risks for line in result.candidate_lines],
    ]
    for values in list_fields:
        for index, value in enumerate(values):
            while length() > maximum and len(value) > 8:
                if any(phrase in value for phrase in VAGUE_PHRASES):
                    break
                excess = length() - maximum
                keep = max(8, len(value) - min(excess + 1, len(value) - 8))
                shortened = _trim_to_complete_sentence(value, keep)
                if len(shortened) >= len(value):
                    shortened = _trim_to_complete_sentence(value, max(8, keep - 2))
                if not shortened or len(shortened) >= len(value):
                    break
                values[index] = shortened
                value = shortened
            if length() <= maximum:
                break
        if length() <= maximum:
            break
    if length() > maximum:
        # Optional prose is removed as a whole item; never shave characters from a sentence.
        for values in reversed(list_fields):
            while values and length() > maximum:
                values.pop()
            if length() <= maximum:
                break
    if length() > maximum:
        # Replace non-display metadata with short, complete sentences before touching user-facing prose.
        atomic_replacements = [
            (result.comparison, "main_difference", ""),
            (result.comparison, "why_first_line_is_best", ""),
            (result.played_move_analysis, "resulting_position", ""),
        ] + [
            item
            for line in result.candidate_lines
            for item in (
                (line, "why_this_rank", ""),
                (line, "resulting_position", ""),
            )
        ]
        for target, attribute, replacement in atomic_replacements:
            if length() <= maximum:
                break
            setattr(target, attribute, replacement)
    return result


def _fit_complex_safe_length(
    analysis: ProfessionalAnalysis,
    move: MoveReview,
) -> ProfessionalAnalysis:
    """Compact only non-structural prose when a complex safe fallback exceeds its ceiling."""
    minimum, maximum = LENGTH_RANGES["complex"]
    result = analysis.model_copy(deep=True)

    def length() -> int:
        return _narrative_length(result.model_dump(by_alias=True))

    if length() <= maximum:
        return (
            _fit_resolved_analysis_length(result, move, "complex")
            if length() < minimum
            else result
        )

    compact = _apply_safe_length_profile(analysis, move, "normal")

    def replace(target: Any, attribute: str, value: Any) -> bool:
        previous = getattr(target, attribute)
        setattr(target, attribute, value)
        current = length()
        if current < minimum:
            setattr(target, attribute, previous)
            return False
        return current <= maximum

    replacements: list[tuple[Any, str, Any]] = [
        (result.comparison, "main_difference", compact.comparison.main_difference),
        (result.comparison, "why_first_line_is_best", compact.comparison.why_first_line_is_best),
        (result.position_assessment, "summary", compact.position_assessment.summary),
    ]
    for line, compact_line in zip(result.candidate_lines, compact.candidate_lines):
        replacements.extend(
            [
                (line, "why_this_rank", compact_line.why_this_rank),
                (line, "advantages", compact_line.advantages),
                (line, "risks", compact_line.risks),
            ]
        )
    replacements.extend(
        [
            (
                result.position_assessment,
                "piece_activity",
                compact.position_assessment.piece_activity,
            ),
            (
                result.position_assessment,
                "pawn_structure",
                compact.position_assessment.pawn_structure,
            ),
            (
                result.position_assessment.king_safety,
                "white",
                compact.position_assessment.king_safety.white,
            ),
            (
                result.position_assessment.king_safety,
                "black",
                compact.position_assessment.king_safety.black,
            ),
            (result.weaknesses, "white", compact.weaknesses.white),
            (result.weaknesses, "black", compact.weaknesses.black),
            (result, "threats", compact.threats),
            (result, "plans", compact.plans),
            (result, "played_move_analysis", compact.played_move_analysis),
        ]
    )
    for line, compact_line in zip(result.candidate_lines, compact.candidate_lines):
        replacements.extend(
            [
                (line, "direct_purpose", compact_line.direct_purpose),
                (line, "opponent_response", compact_line.opponent_response),
                (line, "continuation_phases", compact_line.continuation_phases),
                (line, "resulting_position", compact_line.resulting_position),
            ]
        )

    for target, attribute, value in replacements:
        if replace(target, attribute, value):
            return result

    # The normal profile is already substantially shorter than the complex minimum.
    # Reaching this branch would mean a single unusually long free-text field remains.
    # Trim only comparison prose; structured moves, squares, pieces and evidence stay intact.
    for target, attribute in (
        (result.comparison, "main_difference"),
        (result.comparison, "why_first_line_is_best"),
        (result.position_assessment, "summary"),
    ):
        while length() > maximum:
            text = getattr(target, attribute)
            if len(text) <= 8:
                break
            excess = length() - maximum
            keep = max(8, len(text) - min(excess + 1, len(text) - 8))
            shortened = _trim_to_complete_sentence(text, keep)
            if len(shortened) >= len(text):
                shortened = _trim_to_complete_sentence(text, max(8, keep - 2))
            if not shortened or len(shortened) >= len(text):
                break
            setattr(target, attribute, shortened)
        if length() <= maximum:
            return result

    return result


def _trim_to_complete_sentence(text: str, maximum: int) -> str:
    """Trim prose only at a sentence boundary so length fitting cannot create residue."""
    if len(text) <= maximum:
        return text
    prefix = text[:maximum]
    boundaries = [prefix.rfind(mark) for mark in "。！？；.!?"]
    boundary = max(boundaries)
    if boundary >= 7:
        return prefix[:boundary + 1].strip()
    clause_boundary = max(prefix.rfind(mark) for mark in "，、：,:")
    if clause_boundary < 7:
        return ""
    clause = prefix[:clause_boundary].rstrip(" ，、；：,.!?！？")
    incomplete_endings = ("正在", "准备", "为了", "通过", "因为", "因此", "意大", "可以让", "正", "的")
    if any(clause.endswith(ending) for ending in incomplete_endings):
        return ""
    return f"{clause}。"


def _model_phases(moves: list[Any], count: int) -> list[Any]:
    from .models import ProfessionalContinuationPhase

    return [ProfessionalContinuationPhase.model_validate(item) for item in _safe_phases(moves, count)]


def _short_result_position(line: Any) -> str:
    if line is None:
        return "没有可用续算终点。"
    consequence = _important_material_consequence(line)
    return consequence or "参考线终点已验证；没有需要单独强调的重大子力后果。"


def _very_short_result_position(line: Any) -> str:
    if line is None:
        return "无续算终点。"
    return _important_material_consequence(line) or "终点没有重大子力后果。"


def _safe_phases(moves: list[Any], count: int) -> list[dict[str, Any]]:
    if not moves:
        return []
    chunk_size = max(1, (len(moves) + count - 1) // count)
    phases = []
    for index in range(0, len(moves), chunk_size):
        chunk = moves[index:index + chunk_size]
        phases.append(
            {
                "phase": f"参考变化第{len(phases) + 1}阶段",
                "moves": [item.san for item in chunk],
                "explanation": "先按Stockfish参考顺序走这些合法着，然后再观察结果局面；不把参考变化描述为必然。",
                "evidenceRefs": [item.id for item in chunk],
            }
        )
    return phases


def _safe_strategy_tag(item: Any) -> str:
    if item is None:
        return "improve_worst_piece"
    if item.castling:
        return "improve_king_safety"
    if item.promotion:
        return "create_passed_pawn"
    if item.capture or item.check:
        return "defend_immediate_threat"
    if item.piece.endswith("_pawn"):
        file_name = item.to_square[0]
        if file_name in "abc":
            return "queenside_expansion"
        if file_name in "fgh":
            return "kingside_expansion"
        return "center_control"
    return "improve_worst_piece"


def _safe_strategic_plan_tag(plan_type: str) -> str:
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


def _result_position_text(line: Any) -> str:
    if line is None:
        return "这里没有继续展开变化。"
    facts = line.resulting_position_facts
    consequence = _important_material_consequence(line)
    if facts and consequence:
        return f"算到这里轮到{_human_side_text(facts.side_to_move)}走；{consequence}"
    if facts:
        return f"算到这里轮到{_human_side_text(facts.side_to_move)}走。"
    return "这条变化算到这里为止。"


def _important_material_consequence(line: Any) -> str:
    for item in line.moves:
        captured = (item.captured_piece or "").split("_", 1)[-1]
        if captured in {"knight", "bishop", "rook", "queen"}:
            return f"路线中的{item.san}会直接造成重要棋子得失。"
    return ""


def _initial_exchange_material_summary(line: Any) -> tuple[str, list[str]]:
    """Describe a verified gain inside the opening capture sequence of a PV.

    This is deliberately a stage result rather than a claim about the final
    position: later moves in the same engine line can change the material
    balance again.
    """
    piece_values = {
        "pawn": 1,
        "knight": 3,
        "bishop": 3,
        "rook": 5,
        "queen": 9,
    }
    moves = list(getattr(line, "moves", []) or [])
    if not moves or not moves[0].capture:
        return "", []
    first_side = moves[0].side
    net_value = 0
    refs: list[str] = []
    capture_count = 0
    for item in moves:
        if not item.capture:
            break
        captured = (item.captured_piece or "").split("_", 1)[-1]
        value = piece_values.get(captured)
        if value is None:
            return "", []
        net_value += value if item.side == first_side else -value
        refs.append(item.id)
        capture_count += 1
    if capture_count < 2 or net_value <= 0:
        return "", []
    side_text = "白方" if first_side == "white" else "黑方"
    step_text = {1: "一", 2: "两", 3: "三"}.get(net_value, str(net_value))
    return (
        f"这串交换算下来，{side_text}赚回约{step_text}分子力。",
        refs,
    )


def _advanced_pawn_capture_summary(line: Any) -> tuple[str, list[str]]:
    """Report when a pushed pawn is verifiably captured later in the same PV."""
    moves = list(getattr(line, "moves", []) or [])
    if not moves:
        return "", []
    first_side = moves[0].side
    advanced_pawns: dict[str, tuple[Any, int]] = {}
    for move_index, item in enumerate(moves[1:], start=1):
        piece = (item.piece or "").split("_", 1)[-1]
        captured = (item.captured_piece or "").split("_", 1)[-1]
        tracked_entry = advanced_pawns.get(item.to_square)
        if (
            item.capture
            and captured == "pawn"
            and item.side == first_side
            and tracked_entry is not None
            and tracked_entry[0].side != first_side
        ):
            tracked, push_index = tracked_entry
            mover_text = "白方" if tracked.side == "white" else "黑方"
            capturer_text = "白方" if first_side == "white" else "黑方"
            intermediate = [move.san for move in moves[push_index + 1:move_index]]
            if intermediate:
                summary = (
                    f"{mover_text}走{tracked.san}推兵后，吃兵并非紧接着发生；"
                    f"路线先经过{'、'.join(intermediate)}，"
                    f"{capturer_text}才用{item.san}吃掉这枚兵。"
                )
            else:
                summary = (
                    f"{mover_text}走{tracked.san}推兵后，"
                    f"{capturer_text}随即用{item.san}吃掉这枚兵。"
                )
            return summary, [tracked.id, item.id]
        if piece != "pawn":
            continue
        advanced_pawns.pop(item.from_square, None)
        if not item.capture:
            advanced_pawns[item.to_square] = (item, move_index)
    return "", []


def _joined_fact_text(facts: list[Any], side: str) -> str:
    selected = [fact.description for fact in facts if fact.side == side][:3]
    return "；".join(selected) or "证据不足，无法可靠判断更具体的王安全结论。"


def _played_event_text(move: MoveReview) -> str:
    events = []
    if move.played_move.capture:
        events.append("吃子")
    if move.played_move.checkmate:
        events.append("将杀")
    elif move.played_move.check:
        events.append("将军")
    if move.played_move.castling:
        events.append("易位")
    if move.played_move.promotion:
        events.append("升变")
    return "棋规确认实战着包含" + "、".join(events) if events else "棋规确认实战着是普通合法走法"


def _line_signature(line: Any) -> tuple[str, str]:
    if not line.moves:
        return ("none", "none")
    first = line.moves[0]
    file_index = ord(first.to_square[0]) - ord("a")
    region = "queenside" if file_index <= 2 else "center" if file_index <= 4 else "kingside"
    return (first.piece.split("_")[-1], region)


def _forcing_prefix(moves: list[Any]) -> int:
    count = 0
    for item in moves:
        if not (item.capture or item.check or item.checkmate or item.promotion):
            break
        count += 1
    return count


def _mover_score(centipawn: int | None, mate_in: int | None, side: str) -> int | None:
    if mate_in is not None:
        value = 100_000 if mate_in > 0 else -100_000
    elif centipawn is not None:
        value = centipawn
    else:
        return None
    return value if side == "white" else -value


def _usage(
    results: list[ChatResult],
    *,
    validation_ms: int = 0,
    postprocess_ms: int = 0,
) -> ProfessionalAnalysisUsage:
    def total(field: str) -> int | None:
        values = [getattr(result, field) for result in results]
        return sum(value for value in values if value is not None) if any(value is not None for value in values) else None

    return ProfessionalAnalysisUsage(
        prompt_tokens=total("prompt_tokens"),
        completion_tokens=total("completion_tokens"),
        total_tokens=total("total_tokens"),
        elapsed_ms=sum(result.elapsed_ms for result in results),
        attempts=len(results),
        network_ms=sum(result.elapsed_ms for result in results),
        validation_ms=validation_ms,
        postprocess_ms=postprocess_ms,
    )


def _resolved_validation_issue(error: str) -> DraftValidationIssue:
    path, separator, message = error.partition(": ")
    if separator and re.fullmatch(
        r"\$?(?:[A-Za-z_][A-Za-z0-9_]*)(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*",
        path,
    ):
        return DraftValidationIssue(path, "其他原因", message)
    return DraftValidationIssue("resolvedAnalysis", "其他原因", error)


def _compact_validation_errors(errors: list[str]) -> list[str]:
    compact: list[str] = []
    for error in errors:
        normalized = " ".join(str(error).split())[:240]
        if normalized and normalized not in compact:
            compact.append(normalized)
        if len(compact) == 12:
            break
    return compact or ["返回结构未通过校验，请严格按契约重新生成"]


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
