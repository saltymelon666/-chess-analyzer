from __future__ import annotations

import json
import re

import httpx
from pydantic import ValidationError

from .chess_facts import (
    ChessFactPackage,
    FactExplanationDraft,
    PlanExplanation,
    ThreatExplanation,
    build_engine_fact_package,
    build_move_fact_package,
    render_fact_explanation,
    safe_fact_explanation,
    validate_fact_explanation,
)
from .complexity import EXPLANATION_PROFILES
from .models import EngineResult, GeneratedMoveExplanation, MoveExplanationDetails, MoveReview
from .strategic_plans import StrategicPlanAnalyzer
from .threat_analysis import ThreatAnalyzer


class DeepSeekExplainer:
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
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def explain(self, fen: str, result: EngineResult) -> str:
        package = build_engine_fact_package(fen, result)
        package.threats = ThreatAnalyzer().detect(package)
        package.plans = StrategicPlanAnalyzer().analyze(package).plans
        return await self.explain_fact_package(package)

    async def explain_fact_package(self, package: ChessFactPackage) -> str:
        if not self.configured:
            return safe_fact_explanation(package)

        system = self._fact_system_prompt()
        prompt = self._fact_prompt(package)
        last_errors: list[str] = []
        for attempt in range(2):
            current_prompt = prompt
            if attempt:
                current_prompt += (
                    "\n\n上一次输出未通过程序校验："
                    + "；".join(last_errors)
                    + "。请只修正引用和解释，不要返回任何棋步、格子、威胁类型或新事实。"
                )
            try:
                content = await self._chat(
                    system=system,
                    prompt=current_prompt,
                    max_tokens=600,
                    temperature=0.0,
                    json_mode=True,
                )
            except (httpx.HTTPError, RuntimeError):
                return safe_fact_explanation(package)
            draft, parse_errors = parse_fact_explanation(content)
            last_errors = list(parse_errors)
            if draft is not None:
                last_errors.extend(validate_fact_explanation(draft, package))
                rendered = render_fact_explanation(draft)
                if not last_errors and rendered:
                    return rendered
            if draft is not None and draft.information_insufficient:
                return safe_fact_explanation(package)
        return safe_fact_explanation(package)

    async def explain_move(self, move: MoveReview) -> GeneratedMoveExplanation:
        if not self.configured:
            fallback_details = conservative_move_details(move)
            return GeneratedMoveExplanation(
                explanation=render_move_explanation(fallback_details),
                details=fallback_details,
            )

        package = build_move_fact_package(move)
        package.threats = ThreatAnalyzer().detect(package)
        package.plans = StrategicPlanAnalyzer().analyze(
            package,
            position_facts=move.position_facts,
        ).plans
        profile = EXPLANATION_PROFILES[move.complexity]
        prompt = self._move_prompt(move, profile, package)
        system = (
            self._fact_system_prompt()
            + "你是一位耐心的儿童国际象棋讲解员。不要责怪孩子。"
        )
        try:
            content = await self._chat(
                system=system,
                prompt=prompt,
                max_tokens=int(profile["max_tokens"]),
                temperature=0.2,
                json_mode=True,
            )
        except (httpx.HTTPError, RuntimeError):
            fallback_details = conservative_move_details(move)
            return GeneratedMoveExplanation(
                explanation=render_move_explanation(fallback_details),
                details=fallback_details,
            )
        details, parse_errors = parse_move_explanation_details(content, move, package)
        if details is not None and not parse_errors:
            _apply_threat_explanations(details)
        rendered = render_move_explanation(details) if details else ""
        grounding = (
            validate_fact_explanation(_move_fact_draft(rendered, details), package)
            if rendered else []
        )
        errors = [*parse_errors, *validate_move_explanation(rendered, move), *grounding]
        if not errors:
            return GeneratedMoveExplanation(explanation=rendered, details=details)

        correction = (
            prompt
            + "\n\n上一次回答未通过事实校验，原因："
            + "；".join(errors)
            + f"\n上一次回答：{content}\n请只用允许的数据重写一次。"
        )
        try:
            corrected = await self._chat(
                system=system,
                prompt=correction,
                max_tokens=int(profile["max_tokens"]),
                temperature=0.1,
                json_mode=True,
            )
        except (httpx.HTTPError, RuntimeError):
            fallback_details = conservative_move_details(move)
            return GeneratedMoveExplanation(
                explanation=render_move_explanation(fallback_details),
                details=fallback_details,
            )
        corrected_details, parse_errors = parse_move_explanation_details(
            corrected,
            move,
            package,
        )
        if corrected_details is not None and not parse_errors:
            _apply_threat_explanations(corrected_details)
        corrected_rendered = render_move_explanation(corrected_details) if corrected_details else ""
        corrected_grounding = (
            validate_fact_explanation(
                _move_fact_draft(corrected_rendered, corrected_details),
                package,
            )
            if corrected_rendered else []
        )
        if (
            not parse_errors
            and not validate_move_explanation(corrected_rendered, move)
            and not corrected_grounding
        ):
            return GeneratedMoveExplanation(explanation=corrected_rendered, details=corrected_details)
        fallback_details = conservative_move_details(move)
        return GeneratedMoveExplanation(
            explanation=render_move_explanation(fallback_details),
            details=fallback_details,
        )

    @staticmethod
    def _move_prompt(
        move: MoveReview,
        profile: dict[str, int],
        package: ChessFactPackage,
    ) -> str:
        payload = package.prompt_payload()
        format_rule = {
            "simple": (
                "conclusion、problem、betterMove、childTip 必须有内容；其他字段可以是空字符串或空数组。"
                "直接说明好坏，不展开长变化。"
            ),
            "normal": (
                "currentSituation、problem、betterMove 必须有内容；"
                "opponentThreat必须为空，只能通过threatExplanations按threatId解释程序确认的威胁；"
                "variationExplanation 可包含 0—2 个已验证步骤。"
            ),
            "complex": (
                "除opponentThreat外的文字字段必须有内容。opponentThreat必须为空，"
                "只能通过threatExplanations按threatId解释程序确认的威胁；"
                "currentSituation 要说明为什么难；playedMoveIdea 要说明实战着看起来合理的已验证表面作用；"
                "problem 要分步骤说明忽略的事实；betterMove 要说明推荐着首先处理什么；"
                "variationExplanation 必须按已验证 PV 顺序写 2—4 项，每项只解释对应一步。"
            ),
        }[move.complexity]
        return f"""请把下面的ChessFactPackage改写成适合4—12岁孩子的中文。

结构化事实：
{json.dumps(payload, ensure_ascii=False, indent=2)}

本局面复杂度是 {move.complexity}。所有字段值合计必须为 {profile['min_chars']}—{profile['max_chars']} 个非空白字符。
{format_rule}childTip 必须以“记住：”开头。
不得输出任何SAN、UCI、棋盘格或具体棋步。需要指代时只写“实战走法”“最佳走法”“该路线”，
真实棋步由后端根据route_id插入。不得把路线扩写成输入中没有的威胁或战术。

只返回一个 JSON 对象，不要使用 Markdown，不要添加 JSON 之外的文字。字段必须严格为：
{{
  "complexity": "{move.complexity}",
  "conclusion": "总体评价",
  "currentSituation": "核心局面与难点",
  "opponentThreat": "",
  "playedMoveIdea": "实战走法的表面作用",
  "problem": "实战走法的问题",
  "betterMove": "Stockfish推荐及作用",
  "variationExplanation": ["按PV顺序的第一步", "第二步"],
  "threatExplanations": [
    {{"threatId": "必须来自threats", "explanation": "只解释该威胁，不写棋步或新类型"}}
  ],
  "planExplanations": [
    {{"planId": "必须来自plans", "explanation": "只解释程序确认的计划，不写棋步或新计划"}}
  ],
  "childTip": "记住：儿童提示"
}}"""

    @staticmethod
    def _fact_system_prompt() -> str:
        return (
            "你是国际象棋讲解员，不是国际象棋引擎。所有棋局事实必须来自输入JSON。"
            "禁止自己计算最佳走法、生成棋步、修改路线、判断棋子位置、推测攻击关系、"
            "推测评价变化或添加输入中不存在的信息。"
            "输入中的threat已由程序确认；你不能发现、创建或修改威胁。"
            "输入中的战略计划已经由程序确认；你不能创建、修改或扩展计划。"
            "你只能解释已有route_id、event_id、threat_id和plan_id。"
            "不得在解释文字中写SAN、UCI、棋盘格、数值评价或输入中不存在的威胁类型。"
            "输入不足时返回information_insufficient为true，不要猜测。"
        )

    @staticmethod
    def _fact_prompt(package: ChessFactPackage) -> str:
        payload = json.dumps(package.prompt_payload(), ensure_ascii=False, separators=(",", ":"))
        return f"""请只根据下面的ChessFactPackage生成简洁中文解释：
{payload}

只返回JSON，不要Markdown或额外字段：
{{
  "information_insufficient": false,
  "summary": "只解释程序已经给出的评价方向",
  "actual_move_explanation": "",
  "best_move_explanation": "只解释第一候选的作用，不写棋步",
  "route_explanations": [
    {{"route_id": "必须来自candidate_routes", "explanation": "不含棋步的路线解释"}}
  ],
  "event_explanations": [
    {{"event_id": "必须来自events", "explanation": "不扩展事件的解释"}}
  ],
  "threat_explanations": [
    {{"threat_id": "必须来自threats", "explanation": "只解释该程序确认威胁为何需要注意及一般应对原则，不写棋步"}}
  ],
  "plan_explanations": [
    {{"plan_id": "必须来自plans", "explanation": "只解释该程序确认计划为什么合理，不增加棋步或战略判断"}}
  ]
}}"""

    async def _chat(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool = False,
    ) -> str:
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
                    **({"response_format": {"type": "json_object"}} if json_mode else {}),
                },
            )
            response.raise_for_status()
            data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("DeepSeek 返回了空内容")
        return content

    @staticmethod
    def _score_text(centipawn: int | None, mate_in: int | None) -> str:
        if mate_in is not None:
            return f"白 M{mate_in}" if mate_in > 0 else f"黑 M{abs(mate_in)}"
        return f"{(centipawn or 0) / 100:+.2f}"


def parse_fact_explanation(
    content: str,
) -> tuple[FactExplanationDraft | None, list[str]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
        return FactExplanationDraft.model_validate(payload), []
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return None, [f"返回内容不是规定的事实解释JSON：{exc}"]


def parse_move_explanation_details(
    content: str,
    move: MoveReview,
    package: ChessFactPackage | None = None,
) -> tuple[MoveExplanationDetails | None, list[str]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
        details = MoveExplanationDetails.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return None, [f"返回内容不是规定的 JSON：{exc}"]

    errors: list[str] = []
    if details.complexity != move.complexity:
        errors.append(f"complexity 应为 {move.complexity}")
    required = {
        "simple": ["conclusion", "problem", "better_move", "child_tip"],
        "normal": ["conclusion", "current_situation", "opponent_threat", "problem", "better_move", "child_tip"],
        "complex": [
            "conclusion",
            "current_situation",
            "opponent_threat",
            "played_move_idea",
            "problem",
            "better_move",
            "child_tip",
        ],
    }[move.complexity]
    if package is not None:
        required = [field for field in required if field != "opponent_threat"]
        if details.opponent_threat.strip():
            errors.append("opponent_threat 必须为空；威胁只能通过threatExplanations引用")
        threat_ids = [item.threat_id for item in details.threat_explanations]
        invalid_threats = sorted(set(threat_ids) - package.threat_ids)
        if invalid_threats:
            errors.append("出现不存在的threat_id：" + "、".join(invalid_threats))
        if len(threat_ids) != len(set(threat_ids)):
            errors.append("threat_id重复")
        plan_ids = [item.plan_id for item in details.plan_explanations]
        invalid_plans = sorted(set(plan_ids) - package.plan_ids)
        if invalid_plans:
            errors.append("出现不存在的plan_id：" + "、".join(invalid_plans))
        if len(plan_ids) != len(set(plan_ids)):
            errors.append("plan_id重复")
    for field in required:
        if not getattr(details, field).strip():
            errors.append(f"{field} 不能为空")
    variation_count = len([item for item in details.variation_explanation if item.strip()])
    if move.complexity == "simple" and variation_count > 1:
        errors.append("simple 不应展开多步变化")
    if move.complexity == "normal" and variation_count > 2:
        errors.append("normal 最多解释 2 个半回合")
    if move.complexity == "complex" and not 2 <= variation_count <= 4:
        errors.append("complex 必须逐步解释 2—4 个半回合")
    if not details.child_tip.strip().startswith("记住："):
        errors.append("childTip 必须以“记住：”开头")
    return details, errors


def _apply_threat_explanations(details: MoveExplanationDetails) -> None:
    details.opponent_threat = " ".join(
        item.explanation.strip()
        for item in details.threat_explanations
        if item.explanation.strip()
    )


def _move_fact_draft(
    rendered: str,
    details: MoveExplanationDetails | None,
) -> FactExplanationDraft:
    return FactExplanationDraft(
        summary=rendered,
        threat_explanations=[
            ThreatExplanation(
                threat_id=item.threat_id,
                explanation=item.explanation,
            )
            for item in (details.threat_explanations if details else [])
        ],
        plan_explanations=[
            PlanExplanation(
                plan_id=item.plan_id,
                explanation=item.explanation,
            )
            for item in (details.plan_explanations if details else [])
        ],
    )


def render_move_explanation(details: MoveExplanationDetails) -> str:
    parts = [
        details.conclusion,
        details.current_situation,
        details.opponent_threat,
        *[item.explanation for item in details.plan_explanations],
        details.played_move_idea,
        details.problem,
        details.better_move,
        *details.variation_explanation,
        details.child_tip,
    ]
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def validate_move_explanation(content: str, move: MoveReview) -> list[str]:
    errors: list[str] = []
    compact_length = len(re.sub(r"\s+", "", content))
    profile = EXPLANATION_PROFILES[move.complexity]
    if compact_length < profile["min_chars"] or compact_length > profile["max_chars"]:
        errors.append(
            f"长度应为{profile['min_chars']}—{profile['max_chars']}字，实际{compact_length}字"
        )

    allowed_squares = set(move.allowed_squares)
    square_like = set(re.findall(r"(?<![a-z0-9])([a-z][0-9])(?![a-z0-9])", content, re.IGNORECASE))
    out_of_range = sorted(
        value
        for value in square_like
        if value.lower()[0] != "m" and not re.fullmatch(r"[a-h][1-8]", value, re.IGNORECASE)
    )
    if out_of_range:
        errors.append("出现棋盘范围外的格子：" + "、".join(out_of_range))
    mentioned_squares = set(re.findall(r"(?<![a-z0-9])[a-h][1-8](?![a-z0-9])", content, re.IGNORECASE))
    invalid_squares = sorted(square.lower() for square in mentioned_squares if square.lower() not in allowed_squares)
    if invalid_squares:
        errors.append("出现未提供的格子：" + "、".join(invalid_squares))

    allowed_uci = {value.lower() for value in move.allowed_moves if re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", value)}
    mentioned_uci = set(re.findall(r"(?<![a-z0-9])([a-h][1-8][a-h][1-8][qrbn]?)(?![a-z0-9])", content, re.IGNORECASE))
    invalid_uci = sorted(value for value in mentioned_uci if value.lower() not in allowed_uci)
    if invalid_uci:
        errors.append("出现未提供的 UCI 走法：" + "、".join(invalid_uci))

    allowed_san = {_normal_san(value) for value in move.allowed_moves if not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", value)}
    san_pattern = r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8](?:=[QRBN])?|[a-h]x[a-h][1-8](?:=[QRBN])?|[a-h][18]=[QRBN])[+#]?(?![A-Za-z0-9])"
    mentioned_san = set(re.findall(san_pattern, content))
    invalid_san = sorted(value for value in mentioned_san if _normal_san(value) not in allowed_san)
    if invalid_san:
        errors.append("出现未提供的 SAN 走法：" + "、".join(invalid_san))

    all_moves = [
        move.played_move,
        *([move.best_move] if move.best_move else []),
        *([move.opponent_reply] if move.opponent_reply else []),
        *move.principal_variation_facts,
        *move.opponent_variation_facts,
    ]
    pv_text = " ".join([*move.principal_variation, *move.opponent_variation])
    allows_capture = any(item.capture for item in all_moves) or "x" in pv_text
    allows_checkmate = any(item.checkmate for item in all_moves) or "#" in pv_text or move.mate_involved
    allows_check = allows_checkmate or any(item.check for item in all_moves) or "+" in pv_text
    allows_castling = any(item.castling for item in all_moves) or "O-O" in pv_text
    allows_promotion = any(item.promotion for item in all_moves) or "=" in pv_text
    event_term = r"(?:吃子|吃掉|捕获|拿掉|将军|将杀|绝杀|易位|升变)"
    positive_claims = re.sub(
        rf"(?:不是|没有|并未|并没有|未)(?:形成|完成|发生|造成)?{event_term}(?:、{event_term})*",
        "",
        content,
    )
    if re.search(r"吃子|吃掉|捕获|拿掉", positive_claims) and not allows_capture:
        errors.append("把普通走法描述成了吃子")
    if re.search(r"将杀|绝杀", positive_claims) and not allows_checkmate:
        errors.append("提到了未确认的将杀")
    if "将军" in positive_claims and not allows_check:
        errors.append("提到了未确认的将军")
    if "易位" in positive_claims and not allows_castling:
        errors.append("提到了未确认的易位")
    if re.search(r"升变|变后", positive_claims) and not allows_promotion:
        errors.append("提到了未确认的升变")

    played_scope = " ".join(re.findall(r"(?:实战|这一步|这步)[^。！？\n]{0,36}", positive_claims))
    if re.search(r"吃子|吃掉|捕获|拿掉", played_scope) and not move.played_move.capture:
        errors.append("把实战普通走法描述成了吃子")
    if re.search(r"将杀|绝杀", played_scope) and not move.played_move.checkmate:
        errors.append("把实战走法描述成了将杀")
    if "将军" in played_scope and not move.played_move.check:
        errors.append("把实战走法描述成了将军")
    if "易位" in played_scope and not move.played_move.castling:
        errors.append("把实战走法描述成了易位")
    if re.search(r"升变|变后", played_scope) and not move.played_move.promotion:
        errors.append("把实战走法描述成了升变")

    piece_name_map = {"兵": "pawn", "卒": "pawn", "马": "knight", "象": "bishop", "车": "rook", "后": "queen", "王": "king"}
    move_claim_pattern = r"(白|黑)(?:方的)?(兵|卒|马|象|车|后|王)[^。！？\n]{0,12}?从\s*([a-h][1-8])[^。！？\n]{0,8}?到\s*([a-h][1-8])"
    for color_zh, piece_zh, from_square, to_square in re.findall(move_claim_pattern, content):
        piece_id = f"{'white' if color_zh == '白' else 'black'}_{piece_name_map[piece_zh]}"
        if not any(
            item.piece == piece_id and item.from_square == from_square and item.to_square == to_square
            for item in all_moves
        ):
            errors.append(f"{color_zh}{piece_zh}的起点、终点或颜色与结构化事实不符")

    inventory = set(move.pieces_before.values())
    piece_terms = {
        "pawn": ("兵", "卒"),
        "knight": ("马",),
        "bishop": ("象",),
        "rook": ("车",),
        "queen": ("后",),
        "king": ("王",),
    }
    for color, prefix in (("white", "白"), ("black", "黑")):
        for piece, terms in piece_terms.items():
            patterns = [prefix + term for term in terms] + [prefix + "方的" + term for term in terms]
            if any(pattern in content for pattern in patterns) and f"{color}_{piece}" not in inventory:
                errors.append(f"提到了当前局面不存在的{prefix}{terms[0]}")
    if re.search(r"皇后|王后|棋后", content) and not any(item.endswith("_queen") for item in inventory):
        errors.append("提到了当前局面不存在的后")

    if "记住：" not in content:
        errors.append("缺少以“记住：”开头的提示")
    return errors


def conservative_move_details(move: MoveReview) -> MoveExplanationDetails:
    side = "白方" if move.side == "white" else "黑方"
    played = move.played_move
    best = move.best_move
    event = "完成了一步普通走棋"
    if played.capture:
        event = "完成了吃子"
    if played.castling:
        event = "完成了王车易位"
    if played.promotion:
        event = "完成了升变"
    if played.checkmate:
        event = "形成了将杀"
    elif played.check:
        event = "形成了将军"
    conclusion = f"第{move.move_number}回合，{side}走了{played.san}，这步被评为{move.quality_label}。"
    problem = f"引擎评价从{move.before.evaluation}变为{move.after.evaluation}。"
    better = (
        f"Stockfish更推荐{best.san}。"
        if best and best.uci != played.uci
        else "实战走法已经接近Stockfish的第一选择。"
    )
    child_tip = "记住：先看对手最有力的回应，再决定自己的走法。"
    if move.complexity == "simple":
        return MoveExplanationDetails(
            complexity="simple",
            conclusion=conclusion,
            currentSituation="",
            opponentThreat="",
            playedMoveIdea="",
            problem=problem,
            betterMove=better,
            variationExplanation=[],
            childTip=child_tip,
        )

    current = (
        f"这个局面有{move.complexity_factors.legal_move_count}个合法走法，"
        f"实战着把{_piece_label(played.piece)}从{played.from_square}走到{played.to_square}，{event}。"
    )
    opponent = (
        f"实战走完后，对手的引擎第一选择是{move.opponent_reply.san}"
        f"（{move.opponent_reply.from_square}到{move.opponent_reply.to_square}）。"
        if move.opponent_reply
        else "实战走完后没有完整的对手变化，因此这里不猜测具体威胁。"
    )
    played_idea = f"这步实际完成的事情是把{_piece_label(played.piece)}放到{played.to_square}；没有经过验证的意图不作猜测。"
    better_detail = (
        f"Stockfish更推荐{best.san}（{best.from_square}到{best.to_square}），这是走棋前局面中的合法第一选择。"
        if best and best.uci != played.uci
        else "实战走法就是Stockfish在走棋前局面中的合法第一选择。"
    )
    if move.complexity == "normal":
        return MoveExplanationDetails(
            complexity="normal",
            conclusion=conclusion,
            currentSituation=current,
            opponentThreat=opponent,
            playedMoveIdea="",
            problem=problem,
            betterMove=better_detail,
            variationExplanation=[f"可能的主要变化从{move.principal_variation[0]}开始。"] if move.principal_variation else [],
            childTip=child_tip,
        )

    difficulty = (
        f"它之所以难，是因为评价波动为{move.complexity_factors.evaluation_swing_cp if move.complexity_factors.evaluation_swing_cp is not None else '将杀变化'}，"
        f"连续强制变化有{move.complexity_factors.forcing_line_plies}个半回合，"
        f"并有{move.complexity_factors.engaged_piece_count}枚棋子直接参与攻防。"
    )
    tactic_text = (
        "已确认的战术包括：" + "；".join(tactic.description for tactic in move.verified_tactics[:3]) + "。"
        if move.verified_tactics
        else "结构化事实没有确认钉住、双攻或串击，因此不补写隐藏战术。"
    )
    complex_problem = problem + opponent + tactic_text
    variation = _fallback_variation_steps(move)
    return MoveExplanationDetails(
        complexity="complex",
        conclusion=conclusion,
        currentSituation=current + difficulty,
        opponentThreat=opponent,
        playedMoveIdea=played_idea,
        problem=complex_problem,
        betterMove=better_detail,
        variationExplanation=variation,
        childTip="记住：复杂局面先核对对手最有力的回应，再逐步比较候选走法。",
    )


def conservative_move_explanation(move: MoveReview) -> str:
    return render_move_explanation(conservative_move_details(move))


def _fallback_variation_steps(move: MoveReview) -> list[str]:
    steps = []
    for index, fact in enumerate(move.principal_variation_facts[:4], start=1):
        event = []
        if fact.capture:
            event.append("吃子")
        if fact.checkmate:
            event.append("将杀")
        elif fact.check:
            event.append("将军")
        if fact.promotion:
            event.append("升变")
        suffix = "，并且" + "、".join(event) if event else ""
        steps.append(f"第{index}步是{fact.san}：{_piece_label(fact.piece)}从{fact.from_square}到{fact.to_square}{suffix}。")
    while len(steps) < 2:
        steps.append(f"第{len(steps) + 1}步以后PV信息不完整，因此不补写未经验证的变化。")
    return steps


def _piece_label(piece_id: str) -> str:
    color, piece = piece_id.split("_", 1)
    names = {"pawn": "兵", "knight": "马", "bishop": "象", "rook": "车", "queen": "后", "king": "王"}
    return ("白" if color == "white" else "黑") + names.get(piece, "棋子")


def _normal_san(value: str) -> str:
    return value.replace("0", "O").rstrip("+#")
