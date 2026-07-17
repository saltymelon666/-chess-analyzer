from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .models import (
    GeneratedProfessionalAnalysis,
    MoveReview,
    ProfessionalAnalysis,
    ProfessionalAnalysisUsage,
    ProfessionalComplexity,
)
from .professional_validation import (
    build_validation_context,
    parse_professional_analysis,
    validate_professional_analysis,
)


logger = logging.getLogger(__name__)
PROFESSIONAL_PROMPT_VERSION = "professional-v1"
PROFESSIONAL_TOKEN_LIMITS = {"simple": 1800, "normal": 3200, "complex": 5200}


@dataclass(frozen=True)
class ChatResult:
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    elapsed_ms: int


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

    async def analyze(self, move: MoveReview) -> GeneratedProfessionalAnalysis:
        if not self.configured:
            raise RuntimeError("服务端尚未配置 DeepSeek API Key")
        complexity = compute_professional_complexity(move)
        context = build_validation_context(move, complexity.level)
        payload = build_professional_payload(move, complexity, context.allowed_evidence_ids)
        system = professional_system_prompt()
        prompt = professional_user_prompt(payload, complexity.level)
        usage_results: list[ChatResult] = []
        last_errors: list[str] = []
        last_content = ""
        parsed: ProfessionalAnalysis | None = None

        for attempt in range(2):
            current_prompt = prompt
            if attempt:
                current_prompt += (
                    "\n\n上一次返回未通过程序校验。错误如下：\n- "
                    + "\n- ".join(last_errors)
                    + "\n请对照原始事实包纠正一次，完整返回JSON。不得保留错误字段，也不得新增事实。"
                    + f"\n上一次返回：\n{last_content}"
                )
            result = await self._chat(
                system=system,
                prompt=current_prompt,
                max_tokens=PROFESSIONAL_TOKEN_LIMITS[complexity.level],
                temperature=0.1 if attempt else 0.2,
            )
            usage_results.append(result)
            last_content = result.content
            parsed, parse_errors = parse_professional_analysis(result.content)
            last_errors = parse_errors
            if parsed is not None:
                last_errors.extend(validate_professional_analysis(parsed, context))
            if parsed is not None and not last_errors:
                return GeneratedProfessionalAnalysis(
                    analysis=parsed,
                    complexity_reasons=complexity.reasons,
                    usage=_usage(usage_results),
                )
            logger.warning(
                "Professional DeepSeek validation failed on attempt %s: %s",
                attempt + 1,
                last_errors,
            )

        safe = build_safe_professional_analysis(move, complexity)
        safe_errors = validate_professional_analysis(safe, context, enforce_length=False)
        warnings = ["DeepSeek两次返回均未通过校验，已删除不可信内容并使用结构化事实生成安全结果。", *last_errors]
        if safe_errors:
            warnings.extend("安全结果提示：" + item for item in safe_errors)
        return GeneratedProfessionalAnalysis(
            analysis=safe,
            complexity_reasons=complexity.reasons,
            validation_warnings=warnings,
            usage=_usage(usage_results),
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
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            data = response.json()
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
) -> dict[str, Any]:
    return {
        "promptVersion": PROFESSIONAL_PROMPT_VERSION,
        "currentMove": {
            "id": move.played_move.id or f"move:played:{move.index}",
            "plyIndex": move.index,
            "fullMoveNumber": move.move_number,
            "side": move.side,
            "fenBefore": move.before_fen,
            "fenAfter": move.after_fen,
            "playedMove": move.played_move.model_dump(by_alias=True),
            "evaluationBefore": {"id": f"evaluation:before:{move.index}", **move.before.model_dump()},
            "evaluationAfter": {"id": f"evaluation:after:{move.index}", **move.after.model_dump()},
            "centipawnLoss": move.centipawn_loss,
            "quality": move.quality_label,
        },
        "positionBefore": move.position_facts.model_dump(by_alias=True),
        "positionAfter": move.position_facts_after.model_dump(by_alias=True),
        "playedMoveContinuation": (
            move.actual_move_line.model_dump(by_alias=True) if move.actual_move_line else None
        ),
        "candidateLines": [line.model_dump(by_alias=True) for line in move.candidate_lines],
        "complexity": complexity.level,
        "complexityReasons": complexity.reasons,
        "allowedSquares": sorted(set(move.allowed_squares)),
        "allowedMoves": sorted(set(move.allowed_moves)),
        "allowedEvidenceIds": sorted(allowed_evidence_ids),
    }


def professional_system_prompt() -> str:
    return (
        "你负责根据已经验证的棋盘事实和Stockfish变化，生成专业、具体的国际象棋局面分析。"
        "你不是国际象棋引擎，不能自行计算或猜测棋局。你只能引用输入中存在的棋子和格子、实战走法、"
        "Stockfish候选路线、局面事实和证据ID。每个关于危险、弱点、关键棋子、战略方向和走法目的的结论，"
        "都必须提供evidenceRefs。如果数据不能可靠确定某个战略判断，必须输出“证据不足，无法可靠判断”，"
        "不能编造内容。Stockfish的PV只是双方采用较强应对时的参考路线，不能描述成必然发生。"
    )


def professional_user_prompt(payload: dict[str, Any], complexity: str) -> str:
    length = {"simple": "400—700", "normal": "800—1300", "complex": "1400—2200"}[complexity]
    schema = ProfessionalAnalysis.model_json_schema(by_alias=True)
    return f"""请分析以下完整事实包：
{json.dumps(payload, ensure_ascii=False, indent=2)}

严格规则：
1. 每个evidenceRefs只能取自allowedEvidenceIds，并且必须支持对应颜色和结论。
2. keyPieces必须真实存在于fenBefore；playedMoveAnalysis.move必须等于实际SAN或UCI。
3. candidateLines必须与输入路线数量、rank和firstMove一一对应；每个continuationPhases.moves只能来自自己的PV，不能串线。
4. 禁止只写“加强中心、注意防守、改善子力、形成压力、准备进攻、局面复杂”。如使用类似结论，必须继续说明具体棋子、格子、目标、实现走法和路线证据。
5. 最大危险必须说明处于危险的一方、来源棋子与格子、目标、危险级别和不处理的后果。无可靠危险时sideInDanger写none，并明确证据不足。
6. strategyTags只能使用JSON Schema中的枚举。每个战略方向至少引用一条证据。
7. 变化按阶段解释，不能只堆SAN。PV只能描述为参考变化。
8. 正文目标长度为{length}个中文字符；complexity必须是{complexity}。

只返回一个符合以下JSON Schema的对象，不要Markdown，不要额外文字：
{json.dumps(schema, ensure_ascii=False)}"""


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
    material_ref = str(move.position_facts.material.get("id"))
    king_refs = {
        side: [fact.id for fact in move.position_facts.king_safety if fact.side == side][:3]
        for side in ("white", "black")
    }
    activity_refs = [fact.id for fact in move.position_facts.piece_activity[:4]] or [material_ref]
    pawn_refs = [fact.id for fact in move.position_facts.pawn_structure[:4]] or [material_ref]
    material = move.position_facts.material
    difference = material.get("valueDifferenceWhiteMinusBlack", 0)

    danger_side = "none"
    danger_level = "long_term"
    danger_description = "结构化事实没有确认需要立即处理的单一危险，证据不足，无法可靠判断更具体的威胁。"
    danger_consequence = "继续比较棋规库列出的强制走法和Stockfish第一路线，不补写未验证后果。"
    danger_refs = [material_ref]
    threat_source = next((item for item in move.actual_move_line.moves if item.check or item.capture) if move.actual_move_line else (), None)
    if threat_source:
        danger_side = "black" if threat_source.side == "white" else "white"
        danger_level = "immediate"
        danger_description = (
            f"{danger_side}的直接危险来自{threat_source.side}_{threat_source.piece.split('_')[-1]}从"
            f"{threat_source.from_square}走到{threat_source.to_square}的参考着{threat_source.san}。"
        )
        danger_consequence = "若进入这条参考变化，将发生已由棋规库确认的吃子或将军事件。"
        danger_refs = [threat_source.id]

    key_pieces = []
    pieces = move.position_facts.pieces
    for side in ("white", "black"):
        preferred = next((piece for piece in pieces if piece["side"] == side and piece["piece"] != "pawn"), None)
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
    route_moves = [item for line in move.candidate_lines for item in line.moves]
    if move.actual_move_line:
        route_moves.extend(move.actual_move_line.moves)
    for side in ("white", "black"):
        item = next((route_move for route_move in route_moves if route_move.side == side), None)
        if item:
            tag = _safe_strategy_tag(item)
            plans[side].append(
                {
                    "strategyTag": tag,
                    "description": f"参考路线只确认{item.piece}从{item.from_square}走到{item.to_square}（{item.san}）。",
                    "requiredPreparation": "路线之外的准备步骤证据不足，无法可靠判断。",
                    "evidenceRefs": [item.id],
                }
            )

    weaknesses = {"white": [], "black": []}
    for fact in [*move.position_facts.piece_activity, *move.position_facts.pawn_structure]:
        if fact.side in weaknesses and fact.category in {
            "undefended_piece", "underprotected", "isolated_pawn", "doubled_pawns", "vulnerable_pawn"
        }:
            weaknesses[fact.side].append(
                {
                    "description": fact.description,
                    "exploitation": "对手如何长期利用仍需以候选路线为准，当前不补写路线外走法。",
                    "evidenceRefs": [fact.id],
                }
            )
    threats = []
    for fact in move.position_facts.threats[:4]:
        if fact.side in {"white", "black"}:
            threats.append(
                {
                    "side": fact.side,
                    "level": "immediate" if fact.category.startswith("immediate") else "short_term",
                    "description": fact.description,
                    "target": "、".join(fact.squares) or "证据不足，无法可靠判断具体目标",
                    "evidenceRefs": [fact.id],
                }
            )

    actual = move.actual_move_line
    actual_phases = _safe_phases(actual.moves if actual else [], 3)
    strongest = actual.first_move.san if actual else "证据不足，无法可靠判断"
    candidate_analyses = []
    for line in move.candidate_lines:
        first = line.moves[0] if line.moves else None
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
                "whyThisRank": f"排名和评价直接来自Stockfish：rank={line.rank}。",
                "evidenceRefs": [line.id, *([first.id] if first else [])],
            }
        )

    first_line = move.candidate_lines[0] if move.candidate_lines else None
    comparison_refs = [line.id for line in move.candidate_lines] or [material_ref]
    safe_payload = {
        "complexity": complexity.level,
        "positionAssessment": {
            "summary": f"当前由{move.side}行棋；所有判断仅来自FEN、棋规事实和Stockfish参考路线。",
            "material": {
                "description": f"白方减黑方的结构化子力价值差为{difference}。",
                "evidenceRefs": [material_ref],
            },
            "kingSafety": {
                "white": {
                    "description": _joined_fact_text(move.position_facts.king_safety, "white"),
                    "evidenceRefs": king_refs["white"] or [material_ref],
                },
                "black": {
                    "description": _joined_fact_text(move.position_facts.king_safety, "black"),
                    "evidenceRefs": king_refs["black"] or [material_ref],
                },
            },
            "pieceActivity": {
                "description": "；".join(fact.description for fact in move.position_facts.piece_activity[:4]) or "没有更多可靠活动度事实。",
                "evidenceRefs": activity_refs,
            },
            "pawnStructure": {
                "description": "；".join(fact.description for fact in move.position_facts.pawn_structure[:4]) or "没有更多可靠兵结构事实。",
                "evidenceRefs": pawn_refs,
            },
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
    return ProfessionalAnalysis.model_validate(safe_payload)


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


def _result_position_text(line: Any) -> str:
    if line is None:
        return "没有结果局面。"
    facts = line.resulting_position_facts
    if facts:
        difference = facts.material.get("valueDifferenceWhiteMinusBlack", 0)
        return f"参考路线结束FEN为{line.resulting_fen}；白方减黑方的子力价值差为{difference}。"
    return f"参考路线结束FEN为{line.resulting_fen}。"


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


def _usage(results: list[ChatResult]) -> ProfessionalAnalysisUsage:
    def total(field: str) -> int | None:
        values = [getattr(result, field) for result in results]
        return sum(value for value in values if value is not None) if any(value is not None for value in values) else None

    return ProfessionalAnalysisUsage(
        prompt_tokens=total("prompt_tokens"),
        completion_tokens=total("completion_tokens"),
        total_tokens=total("total_tokens"),
        elapsed_ms=sum(result.elapsed_ms for result in results),
        attempts=len(results),
    )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
