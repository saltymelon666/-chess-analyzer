from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from .position_factor_ranker import FactorFamily, PositionFactorRanking

if TYPE_CHECKING:
    from .chess_facts import ChessFactPackage
    from .position_interpretation import PositionInterpretationPackage
    from .strategic_plans import StrategicPlanPackage
    from .threat_analysis import ThreatPackage


IMPORTANCE_RANKING_VERSION = "1.0"
FAMILIES: tuple[FactorFamily, ...] = (
    "forcing_tactics",
    "king_attack_and_safety",
    "pawn_structure_and_space",
    "piece_activity_and_coordination",
    "conversion_and_compensation",
)


class ImportanceCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_biases: dict[FactorFamily, float] = Field(default_factory=dict)
    minimum_score: float = Field(default=18.0, ge=0, le=100)


class ImportanceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_type: Literal[
        "ranked_factor", "current_threat", "prepared_threat", "verified_plan",
        "analysis_objective", "position_phase",
    ]
    evidence_ref: str
    contribution: float


class ImportanceTheme(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: FactorFamily
    score: float = Field(ge=0, le=100)
    rank: int = Field(ge=1, le=5)
    confidence: Literal["high", "medium", "low"]
    evidence: list[ImportanceEvidence] = Field(default_factory=list)


class PositionImportanceRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = IMPORTANCE_RANKING_VERSION
    position_id: str
    primary_theme: FactorFamily | None = None
    supporting_themes: list[FactorFamily] = Field(default_factory=list, max_length=2)
    ranked_themes: list[ImportanceTheme] = Field(default_factory=list, max_length=5)
    confidence: Literal["high", "medium", "low", "unknown"]
    forbidden_claims: list[str] = Field(default_factory=list)
    boundary: str = (
        "重要性排序只重排程序已经验证的信号；它不能新增棋盘事实、计划、主动权或评价结论。"
    )


PLAN_FAMILIES: dict[str, FactorFamily] = {
    "improve_worst_piece": "piece_activity_and_coordination",
    "prepare_center_break": "pawn_structure_and_space",
    "occupy_open_file": "piece_activity_and_coordination",
    "activate_rook": "piece_activity_and_coordination",
    "improve_king_safety": "king_attack_and_safety",
    "attack_weak_pawn": "pawn_structure_and_space",
    "create_passed_pawn": "pawn_structure_and_space",
    "simplify_endgame": "conversion_and_compensation",
}

OBJECTIVE_FAMILIES: dict[str, FactorFamily] = {
    "forcing_tactics": "forcing_tactics",
    "attack_conversion": "king_attack_and_safety",
    "winning_conversion": "conversion_and_compensation",
    "dynamic_balance": "conversion_and_compensation",
    "endgame_plan": "conversion_and_compensation",
    "strategic_improvement": "piece_activity_and_coordination",
    "move_quality_explanation": "conversion_and_compensation",
}


class PositionImportanceRanker:
    """Rank only verified program signals; source-book prose is never an input."""

    def __init__(self, calibration: ImportanceCalibration | None = None) -> None:
        self.calibration = calibration or ImportanceCalibration()

    def rank(
        self,
        factor_ranking: PositionFactorRanking,
        *,
        fact_package: "ChessFactPackage",
        threat_package: "ThreatPackage",
        plan_package: "StrategicPlanPackage",
        interpretation: "PositionInterpretationPackage",
    ) -> PositionImportanceRanking:
        scores: dict[FactorFamily, float] = {family: 0.0 for family in FAMILIES}
        evidence: dict[FactorFamily, list[ImportanceEvidence]] = {family: [] for family in FAMILIES}

        for factor in [*factor_ranking.dominant_factors, *factor_ranking.supporting_factors]:
            contribution = float(factor.importance_score)
            if contribution > scores[factor.family]:
                scores[factor.family] = contribution
            evidence[factor.family].append(ImportanceEvidence(
                evidence_type="ranked_factor",
                evidence_ref=factor.factor_id,
                contribution=round(contribution, 2),
            ))

        for threat in threat_package.threats:
            if threat.scope != "current_direct_threat":
                continue
            family: FactorFamily = (
                "king_attack_and_safety" if threat.type == "mate_threat" else "forcing_tactics"
            )
            bonus = 30.0 if threat.confidence == "high" else 22.0
            self._add(scores, evidence, family, "current_threat", threat.threat_id, bonus)
        for threat in threat_package.prepared_threats:
            family = "king_attack_and_safety" if threat.type == "mate_threat" else "forcing_tactics"
            self._add(scores, evidence, family, "prepared_threat", threat.threat_id, 12.0)

        for plan in plan_package.plans:
            family = PLAN_FAMILIES[plan.type]
            bonus = 9.0 if plan.confidence == "high" else 6.0
            self._add(scores, evidence, family, "verified_plan", plan.plan_id, bonus)

        objective_family = OBJECTIVE_FAMILIES.get(interpretation.objective.kind)
        if objective_family is not None and interpretation.objective.evidence_ids:
            self._add(
                scores, evidence, objective_family, "analysis_objective",
                interpretation.objective.kind, 5.0,
            )
        if interpretation.position_phase == "endgame":
            self._add(scores, evidence, "conversion_and_compensation", "position_phase", "endgame", 4.0)
            self._add(scores, evidence, "pawn_structure_and_space", "position_phase", "endgame", 2.0)

        for family, bias in self.calibration.family_biases.items():
            if scores[family] > 0:
                scores[family] = max(0.0, min(100.0, scores[family] + bias))

        ordered = sorted(
            (family for family in FAMILIES if scores[family] >= self.calibration.minimum_score),
            key=lambda family: (-scores[family], family),
        )
        themes = []
        for rank, family in enumerate(ordered, start=1):
            score = round(scores[family], 2)
            confidence = "high" if score >= 70 else "medium" if score >= 45 else "low"
            themes.append(ImportanceTheme(
                family=family,
                score=score,
                rank=rank,
                confidence=confidence,
                evidence=evidence[family][:8],
            ))

        primary = ordered[0] if ordered else None
        margin = scores[ordered[0]] - scores[ordered[1]] if len(ordered) > 1 else scores[ordered[0]] if ordered else 0
        overall_confidence: Literal["high", "medium", "low", "unknown"] = (
            "unknown" if primary is None else
            "high" if scores[primary] >= 70 and margin >= 12 else
            "medium" if scores[primary] >= 45 and margin >= 5 else "low"
        )
        forbidden = [
            "不得把候选路线内部事件改写为当前威胁",
            "不得仅凭主题排序声称任何一方拥有主动权",
            "不得生成没有程序证据的计划",
            "不得改变Stockfish评价方向和强度边界",
        ]
        if interpretation.initiative.side == "unknown":
            forbidden.append("主动权证据不足，必须保持unknown")
        return PositionImportanceRanking(
            position_id=factor_ranking.position_id,
            primary_theme=primary,
            supporting_themes=ordered[1:3],
            ranked_themes=themes,
            confidence=overall_confidence,
            forbidden_claims=forbidden,
        )

    @staticmethod
    def _add(
        scores: dict[FactorFamily, float],
        evidence: dict[FactorFamily, list[ImportanceEvidence]],
        family: FactorFamily,
        evidence_type: str,
        evidence_ref: str,
        contribution: float,
    ) -> None:
        scores[family] = min(100.0, scores[family] + contribution)
        evidence[family].append(ImportanceEvidence(
            evidence_type=evidence_type,
            evidence_ref=evidence_ref,
            contribution=contribution,
        ))
