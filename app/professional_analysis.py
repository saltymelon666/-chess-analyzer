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
from .professional_validation import (
    LENGTH_RANGES,
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
PROFESSIONAL_PROMPT_VERSION = "professional-v12-program-direct-purpose"
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
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = max(timeout_seconds, 120.0)

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
            safe = _fit_resolved_analysis_length(
                apply_hard_fact_guard(
                    build_safe_professional_analysis(
                        move,
                        complexity,
                        threat_package=threat_package,
                    ),
                    move,
                    threat_package=threat_package,
                ),
                move,
                complexity.level,
            )
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
        interpretation = build_position_interpretation(
            fact_package,
            position_facts=move.position_facts,
            threat_package=threat_package,
            plan_package=strategic_plan_package,
        )
        payload = build_professional_payload(
            move,
            complexity,
            context.allowed_evidence_ids,
            fact_package=fact_package,
        )
        payload["positionInterpretation"] = interpretation.prompt_payload()
        payload["interpretationPolicy"] = {
            "initiative": initiative.model_dump(),
            "hardFacts": "program_controlled",
        }
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
                )
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
                attempt_postprocess_ms = round((time.perf_counter() - postprocess_started) * 1000)
                postprocess_ms += attempt_postprocess_ms
                resolved_started = time.perf_counter()
                resolved_errors = validate_professional_analysis(parsed, context)
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
            apply_hard_fact_guard(
                build_safe_professional_analysis(
                    move,
                    complexity,
                    threat_package=threat_package,
                ),
                move,
                threat_package=threat_package,
            ),
            move,
            complexity.level,
        )
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
) -> dict[str, Any]:
    # The context still owns the complete allow-list for server-side validation.
    # DeepSeek receives each current-position fact once and refers to it by ID.
    del allowed_evidence_ids
    package = fact_package or build_move_fact_package(move)
    if not package.threats:
        classified_threats = ThreatAnalyzer().classify(package)
        package.threats = classified_threats.threats
    else:
        classified_threats = ThreatAnalyzer().classify(package)
    if not package.plans:
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
    payload = build_reference_payload(move, complexity.level, complexity.reasons)
    payload.get("pos", {}).pop("fen", None)
    payload["chessFacts"] = package.protocol_manifest()
    payload["positionInterpretation"] = interpretation.prompt_payload()
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
        "你的首要任务不是填满所有栏目，而是抓住当前局面中最影响决策的一至三个重点。"
        "不要把所有棋盘事实都写进分析，只有focus.selectedFacts允许进入最终结论。"
        "候选路线必须用lineRef，PV必须用plyRefs，事实必须用evidenceRefs。"
        "自由解释文本只能使用中文、中文标点和常用百分数，禁止任何拉丁字母、棋盘格、SAN或UCI；"
        "自由解释文本也禁止自行写吃子、将军、将杀或绝杀，这些事件由后端从ply事实填充；"
        "需要指代具体对象时只能写‘该棋子’‘该路线’‘该阶段’，后端会从引用ID回填真实棋子、格子和走法。"
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
        "PV只是参考变化，不是必然发生。"
        "所有解释必须是完整、自然、可直接展示给用户的中文棋理句子。"
        "禁止在解释中出现事实依据、判断依据、根据某事实可以判断、证据数量、内部变量名或引用ID。"
        "不要输出white、black、pawn、knight、bishop、rook、queen、king等程序化名称。"
        "输入中的战略计划由程序确认。禁止创建计划、修改计划类型或扩展计划；"
        "只能通过planId解释已有计划，plans.white和plans.black必须保持空数组。"
        "不要使用只有几个字的模板短语，例如‘巩固中心，准备’或‘暂时减缓发展’，必须说明具体作用、后续准备和局面影响。"
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
    return f"""请根据以下引用目录生成分析草稿：
{compact_payload}

严格规则：
1. candidateLines必须恰好返回{len(payload.get('lines', []))}项，lineRef按lines顺序逐条引用；每条路线只返回一个plyRefs数组，必须按顺序完整覆盖该路线plies[].id，不能串线。本次不可改动的引用骨架为：{line_skeleton}
2. playedMoveAnalysis.moveRef必须等于played.ref；strongestReplyRef及唯一的plyRefs数组只能来自并完整覆盖actual.plies。
3. evidenceRefs、dangerRef只能引用输入中出现的ID。每组evidenceRefs只选1—4个最相关ID，不要枚举整份事实目录。每个危险、计划和因果结论必须有证据。
4. 自由文本只能使用中文和中文标点，禁止拉丁字母、数字、棋盘格、SAN和UCI。不得自行写“吃子、将军、将杀、绝杀”等事件词，这些事件由后端根据ply填充。错误示例：“控制d4”“走Qe2”；正确示例：“控制该中心格”“该路线首着完成协调”。
5. mainDanger有具体危险时用dangerRef引用一个已有ply；无可靠直接危险时dangerRef写null且level写none。危险一方由后端从ply推导，不要输出sideInDanger。
6. positionAssessment只允许输出summary，不得输出material、kingSafety、pieceActivity或pawnStructure；这些动态栏目全部由后端重点选择器按selectedFacts回填。
7. positionAssessment.summary必须用完整段落具体说明双方子力状态、活跃与受限棋子、中心和两翼局势，不能只写“当前局面某方子”之类残句。
8. plans.white和plans.black必须返回空数组。战略计划只能通过planExplanations按chessFacts.plans中的plan_id解释；没有程序计划时planExplanations返回空数组。禁止创建planId、修改计划类型或增加棋步。
9. playedMoveAnalysis的intention、positiveEffects和problems都必须是完整句子，分别说明直接解决的问题、后续准备、局面影响与具体风险。禁止使用“依据”“可以判断”“根据”开头。
10. 每条candidateLines的directPurpose、continuationExplanation、advantages和risks必须使用完整具体中文；优点和风险要说明对子力、空间、兵形或线路的实际影响，不能只写标签。
11. 弱点、王安全、子力活动、兵形、全局威胁与路线内部事件由后端重点选择器生成，不要输出这些字段；不要自行拆分PV阶段。strategyTags只能使用：{strategy_tags}。
12. 草稿解释文字目标为{length}个中文字符；后端会追加结构化事实并回填真实走法。complexity必须是{complexity}。
13. 物质差、王位置、易位、评价方向、走法质量以及实战着是否与首选一致全部由程序填写。自由文本不得重写。interpretationPolicy.initiative.side为unknown时，禁止声称任何一方拥有主动权；不得把Stockfish分数直接解释成主动权。
14. 必须先回答positionInterpretation.objective.primaryQuestion，并围绕priorityTopics组织局面概览、实战着解释和路线比较。deemphasizedTopics不得成为主线。winning_conversion应解释优势方如何兑现；attack_conversion应解释攻势配合和防守资源；endgame_plan不得在没有直接危险时泛谈护王；dynamic_balance应比较活动性与静态因素；move_quality_explanation必须按真实评价差控制批评强度。
{analogous_rule}

只返回与以下契约完全一致的JSON，不要Markdown或额外字段。数组对象表示元素结构：
{compact_contract}"""


def professional_cache_key(
    move: MoveReview,
    *,
    stockfish_version: str,
    stockfish_depth: int,
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
            f"{danger_side}的直接危险来自{top_threat.side}_{threat_source.piece.split('_')[-1]}从"
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
                f"程序对{threat.ignore_test.ignored_move or '中性应手'}完成Ignore Test，"
                f"最小评价损失为{threat.ignore_test.evaluation_loss:.2f}兵。"
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
                    f"把{first.piece}从{first.from_square}走到{first.to_square}。更深战略目的证据不足，无法可靠判断。"
                    if first else "路线为空，证据不足，无法可靠判断。"
                ),
                "opponentResponse": line.moves[1].san if len(line.moves) > 1 else "路线未提供对手回应",
                "continuationPhases": _safe_phases(line.moves, 3),
                "resultingPosition": _result_position_text(line),
                "advantages": ["这是Stockfish给出的合法候选路线。"],
                "risks": ["路线以外的发展证据不足，不能视为必然发生。"],
                "events": events,
                "whyThisRank": f"排名和评价直接来自Stockfish：rank={line.rank}。",
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
            "intention": f"实战着把{move.played_move.piece}从{move.played_move.from_square}走到{move.played_move.to_square}；主观意图证据不足，无法可靠判断。",
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
        f"{move.played_move.piece}从{move.played_move.from_square}到{move.played_move.to_square}；主观意图证据不足。"
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
            line.direct_purpose = f"{first.piece}从{first.from_square}到{first.to_square}。"
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
) -> ProfessionalAnalysis:
    """Replace protected conclusions with deterministic program-owned text."""
    result = analysis.model_copy(deep=True)
    result.position_assessment.summary = _controlled_position_summary(move)
    result.played_move_analysis.evaluation_reason = _controlled_move_summary(move)
    # These fields are displayed next to program-owned evaluation facts. Keep
    # them deterministic so model prose cannot reclassify a static score or
    # invent a danger when the program found none.
    if result.main_danger.side_in_danger == "none":
        result.main_danger.description = "当前没有程序确认的单一直接危险，证据不足以指定更具体的威胁。"
        result.main_danger.consequence = "继续比较已验证的合法路线，不把普通PV事件升级为当前威胁。"
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
            f"有界Ignore Test的最小评价损失为"
            f"{prepared.ignore_test.evaluation_loss:.2f}兵，对手不能安全忽略这一构想。"
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
                f"Ignore Test检查了{prepared.ignore_test.ignored_move or '中性应手'}；"
                f"最小评价损失为{prepared.ignore_test.evaluation_loss:.2f}兵。"
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
    result.played_move_analysis.problems = [
        f"程序记录实战前后评价为{move.before.evaluation}和{move.after.evaluation}；具体棋理原因只从已验证路线解释。"
    ]
    current_tactics = [
        tactic for tactic in move.verified_tactics
        if tactic.move_uci == move.played_move.uci
    ]
    if current_tactics:
        tactic_text = _guarded_tactic_text(current_tactics[0].description)
        result.played_move_analysis.intention = tactic_text
        result.played_move_analysis.positive_effects = list(dict.fromkeys([
            tactic_text,
            *result.played_move_analysis.positive_effects,
        ]))
    actual_route_moves = {
        item.uci
        for item in (move.actual_move_line.moves if move.actual_move_line else [])
    }
    route_tactics = [
        tactic for tactic in move.verified_tactics
        if tactic.move_uci in actual_route_moves
        and tactic.move_uci != move.played_move.uci
    ]
    if route_tactics:
        result.played_move_analysis.problems.append(
            f"实战后验证路线包含：{_guarded_tactic_text(route_tactics[0].description)}"
        )
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
    return result


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
        rights_text = (
            f"当前保留{'和'.join(rights)}易位权"
            if rights
            else "当前没有易位权"
        )
        parts.append(
            f"{side_name}王位于{chess.square_name(square)}，{rights_text}；"
            "仅凭当前局面不能判断此前是否已经易位。"
        )
    return "".join(parts)


def _controlled_evaluation_text(
    centipawn: int | None,
    mate_in: int | None,
) -> str:
    if mate_in is not None:
        side = "白方" if mate_in > 0 else "黑方"
        return f"程序确认{side}存在强制将杀。"
    if centipawn is None:
        return "程序暂未取得可靠的评价方向。"
    if abs(centipawn) <= 25:
        return "程序评价显示局面接近均势。"
    side = "白方" if centipawn > 0 else "黑方"
    level = "轻微" if abs(centipawn) <= 100 else "明显" if abs(centipawn) <= 300 else "决定性"
    return f"程序评价显示{side}拥有{level}优势。"


def _controlled_material_text(move: MoveReview) -> str:
    material = move.position_facts.material
    white = material.get("white")
    black = material.get("black")
    difference = material.get("valueDifferenceWhiteMinusBlack")
    if not isinstance(white, dict) or not isinstance(black, dict) or not isinstance(difference, int):
        return "程序未取得完整的物质统计。"
    white_value = white.get("value")
    black_value = black.get("value")
    white_pieces = white.get("pieces")
    black_pieces = black.get("pieces")
    if difference == 0:
        return f"程序统计双方物质相等，白方与黑方均为{white_value}分。"
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
                return f"程序统计{side}多一兵。"
    return (
        f"程序统计白方物质为{white_value}分、黑方为{black_value}分，"
        f"{side}多{value}分子力价值。"
    )


def _controlled_move_summary(move: MoveReview) -> str:
    same_as_best = bool(
        move.best_move_uci
        and move.best_move_uci == move.played_move.uci
    )
    text = f"程序将实战着{move.played_move.san}评为{move.quality_label}。"
    if same_as_best:
        text += "该着与Stockfish首选一致。"
    elif move.best_move_san:
        text += f"该着与Stockfish首选不一致，程序首选为{move.best_move_san}。"
    else:
        text += "当前没有可用于一致性比较的Stockfish首选。"
    text += (
        f"评价方向由{_controlled_score_direction(move.before.centipawn)}"
        f"变为{_controlled_score_direction(move.after.centipawn)}。"
    )
    return text


def _controlled_score_direction(centipawn: int | None) -> str:
    if centipawn is None:
        return "未知"
    if centipawn > 25:
        return "白方较好"
    if centipawn < -25:
        return "黑方较好"
    return "接近均势"


def _fit_resolved_analysis_length(
    analysis: ProfessionalAnalysis,
    move: MoveReview,
    level: str,
) -> ProfessionalAnalysis:
    """Fit generated prose to the existing band without changing any referenced chess fact."""
    result = _trim_profile_max(analysis, level)
    minimum = LENGTH_RANGES[level][0]
    first_line = move.candidate_lines[0] if move.candidate_lines else None
    first = first_line.first_move if first_line else None
    verified = (
        f"事实补充：实战{move.played_move.piece}从{move.played_move.from_square}到"
        f"{move.played_move.to_square}（{move.played_move.san}）"
    )
    if first:
        verified += (
            f"；Stockfish第一路线首着为{first.san}，从{first.from_square}到{first.to_square}"
        )
    verified += "。"
    while _narrative_length(result.model_dump(by_alias=True)) < minimum:
        if len(result.position_assessment.summary) + len(verified) <= 500:
            result.position_assessment.summary += verified
        else:
            result.comparison.main_difference += verified
    return _trim_profile_max(result, level)


def _trim_profile_max(analysis: ProfessionalAnalysis, level: str) -> ProfessionalAnalysis:
    """Trim only redundant prose when a deterministic profile is slightly over its band."""
    result = analysis.model_copy(deep=True)
    maximum = LENGTH_RANGES[level][1]

    def length() -> int:
        return _narrative_length(result.model_dump(by_alias=True))

    fields = [
        (result.comparison, "main_difference"),
        (result.comparison, "why_first_line_is_best"),
        (result.position_assessment, "summary"),
        (result.played_move_analysis, "evaluation_reason"),
        (result.played_move_analysis, "intention"),
        (result.played_move_analysis, "resulting_position"),
    ]
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
            (result.played_move_analysis, "evaluation_reason", ""),
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
        return "没有结果局面。"
    facts = line.resulting_position_facts
    consequence = _important_material_consequence(line)
    if facts and consequence:
        return f"参考路线结束时轮到{facts.side_to_move}方行棋；{consequence}"
    if facts:
        return f"参考路线结束时轮到{facts.side_to_move}方行棋；没有需要单独强调的重大子力变化。"
    return "参考路线已结束；没有额外的结果局面事实可供引用。"


def _important_material_consequence(line: Any) -> str:
    for item in line.moves:
        captured = (item.captured_piece or "").split("_", 1)[-1]
        if captured in {"knight", "bishop", "rook", "queen"}:
            return f"路线中的{item.san}会直接造成重要棋子得失。"
    return ""


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
