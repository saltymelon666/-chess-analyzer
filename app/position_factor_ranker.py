from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .chess_reasoning_rules import ChessReasoningRulePackage, ChessReasoningSignal, RULES_BY_ID


FactorFamily = Literal[
    "forcing_tactics",
    "king_attack_and_safety",
    "pawn_structure_and_space",
    "piece_activity_and_coordination",
    "conversion_and_compensation",
]


class RankedPositionFactor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor_id: str
    family: FactorFamily
    side: Literal["white", "black", "both"]
    role: Literal["dominant", "supporting"]
    importance_score: float = Field(ge=0, le=100)
    label_zh: str
    rule_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    why_it_matters: str


class PositionFactorRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = "1.0"
    position_id: str
    dominant_factors: list[RankedPositionFactor] = Field(default_factory=list, max_length=2)
    supporting_factors: list[RankedPositionFactor] = Field(default_factory=list, max_length=3)
    rejected_signal_count: int = 0
    boundary: str = (
        "显著性排序只决定哪些证据信号值得优先解释；不能单独确认主动权、强制性或胜负。"
    )


FAMILY_LABELS: dict[FactorFamily, str] = {
    "forcing_tactics": "强制战术",
    "king_attack_and_safety": "王安全",
    "pawn_structure_and_space": "兵形与空间",
    "piece_activity_and_coordination": "子力活动与协调",
    "conversion_and_compensation": "转换与补偿",
}


BASE_IMPORTANCE = {
    "tactic.immediate_checkmate": 100,
    "tactic.back_rank_mate": 100,
    "king.in_check": 82,
    "king.mate_threat": 88,
    "tactic.fork": 72,
    "tactic.double_attack": 68,
    "tactic.hanging_piece_capture": 66,
    "tactic.favorable_capture": 62,
    "tactic.absolute_pin": 56,
    "tactic.discovered_check": 62,
    "tactic.immediate_check": 46,
    "tactic.immediate_capture": 20,
    "tactic.trapped_piece": 38,
    "pawn.connected_passed": 68,
    "pawn.protected_passed": 62,
    "pawn.passed": 55,
    "pawn.iqp": 48,
    "pawn.backward": 42,
    "pawn.isolated": 34,
    "pawn.doubled": 30,
    "king.ring_pressure": 52,
    "king.multiple_attackers": 62,
    "king.missing_pawn_shield": 44,
    "king.open_file": 50,
    "king.low_flight_squares": 46,
    "piece.rook_seventh": 58,
    "piece.outpost": 48,
    "piece.rook_open_file": 42,
    "piece.worst_piece": 36,
    "piece.undefended": 24,
    "piece.active": 22,
    "piece.constrained": 26,
    "space.advantage": 46,
    "space.center_control": 38,
    "space.restriction": 50,
    "line.open_diagonal": 24,
}


EVALUATION_FAMILIES: dict[str, FactorFamily] = {
    "evaluation.material_source": "conversion_and_compensation",
    "evaluation.tactical_source": "forcing_tactics",
    "evaluation.king_safety_source": "king_attack_and_safety",
    "evaluation.structure_source": "pawn_structure_and_space",
    "evaluation.activity_source": "piece_activity_and_coordination",
}
PLAN_FAMILIES: dict[str, FactorFamily] = {
    "plan.improve_worst_piece": "piece_activity_and_coordination",
    "plan.center_break": "pawn_structure_and_space",
    "plan.occupy_open_file": "piece_activity_and_coordination",
    "plan.activate_rook": "piece_activity_and_coordination",
    "plan.king_safety": "king_attack_and_safety",
    "plan.attack_weak_pawn": "pawn_structure_and_space",
    "plan.create_passed_pawn": "pawn_structure_and_space",
    "plan.simplify": "conversion_and_compensation",
    "plan.activate_king": "piece_activity_and_coordination",
}


def _family(signal: ChessReasoningSignal) -> FactorFamily | None:
    if signal.rule_id in {"evaluation.initiative_gate", "plan.prophylaxis"}:
        return None
    if signal.rule_id in EVALUATION_FAMILIES:
        return EVALUATION_FAMILIES[signal.rule_id]
    if signal.rule_id in PLAN_FAMILIES:
        return PLAN_FAMILIES[signal.rule_id]
    if signal.category == "tactics":
        return "forcing_tactics"
    if signal.category == "king_safety":
        return "king_attack_and_safety"
    if signal.category in {"pawn_structure", "space_and_lines"}:
        return "pawn_structure_and_space"
    if signal.category == "piece_coordination":
        return "piece_activity_and_coordination"
    return "conversion_and_compensation"


class PositionFactorRanker:
    """Compress raw rule hits into a few evidence-linked factors."""

    def rank(
        self,
        package: ChessReasoningRulePackage,
        *,
        rule_prevalence: dict[str, float] | None = None,
    ) -> PositionFactorRanking:
        prevalence = rule_prevalence or {}
        grouped: dict[tuple[FactorFamily, str], list[tuple[float, ChessReasoningSignal]]] = defaultdict(list)
        for signal in package.signals:
            family = _family(signal)
            if family is None:
                continue
            score = self._signal_score(signal, prevalence.get(signal.rule_id, 0.0))
            grouped[(family, signal.side)].append((score, signal))
        factors = []
        for (family, side), entries in grouped.items():
            entries.sort(key=lambda item: (-item[0], item[1].rule_id, item[1].signal_id))
            distinct_rules = sorted({signal.rule_id for _, signal in entries})
            aggregate = min(100.0, entries[0][0] + min(12, max(0, len(distinct_rules) - 1) * 2))
            evidence = []
            for _, signal in entries:
                if signal.signal_id not in evidence:
                    evidence.append(signal.signal_id)
                if len(evidence) == 5:
                    break
            top_rule = RULES_BY_ID[entries[0][1].rule_id]
            factors.append((aggregate, family, side, distinct_rules, evidence, top_rule.name_zh))
        factors.sort(key=lambda item: (-item[0], item[1], item[2]))
        selected = factors[:5]
        ranked: list[RankedPositionFactor] = []
        for index, (score, family, side, rules, evidence, top_name) in enumerate(selected, start=1):
            role: Literal["dominant", "supporting"] = "dominant" if index <= 2 else "supporting"
            ranked.append(RankedPositionFactor(
                factor_id=f"factor_{index}", family=family, side=side, role=role,
                importance_score=round(score, 2), label_zh=FAMILY_LABELS[family],
                rule_ids=rules[:8], evidence_refs=evidence,
                why_it_matters=f"该组最高优先信号为“{top_name}”，并有{len(rules)}种不同规则支持。",
            ))
        return PositionFactorRanking(
            position_id=package.position_id,
            dominant_factors=ranked[:2],
            supporting_factors=ranked[2:5],
            rejected_signal_count=max(0, len(package.signals) - sum(len(item.evidence_refs) for item in ranked)),
        )

    @staticmethod
    def _signal_score(signal: ChessReasoningSignal, prevalence: float) -> float:
        score = BASE_IMPORTANCE.get(signal.rule_id, 32.0)
        definition = RULES_BY_ID[signal.rule_id]
        if definition.automation in {"stockfish_route", "stockfish_multipv"}:
            score += 18
        if signal.scope == "candidate_route":
            score -= 8
        elif signal.scope == "interpretation":
            score += 5
        if signal.confidence == "high":
            score += 4
        prevalence = max(0.0, min(prevalence, 1.0))
        score *= 0.55 + 0.45 * (1.0 - prevalence)
        return max(0.0, min(score, 100.0))
