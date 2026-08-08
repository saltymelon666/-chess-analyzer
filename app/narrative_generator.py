from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .analysis_report import (
    AnalysisReportPackage,
    AnalysisReportUsage,
    GeneratedAnalysisReport,
    SummarySection,
    build_fallback_report,
    validate_report_package,
)

if TYPE_CHECKING:
    from .book_case_transfer import BookCaseTransferPackage

class ThreatNarrativeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threat_id: str
    explanation: str = Field(min_length=1, max_length=600)


class PlanNarrativeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    explanation: str = Field(min_length=1, max_length=600)


class RouteNarrativeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str
    explanation: str = Field(min_length=1, max_length=600)


class SummaryNarrativeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    source_refs: list[str] = Field(min_length=1, max_length=12)


class NarrativeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    information_insufficient: bool = False
    position_summary: str = Field(min_length=1, max_length=1000)
    move_explanation: str = Field(min_length=1, max_length=1000)
    threat_explanation: list[ThreatNarrativeDraft] = Field(default_factory=list, max_length=8)
    plan_explanation: list[PlanNarrativeDraft] = Field(default_factory=list, max_length=8)
    route_explanation: list[RouteNarrativeDraft] = Field(default_factory=list, max_length=3)
    final_summary: SummaryNarrativeDraft


@dataclass(frozen=True)
class NarrativeChatResult:
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    elapsed_ms: int


class NarrativeGenerator:
    """Organize existing facts into prose without discovering chess facts."""

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

    async def generate(
        self,
        package: AnalysisReportPackage,
        *,
        book_context: "BookCaseTransferPackage | None" = None,
    ) -> GeneratedAnalysisReport:
        if not self.configured:
            return _fallback_result(
                package,
                warning="服务端尚未配置DeepSeek，已使用程序事实模板生成报告。",
                attempts=0,
            )

        try:
            result = await self._chat(
                system=narrative_system_prompt(),
                prompt=narrative_user_prompt(package, book_context=book_context),
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            return _fallback_result(
                package,
                warning=f"Narrative DeepSeek暂不可用，已使用程序事实模板：{exc}",
                attempts=1,
            )

        draft, errors = parse_narrative_draft(result.content)
        if draft is not None:
            errors.extend(validate_narrative_draft(draft, package))
        if draft is None or errors or draft.information_insufficient:
            warnings = errors or ["DeepSeek返回information_insufficient"]
            fallback = build_fallback_report(package)
            final_errors = validate_report_package(fallback)
            if final_errors:
                raise RuntimeError("AnalysisReport安全回退未通过校验：" + "；".join(final_errors))
            return GeneratedAnalysisReport(
                report=fallback,
                validation_warnings=[
                    "Narrative输出未通过校验，已使用程序事实模板。",
                    *warnings,
                ],
                usage=AnalysisReportUsage(
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.total_tokens,
                    elapsed_ms=result.elapsed_ms,
                    attempts=1,
                    used_fallback=True,
                ),
            )

        report = apply_narrative_draft(package, draft)
        final_errors = validate_report_package(report)
        if final_errors:
            fallback = build_fallback_report(package)
            fallback_errors = validate_report_package(fallback)
            if fallback_errors:
                raise RuntimeError("AnalysisReport安全回退未通过校验：" + "；".join(fallback_errors))
            return GeneratedAnalysisReport(
                report=fallback,
                validation_warnings=[
                    "Narrative后处理结果未通过校验，已使用程序事实模板。",
                    *final_errors,
                ],
                usage=AnalysisReportUsage(
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.total_tokens,
                    elapsed_ms=result.elapsed_ms,
                    attempts=1,
                    used_fallback=True,
                ),
            )
        return GeneratedAnalysisReport(
            report=report,
            usage=AnalysisReportUsage(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                elapsed_ms=result.elapsed_ms,
                attempts=1,
                used_fallback=False,
            ),
        )

    async def _chat(
        self,
        *,
        system: str,
        prompt: str,
    ) -> NarrativeChatResult:
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
                    "max_tokens": 2200,
                    "temperature": 0.0,
                    "thinking": {"type": "disabled"},
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("DeepSeek Narrative返回了空内容")
        usage = data.get("usage") or {}
        return NarrativeChatResult(
            content=content,
            prompt_tokens=_optional_int(usage.get("prompt_tokens")),
            completion_tokens=_optional_int(usage.get("completion_tokens")),
            total_tokens=_optional_int(usage.get("total_tokens")),
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )


def narrative_system_prompt() -> str:
    return (
        "你是一名专业国际象棋复盘作者。"
        "所有棋局事实已经由程序确认。"
        "你的任务是把已有事实组织成清晰、完整、连贯的复盘报告。"
        "你可以改写语言、连接不同分析模块、解释已有威胁和解释已有计划。"
        "禁止添加新的棋步，禁止修改Stockfish路线，禁止创建新的威胁，"
        "禁止创建新的战略计划，禁止改变评价方向，禁止判断输入之外的棋盘事实。"
        "路线棋步由程序插入，你不能输出SAN、UCI、棋步序列或数值评价。"
        "只有position_overview事实目录、威胁目标或计划事实中已经出现的格子和非评价数量可以复述。"
        "威胁只能通过threat_id解释，计划只能通过plan_id解释，路线只能通过route_id解释。"
        "route_explanation只能说明路线是程序验证的参考变化、用于比较选择且不代表必然发生；"
        "不得在路线解释中提到任何棋子、格子、棋步、威胁或计划。"
        "position_summary和move_explanation中的评价、物质、王位置、易位、"
        "引擎首选一致性与走法质量全部由程序生成；这两个字段只返回“由程序生成”。"
        "不得在其他字段重新表述这些硬事实。"
        "initiative为unknown时，任何字段都不得声称某方拥有主动权。"
        "final_summary的每个观点都必须由source_refs支持。"
        "不得输出引用ID、内部变量名、证据数量或判断过程作为正文。"
        "如果事实不足，将information_insufficient设为true，不要猜测。"
    )


def narrative_user_prompt(
    package: AnalysisReportPackage,
    *,
    book_context: "BookCaseTransferPackage | None" = None,
) -> str:
    payload = json.dumps(
        package.prompt_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    contract = {
        "information_insufficient": False,
        "position_summary": "由程序生成",
        "move_explanation": "由程序生成",
        "threat_explanation": [
            {"threat_id": "existing-threat-id", "explanation": "只解释该威胁"}
        ],
        "plan_explanation": [
            {"plan_id": "existing-plan-id", "explanation": "只解释该计划"}
        ],
        "route_explanation": [
            {"route_id": "existing-route-id", "explanation": "只解释该路线，不写棋步"}
        ],
        "final_summary": {
            "text": "只总结已有走法、威胁、计划或路线",
            "source_refs": ["existing-move-error/threat/plan/route-id"],
        },
    }
    analogous = ""
    if book_context is not None and book_context.cases:
        analogous_payload = json.dumps(
            book_context.prompt_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        analogous = f"""

以下是相似棋书局面的人类分析案例，只能帮助选择观察角度和组织思考顺序：
{analogous_payload}

棋书案例不是事实引用：
- 不得把其中的棋子、格子、着法、评价、胜负或威胁写入当前局面；
- 只有当AnalysisReportPackage已有对应事实、threat_id或plan_id时，才可借鉴其解释角度；
- 棋书中的变化内部事件不得升级为当前事件；
- final_summary的source_refs仍只能引用AnalysisReportPackage已有ID，不能引用棋书案例ID。
"""
    return f"""请把以下AnalysisReportPackage事实载荷组织成六段专业复盘文本：
{payload}
{analogous}

严格规则：
1. 只允许解释输入中已有事实，不得分析棋盘或增加观点。
2. position_summary和move_explanation必须逐字返回“由程序生成”。评价方向、物质差、双方王位置、易位边界、实战着是否与首选一致、走法质量由程序模板填写；其他字段也不得重新表述这些硬事实。initiative.side为unknown时，禁止使用“主动权”“掌握主动”“攻势完全在某方手中”等结论。
3. threat_explanation必须按输入threats顺序完整返回；无威胁时必须返回空数组。
4. plan_explanation必须按输入plans顺序完整返回；无计划时必须返回空数组。
5. route_explanation必须按输入routes顺序完整返回。每项只能说明“该路线是程序验证的参考变化，用于比较不同选择，并不代表对局必然如此进行”；不得提到棋子、格子、棋步、威胁或计划。
6. final_summary.source_refs只能来自move_analysis.move_error_id、threat_id、plan_id或route_id，不能为空。总结提到本步必须引用move_error_id，提到威胁必须引用threat_id，提到计划必须引用plan_id，提到路线必须引用route_id。
7. 正文禁止SAN、UCI、棋步序列、数值评价、模型外威胁、模型外战略和内部ID。只有输入事实中已有的格子和非评价数量可以复述。
8. 如果threats为空，所有正文都禁止使用“威胁、危险、将杀、升变威胁、赢子威胁”；如果plans为空，所有正文都禁止使用“计划、战略、开放线、通路兵、简化残局、改善王安全”。
9. final_summary.text禁止写任何棋盘格、棋子位置或棋步；只写由source_refs直接支持的学习点。
10. 不要使用Markdown，不要输出契约之外的字段。

只返回以下结构的JSON：
{json.dumps(contract, ensure_ascii=False, separators=(",", ":"))}"""


def parse_narrative_draft(content: str) -> tuple[NarrativeDraft | None, list[str]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return None, [f"JSON结构错误：{exc}"]
    try:
        return NarrativeDraft.model_validate(payload), []
    except ValidationError as exc:
        return None, [
            "JSON结构错误："
            + ".".join(str(part) for part in error.get("loc", ()))
            + " "
            + error.get("msg", "")
            for error in exc.errors(include_url=False)
        ]


def validate_narrative_draft(
    draft: NarrativeDraft,
    package: AnalysisReportPackage,
) -> list[str]:
    errors: list[str] = []
    _validate_reference_list(
        errors,
        "threat_id",
        [item.threat_id for item in draft.threat_explanation],
        package.threat_section.threat_ids,
    )
    _validate_reference_list(
        errors,
        "plan_id",
        [item.plan_id for item in draft.plan_explanation],
        package.strategy_section.plan_ids,
    )
    _validate_reference_list(
        errors,
        "route_id",
        [item.route_id for item in draft.route_explanation],
        package.route_section.route_ids,
    )

    refs = draft.final_summary.source_refs
    invalid_refs = sorted(set(refs) - package.allowed_summary_refs)
    if invalid_refs:
        errors.append("final_summary包含不存在的source_refs：" + "、".join(invalid_refs))
    if len(refs) != len(set(refs)):
        errors.append("final_summary.source_refs重复")
    if not refs:
        errors.append("final_summary.source_refs不能为空")

    prose_items = [
        ("position_summary", draft.position_summary, _position_allowed_squares(package)),
        ("move_explanation", draft.move_explanation, set()),
        *[
            (
                f"threat_explanation.{item.threat_id}",
                item.explanation,
                _threat_allowed_squares(package, item.threat_id),
            )
            for item in draft.threat_explanation
        ],
        *[
            (
                f"plan_explanation.{item.plan_id}",
                item.explanation,
                _plan_allowed_squares(package, item.plan_id),
            )
            for item in draft.plan_explanation
        ],
        *[
            (f"route_explanation.{item.route_id}", item.explanation, set())
            for item in draft.route_explanation
        ],
        (
            "final_summary.text",
            draft.final_summary.text,
            _summary_allowed_squares(package, draft.final_summary.source_refs),
        ),
    ]
    for path, text, allowed_squares in prose_items:
        errors.extend(_validate_prose(path, text, allowed_squares))

    errors.extend(_validate_advantage_direction(draft.position_summary, package))
    errors.extend(_validate_global_advantage_claims(draft, package))
    errors.extend(_validate_hard_fact_claims(draft))
    errors.extend(_validate_initiative_claims(draft, package))
    errors.extend(_validate_threat_claims(draft, package))
    errors.extend(_validate_plan_claims(draft, package))
    errors.extend(_validate_absent_section_claims(draft, package))
    errors.extend(_validate_summary_support(draft, package))
    return errors


def apply_narrative_draft(
    package: AnalysisReportPackage,
    draft: NarrativeDraft,
) -> AnalysisReportPackage:
    report = package.model_copy(deep=True)
    controlled = build_fallback_report(package)
    report.position_overview.text = controlled.position_overview.text
    report.move_analysis.text = controlled.move_analysis.text
    threat_text = {
        item.threat_id: item.explanation.strip()
        for item in draft.threat_explanation
    }
    plan_text = {
        item.plan_id: item.explanation.strip()
        for item in draft.plan_explanation
    }
    route_text = {
        item.route_id: item.explanation.strip()
        for item in draft.route_explanation
    }
    for item in report.threat_section.items:
        item.explanation = threat_text[item.threat_id]
    for item in report.strategy_section.items:
        item.explanation = plan_text[item.plan_id]
    for item in report.route_section.routes:
        item.explanation = route_text[item.route_id]
    report.summary_section = SummarySection(
        text=draft.final_summary.text.strip(),
        source_refs=list(draft.final_summary.source_refs),
    )
    return report


def _fallback_result(
    package: AnalysisReportPackage,
    *,
    warning: str,
    attempts: int,
) -> GeneratedAnalysisReport:
    fallback = build_fallback_report(package)
    errors = validate_report_package(fallback)
    if errors:
        raise RuntimeError("AnalysisReport安全回退未通过校验：" + "；".join(errors))
    return GeneratedAnalysisReport(
        report=fallback,
        validation_warnings=[warning],
        usage=AnalysisReportUsage(
            attempts=attempts,
            used_fallback=True,
        ),
    )


def _validate_reference_list(
    errors: list[str],
    label: str,
    actual: list[str],
    expected: list[str],
) -> None:
    invalid = sorted(set(actual) - set(expected))
    if invalid:
        errors.append(f"出现不存在的{label}：" + "、".join(invalid))
    if len(actual) != len(set(actual)):
        errors.append(f"{label}重复")
    if actual != expected:
        errors.append(f"{label}必须按程序目录完整、原序返回")


def _validate_prose(
    path: str,
    text: str,
    allowed_squares: set[str],
) -> list[str]:
    errors: list[str] = []
    if re.search(
        r"(?<![A-Za-z0-9])(?:[a-h][1-8]){2}[qrbn]?(?![A-Za-z0-9])",
        text,
        re.IGNORECASE,
    ):
        errors.append(f"{path}不得输出UCI棋步")
    san_matches = re.findall(
        r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8](?:=[QRBN])?|"
        r"[a-h]x[a-h][1-8](?:=[QRBN])?|[a-h][1-8](?:=[QRBN])?)[+#]?(?![A-Za-z0-9])",
        text,
    )
    invalid_san = [
        item
        for item in san_matches
        if not re.fullmatch(r"[a-h][1-8]", item, re.IGNORECASE)
        or item.lower() not in allowed_squares
    ]
    if invalid_san:
        errors.append(
            f"{path}不得输出SAN棋步：" + "、".join(sorted(set(invalid_san)))
        )
    if re.search(
        r"(?:[+-]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:厘兵|cp|兵优势|分优势)"
        r"|评价(?:为|达到|变为|是)?\s*[+-]?\d",
        text,
        re.IGNORECASE,
    ):
        errors.append(f"{path}不得输出数值评价")
    if re.search(r"\b(?:threat|plan|route|pv|move_error)[_:\-][A-Za-z0-9_-]+\b", text):
        errors.append(f"{path}不得在正文显示内部引用ID")
    if re.search(
        r"双攻|钉住|串击|闪击|引离|诱离|过载|清除防守|弃子|少数兵进攻|王翼进攻|后翼进攻",
        text,
    ):
        errors.append(f"{path}包含程序未提供的战术或战略类型")
    return errors


def _squares_in_text(text: str) -> set[str]:
    return {
        value.lower()
        for value in re.findall(
            r"(?<![A-Za-z0-9])([a-h][1-8])(?![A-Za-z0-9])",
            text,
            re.IGNORECASE,
        )
    }


def _position_allowed_squares(package: AnalysisReportPackage) -> set[str]:
    return set().union(*[
        _squares_in_text(item.description)
        for item in package.position_overview.facts
    ]) if package.position_overview.facts else set()


def _threat_allowed_squares(
    package: AnalysisReportPackage,
    threat_id: str,
) -> set[str]:
    fact = next(
        (item for item in package.threat_section.items if item.threat_id == threat_id),
        None,
    )
    return _squares_in_text(fact.target or "") if fact is not None else set()


def _plan_allowed_squares(
    package: AnalysisReportPackage,
    plan_id: str,
) -> set[str]:
    fact = next(
        (item for item in package.strategy_section.items if item.plan_id == plan_id),
        None,
    )
    if fact is None:
        return set()
    return _squares_in_text(
        " ".join([fact.goal, *fact.structural_evidence])
    )


def _summary_allowed_squares(
    package: AnalysisReportPackage,
    refs: list[str],
) -> set[str]:
    result: set[str] = set()
    for ref in refs:
        result.update(_threat_allowed_squares(package, ref))
        result.update(_plan_allowed_squares(package, ref))
    return result


def _validate_advantage_direction(
    text: str,
    package: AnalysisReportPackage,
) -> list[str]:
    errors: list[str] = []
    white_claim = bool(re.search(r"白方.{0,5}(?:占优|优势|领先|更好)", text))
    black_claim = bool(re.search(r"黑方.{0,5}(?:占优|优势|领先|更好)", text))
    equal_claim = bool(re.search(r"均势|势均力敌|完全平衡", text))
    side = package.position_overview.advantage_side
    if white_claim and side != "white":
        errors.append("position_summary把评价方向错误地写成白方优势")
    if black_claim and side != "black":
        errors.append("position_summary把评价方向错误地写成黑方优势")
    if equal_claim and side != "equal":
        errors.append("position_summary把非均势评价写成均势")
    return errors


def _validate_global_advantage_claims(
    draft: NarrativeDraft,
    package: AnalysisReportPackage,
) -> list[str]:
    prose = "\n".join([
        draft.move_explanation,
        *[item.explanation for item in draft.threat_explanation],
        *[item.explanation for item in draft.plan_explanation],
        *[item.explanation for item in draft.route_explanation],
        draft.final_summary.text,
    ])
    allowed = {package.position_overview.advantage_side}
    allowed.add(_direction_from_cp(package.move_analysis.evaluation_after))
    errors: list[str] = []
    if re.search(r"白方.{0,5}(?:占优|优势|领先|更好)", prose) and "white" not in allowed:
        errors.append("Narrative写入了程序评价不支持的白方优势")
    if re.search(r"黑方.{0,5}(?:占优|优势|领先|更好)", prose) and "black" not in allowed:
        errors.append("Narrative写入了程序评价不支持的黑方优势")
    if re.search(r"均势|势均力敌|完全平衡", prose) and "equal" not in allowed:
        errors.append("Narrative写入了程序评价不支持的均势")
    return errors


def _direction_from_cp(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value > 25:
        return "white"
    if value < -25:
        return "black"
    return "equal"


def _validate_hard_fact_claims(draft: NarrativeDraft) -> list[str]:
    errors: list[str] = []
    if draft.position_summary.strip() != "由程序生成":
        errors.append("position_summary必须由程序模板生成")
    if draft.move_explanation.strip() != "由程序生成":
        errors.append("move_explanation必须由程序模板生成")
    prose = "\n".join([
        *[item.explanation for item in draft.threat_explanation],
        *[item.explanation for item in draft.plan_explanation],
        *[item.explanation for item in draft.route_explanation],
        draft.final_summary.text,
    ])
    protected_patterns = (
        r"(?:白方|黑方).{0,8}(?:多|少)(?:一|两|二)(?:枚|个)?(?:兵|子|子力)",
        r"物质.{0,8}(?:领先|落后|相等|均衡|平衡|多|少)",
        r"(?:白王|黑王|白方的王|黑方的王).{0,6}[a-h][1-8]",
        r"准备易位|已经易位|尚未易位|完成易位|保留易位权|没有易位权",
        r"与.{0,8}(?:引擎|程序|Stockfish).{0,8}(?:首选|第一选择).{0,5}(?:一致|相同)",
        r"(?:白方|黑方).{0,5}(?:占优|优势|领先|更好)|均势|势均力敌|完全平衡",
        r"(?:实战|本步|走法).{0,8}(?:最佳|优秀|好棋|不精确|失误|严重失误)",
    )
    if any(re.search(pattern, prose, re.IGNORECASE) for pattern in protected_patterns):
        errors.append("Narrative不得自由重写程序控制的硬事实")
    return errors


def _validate_initiative_claims(
    draft: NarrativeDraft,
    package: AnalysisReportPackage,
) -> list[str]:
    prose = "\n".join([
        draft.position_summary,
        draft.move_explanation,
        *[item.explanation for item in draft.threat_explanation],
        *[item.explanation for item in draft.plan_explanation],
        *[item.explanation for item in draft.route_explanation],
        draft.final_summary.text,
    ])
    if not re.search(
        r"主动权|掌握主动|保持主动|占据主动|取得主动|攻势.{0,6}(?:手中|掌控)",
        prose,
    ):
        return []
    if package.initiative.side == "unknown":
        return ["主动权证据门禁未通过，Narrative不得输出主动权结论"]
    expected = "白方" if package.initiative.side == "white" else "黑方"
    if not re.search(
        rf"{expected}.{{0,10}}(?:主动权|掌握主动|攻势)",
        prose,
    ):
        return ["Narrative的主动权归属与程序门禁不一致"]
    return []


THREAT_TERMS = {
    "mate_threat": {"将杀", "绝杀"},
    "tactical_capture": {"战术吃子"},
    "material_win": {"赢子", "赢得子力"},
    "promotion_threat": {"升变"},
    "center_break": {"中心突破"},
}
PLAN_TERMS = {
    "improve_worst_piece": {"改善最差棋子"},
    "prepare_center_break": {"准备中心突破"},
    "occupy_open_file": {"占领开放线"},
    "activate_rook": {"激活车"},
    "improve_king_safety": {"改善王安全"},
    "attack_weak_pawn": {"攻击弱兵"},
    "create_passed_pawn": {"制造通路兵", "通路兵"},
    "simplify_endgame": {"有利简化", "简化残局"},
}


def _validate_threat_claims(
    draft: NarrativeDraft,
    package: AnalysisReportPackage,
) -> list[str]:
    errors: list[str] = []
    source = {item.threat_id: item for item in package.threat_section.items}
    all_terms = set().union(*THREAT_TERMS.values())
    for item in draft.threat_explanation:
        fact = source.get(item.threat_id)
        if fact is None:
            continue
        allowed = THREAT_TERMS.get(fact.type, set())
        wrong = sorted(term for term in all_terms - allowed if term in item.explanation)
        if wrong:
            errors.append(
                f"threat_explanation.{item.threat_id}创建或修改了威胁类型："
                + "、".join(wrong)
            )
    return errors


def _validate_plan_claims(
    draft: NarrativeDraft,
    package: AnalysisReportPackage,
) -> list[str]:
    errors: list[str] = []
    source = {item.plan_id: item for item in package.strategy_section.items}
    all_terms = set().union(*PLAN_TERMS.values())
    for item in draft.plan_explanation:
        fact = source.get(item.plan_id)
        if fact is None:
            continue
        allowed = PLAN_TERMS.get(fact.type, set())
        wrong = sorted(term for term in all_terms - allowed if term in item.explanation)
        if wrong:
            errors.append(
                f"plan_explanation.{item.plan_id}创建或修改了战略类型："
                + "、".join(wrong)
            )
    return errors


def _validate_absent_section_claims(
    draft: NarrativeDraft,
    package: AnalysisReportPackage,
) -> list[str]:
    prose = "\n".join([
        draft.position_summary,
        draft.move_explanation,
        *[item.explanation for item in draft.route_explanation],
        draft.final_summary.text,
    ])
    errors: list[str] = []
    if (
        not package.threat_section.items
        and re.search(r"威胁|直接危险|将杀|升变威胁|赢子威胁", prose)
    ):
        errors.append("ThreatPackage为空时不得生成威胁观点")
    if (
        not package.strategy_section.items
        and re.search(r"战略|计划|开放线|通路兵|简化残局|改善王安全", prose)
    ):
        errors.append("StrategicPlanPackage为空时不得生成战略观点")
    return errors


def _validate_summary_support(
    draft: NarrativeDraft,
    package: AnalysisReportPackage,
) -> list[str]:
    text = draft.final_summary.text
    refs = set(draft.final_summary.source_refs)
    errors: list[str] = []
    threat_refs = refs.intersection(package.threat_section.threat_ids)
    plan_refs = refs.intersection(package.strategy_section.plan_ids)
    route_refs = refs.intersection(package.route_section.route_ids)
    if re.search(r"威胁|直接危险|将杀|升变威胁|赢子威胁", text) and not threat_refs:
        errors.append("final_summary的威胁观点没有threat_id支持")
    if re.search(r"战略|计划|开放线|通路兵|简化残局|改善王安全", text) and not plan_refs:
        errors.append("final_summary的战略观点没有plan_id支持")
    if re.search(r"路线|变化|后续延续", text) and not route_refs:
        errors.append("final_summary的路线观点没有route_id支持")
    if re.search(r"本步|实战|走法|失误|改进方向", text):
        if package.move_analysis.move_error_id not in refs:
            errors.append("final_summary的走法观点没有move_error_id支持")
    return errors


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None
