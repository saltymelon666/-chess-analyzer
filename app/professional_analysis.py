from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

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
)
from .professional_validation import (
    LENGTH_RANGES,
    VAGUE_PHRASES,
    _narrative_length,
    build_validation_context,
    validate_professional_analysis,
)
from .strategic_plans import (
    STRATEGIC_PLAN_PACKAGE_VERSION,
    StrategicPlanAnalyzer,
)
from .threat_analysis import THREAT_PACKAGE_VERSION, ThreatAnalyzer
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
PROFESSIONAL_PROMPT_VERSION = "professional-v9-strategic-plan-package"
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
    ) -> GeneratedProfessionalAnalysis:
        complexity = compute_professional_complexity(move)
        context = build_validation_context(move, complexity.level)
        if not self.configured:
            safe = build_safe_professional_analysis(move, complexity)
            errors = validate_professional_analysis(safe, context)
            if errors:
                raise RuntimeError("安全专业分析未通过事实校验")
            return GeneratedProfessionalAnalysis(
                analysis=safe,
                complexity_reasons=complexity.reasons,
                validation_warnings=["服务端尚未配置DeepSeek，已使用统一事实包生成安全结果。"],
                usage=_usage([]),
            )
        fact_package = build_move_fact_package(move)
        if not fact_package.threats:
            fact_package.threats = ThreatAnalyzer().detect(fact_package)
        strategic_plan_package = StrategicPlanAnalyzer().analyze(
            fact_package,
            position_facts=move.position_facts,
        )
        fact_package.plans = strategic_plan_package.plans
        payload = build_professional_payload(
            move,
            complexity,
            context.allowed_evidence_ids,
            fact_package=fact_package,
        )
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
        safe = build_safe_professional_analysis(move, complexity)
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
        package.threats = ThreatAnalyzer().detect(package)
    if not package.plans:
        package.plans = StrategicPlanAnalyzer().analyze(
            package,
            position_facts=move.position_facts,
        ).plans
    payload = build_reference_payload(move, complexity.level, complexity.reasons)
    payload.get("pos", {}).pop("fen", None)
    payload["chessFacts"] = package.protocol_manifest()
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
        "所有棋子必须用pieceRef，候选路线必须用lineRef，PV必须用plyRefs，事实必须用evidenceRefs。"
        "自由解释文本只能使用中文、中文标点和常用百分数，禁止任何拉丁字母、棋盘格、SAN或UCI；"
        "自由解释文本也禁止自行写吃子、将军、将杀或绝杀，这些事件由后端从ply事实填充；"
        "需要指代具体对象时只能写‘该棋子’‘该路线’‘该阶段’，后端会从引用ID回填真实棋子、格子和走法。"
        "不能引用输入目录之外的ID，不能把白方与黑方说反。证据不足时返回空数组、null或isRelevant为false。"
        "没有保护不等于弱点，王前兵较少不等于存在攻王，没有易位权不等于王不安全。"
        "单条Stockfish路线中的普通吃子不等于全局潜在威胁，物质数量不作为固定栏目。"
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
    return f"""请根据以下引用目录生成分析草稿：
{compact_payload}

严格规则：
1. pieceRef只能来自pos.pieces[].id；不得输出piece、square或自造棋子名称。
2. candidateLines必须恰好返回{len(payload.get('lines', []))}项，lineRef按lines顺序逐条引用；每条路线只返回一个plyRefs数组，必须按顺序完整覆盖该路线plies[].id，不能串线。本次不可改动的引用骨架为：{line_skeleton}
3. playedMoveAnalysis.moveRef必须等于played.ref；strongestReplyRef及唯一的plyRefs数组只能来自并完整覆盖actual.plies。
4. evidenceRefs、dangerRef只能引用输入中出现的ID。每组evidenceRefs只选1—4个最相关ID，不要枚举整份事实目录。每个危险、计划和因果结论必须有证据。
5. 自由文本只能使用中文和中文标点，禁止拉丁字母、数字、棋盘格、SAN和UCI。不得自行写“吃子、将军、将杀、绝杀”等事件词，这些事件由后端根据ply填充。错误示例：“控制d4”“走Qe2”；正确示例：“控制该中心格”“该路线首着完成协调”。
6. mainDanger有具体危险时用dangerRef引用一个已有ply；无可靠直接危险时dangerRef写null且level写none。危险一方由后端从ply推导，不要输出sideInDanger。
7. positionAssessment只允许输出summary，不得输出material、kingSafety、pieceActivity或pawnStructure；这些动态栏目全部由后端重点选择器按selectedFacts回填。
8. positionAssessment.summary必须用完整段落具体说明双方子力状态、活跃与受限棋子、中心和两翼局势，不能只写“当前局面某方子”之类残句。
9. keyPieces的role只说明棋子当前位置、控制线路、活跃或受限状态及实际影响；不要写“下一项任务”。futureTask字段仍按契约返回，但只作内部数据，不要在role中重复。
10. plans.white和plans.black必须返回空数组。战略计划只能通过planExplanations按chessFacts.plans中的plan_id解释；没有程序计划时planExplanations返回空数组。禁止创建planId、修改计划类型或增加棋步。
11. playedMoveAnalysis的intention、positiveEffects和problems都必须是完整句子，分别说明直接解决的问题、后续准备、局面影响与具体风险。禁止使用“依据”“可以判断”“根据”开头。
12. 每条candidateLines的directPurpose、continuationExplanation、advantages和risks必须使用完整具体中文；优点和风险要说明对子力、空间、兵形或线路的实际影响，不能只写标签。
13. 为避免重复，keyPieces.white只能引用白方棋子，keyPieces.black只能引用黑方棋子；弱点、王安全、子力活动、兵形、全局威胁与路线内部事件由后端重点选择器生成，不要输出这些字段；不要自行拆分PV阶段。strategyTags只能使用：{strategy_tags}。
14. 草稿解释文字目标为{length}个中文字符；后端会追加结构化事实并回填真实走法。complexity必须是{complexity}。

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
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_safe_professional_analysis(
    move: MoveReview,
    complexity: ProfessionalComplexity,
) -> ProfessionalAnalysis:
    focus = select_analysis_focus(move)
    played_ref = move.played_move.id or f"move:played:{move.index}"
    raw_by_id = {
        fact.id: fact
        for group in (
            move.position_facts.piece_activity,
            move.position_facts.king_safety,
            move.position_facts.pawn_structure,
            move.position_facts.threats,
            move.position_facts.key_pieces,
        )
        for fact in group
    }

    danger_side = "none"
    danger_level = "long_term"
    danger_description = "结构化事实没有确认需要立即处理的单一危险，证据不足，无法可靠判断更具体的威胁。"
    danger_consequence = "继续比较棋规库列出的强制走法和Stockfish第一路线，不补写未验证后果。"
    danger_refs = [played_ref]
    top_threat = focus.global_threats[0] if focus.global_threats else None
    threat_source = next(
        (
            item
            for item in (*move.position_facts.immediate_checks, *move.position_facts.immediate_captures)
            if top_threat and item.id in top_threat.evidence_refs
        ),
        None,
    )
    if top_threat and threat_source:
        danger_side = "black" if top_threat.side == "white" else "white"
        danger_level = "immediate"
        danger_description = (
            f"{danger_side}的直接危险来自{top_threat.side}_{threat_source.piece.split('_')[-1]}从"
            f"{threat_source.from_square}走到{threat_source.to_square}的参考着{threat_source.san}。"
        )
        danger_consequence = top_threat.decision_impact
        danger_refs = list(top_threat.evidence_refs)

    key_pieces = []
    pieces = move.position_facts.pieces
    for side in ("white", "black"):
        selected_squares = [square for fact in focus.key_piece_facts[side] for square in fact.squares]
        preferred = next(
            (
                piece
                for square in selected_squares
                for piece in pieces
                if piece["side"] == side and piece["square"] == square
            ),
            None,
        ) or next((piece for piece in pieces if piece["side"] == side and piece["piece"] != "pawn"), None)
        if preferred:
            key_pieces.append(
                {
                    "side": side,
                    "piece": preferred["piece"],
                    "square": preferred["square"],
                    "role": "当前棋盘上真实存在；更具体作用证据不足，无法可靠判断。",
                    "futureTask": "只沿Stockfish参考路线观察，不补写路线外任务。",
                    "evidenceRefs": [preferred["id"]],
                }
            )

    plans = {"white": [], "black": []}
    fact_package = build_move_fact_package(move)
    if not fact_package.threats:
        fact_package.threats = ThreatAnalyzer().detect(fact_package)
    strategic_package = StrategicPlanAnalyzer().analyze(
        fact_package,
        position_facts=move.position_facts,
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
        "keyPieces": key_pieces,
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
    for piece in result.key_pieces:
        piece.role = f"{piece.side}_{piece.piece}位于{piece.square}。"
        piece.future_task = "仅沿参考线观察。"
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
    return _trim_profile_max(result, level)


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
    for piece in result.key_pieces:
        fields.extend([(piece, "role"), (piece, "future_task")])
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
            (piece, "future_task", "")
            for piece in result.key_pieces
        ] + [
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
        return result

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
            (result, "key_pieces", compact.key_pieces),
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
