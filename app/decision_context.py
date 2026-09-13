from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import MoveReview


DECISION_CONTEXT_VERSION = "1.2"


class RecentDecisionSignal(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    plies_ago: int = Field(alias="pliesAgo", ge=1, le=5)
    side: Literal["white", "black"]
    quality: str
    loss_band: Literal["none", "small", "moderate", "large"] = Field(alias="lossBand")
    matched_first_choice: bool | None = Field(alias="matchedFirstChoice")


class DecisionContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    version: Literal["1.2"] = DECISION_CONTEXT_VERSION
    history_scope: str = Field(alias="historyScope")
    trend: Literal["first_decision", "stable", "repeated_non_best", "repeated_large_loss"]
    core_pain_point: str = Field(alias="corePainPoint", min_length=1)
    must_answer: list[str] = Field(alias="mustAnswer", min_length=2, max_length=3)
    recent_signals: list[RecentDecisionSignal] = Field(
        alias="recentSignals",
        default_factory=list,
        max_length=5,
    )
    boundary: str = (
        "历史信号只描述已经由程序确认的着法质量与首选一致性；它不能证明棋手主观意图，"
        "也不能把不同局面的偏差自动归为同一种棋理错误。"
    )

    def prompt_payload(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


def build_decision_context(
    current: MoveReview,
    recent_moves: Sequence[MoveReview],
    *,
    objective_kind: str,
    objective_question: str,
) -> DecisionContext:
    window = list(recent_moves)[-5:]
    signals = [
        RecentDecisionSignal(
            pliesAgo=len(window) - index,
            side=move.side,
            quality=move.quality_label,
            lossBand=_loss_band(move.centipawn_loss),
            matchedFirstChoice=_matched_first_choice(move),
        )
        for index, move in enumerate(window)
    ]
    same_side = [
        signal for signal in signals
        if signal.side == current.side
    ]
    current_match = _matched_first_choice(current)
    current_large_loss = _loss_band(current.centipawn_loss) == "large"
    prior_non_best = sum(signal.matched_first_choice is False for signal in same_side)
    prior_large_loss = sum(signal.loss_band == "large" for signal in same_side)

    if not signals:
        trend: Literal[
            "first_decision", "stable", "repeated_non_best", "repeated_large_loss"
        ] = "first_decision"
    elif current_large_loss and prior_large_loss:
        trend = "repeated_large_loss"
    elif current_match is False and prior_non_best:
        trend = "repeated_non_best"
    else:
        trend = "stable"

    core_pain_point = _core_pain_point(
        current,
        objective_kind=objective_kind,
        objective_question=objective_question,
        trend=trend,
    )
    must_answer = [
        core_pain_point,
        (
            "实战着与首选相同，只解释这步如何回应局面矛盾，不制造两者差异。"
            if current_match is True else
            "评价差距很小，只在机制证据充分时讲两种选择的侧重点，不强分高下或补写惩罚。"
            if current.centipawn_loss is not None and current.centipawn_loss < 50 else
            "有不同候选及机制证据时，围绕同一问题解释作用与代价；证据不足不能从分数倒推原因。"
        ),
    ]
    if trend == "repeated_large_loss":
        must_answer.append("指出同一方近期多次出现明显评价损失，但不得臆测这些失误具有相同主观原因。")
    elif trend == "repeated_non_best":
        must_answer.append("近期多次偏离首选只作背景，不据此认定当前选择有错；只解释本步已有证据支持的具体作用。")

    return DecisionContext(
        historyScope="当前着之前最多5个半回合；只保留程序确认的决策信号",
        trend=trend,
        corePainPoint=core_pain_point,
        mustAnswer=must_answer,
        recentSignals=signals,
    )


def decision_history_signature(recent_moves: Sequence[MoveReview]) -> list[dict[str, object]]:
    """Stable cache input that omits board state and free-form prose."""
    return [
        {
            "side": move.side,
            "played": move.played_move.uci,
            "best": move.best_move_uci,
            "quality": move.quality_key,
            "loss": move.centipawn_loss,
        }
        for move in list(recent_moves)[-5:]
    ]


def _matched_first_choice(move: MoveReview) -> bool | None:
    if not move.best_move_uci:
        return None
    return move.played_move.uci == move.best_move_uci


def _loss_band(loss: int | None) -> Literal["none", "small", "moderate", "large"]:
    if loss is None or loss <= 0:
        return "none"
    if loss < 50:
        return "small"
    if loss < 150:
        return "moderate"
    return "large"


def _core_pain_point(
    current: MoveReview,
    *,
    objective_kind: str,
    objective_question: str,
    trend: str,
) -> str:
    if _loss_band(current.centipawn_loss) == "large":
        return "先解释本步为何造成明显评价损失，以及首选路线优先处理了哪个当前问题。"
    if _matched_first_choice(current) is False and (
        current.centipawn_loss is not None and current.centipawn_loss < 50
    ):
        return "先解释当前局面的核心矛盾与实战着的客观作用；评价差距很小，不制造未解决问题或受到惩罚的结论。"
    if trend == "repeated_non_best":
        return "先解释当前局面的首要矛盾与实战着的作用，再按本步证据说明效果或代价，不由历史偏离推断当前机制。"
    if objective_kind == "move_quality_explanation" and _matched_first_choice(current) is False:
        return "先说明实战着的合理意图，再指出它与首选路线在处理当前首要问题上的本质差异。"
    return objective_question
