from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

from .analysis_focus import select_analysis_focus
from .chess_facts import ChessFactPackage, FactEvaluation
from .models import EvidenceFact, MoveReview
from .strategic_plans import StrategicPlanPackage
from .threat_analysis import ThreatPackage, position_id


ANALYSIS_REPORT_PACKAGE_VERSION = "1.0"


class ReportMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    white_value: int
    black_value: int
    value_difference_white_minus_black: int
    advantage: Literal["white", "black", "equal"]


class ReportPositionFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    type: str
    side: Literal["white", "black"] | None = None
    description: str


class PositionOverviewSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation: FactEvaluation
    advantage_side: Literal["white", "black", "equal", "unknown"]
    advantage_level: Literal["equal", "slight", "clear", "decisive", "forced_mate", "unknown"]
    material: ReportMaterial
    king_safety_fact_ids: list[str] = Field(default_factory=list)
    position_fact_ids: list[str] = Field(default_factory=list, min_length=1)
    facts: list[ReportPositionFact] = Field(default_factory=list)
    text: str = ""


class ReportMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    san: str
    uci: str
    source: str


class MoveAnalysisSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    move_error_id: str
    side: Literal["white", "black"]
    actual_move: ReportMove
    best_move: ReportMove | None = None
    evaluation_before: int | None = None
    evaluation_after: int | None = None
    classification: str
    actual_move_legal: bool
    same_as_best: bool
    text: str = ""


class ReportThreatItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threat_id: str
    type: str
    side: Literal["white", "black"]
    target: str | None = None
    supporting_moves: list[str] = Field(default_factory=list)
    evidence_route_ids: list[str] = Field(default_factory=list)
    confidence: Literal["medium", "high"]
    explanation: str = ""


class ThreatSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threat_ids: list[str] = Field(default_factory=list)
    items: list[ReportThreatItem] = Field(default_factory=list)


class ReportPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    type: str
    side: Literal["white", "black"]
    goal: str
    supporting_moves: list[str] = Field(default_factory=list)
    evidence_route_ids: list[str] = Field(default_factory=list)
    structural_evidence: list[str] = Field(default_factory=list)
    confidence: Literal["medium", "high"]
    explanation: str = ""


class StrategySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_ids: list[str] = Field(default_factory=list)
    items: list[ReportPlanItem] = Field(default_factory=list)


class ReportRouteItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str
    moves_san: list[str] = Field(default_factory=list, min_length=1)
    evaluation: int | None = None
    mate: int | None = None
    verified: Literal[True] = True
    explanation: str = ""


class RouteSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_ids: list[str] = Field(default_factory=list)
    routes: list[ReportRouteItem] = Field(default_factory=list)


class SummarySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    source_refs: list[str] = Field(default_factory=list)


class AnalysisReportPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = ANALYSIS_REPORT_PACKAGE_VERSION
    report_id: str
    position_overview: PositionOverviewSection
    move_analysis: MoveAnalysisSection
    threat_section: ThreatSection
    strategy_section: StrategySection
    route_section: RouteSection
    summary_section: SummarySection

    @property
    def allowed_summary_refs(self) -> set[str]:
        return {
            self.move_analysis.move_error_id,
            *self.threat_section.threat_ids,
            *self.strategy_section.plan_ids,
            *self.route_section.route_ids,
        }

    def prompt_payload(self) -> dict[str, Any]:
        """Return the only Phase 4 payload allowed to cross the LLM boundary."""
        position = self.position_overview
        move = self.move_analysis
        return {
            "position_overview": {
                "evaluation": {
                    "perspective": position.evaluation.perspective,
                    "advantage_side": position.advantage_side,
                    "advantage_level": position.advantage_level,
                    "forced_mate": position.evaluation.mate is not None,
                    "source": position.evaluation.source,
                },
                "advantage_side": position.advantage_side,
                "advantage_level": position.advantage_level,
                "material": {
                    "fact_id": position.material.fact_id,
                    "advantage": position.material.advantage,
                },
                "king_safety_fact_ids": position.king_safety_fact_ids,
                "position_fact_ids": position.position_fact_ids,
                "facts": [item.model_dump() for item in position.facts],
            },
            "move_analysis": {
                "move_error_id": move.move_error_id,
                "side": move.side,
                "actual_move_legal": move.actual_move_legal,
                "best_move_available": move.best_move is not None,
                "same_as_best": move.same_as_best,
                "evaluation_before": _score_direction(move.evaluation_before),
                "evaluation_after": _score_direction(move.evaluation_after),
                "evaluation_change_for_mover": _evaluation_change_for_mover(move),
                "classification": move.classification,
            },
            "threats": [
                {
                    "threat_id": item.threat_id,
                    "type": item.type,
                    "side": item.side,
                    "target": item.target,
                    "evidence_route_ids": item.evidence_route_ids,
                    "confidence": item.confidence,
                }
                for item in self.threat_section.items
            ],
            "plans": [
                {
                    "plan_id": item.plan_id,
                    "type": item.type,
                    "side": item.side,
                    "goal": item.goal,
                    "evidence_route_ids": item.evidence_route_ids,
                    "structural_evidence": item.structural_evidence,
                    "confidence": item.confidence,
                }
                for item in self.strategy_section.items
            ],
            # Route notation and evaluation are deliberately server-only.
            "routes": [{"route_id": item.route_id} for item in self.route_section.routes],
        }


class AnalysisReportUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    elapsed_ms: int = 0
    attempts: int = 0
    used_fallback: bool = False


class GeneratedAnalysisReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: AnalysisReportPackage
    validation_warnings: list[str] = Field(default_factory=list)
    usage: AnalysisReportUsage


class AnalysisReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: AnalysisReportPackage
    validation_warnings: list[str] = Field(default_factory=list)
    usage: AnalysisReportUsage
    cached: bool = False


def build_analysis_report(
    move: MoveReview,
    fact_package: ChessFactPackage,
    threat_package: ThreatPackage,
    strategic_plan_package: StrategicPlanPackage,
) -> AnalysisReportPackage:
    """Build the Phase 4 package exclusively from program-verified inputs."""
    canonical_fen = chess.Board(move.before_fen).fen()
    if fact_package.position.fen != canonical_fen:
        raise ValueError("ChessFactPackage position does not match MoveReview")
    expected_position_id = position_id(canonical_fen)
    if threat_package.position_id != expected_position_id:
        raise ValueError("ThreatPackage position does not match ChessFactPackage")
    if strategic_plan_package.position_id != expected_position_id:
        raise ValueError("StrategicPlanPackage position does not match ChessFactPackage")
    if (
        fact_package.actual_move is None
        or fact_package.actual_move.uci != move.played_move.uci
        or fact_package.actual_move.san != move.played_move.san
    ):
        raise ValueError("ChessFactPackage actual move does not match MoveReview")

    verified_route_ids = fact_package.verified_route_ids
    threats = [
        item
        for item in threat_package.threats
        if item.evidence_route_ids
        and set(item.evidence_route_ids) <= verified_route_ids
    ]
    plans = [
        item
        for item in strategic_plan_package.plans
        if len(set(item.evidence_route_ids)) >= 2
        and set(item.evidence_route_ids) <= verified_route_ids
    ]
    routes = [
        item
        for item in fact_package.candidate_routes
        if item.verified and item.moves_san
    ]
    _require_unique_ids("route_id", [item.route_id for item in routes])
    _require_unique_ids("threat_id", [item.threat_id for item in threats])
    _require_unique_ids("plan_id", [item.plan_id for item in plans])

    material = _material(move)
    position_facts = _position_facts(move, material.fact_id)
    king_ids = [
        item.fact_id for item in position_facts
        if item.type.startswith("king:")
    ]
    general_ids = [
        item.fact_id for item in position_facts
        if not item.type.startswith("king:")
    ]
    actual = fact_package.actual_move

    best = fact_package.best_move
    report_id = _report_id(
        move,
        fact_package,
        threat_package,
        strategic_plan_package,
    )
    return AnalysisReportPackage(
        report_id=report_id,
        position_overview=PositionOverviewSection(
            evaluation=fact_package.evaluation,
            advantage_side=_advantage_side(fact_package.evaluation),
            advantage_level=_advantage_level(fact_package.evaluation),
            material=material,
            king_safety_fact_ids=king_ids,
            position_fact_ids=general_ids,
            facts=position_facts,
        ),
        move_analysis=MoveAnalysisSection(
            move_error_id=f"move_error:{move.index}",
            side="white" if move.side == "white" else "black",
            actual_move=ReportMove(
                san=actual.san,
                uci=actual.uci,
                source=actual.source,
            ),
            best_move=(
                ReportMove(san=best.san, uci=best.uci, source=best.source)
                if best is not None else None
            ),
            evaluation_before=actual.evaluation_before,
            evaluation_after=actual.evaluation_after,
            classification=actual.classification,
            actual_move_legal=actual.legal,
            same_as_best=bool(best and best.uci == actual.uci),
        ),
        threat_section=ThreatSection(
            threat_ids=[item.threat_id for item in threats],
            items=[
                ReportThreatItem(
                    threat_id=item.threat_id,
                    type=item.type,
                    side=item.side,
                    target=item.target,
                    supporting_moves=list(item.supporting_moves),
                    evidence_route_ids=list(item.evidence_route_ids),
                    confidence=item.confidence,
                )
                for item in threats
            ],
        ),
        strategy_section=StrategySection(
            plan_ids=[item.plan_id for item in plans],
            items=[
                ReportPlanItem(
                    plan_id=item.plan_id,
                    type=item.type,
                    side=item.side,
                    goal=item.goal,
                    supporting_moves=list(item.supporting_moves),
                    evidence_route_ids=list(item.evidence_route_ids),
                    structural_evidence=list(item.structural_evidence),
                    confidence=item.confidence,
                )
                for item in plans
            ],
        ),
        route_section=RouteSection(
            route_ids=[item.route_id for item in routes],
            routes=[
                ReportRouteItem(
                    route_id=item.route_id,
                    moves_san=list(item.moves_san),
                    evaluation=item.evaluation,
                    mate=item.mate,
                )
                for item in routes
            ],
        ),
        summary_section=SummarySection(),
    )


def build_fallback_report(package: AnalysisReportPackage) -> AnalysisReportPackage:
    """Fill every narrative slot from program data without using an LLM."""
    report = package.model_copy(deep=True)
    report.position_overview.text = _fallback_position_text(report.position_overview)
    report.move_analysis.text = _fallback_move_text(report.move_analysis)

    for item in report.threat_section.items:
        item.explanation = _fallback_threat_text(item)
    for item in report.strategy_section.items:
        item.explanation = _fallback_plan_text(item)
    for item in report.route_section.routes:
        item.explanation = (
            f"程序验证路线为{'、'.join(item.moves_san)}。"
            "这条变化仅作为已验证参考，不代表对局必然如此进行。"
        )

    refs = [report.move_analysis.move_error_id]
    summary_parts = [_fallback_move_summary(report.move_analysis)]
    if report.threat_section.items:
        refs.append(report.threat_section.items[0].threat_id)
        summary_parts.append("复盘时还应优先检查程序确认的直接威胁。")
    if report.strategy_section.items:
        refs.append(report.strategy_section.items[0].plan_id)
        summary_parts.append(
            f"长期改进方向是{report.strategy_section.items[0].goal}。"
        )
    report.summary_section = SummarySection(
        text="".join(summary_parts),
        source_refs=refs,
    )
    return report


def validate_report_package(package: AnalysisReportPackage) -> list[str]:
    errors: list[str] = []
    if not package.position_overview.text.strip():
        errors.append("position_overview.text为空")
    if not package.move_analysis.text.strip():
        errors.append("move_analysis.text为空")
    if not package.summary_section.text.strip():
        errors.append("summary_section.text为空")

    if package.threat_section.threat_ids != [
        item.threat_id for item in package.threat_section.items
    ]:
        errors.append("threat_section的ID目录与条目不一致")
    if package.strategy_section.plan_ids != [
        item.plan_id for item in package.strategy_section.items
    ]:
        errors.append("strategy_section的ID目录与条目不一致")
    if package.route_section.route_ids != [
        item.route_id for item in package.route_section.routes
    ]:
        errors.append("route_section的ID目录与条目不一致")

    if any(not item.explanation.strip() for item in package.threat_section.items):
        errors.append("存在空的威胁解释")
    if any(not item.explanation.strip() for item in package.strategy_section.items):
        errors.append("存在空的战略解释")
    if any(not item.explanation.strip() for item in package.route_section.routes):
        errors.append("存在空的路线解释")

    refs = package.summary_section.source_refs
    if not refs:
        errors.append("summary_section.source_refs不能为空")
    invalid_refs = sorted(set(refs) - package.allowed_summary_refs)
    if invalid_refs:
        errors.append("summary_section包含不存在的source_refs：" + "、".join(invalid_refs))
    if len(refs) != len(set(refs)):
        errors.append("summary_section.source_refs重复")
    return errors


def report_cache_key(package: AnalysisReportPackage) -> str:
    payload = package.model_dump()
    payload["position_overview"]["text"] = ""
    payload["move_analysis"]["text"] = ""
    payload["summary_section"] = {"text": "", "source_refs": []}
    for item in payload["threat_section"]["items"]:
        item["explanation"] = ""
    for item in payload["strategy_section"]["items"]:
        item["explanation"] = ""
    for item in payload["route_section"]["routes"]:
        item["explanation"] = ""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _material(move: MoveReview) -> ReportMaterial:
    source = move.position_facts.material
    white = source.get("white") if isinstance(source.get("white"), dict) else {}
    black = source.get("black") if isinstance(source.get("black"), dict) else {}
    difference = int(source.get("valueDifferenceWhiteMinusBlack", 0) or 0)
    advantage = source.get("advantage")
    if advantage not in {"white", "black", "equal"}:
        advantage = "white" if difference > 0 else "black" if difference < 0 else "equal"
    return ReportMaterial(
        fact_id=str(source.get("id") or f"fact:move-{move.index}:material"),
        white_value=int(white.get("value", 0) or 0),
        black_value=int(black.get("value", 0) or 0),
        value_difference_white_minus_black=difference,
        advantage=advantage,
    )


def _position_facts(move: MoveReview, material_id: str) -> list[ReportPositionFact]:
    result = [
        ReportPositionFact(
            fact_id=material_id,
            type="material",
            description=_material_fact_description(move),
        )
    ]
    focus = select_analysis_focus(move)
    general_by_id = {
        item.id: item
        for item in (
            *move.position_facts.piece_activity,
            *move.position_facts.pawn_structure,
            *move.position_facts.key_pieces,
        )
        if item.id
    }
    selected = [
        general_by_id[item.id]
        for item in focus.selected_facts
        if item.id in general_by_id
    ]
    if not selected:
        selected = list(general_by_id.values())[:8]
    result.extend(
        _report_fact(item, f"position:{index}")
        for index, item in enumerate(selected, start=1)
    )
    result.extend(
        _report_fact(item, f"king:{index}", type_prefix="king:")
        for index, item in enumerate(move.position_facts.king_safety, start=1)
    )
    return _deduplicate_facts(result)


def _report_fact(
    fact: EvidenceFact,
    fallback_id: str,
    *,
    type_prefix: str = "",
) -> ReportPositionFact:
    side = fact.side if fact.side in {"white", "black"} else None
    return ReportPositionFact(
        fact_id=fact.id or fallback_id,
        type=f"{type_prefix}{fact.category}",
        side=side,
        description=fact.description,
    )


def _deduplicate_facts(items: list[ReportPositionFact]) -> list[ReportPositionFact]:
    result: list[ReportPositionFact] = []
    seen: set[str] = set()
    for item in items:
        if item.fact_id in seen:
            continue
        seen.add(item.fact_id)
        result.append(item)
    return result


def _require_unique_ids(label: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"AnalysisReport contains duplicate {label}")


def _material_fact_description(move: MoveReview) -> str:
    material = move.position_facts.material
    white = material.get("white") if isinstance(material.get("white"), dict) else {}
    black = material.get("black") if isinstance(material.get("black"), dict) else {}
    return (
        f"白方子力价值为{int(white.get('value', 0) or 0)}，"
        f"黑方子力价值为{int(black.get('value', 0) or 0)}"
    )


def _advantage_side(
    evaluation: FactEvaluation,
) -> Literal["white", "black", "equal", "unknown"]:
    if evaluation.mate is not None:
        if evaluation.mate > 0:
            return "white"
        if evaluation.mate < 0:
            return "black"
        return "equal"
    if evaluation.evaluation_cp is None:
        return "unknown"
    if evaluation.evaluation_cp > 25:
        return "white"
    if evaluation.evaluation_cp < -25:
        return "black"
    return "equal"


def _advantage_level(
    evaluation: FactEvaluation,
) -> Literal["equal", "slight", "clear", "decisive", "forced_mate", "unknown"]:
    if evaluation.mate is not None:
        return "forced_mate"
    if evaluation.evaluation_cp is None:
        return "unknown"
    absolute = abs(evaluation.evaluation_cp)
    if absolute <= 25:
        return "equal"
    if absolute < 100:
        return "slight"
    if absolute < 250:
        return "clear"
    return "decisive"


def _score_direction(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value > 25:
        return "white"
    if value < -25:
        return "black"
    return "equal"


def _evaluation_change_for_mover(section: MoveAnalysisSection) -> str:
    before = section.evaluation_before
    after = section.evaluation_after
    if before is None or after is None:
        return "unknown"
    delta = after - before
    mover_delta = delta if section.side == "white" else -delta
    if mover_delta > 25:
        return "improved"
    if mover_delta < -25:
        return "worsened"
    return "stable"


def _report_id(
    move: MoveReview,
    fact_package: ChessFactPackage,
    threat_package: ThreatPackage,
    strategic_plan_package: StrategicPlanPackage,
) -> str:
    raw = json.dumps(
        {
            "version": ANALYSIS_REPORT_PACKAGE_VERSION,
            "fen": move.before_fen,
            "move": move.played_move.uci,
            "fact_version": fact_package.version,
            "routes": [
                {"id": item.route_id, "moves": item.moves_uci}
                for item in fact_package.candidate_routes if item.verified
            ],
            "threat_version": threat_package.version,
            "threats": [item.model_dump() for item in threat_package.threats],
            "plan_version": strategic_plan_package.version,
            "plans": [item.model_dump() for item in strategic_plan_package.plans],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "report_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _fallback_position_text(section: PositionOverviewSection) -> str:
    if section.advantage_side == "white":
        evaluation = "程序评价显示白方占优。"
    elif section.advantage_side == "black":
        evaluation = "程序评价显示黑方占优。"
    elif section.advantage_side == "equal":
        evaluation = "程序评价显示局面接近均势。"
    else:
        evaluation = "程序暂未取得完整的数值评价。"
    material = section.material
    material_text = (
        f"子力统计为白方{material.white_value}分、黑方{material.black_value}分。"
    )
    selected = [
        item.description
        for item in section.facts
        if item.fact_id != material.fact_id
    ][:2]
    return evaluation + material_text + "".join(
        text if text.endswith(("。", "！", "？")) else f"{text}。"
        for text in selected
    )


def _fallback_move_text(section: MoveAnalysisSection) -> str:
    classification = {
        "best": "最佳",
        "excellent": "优秀",
        "good": "良好",
        "inaccuracy": "不精确",
        "mistake": "失误",
        "blunder": "严重失误",
        "forced": "唯一合法",
    }.get(section.classification, section.classification)
    if section.same_as_best:
        return (
            f"实战走法{section.actual_move.san}属于{classification}选择，"
            "并且与程序首选一致。"
        )
    best = section.best_move.san if section.best_move else "当前没有完整的程序首选"
    return (
        f"实战走法{section.actual_move.san}被程序归类为{classification}。"
        f"程序给出的主要改进是{best}，应据此比较评价变化与后续路线。"
    )


def _fallback_threat_text(item: ReportThreatItem) -> str:
    names = {
        "mate_threat": "将杀威胁",
        "tactical_capture": "战术吃子",
        "material_win": "赢得子力",
        "promotion_threat": "升变威胁",
        "center_break": "中心突破",
    }
    target = f"，目标为{item.target}" if item.target else ""
    return (
        f"程序确认{_side_name(item.side)}存在{names.get(item.type, '直接威胁')}{target}。"
        "复盘时应优先检查该威胁的执行条件和应对次序。"
    )


def _fallback_plan_text(item: ReportPlanItem) -> str:
    evidence = (
        f"程序结构证据包括{'；'.join(item.structural_evidence[:2])}。"
        if item.structural_evidence else ""
    )
    return f"{_side_name(item.side)}的程序化计划是{item.goal}。{evidence}"


def _fallback_move_summary(section: MoveAnalysisSection) -> str:
    if section.same_as_best:
        return "本步与程序首选一致，复盘重点是理解它对局面的改善作用。"
    if section.best_move is not None:
        return (
            f"本步最大的改进方向是比较实战走法{section.actual_move.san}"
            f"与程序首选{section.best_move.san}的差异。"
        )
    return "本步需要依据现有评价继续比较，不补写程序未确认的观点。"


def _side_name(side: str) -> str:
    return "白方" if side == "white" else "黑方"
