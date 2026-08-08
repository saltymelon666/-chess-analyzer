from __future__ import annotations

from collections import defaultdict
from typing import Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

from .chess_facts import ChessFactPackage, FactCandidateRoute
from .chess_reasoning_rules import ChessReasoningRuleEngine, ChessReasoningRulePackage
from .evaluation_sensitivity import EvaluationSensitivityPackage
from .position_factor_ranker import FactorFamily, PositionFactorRanking
from .position_importance_ranker import FAMILIES, PositionImportanceRanking


THEME_CAUSAL_VERSION = "1.0"
CausalThemeStatus = Literal["confirmed", "supporting", "unproven"]


class ThemeCausalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: FactorFamily
    base_score: float = Field(ge=0, le=100)
    all_route_coverage: float = Field(ge=0, le=1)
    stable_route_coverage: float = Field(ge=0, le=1)
    ignore_loss_cp: int = Field(ge=0, le=1000)
    continuation_persistence: float | None = Field(default=None, ge=0, le=1)
    direct_current_evidence: bool = False
    causal_status: CausalThemeStatus
    causal_score: float = Field(ge=0, le=100)
    supporting_route_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    explanation: str


class ThemeCausalImportancePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = THEME_CAUSAL_VERSION
    position_id: str
    primary_theme: FactorFamily | None = None
    supporting_themes: list[FactorFamily] = Field(default_factory=list, max_length=2)
    themes: list[ThemeCausalEvidence] = Field(default_factory=list, max_length=5)
    confidence: Literal["high", "medium", "low", "unknown"]
    forbidden_claims: list[str] = Field(default_factory=list, min_length=4)
    boundary: str = (
        "主题级因果分数只重排当前程序已经识别的主题；MultiPV覆盖和候选着分差不能单独证明"
        "静态主题是评价原因，也不能创建新事实、计划或主动权结论。"
    )


class ThemeCausalImportanceAnalyzer:
    """Aggregate factor evidence into conservative, theme-level causal importance."""

    def analyze(
        self,
        fact_package: ChessFactPackage,
        rule_package: ChessReasoningRulePackage,
        factor_ranking: PositionFactorRanking,
        sensitivity: EvaluationSensitivityPackage,
        importance: PositionImportanceRanking,
    ) -> ThemeCausalImportancePackage:
        routes = [
            route for route in fact_package.candidate_routes
            if route.verified and route.moves_uci
        ]
        routes.sort(key=lambda route: _route_rank(route.route_id))
        stable_ids = set(sensitivity.stable_route_ids)
        signals = {signal.signal_id: signal for signal in rule_package.signals}
        factor_evidence = {item.factor_id: item for item in sensitivity.factors}
        factors_by_family = defaultdict(list)
        for factor in [*factor_ranking.dominant_factors, *factor_ranking.supporting_factors]:
            factors_by_family[factor.family].append(factor)
        base_by_family = {
            theme.family: theme.score for theme in importance.ranked_themes
        }
        direct_by_family = {
            theme.family: any(item.evidence_type == "current_threat" for item in theme.evidence)
            for theme in importance.ranked_themes
        }
        persistent = self._continuation_persistence(fact_package.position.fen, routes)
        results = []
        for family in FAMILIES:
            base = float(base_by_family.get(family, 0.0))
            factors = factors_by_family.get(family, [])
            if base <= 0 and not factors:
                continue
            explicit_moves = {
                move
                for factor in factors
                for reference in factor.evidence_refs
                if reference in signals
                for move in signals[reference].moves
            }
            supporting = [route for route in routes if _route_supports(route, explicit_moves)]
            supporting_ids = {route.route_id for route in supporting}
            all_coverage = len(supporting) / len(routes) if routes else 0.0
            stable_coverage = (
                len(supporting_ids & stable_ids) / len(stable_ids) if stable_ids else 0.0
            )
            ignore_loss = _ignore_loss(
                routes, supporting, fact_package.position.side_to_move,
            )
            factor_ignore = max(
                (
                    factor_evidence[factor.factor_id].evaluation_sensitivity_cp
                    for factor in factors if factor.factor_id in factor_evidence
                ),
                default=0,
            )
            ignore_loss = max(ignore_loss, factor_ignore)
            direct = direct_by_family.get(family, False)
            persistence = (
                None if family in {"forcing_tactics", "conversion_and_compensation"}
                else persistent.get(family, 0.0)
            )
            score = _causal_score(
                base=base,
                route_coverage=all_coverage,
                stable_coverage=stable_coverage,
                ignore_loss=ignore_loss,
                persistence=persistence,
                direct=direct,
            )
            status = _causal_status(
                route_coverage=all_coverage,
                stable_coverage=stable_coverage,
                ignore_loss=ignore_loss,
                persistence=persistence,
                direct=direct,
            )
            refs = list(dict.fromkeys(
                [factor.factor_id for factor in factors]
                + [reference for factor in factors for reference in factor.evidence_refs]
            ))
            results.append(ThemeCausalEvidence(
                family=family,
                base_score=round(base, 2),
                all_route_coverage=round(all_coverage, 4),
                stable_route_coverage=round(stable_coverage, 4),
                ignore_loss_cp=ignore_loss,
                continuation_persistence=(round(persistence, 4) if persistence is not None else None),
                direct_current_evidence=direct,
                causal_status=status,
                causal_score=round(score, 2),
                supporting_route_ids=[route.route_id for route in supporting],
                evidence_refs=refs[:12],
                explanation=_explanation(
                    status, all_coverage, stable_coverage, ignore_loss, persistence, direct,
                ),
            ))
        results.sort(key=lambda item: (-item.causal_score, item.family))
        primary = results[0].family if results else None
        margin = (
            results[0].causal_score - results[1].causal_score
            if len(results) > 1 else results[0].causal_score if results else 0.0
        )
        confidence: Literal["high", "medium", "low", "unknown"] = (
            "unknown" if not results else
            "high" if results[0].causal_status == "confirmed" and margin >= 12 else
            "medium" if results[0].causal_status != "unproven" and margin >= 5 else
            "low"
        )
        return ThemeCausalImportancePackage(
            position_id=importance.position_id,
            primary_theme=primary,
            supporting_themes=[item.family for item in results[1:3]],
            themes=results,
            confidence=confidence,
            forbidden_claims=[
                "不得把候选路线内部事件升级为当前直接威胁",
                "不得把MultiPV分差单独解释为静态主题的因果证明",
                "不得从主题分数创建未经验证的计划",
                "不得从主题分数推导主动权、物质或胜负结论",
            ],
        )

    @staticmethod
    def _continuation_persistence(
        fen: str,
        routes: list[FactCandidateRoute],
    ) -> dict[FactorFamily, float]:
        if not routes:
            return {}
        counts: defaultdict[FactorFamily, int] = defaultdict(int)
        for route in routes:
            board = chess.Board(fen)
            try:
                move = chess.Move.from_uci(route.moves_uci[0])
                if move not in board.legal_moves:
                    continue
                board.push(move)
                package = ChessReasoningRuleEngine().evaluate(board.fen())
            except (ValueError, IndexError):
                continue
            present = {_signal_family(signal.category) for signal in package.signals}
            for family in present:
                counts[family] += 1
        return {family: counts[family] / len(routes) for family in counts}


def _signal_family(category: str) -> FactorFamily:
    if category == "tactics":
        return "forcing_tactics"
    if category == "king_safety":
        return "king_attack_and_safety"
    if category in {"pawn_structure", "space_and_lines"}:
        return "pawn_structure_and_space"
    if category == "piece_coordination":
        return "piece_activity_and_coordination"
    return "conversion_and_compensation"


def _route_supports(route: FactCandidateRoute, moves: set[str]) -> bool:
    return bool(moves.intersection(route.moves_uci) or moves.intersection(route.moves_san))


def _ignore_loss(
    routes: list[FactCandidateRoute],
    supporting: list[FactCandidateRoute],
    root_side: str,
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


def _causal_score(
    *,
    base: float,
    route_coverage: float,
    stable_coverage: float,
    ignore_loss: int,
    persistence: float | None,
    direct: bool,
) -> float:
    components = [
        (0.35, base / 100),
        (0.15, route_coverage),
        (0.15, stable_coverage),
        (0.20, min(ignore_loss / 200, 1.0)),
        (0.10, 1.0 if direct else 0.0),
    ]
    if persistence is not None:
        components.append((0.15, persistence))
    total_weight = sum(weight for weight, _ in components)
    return min(100.0, 100 * sum(weight * value for weight, value in components) / total_weight)


def _causal_status(
    *,
    route_coverage: float,
    stable_coverage: float,
    ignore_loss: int,
    persistence: float | None,
    direct: bool,
) -> CausalThemeStatus:
    if direct and stable_coverage >= 0.5:
        return "confirmed"
    if stable_coverage >= 0.5 and ignore_loss >= 75:
        return "confirmed"
    if route_coverage > 0 or stable_coverage > 0 or ignore_loss >= 25:
        return "supporting"
    if persistence is not None and persistence >= 0.67:
        return "supporting"
    return "unproven"


def _explanation(
    status: CausalThemeStatus,
    route_coverage: float,
    stable_coverage: float,
    ignore_loss: int,
    persistence: float | None,
    direct: bool,
) -> str:
    parts = [
        f"候选路线覆盖{route_coverage:.0%}",
        f"稳定路线覆盖{stable_coverage:.0%}",
        f"替代路线评价损失上限约{ignore_loss / 100:.2f}兵",
    ]
    if persistence is not None:
        parts.append(f"执行根着后主题持续率{persistence:.0%}")
    if direct:
        parts.append("存在当前直接威胁证据")
    prefix = "已确认" if status == "confirmed" else "有支持" if status == "supporting" else "尚未证明"
    return f"{prefix}：" + "；".join(parts) + "。"


def _numeric_score(route: FactCandidateRoute) -> int | None:
    if route.mate is not None:
        return 100_000 if route.mate > 0 else -100_000
    return route.evaluation


def _route_rank(route_id: str) -> tuple[int, str]:
    suffix = route_id.rsplit("_", 1)[-1]
    return (int(suffix), route_id) if suffix.isdigit() else (999, route_id)
