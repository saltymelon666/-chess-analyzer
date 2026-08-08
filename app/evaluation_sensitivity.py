from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .chess_facts import ChessFactPackage, FactCandidateRoute
from .chess_reasoning_rules import ChessReasoningRulePackage
from .position_factor_ranker import PositionFactorRanking, RankedPositionFactor


CausalStatus = Literal["confirmed", "supporting", "unproven"]


class FactorCausalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor_id: str
    causal_status: CausalStatus
    best_route_supports: bool
    supporting_route_ids: list[str] = Field(default_factory=list)
    stable_route_coverage: float = Field(ge=0, le=1)
    evaluation_sensitivity_cp: int = Field(ge=0)
    reason: str


class EvaluationSensitivityPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = "1.0"
    position_id: str
    root_side: Literal["white", "black"]
    route_count: int
    stable_route_ids: list[str] = Field(default_factory=list)
    factors: list[FactorCausalEvidence] = Field(default_factory=list)
    boundary: str = (
        "MultiPV分差只能说明候选着的评价敏感度，不能单独证明某个静态主题是评价原因。"
    )


class EvaluationSensitivityAnalyzer:
    """Attach conservative route and score evidence to already-ranked factors."""

    def __init__(self, *, stable_route_threshold_cp: int = 100) -> None:
        self.stable_route_threshold_cp = max(0, stable_route_threshold_cp)

    def analyze(
        self,
        ranking: PositionFactorRanking,
        rule_package: ChessReasoningRulePackage,
        fact_package: ChessFactPackage,
    ) -> EvaluationSensitivityPackage:
        routes = [route for route in fact_package.candidate_routes if route.verified and route.moves_uci]
        routes.sort(key=lambda route: _route_rank(route.route_id))
        stable = self._stable_routes(routes, fact_package.position.side_to_move)
        signals = {signal.signal_id: signal for signal in rule_package.signals}
        factors = []
        for factor in [*ranking.dominant_factors, *ranking.supporting_factors]:
            explicit_moves = {
                move
                for reference in factor.evidence_refs
                if reference in signals
                for move in signals[reference].moves
            }
            supporting = [
                route for route in stable
                if explicit_moves.intersection(route.moves_uci)
            ]
            best_supports = bool(routes and routes[0] in supporting)
            sensitivity = self._sensitivity(routes, supporting, fact_package.position.side_to_move)
            coverage = len(supporting) / len(stable) if stable else 0.0
            status = self._status(best_supports, coverage, sensitivity, bool(explicit_moves))
            factors.append(FactorCausalEvidence(
                factor_id=factor.factor_id,
                causal_status=status,
                best_route_supports=best_supports,
                supporting_route_ids=[route.route_id for route in supporting],
                stable_route_coverage=round(coverage, 4),
                evaluation_sensitivity_cp=sensitivity,
                reason=self._reason(status, best_supports, coverage, sensitivity, bool(explicit_moves)),
            ))
        return EvaluationSensitivityPackage(
            position_id=ranking.position_id,
            root_side=fact_package.position.side_to_move,
            route_count=len(routes),
            stable_route_ids=[route.route_id for route in stable],
            factors=factors,
        )

    def _stable_routes(
        self, routes: list[FactCandidateRoute], root_side: str,
    ) -> list[FactCandidateRoute]:
        if not routes:
            return []
        best = _numeric_score(routes[0])
        if best is None:
            return routes[:1]
        stable = []
        for route in routes:
            value = _numeric_score(route)
            if value is None:
                continue
            loss = best - value if root_side == "white" else value - best
            if loss <= self.stable_route_threshold_cp:
                stable.append(route)
        return stable or routes[:1]

    @staticmethod
    def _sensitivity(
        routes: list[FactCandidateRoute], supporting: list[FactCandidateRoute], root_side: str,
    ) -> int:
        if not routes or routes[0] not in supporting:
            return 0
        unsupported = [route for route in routes if route not in supporting]
        if not unsupported:
            return 0
        best = _numeric_score(routes[0])
        alternative = _numeric_score(unsupported[0])
        if best is None or alternative is None:
            return 1000 if routes[0].mate is not None and unsupported[0].mate is None else 0
        loss = best - alternative if root_side == "white" else alternative - best
        return max(0, min(int(loss), 1000))

    @staticmethod
    def _status(
        best_supports: bool, coverage: float, sensitivity: int, has_explicit_moves: bool,
    ) -> CausalStatus:
        if has_explicit_moves and best_supports and coverage >= 0.5 and sensitivity >= 75:
            return "confirmed"
        if has_explicit_moves and best_supports and (coverage >= 0.5 or sensitivity >= 25):
            return "supporting"
        return "unproven"

    @staticmethod
    def _reason(
        status: CausalStatus, best_supports: bool, coverage: float, sensitivity: int, has_moves: bool,
    ) -> str:
        if not has_moves:
            return "该因素没有可与Stockfish路线对应的具体着法，只保留为静态候选主题。"
        if status == "confirmed":
            return f"最佳路线支持该因素，稳定路线覆盖{coverage:.0%}，不采用相关着法最多损失约{sensitivity / 100:.2f}兵。"
        if status == "supporting":
            return f"最佳路线支持该因素，但覆盖或评价分差不足以确认其为主导原因。"
        if not best_supports:
            return "相关着法没有出现在Stockfish首选路线中，不能把该因素升级为主导原因。"
        return "路线证据不足，暂不确认该因素的因果重要性。"


def apply_causal_scores(
    ranking: PositionFactorRanking,
    sensitivity: EvaluationSensitivityPackage,
) -> PositionFactorRanking:
    evidence = {item.factor_id: item for item in sensitivity.factors}
    factors: list[RankedPositionFactor] = []
    for factor in [*ranking.dominant_factors, *ranking.supporting_factors]:
        item = factor.model_copy(deep=True)
        causal = evidence.get(factor.factor_id)
        if causal is not None:
            bonus = 18 if causal.causal_status == "confirmed" else 7 if causal.causal_status == "supporting" else 0
            item.importance_score = min(100, round(item.importance_score + bonus, 2))
            item.why_it_matters = causal.reason
        factors.append(item)
    factors.sort(key=lambda item: (-item.importance_score, item.family, item.side))
    for index, factor in enumerate(factors, start=1):
        factor.role = "dominant" if index <= 2 else "supporting"
    return PositionFactorRanking(
        position_id=ranking.position_id,
        dominant_factors=factors[:2],
        supporting_factors=factors[2:5],
        rejected_signal_count=ranking.rejected_signal_count,
        boundary=ranking.boundary,
    )


def _numeric_score(route: FactCandidateRoute) -> int | None:
    if route.mate is not None:
        return 100_000 if route.mate > 0 else -100_000
    return route.evaluation


def _route_rank(route_id: str) -> tuple[int, str]:
    suffix = route_id.rsplit("_", 1)[-1]
    return (int(suffix), route_id) if suffix.isdigit() else (999, route_id)
